"""数据访问层：Agent 配置、A2A 目标注册表、MCP 服务注册表、管理员账号。

绑定模型（关键设计）：
- 「A2A 管理 / MCP 管理」维护可复用的**资源定义**
- Agent 只保存**勾选的 id**，由后端解析成运行时快照
  - a2a_target_ids → a2a_targets（[{url, token}]）
  - mcp_server_ids → mcp_servers（[{name, transport, url, command, args, env}]）
- 这样运行时（agent_factory）无需访问数据库即可构造工具，且注册表变更后统一刷新
"""

from typing import Any

from urllib.parse import urlparse

import logging

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import A2AEndpoint, AdminUser, AgentConfig, AgentStatus, ApiKey, McpServer
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


def _merge_manual_items(
    existing: list[dict[str, Any]] | None,
    resolved: list[dict[str, Any]],
    manual: list[dict[str, Any]] | None,
    key: str,
) -> list[dict[str, Any]]:
    """合并手动绑定条目与注册表解析结果。

    - resolved：由 a2a_target_ids / mcp_server_ids 解析出的注册表快照
    - key：去重键（A2A 用 url，MCP 用 name）；手动条目与注册表条目重复时以注册表为准
    - manual 为 None 表示请求未显式提供手动列表（如仅改状态的老客户端），
      此时保留既有的手动条目，避免只改勾选时静默丢失手动绑定
    """
    resolved_keys = {item.get(key) for item in resolved}
    if manual is None:
        return [item for item in (existing or []) if item.get(key) not in resolved_keys]
    seen: set[Any] = set()
    merged: list[dict[str, Any]] = []
    for item in manual:
        k = item.get(key)
        if k in seen:
            continue
        seen.add(k)
        if k not in resolved_keys:
            merged.append(item)
    return merged


async def _resolve_bindings(session: AsyncSession, data: AgentCreate):
    """解析 Agent 载荷中的绑定，返回 (a2a_ids, a2a_targets, mcp_ids, mcp_snapshot)。

    注册表勾选与手动绑定可并存：快照 = 勾选解析结果 + 手动条目。
    """
    a2a_ids = list(data.a2a_target_ids or [])
    a2a_resolved = await resolve_a2a_targets(session, a2a_ids)
    a2a_targets = a2a_resolved + [t.model_dump() for t in (data.a2a_targets or [])]

    mcp_ids = list(data.mcp_server_ids or [])
    mcp_resolved = await resolve_mcp_snapshot(session, mcp_ids)
    mcp_snapshot = mcp_resolved + [m.model_dump() for m in (data.mcp_servers or [])]
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
        agent.a2a_targets = _merge_manual_items(
            agent.a2a_targets, a2a_resolved, manual_a2a, key="url"
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
        agent.mcp_servers = _merge_manual_items(
            agent.mcp_servers, mcp_resolved, manual_mcp, key="name"
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
