"""数据访问层：Agent 配置、A2A 目标注册表、MCP 服务注册表、管理员账号。

绑定模型（关键设计）：
- 「A2A 管理 / MCP 管理」维护可复用的**资源定义**
- Agent 只保存**勾选的 id**，由后端解析成运行时快照
  - a2a_target_ids → a2a_targets（[{url, token}]）
  - mcp_server_ids → mcp_servers（[{name, transport, url, command, args, env}]）
  - skill_ids → skills（[{name, content, load_mode, files, ...}]）
- 这样运行时（agent_factory）无需访问数据库即可构造工具，且注册表变更后统一刷新
"""

from typing import Any, cast

from urllib.parse import urlparse

import logging

import secrets

from datetime import datetime, timezone

import hashlib

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .identity import IDENTITY_KIND_USER, IDENTITY_KIND_VISITOR, Identity
from .skill_import import files_differ
from .models import (
    A2AEndpoint,
    AdminUser,
    AgentConfig,
    AgentStatus,
    ApiKey,
    ChatConnector,
    ConnectorConversation,
    ConnectorPlatform,
    Conversation,
    McpServer,
    PendingA2ATask,
    Skill,
    SkillReviewStatus,
)
from .schemas import (
    A2AEndpointCreate,
    A2AEndpointUpdate,
    AgentCreate,
    AgentUpdate,
    ApiKeyCreate,
    ConnectorCreate,
    McpServerCreate,
    McpServerUpdate,
    SkillUpdate,
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
    skill_ids = list(data.skill_ids or [])
    skills_snapshot = await resolve_skills(session, skill_ids)
    agent = AgentConfig(
        slug=data.slug,
        name=data.name,
        description=data.description,
        a2a_target_ids=a2a_ids,
        a2a_targets=a2a_targets,
        mcp_server_ids=mcp_ids,
        mcp_servers=mcp_snapshot,
        skill_ids=skill_ids,
        skills=skills_snapshot,
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

    # Skill 绑定：只有 approved 的技能会被解析进快照（无手动条目，不做合并）
    if data.skill_ids is not None:
        agent.skill_ids = list(data.skill_ids)
        agent.skills = await resolve_skills(session, agent.skill_ids)

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
# Skill 注册表（编排方法论）
# ---------------------------------------------------------------------------
def skill_snapshot(skill: Skill) -> dict[str, Any]:
    """Skill 运行时快照：正文与附件全量，供注入 / load_skill 闭包使用。"""
    review: Any = skill.review_status
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "content": skill.content,
        "load_mode": skill.load_mode,
        "files": list(skill.files or []),
        # Phase C：run_skill_script 工具按此决定是否对模型可用
        "allow_scripts": bool(skill.allow_scripts),
        # 纵深防御：正常路径 resolve 已过滤，恒为 approved；防绕过路径多一道闸
        "review_status": review.value if isinstance(review, SkillReviewStatus) else str(review),
    }


def binding_content_bytes(snapshots: list[dict[str, Any]]) -> int:
    """绑定正文总量（不含附件），用于 MAX_BINDING_CONTENT_BYTES 门禁。"""
    return sum(len(str(s.get("content") or "").encode("utf-8")) for s in snapshots)


def validate_skill_bindings(
    *,
    records: list[Any],
    statuses: dict[int, str],
    requested_ids: list[int] | None = None,
) -> None:
    """绑定门禁：缺记录 / pending / rejected / 正文总量超限一律拒绝。

    enabled 不参与门禁（宽松语义：保留勾选、静默跳过、启用即恢复）。
    @param records 解析到的 Skill ORM 对象；@param statuses id → 审核状态值
    @param requested_ids Agent 提交的完整 id 清单（缺失即「勾选了不存在的技能」）
    """
    from .skills import MAX_BINDING_CONTENT_BYTES

    ids = list(requested_ids if requested_ids is not None else [r.id for r in records])
    found = {r.id for r in records}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ValueError(f"技能不存在或不可用：id {missing}")
    total = binding_content_bytes([skill_snapshot(r) for r in records])
    if total > MAX_BINDING_CONTENT_BYTES:
        raise ValueError(f"绑定技能正文总量超过 {MAX_BINDING_CONTENT_BYTES} 字节上限")
    for record in records:
        status = statuses.get(record.id)
        if status == "pending":
            raise ValueError(f"技能「{record.name}」尚未审核通过（pending），不能绑定")
        if status == "rejected":
            raise ValueError(f"技能「{record.name}」审核未通过（rejected），不能绑定")


async def resolve_skills(session: AsyncSession, ids: list[int]) -> list[dict[str, Any]]:
    """按勾选顺序解析技能快照；已删除 / 停用 / 未审核通过的静默跳过。"""
    if not ids:
        return []
    rows = (
        await session.execute(select(Skill).where(Skill.id.in_(ids)))
    ).scalars().all()
    by_id = {row.id: row for row in rows}
    snapshots: list[dict[str, Any]] = []
    for skill_id in ids:
        skill = by_id.get(skill_id)
        if skill is None or not skill.enabled:
            continue
        if skill.review_status != SkillReviewStatus.APPROVED:
            continue
        snapshots.append(skill_snapshot(skill))
    return snapshots


async def list_skills(session: AsyncSession) -> list[Skill]:
    result = await session.execute(select(Skill).order_by(Skill.id))
    return list(result.scalars().all())


async def get_skill(session: AsyncSession, skill_id: int) -> Skill | None:
    return await session.get(Skill, skill_id)


async def get_skill_by_name(session: AsyncSession, name: str) -> Skill | None:
    result = await session.execute(select(Skill).where(Skill.name == name))
    return result.scalar_one_or_none()


async def create_skill(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    content: str,
    frontmatter: dict[str, Any],
    files: list[dict[str, Any]],
    load_mode: str,
    size_bytes: int,
    source: str,
    source_ref: str = "",
    review_status: SkillReviewStatus = SkillReviewStatus.PENDING,
) -> Skill:
    skill = Skill(
        name=name,
        description=description,
        content=content,
        frontmatter=frontmatter,
        files=files,
        load_mode=load_mode,
        size_bytes=size_bytes,
        file_count=len(files),
        source=source,
        source_ref=source_ref,
        review_status=review_status,
    )
    session.add(skill)
    await session.commit()
    await session.refresh(skill)
    return skill


async def update_skill(
    session: AsyncSession,
    skill: Skill,
    data: "SkillUpdate",
    files: list[dict[str, Any]] | None = None,
) -> Skill:
    """编辑技能：description / content / 附件变更即重置审核为 pending（内容变了必须重审）。"""
    content_changed = False
    if data.description is not None and data.description != skill.description:
        skill.description = data.description
        content_changed = True
    if data.content is not None and data.content != skill.content:
        skill.content = data.content
        content_changed = True
    if data.load_mode is not None and data.load_mode != skill.load_mode:
        skill.load_mode = data.load_mode
    if data.enabled is not None:
        skill.enabled = data.enabled
    if data.allow_scripts is not None:
        skill.allow_scripts = data.allow_scripts
    if files is not None and files_differ(skill.files or [], files):
        skill.files = files
        skill.file_count = len(files)
        content_changed = True
    if content_changed:
        skill.review_status = SkillReviewStatus.PENDING
        skill.reviewed_at = None
    await session.commit()
    await session.refresh(skill)
    return skill


async def set_skill_review(
    session: AsyncSession, skill: Skill, review_status: SkillReviewStatus, note: str
) -> Skill:
    skill.review_status = review_status
    skill.review_note = note
    skill.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(skill)
    return skill


async def upsert_imported_skill(
    session: AsyncSession,
    *,
    parsed: dict[str, Any],
    load_mode: str,
    source: str,
    source_ref: str,
    overwrite: bool,
) -> tuple[Skill, bool]:
    """落库一条导入结果；重名时按 overwrite 决定覆盖或跳过。

    覆盖必须把 review_status 重置为 pending（内容变了必须重审）。
    @returns (skill, created)
    """
    name = str(parsed["name"])
    existing = await get_skill_by_name(session, name)
    if existing is not None:
        if not overwrite:
            return existing, False
        existing.description = str(parsed["description"])
        existing.content = str(parsed["content"])
        existing.frontmatter = dict(parsed.get("frontmatter") or {})
        existing.files = list(parsed.get("files") or [])
        existing.load_mode = load_mode
        existing.size_bytes = int(parsed.get("size_bytes") or 0)
        existing.file_count = len(parsed.get("files") or [])
        existing.source = source
        existing.source_ref = source_ref
        existing.review_status = SkillReviewStatus.PENDING
        existing.reviewed_at = None
        await session.commit()
        await session.refresh(existing)
        return existing, False
    skill = await create_skill(
        session,
        name=name,
        description=str(parsed["description"]),
        content=str(parsed["content"]),
        frontmatter=dict(parsed.get("frontmatter") or {}),
        files=list(parsed.get("files") or []),
        load_mode=load_mode,
        size_bytes=int(parsed.get("size_bytes") or 0),
        source=source,
        source_ref=source_ref,
    )
    return skill, True


async def delete_skill_record(session: AsyncSession, skill: Skill) -> None:
    await session.delete(skill)
    await session.commit()


async def agents_using_skill(session: AsyncSession, skill_id: int) -> list[AgentConfig]:
    agents = await list_agents(session)
    return [a for a in agents if skill_id in (a.skill_ids or [])]


async def refresh_agents_for_skills(session: AsyncSession, skill_ids: list[int]) -> int:
    """Skill 内容 / 审核状态 / load_mode / enabled 变更后，重解析引用它的 Agent 快照。"""
    if not skill_ids:
        return 0
    wanted = set(skill_ids)
    agents = await list_agents(session)
    changed = 0
    for agent in agents:
        if wanted & set(agent.skill_ids or []):
            agent.skills = await resolve_skills(session, agent.skill_ids)
            changed += 1
    if changed:
        await session.commit()
    return changed


async def detach_skill_from_agents(session: AsyncSession, skill_id: int) -> int:
    """从所有 Agent 移除对某技能的勾选并重解析快照（删除前调用）。"""
    agents = await list_agents(session)
    changed = 0
    for agent in agents:
        ids = list(agent.skill_ids or [])
        if skill_id in ids:
            agent.skill_ids = [i for i in ids if i != skill_id]
            agent.skills = await resolve_skills(session, agent.skill_ids)
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


# ---------------------------------------------------------------------------
# 聊天连接器（连接器管理 + webhook 链路共用）
# ---------------------------------------------------------------------------
def build_connector_thread_id(connector_id: int, platform: str, chat_id: str) -> str:
    """平台会话 → LangGraph thread_id 的确定性映射（同会话永远同 thread）。"""
    base = f"conn-{connector_id}-{platform}-{chat_id}"
    if len(base) <= 128:
        return base
    digest = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:24]
    return f"conn-{connector_id}-{platform}-{digest}"


async def list_connectors(session: AsyncSession) -> list[ChatConnector]:
    rows = await session.execute(select(ChatConnector).order_by(ChatConnector.id))
    return list(rows.scalars().all())


async def get_connector(session: AsyncSession, connector_id: int) -> ChatConnector | None:
    return await session.get(ChatConnector, connector_id)


async def get_connector_by_name(session: AsyncSession, name: str) -> ChatConnector | None:
    row = await session.execute(select(ChatConnector).where(ChatConnector.name == name))
    return row.scalars().first()


async def create_connector(
    session: AsyncSession, data: ConnectorCreate, credentials: dict[str, Any]
) -> ChatConnector:
    connector = ChatConnector(
        name=data.name.strip(),
        description=data.description or "",
        platform=ConnectorPlatform(data.platform),
        credentials=credentials,
        agent_id=data.agent_id,
        enabled=data.enabled,
    )
    session.add(connector)
    await session.commit()
    await session.refresh(connector)
    return connector


async def update_connector(
    session: AsyncSession, connector: ChatConnector, changes: dict[str, Any]
) -> ChatConnector:
    for field, value in changes.items():
        setattr(connector, field, value)
    await session.commit()
    await session.refresh(connector)
    return connector


async def delete_connector(session: AsyncSession, connector: ChatConnector) -> None:
    await session.delete(connector)
    await session.commit()


async def get_connector_conversation(
    session: AsyncSession, connector_id: int, chat_id: str
) -> ConnectorConversation | None:
    row = await session.execute(
        select(ConnectorConversation).where(
            ConnectorConversation.connector_id == connector_id,
            ConnectorConversation.chat_id == chat_id,
        )
    )
    return row.scalars().first()


async def upsert_connector_conversation(
    session: AsyncSession,
    connector_id: int,
    platform: str,
    chat_id: str,
    chat_type: str,
    user_id: str,
    user_name: str,
) -> ConnectorConversation:
    """获取或创建会话映射，并刷新最近发言人/活跃时间（幂等）。"""
    thread_id = build_connector_thread_id(connector_id, platform, chat_id)
    stmt = pg_insert(ConnectorConversation).values(
        connector_id=connector_id,
        chat_id=chat_id,
        chat_type=chat_type,
        thread_id=thread_id,
        last_user_ref={"user_id": user_id, "display_name": user_name},
        last_active_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_connector_conversations_chat",
        set_={
            "chat_type": stmt.excluded.chat_type,
            "last_user_ref": stmt.excluded.last_user_ref,
            "last_active_at": stmt.excluded.last_active_at,
        },
    )
    await session.execute(stmt)
    await session.commit()
    conversation = await get_connector_conversation(session, connector_id, chat_id)
    if conversation is None:  # pragma: no cover - upsert 后必然存在
        raise RuntimeError(f"会话映射创建失败 connector={connector_id} chat={chat_id}")
    return conversation


async def list_recent_connector_conversations(
    session: AsyncSession, connector_id: int, limit: int = 20
) -> list[ConnectorConversation]:
    rows = await session.execute(
        select(ConnectorConversation)
        .where(ConnectorConversation.connector_id == connector_id)
        .order_by(ConnectorConversation.last_active_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def agent_name_map(session: AsyncSession, agent_ids: list[int]) -> dict[int, str]:
    """批量取 Agent 名称（连接器列表展示绑定关系用）。"""
    if not agent_ids:
        return {}
    rows = await session.execute(
        select(AgentConfig.id, AgentConfig.name).where(AgentConfig.id.in_(agent_ids))
    )
    return {aid: name for aid, name in rows.all()}


async def connector_last_active_map(
    session: AsyncSession, connector_ids: list[int]
) -> dict[int, datetime | None]:
    """各连接器最近活跃时间（聚合会话映射表）。"""
    if not connector_ids:
        return {}
    rows = await session.execute(
        select(
            ConnectorConversation.connector_id,
            func.max(ConnectorConversation.last_active_at),
        )
        .where(ConnectorConversation.connector_id.in_(connector_ids))
        .group_by(ConnectorConversation.connector_id)
    )
    return {cid: ts for cid, ts in rows.all()}
