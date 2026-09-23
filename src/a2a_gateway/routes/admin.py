"""管理中心路由：Agent 配置 CRUD + 发布/下线 + 测试对话 + A2A 连通性测试。

除 /login 外均需 JWT 管理员认证。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..a2a_client import A2AClientWrapper
from ..agent_factory import get_agent_instance, invalidate_agent
from ..auth import create_access_token, verify_password
from ..database import get_session
from ..deps import get_current_admin, new_thread_id
from ..identity import (
    IDENTITY_KIND_USER,
    IDENTITY_KIND_VISITOR,
    Identity,
    new_visitor_id,
    read_identity,
    set_identity_cookie,
)
from ..models import AdminUser, AgentStatus, Skill
from ..repository import (
    claim_conversations,
    create_agent,
    create_api_key,
    delete_agent,
    delete_api_key,
    get_agent_by_id,
    get_agent_by_slug,
    get_api_key,
    list_agent_api_keys as _list_agent_api_keys,
    list_agents,
    set_agent_status,
    update_agent,
    validate_skill_bindings,
)
from ..schemas import (
    AdminLoginRequest,
    AgentCreate,
    AgentOut,
    AgentUpdate,
    ApiKeyCreate,
    ApiKeyOut,
    ChatRequest,
    Token,
)
from sse_starlette.sse import EventSourceResponse

from .chat import _stream_chat

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

# 禁止自定义 Agent 使用的保留 slug
# - "/" 与 "" 为默认 Agent 保留
# - "a2a" 为 A2A 对外服务地址前缀（/a2a/{slug}），避免冲突
RESERVED_SLUGS = {"/", "", "a2a"}


async def _validate_skill_bindings(session: AsyncSession, skill_ids: list[int]) -> None:
    """解析 Agent 勾选的技能并施加门禁；不合法抛 ValueError（由调用方转 4xx）。

    只校验 review_status 与总量；enabled 不拦（宽松语义：保留勾选、运行时静默跳过）。
    statuses 必须由查出的 records 自己构造，否则缺 id 会被静默放行。
    """
    if not skill_ids:
        return
    rows = (
        await session.execute(select(Skill).where(Skill.id.in_(skill_ids)))
    ).scalars().all()
    validate_skill_bindings(
        records=list(rows),
        statuses={row.id: row.review_status.value for row in rows},
        requested_ids=skill_ids,
    )


@router.post("/login", response_model=Token)
async def login(
    req: AdminLoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """管理员登录，返回 JWT。

    登录是「匿名 → 登录」会话归并的唯一时机：把当前匿名访客名下的会话
    过户到该账号，再把身份 cookie 从 visitor 换成 user。
    消息本体按 thread_id 存放在 checkpoints 表里，不需要搬迁。
    """
    from ..repository import get_admin_by_username

    admin = await get_admin_by_username(session, req.username)
    if admin is None or not verify_password(req.password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    claimed = 0
    previous = read_identity(request)
    if previous is not None and previous.is_visitor:
        claimed = await claim_conversations(session, previous.id, admin.username)

    token = create_access_token(admin.username)
    # 身份 cookie 与管理员 JWT 分开：前者管「会话归属」，有效期更长
    set_identity_cookie(
        response, Identity(kind=IDENTITY_KIND_USER, id=admin.username)
    )
    return Token(access_token=token, expires_in=1440, claimed=claimed)


@router.post("/logout")
async def logout(response: Response):
    """退出登录：重新签发一个全新的匿名身份。

    刻意不复用原 visitor id —— 退出后即从零开始，不会残留任何已归并走的会话。
    """
    set_identity_cookie(
        response,
        Identity(kind=IDENTITY_KIND_VISITOR, id=new_visitor_id()),
    )
    return {"ok": True}


@router.get("/agents", response_model=list[AgentOut])
async def list_all_agents(
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    return await list_agents(session)


@router.post("/agents", response_model=AgentOut, status_code=201)
async def create_new_agent(
    data: AgentCreate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    if data.slug in RESERVED_SLUGS:
        raise HTTPException(400, "slug 为系统保留（'/' 为默认 Agent，'a2a' 为 A2A 服务地址前缀）")
    existing = await get_agent_by_slug(session, data.slug)
    if existing is not None:
        raise HTTPException(409, f"slug '{data.slug}' 已被占用")
    try:
        await _validate_skill_bindings(session, data.skill_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    try:
        return await create_agent(session, data)
    except ValueError as exc:
        # 模型绑定校验失败（如所选模型不存在）
        raise HTTPException(400, str(exc))


@router.put("/agents/{agent_id}", response_model=AgentOut)
async def update_existing_agent(
    agent_id: int,
    data: AgentUpdate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    agent = await get_agent_by_id(session, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent 不存在")
    try:
        await _validate_skill_bindings(session, data.skill_ids or [])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    try:
        updated = await update_agent(session, agent, data)
    except ValueError as exc:
        # 模型绑定校验失败（如所选模型不存在）
        raise HTTPException(400, str(exc))
    await invalidate_agent(agent_id)
    return updated


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_existing_agent(
    agent_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    agent = await get_agent_by_id(session, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent 不存在")
    if agent.slug == "/":
        raise HTTPException(400, "默认 Agent 不可删除")
    await invalidate_agent(agent_id)
    await delete_agent(session, agent)
    return None


@router.post("/agents/{agent_id}/publish", response_model=AgentOut)
async def publish_agent(
    agent_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    agent = await get_agent_by_id(session, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent 不存在")
    try:
        await _validate_skill_bindings(session, agent.skill_ids or [])
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    await invalidate_agent(agent_id)
    return await set_agent_status(session, agent, AgentStatus.PUBLISHED)


@router.post("/agents/{agent_id}/unpublish", response_model=AgentOut)
async def unpublish_agent(
    agent_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    agent = await get_agent_by_id(session, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent 不存在")
    if agent.slug == "/":
        raise HTTPException(400, "默认 Agent 不可下线")
    await invalidate_agent(agent_id)
    return await set_agent_status(session, agent, AgentStatus.DRAFT)


@router.post("/agents/{agent_id}/test")
async def test_agent_chat(
    agent_id: int,
    req: ChatRequest,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """管理中心内直接测试对话（不经过公开路由，draft 状态也可测）。"""
    agent = await get_agent_by_id(session, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent 不存在")
    thread_id = req.thread_id or new_thread_id()
    return EventSourceResponse(_stream_chat(agent, req.message, thread_id))


@router.post("/agents/test-connection")
async def test_a2a_connection(
    data: ChatRequest,
    _: AdminUser = Depends(get_current_admin),
):
    """测试给定 A2A 目标的连通性。data.message 字段复用为 target JSON。"""
    import json

    from ..schemas import A2ATarget

    try:
        payload = json.loads(data.message)
        target = A2ATarget(**payload)
    except Exception:
        raise HTTPException(400, "请求体需为 {\"url\":..., \"token\":...} 的 JSON 字符串")
    wrapper = A2AClientWrapper(target)
    ok, msg = await wrapper.test_connection()
    await wrapper.close()
    return {"ok": ok, "message": msg}


# ---------------------------------------------------------------------------
# API Key 管理（每个 Agent 独立配置的对外 A2A 调用凭据）
# ---------------------------------------------------------------------------
async def _get_agent_or_404(session: AsyncSession, agent_id: int):
    agent = await get_agent_by_id(session, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent 不存在")
    return agent


@router.get("/agents/{agent_id}/api-keys", response_model=list[ApiKeyOut])
async def list_agent_api_keys(
    agent_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    await _get_agent_or_404(session, agent_id)
    return await _list_agent_api_keys(session, agent_id)


@router.post("/agents/{agent_id}/api-keys", response_model=ApiKeyOut, status_code=201)
async def create_agent_api_key(
    agent_id: int,
    data: ApiKeyCreate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    agent = await _get_agent_or_404(session, agent_id)
    existing = [
        k for k in await _list_agent_api_keys(session, agent_id) if k.name == data.name
    ]
    if existing:
        raise HTTPException(409, f"该 Agent 下已存在名为 '{data.name}' 的 Key")
    return await create_api_key(session, agent, data)


@router.delete("/agents/{agent_id}/api-keys/{key_id}", status_code=204)
async def delete_agent_api_key(
    agent_id: int,
    key_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    await _get_agent_or_404(session, agent_id)
    api_key = await get_api_key(session, key_id)
    if api_key is None or api_key.agent_id != agent_id:
        raise HTTPException(404, "API Key 不存在")
    if api_key.is_default:
        raise HTTPException(400, "默认 Key 不可删除")
    await delete_api_key(session, api_key)
    return None
