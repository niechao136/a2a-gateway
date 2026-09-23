"""连接器管理路由：管理端 CRUD + 平台 webhook 回调。

- admin_router：/api/admin/connectors（JWT 管理员认证，风格对齐 admin.py）
- webhook_router：/api/connectors（无 admin 鉴权，安全依赖平台验签）
"""

import asyncio
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..connectors.base import VerifyError
from ..connectors.pipeline import REPLY_BUSY, ConnectorRef, enqueue_message, seen_recently
from ..connectors.registry import get_adapter
from ..connectors.telegram import register_webhook
from ..database import get_session
from ..deps import get_current_admin
from ..models import AdminUser, ChatConnector
from ..public_url import public_base_url
from ..repository import (
    agent_name_map,
    connector_last_active_map,
    create_connector,
    delete_connector,
    get_agent_by_id,
    get_connector,
    get_connector_by_name,
    list_connectors,
    list_recent_connector_conversations,
    update_connector,
)
from ..schemas import (
    ConnectorConversationOut,
    ConnectorCreate,
    ConnectorOut,
    ConnectorSendRequest,
    ConnectorUpdate,
    mask_connector_credentials,
    merge_connector_credentials,
)

logger = logging.getLogger(__name__)
_settings = get_settings()

admin_router = APIRouter(prefix="/api/admin/connectors", tags=["connectors"])
webhook_router = APIRouter(prefix="/api/connectors", tags=["connectors"])


def _connector_base_url(request: Request) -> str:
    """Webhook 地址基址：PUBLIC_BASE_URL 优先；未配置时按当前访问地址推导（入口自适应）。"""
    return _settings.public_base_url.rstrip("/") or public_base_url(request)


def _webhook_url(connector: ChatConnector, base_url: str) -> str:
    path = f"/api/connectors/{connector.platform.value}/{connector.id}/webhook"
    return f"{base_url}{path}" if base_url else path


def _connector_out(
    connector: ChatConnector,
    agent_name: str,
    base_url: str,
    last_active_at: datetime | None = None,
    setup_warning: str = "",
) -> ConnectorOut:
    return ConnectorOut(
        id=connector.id,
        name=connector.name,
        description=connector.description or "",
        platform=connector.platform.value,
        agent_id=connector.agent_id,
        agent_name=agent_name,
        enabled=connector.enabled,
        webhook_url=_webhook_url(connector, base_url),
        credentials_masked=mask_connector_credentials(
            connector.platform.value, connector.credentials
        ),
        setup_warning=setup_warning,
        last_active_at=last_active_at,
        created_at=connector.created_at,
        updated_at=connector.updated_at,
    )


async def _get_or_404(session: AsyncSession, connector_id: int) -> ChatConnector:
    connector = await get_connector(session, connector_id)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "连接器不存在")
    return connector


async def _maybe_register_telegram(
    session: AsyncSession, connector: ChatConnector, base_url: str
) -> str:
    """Telegram 且启用时自动注册 webhook；返回警告（空串 = 成功或不需要）。"""
    if connector.platform.value != "telegram" or not connector.enabled:
        return ""
    old = dict(connector.credentials or {})
    updated, warning = await register_webhook(old, connector.id, base_url)
    if updated != old:
        connector.credentials = updated
        await update_connector(session, connector, {"credentials": updated})
    return warning


@admin_router.get("", response_model=list[ConnectorOut])
async def list_all(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    connectors = await list_connectors(session)
    names = await agent_name_map(session, [c.agent_id for c in connectors])
    last_active = await connector_last_active_map(session, [c.id for c in connectors])
    base_url = _connector_base_url(request)
    return [
        _connector_out(c, names.get(c.agent_id, ""), base_url, last_active.get(c.id))
        for c in connectors
    ]


@admin_router.post("", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
async def create(
    req: ConnectorCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    if await get_connector_by_name(session, req.name.strip()):
        raise HTTPException(status.HTTP_409_CONFLICT, "连接器名称已存在")
    agent = await get_agent_by_id(session, req.agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "绑定的 Agent 不存在")
    credentials = dict(req.credentials)
    if req.platform == "telegram" and not (credentials.get("secret_token") or "").strip():
        credentials["secret_token"] = secrets.token_urlsafe(32)
    base_url = _connector_base_url(request)
    connector = await create_connector(session, req, credentials)
    warning = await _maybe_register_telegram(session, connector, base_url)
    return _connector_out(connector, agent.name, base_url, setup_warning=warning)


@admin_router.put("/{connector_id}", response_model=ConnectorOut)
async def update(
    connector_id: int,
    req: ConnectorUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    connector = await _get_or_404(session, connector_id)
    changes: dict = {}
    if req.name is not None and req.name.strip() != connector.name:
        existing = await get_connector_by_name(session, req.name.strip())
        if existing is not None and existing.id != connector.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "连接器名称已存在")
        changes["name"] = req.name.strip()
    if req.description is not None:
        changes["description"] = req.description
    if req.agent_id is not None and req.agent_id != connector.agent_id:
        if await get_agent_by_id(session, req.agent_id) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "绑定的 Agent 不存在")
        changes["agent_id"] = req.agent_id
    if req.enabled is not None:
        changes["enabled"] = req.enabled
    if req.credentials is not None:
        merged = merge_connector_credentials(
            connector.platform.value, connector.credentials, req.credentials
        )
        if merged != dict(connector.credentials or {}):
            changes["credentials"] = merged
    connector = await update_connector(session, connector, changes)
    base_url = _connector_base_url(request)
    warning = await _maybe_register_telegram(session, connector, base_url)
    agent = await get_agent_by_id(session, connector.agent_id)
    return _connector_out(
        connector,
        agent.name if agent is not None else "",
        base_url,
        setup_warning=warning,
    )


@admin_router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(
    connector_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    connector = await _get_or_404(session, connector_id)
    await delete_connector(session, connector)


@admin_router.get(
    "/{connector_id}/conversations", response_model=list[ConnectorConversationOut]
)
async def conversations(
    connector_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    connector = await _get_or_404(session, connector_id)
    return await list_recent_connector_conversations(session, connector.id)


@admin_router.post("/{connector_id}/send")
async def send_message(
    connector_id: int,
    req: ConnectorSendRequest,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """主动推送（系统侧通知的最小闭环 + 管理页发送测试）。"""
    connector = await _get_or_404(session, connector_id)
    if not connector.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "连接器已停用")
    chat_id = req.chat_id
    if not chat_id:
        recent = await list_recent_connector_conversations(session, connector.id, limit=1)
        if not recent:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "该连接器暂无会话，请先在聊天应用中发起对话"
            )
        chat_id = recent[0].chat_id
    adapter = get_adapter(connector.platform.value)
    await adapter.send(dict(connector.credentials or {}), chat_id, req.text)
    return {"chat_id": chat_id, "ok": True}


# ---------------------------------------------------------------------------
# 平台 webhook 回调（无 admin 鉴权；安全 = 平台验签 → connector 定位 → 事件去重）
# ---------------------------------------------------------------------------
async def _busy_reply(adapter, credentials: dict, chat_id: str) -> None:
    try:
        await adapter.send(credentials, chat_id, REPLY_BUSY)
    except Exception:
        logger.warning("忙提示发送失败 chat=%s", chat_id, exc_info=True)


@webhook_router.post("/{platform}/{connector_id}/webhook")
async def platform_webhook(
    platform: str,
    connector_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """平台事件入口：先处理接入握手，再验签归一化，最后异步投递并立即确认。"""
    body = await request.body()
    try:
        adapter = get_adapter(platform)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "不支持的平台")
    connector = await get_connector(session, connector_id)
    if connector is None or connector.platform.value != platform or not connector.enabled:
        # 停用与不存在对外不暴露差异
        raise HTTPException(status.HTTP_404_NOT_FOUND, "连接器不存在")
    credentials = dict(connector.credentials or {})
    try:
        challenge = await adapter.build_challenge(body, request.headers, credentials)
    except VerifyError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "验签失败")
    if challenge is not None:
        return challenge
    try:
        messages = await adapter.verify_and_parse(body, request.headers, credentials)
    except VerifyError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "验签失败")

    ref = ConnectorRef(
        id=connector.id,
        name=connector.name,
        platform=connector.platform.value,
        credentials=credentials,
        agent_id=connector.agent_id,
        enabled=connector.enabled,
    )
    for message in messages:
        dedup_key = f"{platform}:{connector_id}:{message.event_id}"
        if seen_recently(dedup_key):
            continue
        if not enqueue_message(ref, message):
            asyncio.create_task(_busy_reply(adapter, credentials, message.chat_id))
    return {"ok": True}
