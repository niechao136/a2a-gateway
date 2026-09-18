"""数据访问层：Agent 配置、A2A 目标注册表、MCP 服务注册表、管理员账号。

绑定模型（关键设计）：
- 「A2A 管理 / MCP 管理」维护可复用的**资源定义**
- Agent 只保存**勾选的 id**，由后端解析成运行时快照
  - a2a_target_ids → a2a_targets（[{url, token}]）
  - mcp_server_ids → mcp_servers（[{name, transport, url, command, args, env}]）
- 这样运行时（agent_factory）无需访问数据库即可构造工具，且注册表变更后统一刷新
"""

from typing import Any, cast

from urllib.parse import urlparse

import logging

import secrets

from datetime import datetime, timezone

from sqlalchemy import CursorResult, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .identity import IDENTITY_KIND_USER, IDENTITY_KIND_VISITOR, Identity
from .models import (
    A2AEndpoint,
    AdminUser,
    AgentConfig,
    AgentStatus,
    ApiKey,
    Conversation,
    McpServer,
    PendingA2ATask,
)
from .schemas import (
    A2AEndpointCreate,
    A2AEndpointUpdate,
    AgentCreate,
    AgentUpdate,
    ApiKeyCreate,
    McpServerCreate,
    McpServerUpdate,
)
from .auth import hash_password

logger = logging.getLogger(__name__)

_settings = get_settings()

# 默认 Agent 绑定的 A2A 目标在注册表中的名称
DEFAULT_ENDPOINT_NAME = "Hermes（默认）"


# ---------------------------------------------------------------------------
# 绑定解析
# ---------------------------------------------------------------------------
def a2a_target_snapshot(endpoint: A2AEndpoint) -> dict[str, str]:
    """A2A 目标快照：运行时构造工具所需的全部信息。

    description 会写进工具说明，让大模型知道「该目标擅长什么」，
    从而在绑定了多个目标时选出正确的那个。
    """
    return {
        "url": endpoint.url,
        "token": endpoint.token,
        "name": endpoint.name or "",
        "description": endpoint.description or "",
        "auth_type": endpoint.auth_type or "bearer",
        "auth_name": endpoint.auth_name or "",
    }


async def resolve_a2a_targets(session: AsyncSession, ids: list[int]) -> list[dict[str, str]]:
    """按勾选顺序解析 A2A 目标；已删除或被停用的条目自动跳过。"""
    if not ids:
        return []
    rows = (
        await session.execute(select(A2AEndpoint).where(A2AEndpoint.id.in_(ids)))
    ).scalars().all()
    by_id = {row.id: row for row in rows}
    targets: list[dict[str, str]] = []
    for endpoint_id in ids:
        endpoint = by_id.get(endpoint_id)
        if endpoint is None or not endpoint.enabled:
            continue
        targets.append(a2a_target_snapshot(endpoint))
    return targets


async def resolve_mcp_servers(session: AsyncSession, ids: list[int]) -> list[McpServer]:
    """按勾选顺序解析 MCP 服务；已删除或被停用的条目自动跳过。"""
    if not ids:
        return []
    rows = (
        await session.execute(select(McpServer).where(McpServer.id.in_(ids)))
    ).scalars().all()
    by_id = {row.id: row for row in rows}
    return [by_id[i] for i in ids if by_id.get(i) is not None and by_id[i].enabled]


def mcp_server_snapshot(server: McpServer) -> dict[str, Any]:
    """把 MCP 服务记录压成运行时快照（供 agent_factory 构造工具）。"""
    return {
        "name": server.name,
        "transport": server.transport,
        "url": server.url,
        "command": server.command,
        "args": list(server.args or []),
        "env": dict(server.env or {}),
        # 鉴权：远程传输走请求头/查询参数，stdio 注入环境变量
        "token": server.token or "",
        "auth_type": server.auth_type or "bearer",
        "auth_name": server.auth_name or "",
    }


async def resolve_mcp_snapshot(session: AsyncSession, ids: list[int]) -> list[dict[str, Any]]:
    return [mcp_server_snapshot(s) for s in await resolve_mcp_servers(session, ids)]


def _merge_bindings(
    resolved: list[dict[str, Any]],
    manual: list[dict[str, Any]] | None,
    existing: list[dict[str, Any]] | None,
    key: str,
) -> list[dict[str, Any]]:
    """合并绑定快照：注册表解析结果 + 手动条目（回归测试见 tests/test_bindings.py）。

    - resolved：由 a2a_target_ids / mcp_server_ids 解析出的注册表快照，始终保留
    - key：去重键（A2A 用 url，MCP 用 name）；手动条目与注册表条目重复时以注册表为准
    - manual 为 None 表示请求未显式提供手动列表（如仅改状态的老客户端），
      此时保留既有的手动条目，避免只改勾选时静默丢失手动绑定
    """
    resolved_keys = {item.get(key) for item in resolved}
    if manual is None:
        manual = [item for item in (existing or []) if item.get(key) not in resolved_keys]
    seen: set[Any] = set()
    extras: list[dict[str, Any]] = []
    for item in manual:
        k = item.get(key)
        if k in seen or k in resolved_keys:
            continue
        seen.add(k)
        extras.append(item)
    # 关键：注册表解析结果必须包含在内（曾因只返回手动条目，
    # 导致每次保存 Agent 都把勾选的注册表绑定从快照中清空）
    return resolved + extras


async def _resolve_bindings(session: AsyncSession, data: AgentCreate):
    """解析 Agent 载荷中的绑定，返回 (a2a_ids, a2a_targets, mcp_ids, mcp_snapshot)。

    注册表勾选与手动绑定可并存：快照 = 勾选解析结果 + 手动条目。
    """
    a2a_ids = list(data.a2a_target_ids or [])
    a2a_resolved = await resolve_a2a_targets(session, a2a_ids)
    a2a_targets = _merge_bindings(
        a2a_resolved, [t.model_dump() for t in (data.a2a_targets or [])], None, key="url"
    )

    mcp_ids = list(data.mcp_server_ids or [])
    mcp_resolved = await resolve_mcp_snapshot(session, mcp_ids)
    mcp_snapshot = _merge_bindings(
        mcp_resolved, [m.model_dump() for m in (data.mcp_servers or [])], None, key="name"
    )
    return a2a_ids, a2a_targets, mcp_ids, mcp_snapshot


# ---------------------------------------------------------------------------
# AgentConfig CRUD
# ---------------------------------------------------------------------------
async def create_agent(session: AsyncSession, data: AgentCreate) -> AgentConfig:
    a2a_ids, a2a_targets, mcp_ids, mcp_snapshot = await _resolve_bindings(session, data)
    agent = AgentConfig(
        slug=data.slug,
        name=data.name,
        description=data.description,
        a2a_target_ids=a2a_ids,
        a2a_targets=a2a_targets,
        mcp_server_ids=mcp_ids,
        mcp_servers=mcp_snapshot,
        system_prompt=data.system_prompt,
        status=AgentStatus.DRAFT,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    # 每个 Agent 创建时自动生成默认 API Key（对外 A2A 调用凭据）
    await ensure_agent_default_api_key(session, agent)
    return agent


async def get_agent_by_slug(session: AsyncSession, slug: str) -> AgentConfig | None:
    result = await session.execute(select(AgentConfig).where(AgentConfig.slug == slug))
    return result.scalar_one_or_none()


async def get_agent_by_id(session: AsyncSession, agent_id: int) -> AgentConfig | None:
    return await session.get(AgentConfig, agent_id)


async def list_agents(session: AsyncSession) -> list[AgentConfig]:
    result = await session.execute(select(AgentConfig).order_by(AgentConfig.id))
    return list(result.scalars().all())


async def update_agent(
    session: AsyncSession, agent: AgentConfig, data: AgentUpdate
) -> AgentConfig:
    if data.name is not None:
        agent.name = data.name
    if data.description is not None:
        agent.description = data.description
    if data.system_prompt is not None:
        agent.system_prompt = data.system_prompt
    if data.status is not None:
        agent.status = AgentStatus(data.status)

    # A2A 绑定：勾选（注册表 id）与手动条目并存，重复 url 以注册表为准
    if data.a2a_target_ids is not None or data.a2a_targets is not None:
        if data.a2a_target_ids is not None:
            agent.a2a_target_ids = list(data.a2a_target_ids)
        a2a_resolved = await resolve_a2a_targets(session, agent.a2a_target_ids)
        manual_a2a = (
            [t.model_dump() for t in data.a2a_targets]
            if data.a2a_targets is not None
            else None
        )
        agent.a2a_targets = _merge_bindings(
            a2a_resolved, manual_a2a, agent.a2a_targets, key="url"
        )

    # MCP 绑定：勾选（注册表 id）与手动条目并存，重复 name 以注册表为准
    if data.mcp_server_ids is not None or data.mcp_servers is not None:
        if data.mcp_server_ids is not None:
            agent.mcp_server_ids = list(data.mcp_server_ids)
        mcp_resolved = await resolve_mcp_snapshot(session, agent.mcp_server_ids)
        manual_mcp = (
            [m.model_dump() for m in data.mcp_servers]
            if data.mcp_servers is not None
            else None
        )
        agent.mcp_servers = _merge_bindings(
            mcp_resolved, manual_mcp, agent.mcp_servers, key="name"
        )

    await session.commit()
    await session.refresh(agent)
    return agent


async def delete_agent(session: AsyncSession, agent: AgentConfig) -> None:
    await delete_agent_api_keys(session, agent.id)
    await session.delete(agent)
    await session.commit()


async def set_agent_status(
    session: AsyncSession, agent: AgentConfig, status: AgentStatus
) -> AgentConfig:
    agent.status = status
    await session.commit()
    await session.refresh(agent)
    return agent


# ---------------------------------------------------------------------------
# A2A 目标注册表 CRUD
# ---------------------------------------------------------------------------
async def list_a2a_endpoints(session: AsyncSession) -> list[A2AEndpoint]:
    result = await session.execute(select(A2AEndpoint).order_by(A2AEndpoint.id))
    return list(result.scalars().all())


async def get_a2a_endpoint(session: AsyncSession, endpoint_id: int) -> A2AEndpoint | None:
    return await session.get(A2AEndpoint, endpoint_id)


async def get_a2a_endpoint_by_name(
    session: AsyncSession, name: str
) -> A2AEndpoint | None:
    result = await session.execute(select(A2AEndpoint).where(A2AEndpoint.name == name))
    return result.scalar_one_or_none()


async def get_a2a_endpoint_by_url(session: AsyncSession, url: str) -> A2AEndpoint | None:
    result = await session.execute(select(A2AEndpoint).where(A2AEndpoint.url == url))
    return result.scalar_one_or_none()


async def create_a2a_endpoint(
    session: AsyncSession, data: A2AEndpointCreate
) -> A2AEndpoint:
    endpoint = A2AEndpoint(**data.model_dump())
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)
    return endpoint


async def update_a2a_endpoint(
    session: AsyncSession, endpoint: A2AEndpoint, data: A2AEndpointUpdate
) -> A2AEndpoint:
    for field in ("name", "url", "token", "description", "auth_type", "auth_name", "enabled"):
        value = getattr(data, field)
        if value is not None:
            setattr(endpoint, field, value)
    await session.commit()
    await session.refresh(endpoint)
    return endpoint


async def delete_a2a_endpoint(session: AsyncSession, endpoint: A2AEndpoint) -> None:
    await session.delete(endpoint)
    await session.commit()


# ---------------------------------------------------------------------------
# MCP 服务注册表 CRUD
# ---------------------------------------------------------------------------
async def list_mcp_servers(session: AsyncSession) -> list[McpServer]:
    result = await session.execute(select(McpServer).order_by(McpServer.id))
    return list(result.scalars().all())


async def get_mcp_server(session: AsyncSession, server_id: int) -> McpServer | None:
    return await session.get(McpServer, server_id)


async def get_mcp_server_by_name(session: AsyncSession, name: str) -> McpServer | None:
    result = await session.execute(select(McpServer).where(McpServer.name == name))
    return result.scalar_one_or_none()


async def create_mcp_server(session: AsyncSession, data: McpServerCreate) -> McpServer:
    server = McpServer(**data.model_dump())
    session.add(server)
    await session.commit()
    await session.refresh(server)
    return server


async def update_mcp_server(
    session: AsyncSession, server: McpServer, data: McpServerUpdate
) -> McpServer:
    for field in (
        "name",
        "description",
        "transport",
        "url",
        "command",
        "args",
        "env",
        "token",
        "auth_type",
        "auth_name",
        "enabled",
    ):
        value = getattr(data, field)
        if value is not None:
            setattr(server, field, value)
    await session.commit()
    await session.refresh(server)
    return server


async def delete_mcp_server(session: AsyncSession, server: McpServer) -> None:
    await session.delete(server)
    await session.commit()


# ---------------------------------------------------------------------------
# 引用关系：Agent ↔ 注册表
# ---------------------------------------------------------------------------
async def agents_using_a2a_endpoint(
    session: AsyncSession, endpoint_id: int
) -> list[AgentConfig]:
    agents = await list_agents(session)
    return [a for a in agents if endpoint_id in (a.a2a_target_ids or [])]


async def agents_using_mcp_server(session: AsyncSession, server_id: int) -> list[AgentConfig]:
    agents = await list_agents(session)
    return [a for a in agents if server_id in (a.mcp_server_ids or [])]


async def refresh_agents_for_a2a_endpoints(
    session: AsyncSession, endpoint_ids: list[int]
) -> int:
    """端点信息变更后，重新解析引用它的 Agent 的绑定快照。"""
    if not endpoint_ids:
        return 0
    wanted = set(endpoint_ids)
    agents = await list_agents(session)
    changed = 0
    for agent in agents:
        if wanted & set(agent.a2a_target_ids or []):
            agent.a2a_targets = await resolve_a2a_targets(session, agent.a2a_target_ids)
            changed += 1
    if changed:
        await session.commit()
    return changed


async def refresh_agents_for_mcp_servers(session: AsyncSession, server_ids: list[int]) -> int:
    """MCP 服务信息变更后，重新解析引用它的 Agent 的绑定快照。"""
    if not server_ids:
        return 0
    wanted = set(server_ids)
    agents = await list_agents(session)
    changed = 0
    for agent in agents:
        if wanted & set(agent.mcp_server_ids or []):
            agent.mcp_servers = await resolve_mcp_snapshot(session, agent.mcp_server_ids)
            changed += 1
    if changed:
        await session.commit()
    return changed


async def detach_a2a_endpoint_from_agents(session: AsyncSession, endpoint_id: int) -> int:
    """从所有 Agent 中移除对某端点的勾选（删除端点前调用）。"""
    agents = await list_agents(session)
    changed = 0
    for agent in agents:
        ids = list(agent.a2a_target_ids or [])
        if endpoint_id in ids:
            agent.a2a_target_ids = [i for i in ids if i != endpoint_id]
            agent.a2a_targets = await resolve_a2a_targets(session, agent.a2a_target_ids)
            changed += 1
    if changed:
        await session.commit()
    return changed


async def detach_mcp_server_from_agents(session: AsyncSession, server_id: int) -> int:
    agents = await list_agents(session)
    changed = 0
    for agent in agents:
        ids = list(agent.mcp_server_ids or [])
        if server_id in ids:
            agent.mcp_server_ids = [i for i in ids if i != server_id]
            agent.mcp_servers = await resolve_mcp_snapshot(session, agent.mcp_server_ids)
            changed += 1
    if changed:
        await session.commit()
    return changed


# ---------------------------------------------------------------------------
# ApiKey（对外 A2A 服务调用凭据）
# ---------------------------------------------------------------------------
DEFAULT_API_KEY_NAME = "默认 Key"


def generate_api_key() -> str:
    """生成形如 a2a-<随机串> 的 API Key。"""
    return f"a2a-{secrets.token_urlsafe(24)}"


async def list_agent_api_keys(session: AsyncSession, agent_id: int) -> list[ApiKey]:
    """列出某个 Agent 的全部 API Key。"""
    result = await session.execute(
        select(ApiKey).where(ApiKey.agent_id == agent_id).order_by(ApiKey.id)
    )
    return list(result.scalars().all())


async def get_api_key(session: AsyncSession, key_id: int) -> ApiKey | None:
    return await session.get(ApiKey, key_id)


async def get_api_key_by_key(session: AsyncSession, key: str) -> ApiKey | None:
    """按明文 key 查找（A2A 端点鉴权用）。"""
    if not key:
        return None
    result = await session.execute(select(ApiKey).where(ApiKey.key == key))
    return result.scalar_one_or_none()


async def create_api_key(
    session: AsyncSession, agent: AgentConfig, data: ApiKeyCreate
) -> ApiKey:
    api_key = ApiKey(
        agent_id=agent.id,
        name=data.name,
        key=generate_api_key(),
        enabled=True,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return api_key


async def delete_api_key(session: AsyncSession, api_key: ApiKey) -> None:
    await session.delete(api_key)
    await session.commit()


async def ensure_agent_default_api_key(session: AsyncSession, agent: AgentConfig) -> ApiKey:
    """确保该 Agent 存在默认 API Key（首个启动时自动生成；后续启动幂等返回）。"""
    result = await session.execute(
        select(ApiKey).where(ApiKey.agent_id == agent.id, ApiKey.is_default.is_(True))
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    api_key = ApiKey(
        agent_id=agent.id,
        name=DEFAULT_API_KEY_NAME,
        key=generate_api_key(),
        is_default=True,
        enabled=True,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    logger.info("已为 Agent %s 生成默认 API Key：%s", agent.slug, api_key.key)
    return api_key


async def ensure_all_agent_api_keys(session: AsyncSession) -> None:
    """应用启动时为每个尚无默认 Key 的 Agent 补齐（含历史 Agent 升级）。"""
    agents = await list_agents(session)
    for agent in agents:
        await ensure_agent_default_api_key(session, agent)


async def delete_agent_api_keys(session: AsyncSession, agent_id: int) -> None:
    """删除 Agent 的全部 API Key（删除 Agent 时调用）。"""
    result = await session.execute(select(ApiKey).where(ApiKey.agent_id == agent_id))
    for api_key in result.scalars().all():
        await session.delete(api_key)
    await session.commit()


# ---------------------------------------------------------------------------
# 默认 Agent 初始化（含历史配置升级）
# ---------------------------------------------------------------------------
def _name_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc
    except Exception:
        host = ""
    return (host or url)[:120]


async def _unique_endpoint_name(session: AsyncSession, base: str) -> str:
    name = base
    suffix = 1
    while await get_a2a_endpoint_by_name(session, name) is not None:
        suffix += 1
        name = f"{base}-{suffix}"
    return name


async def _find_or_create_endpoint_for_target(
    session: AsyncSession, target: dict[str, Any]
) -> A2AEndpoint | None:
    """为历史 Agent 内嵌的 url/token 在注册表中补一条记录。"""
    url = (target or {}).get("url") or ""
    if not url:
        return None
    existing = await get_a2a_endpoint_by_url(session, url)
    if existing is not None:
        return existing
    name = await _unique_endpoint_name(session, _name_from_url(url))
    endpoint = A2AEndpoint(
        name=name,
        url=url,
        token=(target or {}).get("token") or "",
        description="由历史 Agent 配置自动登记",
    )
    session.add(endpoint)
    await session.flush()
    return endpoint


async def ensure_default_agent(session: AsyncSession) -> AgentConfig:
    """确保 slug='/' 的默认 Agent 存在，并绑定 Hermes A2A 目标。

    升级路径：历史库中的默认 Agent 只有内嵌 a2a_targets（无 a2a_target_ids），
    这里自动把该目标登记进注册表并回填关联，使管理中心可正常展示「已勾选」。
    """
    existing = await get_agent_by_slug(session, "/")
    if existing is not None:
        if not existing.a2a_target_ids and existing.a2a_targets:
            ids: list[int] = []
            for target in existing.a2a_targets:
                endpoint = await _find_or_create_endpoint_for_target(session, target)
                if endpoint is not None:
                    ids.append(endpoint.id)
            if ids:
                existing.a2a_target_ids = ids
                await session.commit()
                await session.refresh(existing)
        return existing

    name = await _unique_endpoint_name(session, DEFAULT_ENDPOINT_NAME)
    endpoint = A2AEndpoint(
        name=name,
        url=_settings.hermes_a2a_url,
        token=_settings.hermes_a2a_token,
        description="系统默认目标（来自环境变量 HERMES_A2A_URL / HERMES_A2A_TOKEN）",
    )
    session.add(endpoint)
    await session.flush()

    default = AgentConfig(
        slug="/",
        name="默认 Agent",
        description="绑定服务器 Hermes 的默认 Agent",
        a2a_target_ids=[endpoint.id],
        a2a_targets=[{"url": endpoint.url, "token": endpoint.token}],
        mcp_server_ids=[],
        mcp_servers=[],
        system_prompt=None,
        status=AgentStatus.PUBLISHED,
    )
    session.add(default)
    await session.commit()
    await session.refresh(default)
    return default


# ---------------------------------------------------------------------------
# AdminUser
# ---------------------------------------------------------------------------
async def get_admin_by_username(
    session: AsyncSession, username: str
) -> AdminUser | None:
    result = await session.execute(select(AdminUser).where(AdminUser.username == username))
    return result.scalar_one_or_none()


async def ensure_default_admin(session: AsyncSession) -> AdminUser:
    """首次启动时若不存在配置的管理员账号，则自动创建。"""
    existing = await get_admin_by_username(session, _settings.admin_username)
    if existing is not None:
        return existing
    admin = AdminUser(
        username=_settings.admin_username,
        password_hash=hash_password(_settings.admin_password),
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    return admin


# ---------------------------------------------------------------------------
# Conversation（对话会话目录：thread_id → 身份归属）
# ---------------------------------------------------------------------------
def normalize_slug(slug: str | None) -> str:
    """前端用空串表示默认 Agent，库里统一存 "/"。"""
    return (slug or "").strip() or "/"


async def list_conversations(
    session: AsyncSession, identity: Identity, slug: str | None = None
) -> list[Conversation]:
    """列出某身份名下的会话（按最近更新倒序）。"""
    stmt = select(Conversation).where(
        Conversation.owner_kind == identity.kind,
        Conversation.owner_id == identity.id,
    )
    if slug is not None:
        stmt = stmt.where(Conversation.agent_slug == normalize_slug(slug))
    stmt = stmt.order_by(Conversation.updated_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_conversation(
    session: AsyncSession, thread_id: str
) -> Conversation | None:
    """按 thread_id 查会话目录（不校验归属，由调用方判断）。

    返回 None 表示「未登记」——历史遗留会话与回填前的 thread 都不在目录里，
    此时读写一律放行，保证升级过程不打断现有用户。
    """
    result = await session.execute(
        select(Conversation).where(Conversation.thread_id == thread_id)
    )
    return result.scalar_one_or_none()


async def upsert_conversation(
    session: AsyncSession,
    identity: Identity,
    thread_id: str,
    slug: str | None = None,
    title: str | None = None,
) -> Conversation | None:
    """登记/刷新一条会话。

    - 已登记且属于本身份：有 title 且原标题为空时补标题，并刷新 updated_at
    - 已登记但属于他人：返回 None（不抢归属，避免分享链接被任意收编）
    - 未登记：新建
    """
    existing = await get_conversation(session, thread_id)
    if existing is not None:
        if not identity.owns(existing):
            return None
        # 只在标题为空时写入：首条消息定标题，后续消息不应覆盖
        if title and not existing.title:
            existing.title = title[:255]
        existing.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(existing)
        return existing

    conv = Conversation(
        thread_id=thread_id,
        agent_slug=normalize_slug(slug),
        title=(title or "")[:255],
        owner_kind=identity.kind,
        owner_id=identity.id,
    )
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv


async def rename_conversation(
    session: AsyncSession, identity: Identity, thread_id: str, title: str
) -> Conversation | None:
    """重命名会话；不属于本身份时返回 None。"""
    conv = await get_conversation(session, thread_id)
    if conv is None or not identity.owns(conv):
        return None
    conv.title = title[:255]
    conv.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(conv)
    return conv


async def delete_conversation(
    session: AsyncSession, identity: Identity, thread_id: str
) -> bool:
    """删除会话目录；不属于本身份时返回 False。"""
    conv = await get_conversation(session, thread_id)
    if conv is None or not identity.owns(conv):
        return False
    await session.delete(conv)
    await session.commit()
    return True


async def claim_conversations(
    session: AsyncSession, visitor_id: str, username: str
) -> int:
    """登录归并：把某个匿名访客名下的会话过户给管理员账号。

    单条 UPDATE，天然幂等（重复登录第二次影响 0 行）且原子。
    消息本体在 checkpoints 表里按 thread_id 存储，不随归属变化搬迁。
    """
    # 类型上 session.execute 返回 Result[Any]，但 DML 实际返回 CursorResult（带 rowcount）
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(Conversation)
            .where(
                Conversation.owner_kind == IDENTITY_KIND_VISITOR,
                Conversation.owner_id == visitor_id,
            )
            .values(owner_kind=IDENTITY_KIND_USER, owner_id=username)
        ),
    )
    await session.commit()
    claimed = result.rowcount or 0
    if claimed:
        logger.info("登录归并：匿名访客 %s 的 %s 条会话已归属 %s", visitor_id, claimed, username)
    return claimed


async def import_conversations(
    session: AsyncSession,
    identity: Identity,
    slug: str | None,
    items: list[dict[str, Any]],
) -> int:
    """批量导入历史会话（前端 localStorage 迁移用）。

    已存在的 thread_id 一律跳过：既不重复插入，也不会把别人的会话收编过来。
    """
    if not items:
        return 0
    normalized = normalize_slug(slug)
    wanted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        thread_id = str(item.get("thread_id") or "").strip()
        if not thread_id or thread_id in seen:
            continue
        seen.add(thread_id)
        wanted.append(
            {
                "thread_id": thread_id[:64],
                "agent_slug": normalized,
                "title": str(item.get("title") or "")[:255],
                "owner_kind": identity.kind,
                "owner_id": identity.id,
            }
        )
    if not wanted:
        return 0

    existing = (
        await session.execute(
            select(Conversation.thread_id).where(Conversation.thread_id.in_(list(seen)))
        )
    ).scalars().all()
    rows = [row for row in wanted if row["thread_id"] not in set(existing)]
    if not rows:
        return 0

    await session.execute(
        pg_insert(Conversation)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["thread_id"])
    )
    await session.commit()
    logger.info("导入历史会话 %s 条（身份 %s:%s）", len(rows), identity.kind, identity.id)
    return len(rows)


# ---------------------------------------------------------------------------
# 挂起任务（input-required）
# ---------------------------------------------------------------------------
async def get_pending_a2a_task(
    session: AsyncSession, thread_id: str
) -> PendingA2ATask | None:
    """按 thread_id 取挂起任务（TTL 判定由 store 层负责）。"""
    result = await session.execute(
        select(PendingA2ATask).where(PendingA2ATask.thread_id == thread_id)
    )
    return result.scalar_one_or_none()


async def upsert_pending_a2a_task(
    session: AsyncSession,
    *,
    thread_id: str,
    agent_id: int,
    target_url: str,
    target_name: str,
    task_id: str,
    context_id: str,
    question: str,
) -> None:
    """登记/刷新一条挂起任务（按 thread_id 冲突更新）。"""
    existing = await get_pending_a2a_task(session, thread_id)
    if existing is not None:
        existing.agent_id = agent_id
        existing.target_url = target_url
        existing.target_name = target_name
        existing.task_id = task_id
        existing.context_id = context_id
        existing.question = question
        existing.updated_at = datetime.now(timezone.utc)
    else:
        session.add(
            PendingA2ATask(
                thread_id=thread_id,
                agent_id=agent_id,
                target_url=target_url,
                target_name=target_name,
                task_id=task_id,
                context_id=context_id,
                question=question,
            )
        )
    await session.commit()


async def delete_pending_a2a_task(session: AsyncSession, thread_id: str) -> bool:
    """删除挂起任务；不存在时返回 False。"""
    row = await get_pending_a2a_task(session, thread_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True
