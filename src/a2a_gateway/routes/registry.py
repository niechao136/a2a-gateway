"""注册表路由：A2A 目标、MCP 服务与 Skill 的 CRUD、连通性测试与工具清单。

所有接口均位于 /api/admin 下，需要 JWT 管理员认证。

删除保护：注册表项被 Agent 引用时默认拒绝删除（409），
可用 `?force=true` 强制删除并自动从所有 Agent 上解绑。
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import repository as repo
from .. import skill_import as si
from ..a2a_client import A2AClientWrapper
from ..agent_factory import invalidate_agent
from ..database import get_session
from ..deps import get_current_admin
from ..mcp_client import connection_from_snapshot, list_tools, test_connection
from ..models import AdminUser, SkillReviewStatus
from ..schemas import (
    A2AEndpointCreate,
    A2AEndpointOut,
    A2AEndpointUpdate,
    A2ATarget,
    McpServerCreate,
    McpServerOut,
    McpServerUpdate,
    SkillCreate,
    SkillImportCommitRequest,
    SkillImportPreviewItem,
    SkillImportPreviewOut,
    SkillImportRequest,
    SkillOut,
    SkillReviewRequest,
    SkillUpdate,
    validate_auth,
    validate_mcp_transport,
)
from ..skills import parse_skill_md, validate_skill_fields

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin-registry"])


def _used_by_message(agents) -> str:
    names = "、".join(a.name for a in agents)
    return f"仍被 {len(agents)} 个 Agent 引用（{names}）；可先取消勾选，或用 ?force=true 强制删除并自动解绑"


# ---------------------------------------------------------------------------
# A2A 目标注册表
# ---------------------------------------------------------------------------
@router.get("/a2a-endpoints", response_model=list[A2AEndpointOut])
async def list_a2a_endpoints(
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    return await repo.list_a2a_endpoints(session)


@router.post("/a2a-endpoints", response_model=A2AEndpointOut, status_code=status.HTTP_201_CREATED)
async def create_a2a_endpoint(
    data: A2AEndpointCreate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    if not data.name.strip():
        raise HTTPException(400, "名称不能为空")
    if not data.url.strip():
        raise HTTPException(400, "服务地址不能为空")
    existing = await repo.get_a2a_endpoint_by_name(session, data.name.strip())
    if existing is not None:
        raise HTTPException(409, f"名称 '{data.name}' 已存在")
    return await repo.create_a2a_endpoint(session, data)


@router.put("/a2a-endpoints/{endpoint_id}", response_model=A2AEndpointOut)
async def update_a2a_endpoint(
    endpoint_id: int,
    data: A2AEndpointUpdate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    endpoint = await repo.get_a2a_endpoint(session, endpoint_id)
    if endpoint is None:
        raise HTTPException(404, "A2A 目标不存在")
    if data.name is not None and not data.name.strip():
        raise HTTPException(400, "名称不能为空")

    # 鉴权方式与密钥必须匹配（与 A2AEndpointCreate 的校验保持一致）
    if data.auth_type is not None or data.auth_name is not None or data.token is not None:
        auth_type = data.auth_type or endpoint.auth_type
        auth_name = data.auth_name if data.auth_name is not None else endpoint.auth_name
        token = data.token if data.token is not None else endpoint.token
        try:
            validate_auth(auth_type, auth_name, token)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    new_name = data.name.strip() if data.name else endpoint.name
    if new_name != endpoint.name:
        dup = await repo.get_a2a_endpoint_by_name(session, new_name)
        if dup is not None:
            raise HTTPException(409, f"名称 '{new_name}' 已存在")

    affected = await repo.agents_using_a2a_endpoint(session, endpoint_id)
    updated = await repo.update_a2a_endpoint(session, endpoint, data)
    # 地址/token 变了 → 重新解析引用它的 Agent 的绑定快照并失效图缓存
    await repo.refresh_agents_for_a2a_endpoints(session, [endpoint_id])
    for agent in affected:
        await invalidate_agent(agent.id)
    return updated


@router.delete("/a2a-endpoints/{endpoint_id}", status_code=204)
async def delete_a2a_endpoint(
    endpoint_id: int,
    force: bool = Query(False, description="为 true 时自动从所有 Agent 解绑后删除"),
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    endpoint = await repo.get_a2a_endpoint(session, endpoint_id)
    if endpoint is None:
        raise HTTPException(404, "A2A 目标不存在")

    used = await repo.agents_using_a2a_endpoint(session, endpoint_id)
    if used and not force:
        raise HTTPException(409, _used_by_message(used))
    if used:
        for agent in used:
            await invalidate_agent(agent.id)
        await repo.detach_a2a_endpoint_from_agents(session, endpoint_id)

    await repo.delete_a2a_endpoint(session, endpoint)
    return None


@router.post("/a2a-endpoints/{endpoint_id}/test")
async def test_a2a_endpoint(
    endpoint_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """测试该 A2A 目标的连通性。"""
    endpoint = await repo.get_a2a_endpoint(session, endpoint_id)
    if endpoint is None:
        raise HTTPException(404, "A2A 目标不存在")
    wrapper = A2AClientWrapper(A2ATarget(url=endpoint.url, token=endpoint.token))
    try:
        ok, message = await wrapper.test_connection()
    except Exception as exc:
        return {"ok": False, "message": f"连接失败：{type(exc).__name__}: {exc}"}
    finally:
        await wrapper.close()
    return {"ok": ok, "message": message}


# ---------------------------------------------------------------------------
# MCP 服务注册表
# ---------------------------------------------------------------------------
@router.get("/mcp-servers", response_model=list[McpServerOut])
async def list_mcp_servers(
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    return await repo.list_mcp_servers(session)


@router.post("/mcp-servers", response_model=McpServerOut, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    data: McpServerCreate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    if not data.name.strip():
        raise HTTPException(400, "名称不能为空")
    existing = await repo.get_mcp_server_by_name(session, data.name.strip())
    if existing is not None:
        raise HTTPException(409, f"名称 '{data.name}' 已存在")
    return await repo.create_mcp_server(session, data)


@router.put("/mcp-servers/{server_id}", response_model=McpServerOut)
async def update_mcp_server(
    server_id: int,
    data: McpServerUpdate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    server = await repo.get_mcp_server(session, server_id)
    if server is None:
        raise HTTPException(404, "MCP 服务不存在")
    if data.name is not None and not data.name.strip():
        raise HTTPException(400, "名称不能为空")

    # 传输方式与连接参数必须匹配（与 McpServerCreate 的校验保持一致）
    if data.transport is not None or data.url is not None or data.command is not None:
        transport = data.transport or server.transport
        url = data.url if data.url is not None else server.url
        command = data.command if data.command is not None else server.command
        try:
            validate_mcp_transport(transport, url, command)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    # 鉴权方式与密钥必须匹配（与 McpServerCreate 的校验保持一致）
    if data.auth_type is not None or data.auth_name is not None or data.token is not None:
        auth_type = data.auth_type or server.auth_type
        auth_name = data.auth_name if data.auth_name is not None else server.auth_name
        token = data.token if data.token is not None else server.token
        try:
            validate_auth(auth_type, auth_name, token)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    new_name = data.name.strip() if data.name else server.name
    if new_name != server.name:
        dup = await repo.get_mcp_server_by_name(session, new_name)
        if dup is not None:
            raise HTTPException(409, f"名称 '{new_name}' 已存在")

    affected = await repo.agents_using_mcp_server(session, server_id)
    updated = await repo.update_mcp_server(session, server, data)
    await repo.refresh_agents_for_mcp_servers(session, [server_id])
    for agent in affected:
        await invalidate_agent(agent.id)
    return updated


@router.delete("/mcp-servers/{server_id}", status_code=204)
async def delete_mcp_server(
    server_id: int,
    force: bool = Query(False, description="为 true 时自动从所有 Agent 解绑后删除"),
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    server = await repo.get_mcp_server(session, server_id)
    if server is None:
        raise HTTPException(404, "MCP 服务不存在")

    used = await repo.agents_using_mcp_server(session, server_id)
    if used and not force:
        raise HTTPException(409, _used_by_message(used))
    if used:
        for agent in used:
            await invalidate_agent(agent.id)
        await repo.detach_mcp_server_from_agents(session, server_id)

    await repo.delete_mcp_server(session, server)
    return None


@router.post("/mcp-servers/{server_id}/test")
async def test_mcp_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """测试该 MCP 服务的连通性（握手 + 取一次工具列表）。"""
    server = await repo.get_mcp_server(session, server_id)
    if server is None:
        raise HTTPException(404, "MCP 服务不存在")
    ok, message = await test_connection(connection_from_snapshot(repo.mcp_server_snapshot(server)))
    return {"ok": ok, "message": message}


@router.get("/mcp-servers/{server_id}/tools")
async def get_mcp_server_tools(
    server_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """列出该 MCP 服务提供的工具（便于在 Agent 配置时确认可用能力）。"""
    server = await repo.get_mcp_server(session, server_id)
    if server is None:
        raise HTTPException(404, "MCP 服务不存在")
    ok, tools, message = await list_tools(
        connection_from_snapshot(repo.mcp_server_snapshot(server))
    )
    return {"ok": ok, "tools": tools, "message": message}


# ---------------------------------------------------------------------------
# Skill 注册表（编排方法论）
# ---------------------------------------------------------------------------
# 异常收口说明：SkillParseError（skills 模块）与 SkillImportError（skill_import 模块）
# 都继承 ValueError 但互不继承，且导入链路会「外层的 import 校验包住内层的 SKILL.md 解析」
# （例如 zip 内 SKILL.md 不合法 → 抛 SkillParseError），故一律按 ValueError 收口，
# 只捕获单一类型会让另一类逃逸成 500。
def _preview_item(parsed: dict[str, Any], conflict: bool) -> SkillImportPreviewItem:
    entries = list(parsed.get("files") or [])
    files = [
        str(f.get("path") or "")
        for f in entries
        if str(f.get("entry_type") or "text") == "text"
    ]
    scripts = [
        str(f.get("path") or "")
        for f in entries
        if str(f.get("entry_type") or "") == "script"
    ]
    content_bytes = int(parsed.get("size_bytes") or 0)
    attachments = sum(int(f.get("size") or 0) for f in entries)
    return SkillImportPreviewItem(
        name=str(parsed.get("name") or ""),
        description=str(parsed.get("description") or ""),
        content_bytes=content_bytes,
        file_count=len(files) + len(scripts),
        total_bytes=content_bytes + attachments,
        files=files,
        scripts=scripts,
        skipped_binary=[str(p) for p in (parsed.get("skipped_binary") or [])],
        conflict=conflict,
    )


def _error_item(message: str, name: str = "") -> SkillImportPreviewItem:
    """解析失败条目；带上名字便于前端/落库阶段按名字对账。"""
    return SkillImportPreviewItem(name=name, error=message)


def _source_groups(
    payload: SkillImportRequest,
) -> tuple[list[dict[str, Any]], list[str], str, str]:
    """按来源解析出 skill 组清单。

    @returns (groups, 来源级 errors, source, source_ref)
    解析失败不抛异常：以 errors 返回，其余条目照常（规格 §9）。
    """
    if payload.source == "text":
        if not payload.skill_md:
            return [], ["text 来源需要提供 skill_md"], "text", ""
        try:
            return (
                [{**parse_skill_md(payload.skill_md), "skipped_binary": []}],
                [],
                "text",
                "",
            )
        except ValueError as exc:
            return [], [str(exc)], "text", ""
    if payload.source == "zip":
        if not payload.zip_b64:
            return [], ["zip 来源需要提供 zip_b64"], "zip", ""
        try:
            return si.parse_zip_base64(payload.zip_b64), [], "zip", ""
        except ValueError as exc:
            return [], [str(exc)], "zip", ""
    if payload.source == "dir":
        if payload.dir_files is None:
            return [], ["dir 来源需要提供 dir_files"], "dir", ""
        try:
            return (
                si.parse_dir_files([f.model_dump() for f in payload.dir_files]),
                [],
                "dir",
                "",
            )
        except ValueError as exc:
            return [], [str(exc)], "dir", ""
    # URL 来源由 _resolve_groups_async 处理（fetch_url 是协程）
    return [], [f"未知来源：{payload.source}"], "", ""


async def _resolve_groups_async(
    payload: SkillImportRequest,
) -> tuple[list[dict[str, Any]], list[str], str, str]:
    """_source_groups 的异步版本：URL 来源需要 await fetch_url。"""
    if payload.source != "url":
        return _source_groups(payload)
    if not payload.url:
        return [], ["url 来源需要提供 url"], "url", ""
    try:
        blob = await si.fetch_url(payload.url)
    except ValueError as exc:
        return [], [str(exc)], "url", payload.url
    source_ref = payload.url
    if blob[:2] == b"PK":
        try:
            return si.parse_zip(blob), [], "url", source_ref
        except ValueError as exc:
            return [], [str(exc)], "url", source_ref
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        return [], ["URL 内容不是文本或 zip"], "url", source_ref
    try:
        return [{**parse_skill_md(text), "skipped_binary": []}], [], "url", source_ref
    except ValueError as exc:
        return [], [str(exc)], "url", source_ref


@router.get("/skills", response_model=list[SkillOut])
async def list_skills(
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    return await repo.list_skills(session)


@router.post("/skills", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
async def create_skill(
    data: SkillCreate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    try:
        validate_skill_fields(name=data.name, description=data.description, content=data.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if await repo.get_skill_by_name(session, data.name.strip()) is not None:
        raise HTTPException(409, f"名称 '{data.name}' 已存在")
    return await repo.create_skill(
        session,
        name=data.name.strip(),
        description=data.description,
        content=data.content,
        frontmatter={},
        files=[],
        load_mode=data.load_mode,
        size_bytes=len(data.content.encode("utf-8")),
        source="manual",
    )


@router.put("/skills/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: int,
    data: SkillUpdate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    skill = await repo.get_skill(session, skill_id)
    if skill is None:
        raise HTTPException(404, "Skill 不存在")
    try:
        validate_skill_fields(
            name=skill.name,
            description=data.description if data.description is not None else skill.description,
            content=data.content if data.content is not None else skill.content,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    affected = await repo.agents_using_skill(session, skill_id)
    updated = await repo.update_skill(session, skill, data)
    await repo.refresh_agents_for_skills(session, [skill_id])
    for agent in affected:
        await invalidate_agent(agent.id)
    return updated


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: int,
    force: bool = Query(False, description="为 true 时自动从所有 Agent 解绑后删除"),
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    skill = await repo.get_skill(session, skill_id)
    if skill is None:
        raise HTTPException(404, "Skill 不存在")
    used = await repo.agents_using_skill(session, skill_id)
    if used and not force:
        raise HTTPException(409, _used_by_message(used))
    if used:
        for agent in used:
            await invalidate_agent(agent.id)
        await repo.detach_skill_from_agents(session, skill_id)
    await repo.delete_skill_record(session, skill)
    return None


async def _build_preview(
    resolved: tuple[list[dict[str, Any]], list[str], str, str],
    session: AsyncSession,
) -> SkillImportPreviewOut:
    """组清单 → 预览清单（重名冲突 + 来源级 errors + 是否有解析失败条目）。

    接收 `_resolve_groups_async` 的结果而非 payload：URL 来源只能抓一次，
    预览与落库必须共用同一次解析（否则同一 URL 抓两遍，且两趟之间内容可能变）。
    """
    groups, errors = resolved[0], list(resolved[1])
    items: list[SkillImportPreviewItem] = []
    for group in groups:
        name = str(group.get("name") or "")
        try:
            conflict = await repo.get_skill_by_name(session, name) is not None
            items.append(_preview_item(group, conflict))
        except ValueError as exc:
            items.append(_error_item(str(exc), name))
    return SkillImportPreviewOut(
        items=items,
        errors=errors,
        over_limit=bool(errors or any(i.error for i in items)),
    )


@router.post("/skills/import/preview", response_model=SkillImportPreviewOut)
async def preview_skill_import(
    payload: SkillImportRequest,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """预览（dry-run，不落库；多 skill 包返回清单供勾选）。"""
    return await _build_preview(await _resolve_groups_async(payload), session)


@router.post("/skills/import/commit")
async def commit_skill_import(
    payload: SkillImportCommitRequest,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """预览确认后重新解析并落库（无服务端预览态，幂等可靠）。

    @returns {"created", "updated", "skipped", "failed", "items"}
    """
    # 预览与落库共用同一次解析（URL 只抓一次）；这里不能解包到 `_`（该名字已是认证依赖项）
    resolved = await _resolve_groups_async(payload)
    preview = await _build_preview(resolved, session)
    groups, source, source_ref = resolved[0], resolved[2], resolved[3]
    feasible = {item.name for item in preview.items if item.name and not item.error}
    requested = payload.names or sorted(feasible)
    wanted = [n for n in requested if n in feasible]
    # 名单里指向「解析失败条目 / 预览里没有」的名字计 skipped，不静默丢弃（前端要能对账）
    skipped = len(requested) - len(wanted)
    created = updated = 0
    touched_skill_ids: list[int] = []
    touched_agent_ids: set[int] = set()
    conflicts = {item.name: bool(item.conflict) for item in preview.items}
    by_name = {str(g.get("name") or ""): g for g in groups}
    for name in wanted:
        group = by_name.get(name)
        if group is None:
            skipped += 1
            continue
        try:
            skill, is_new = await repo.upsert_imported_skill(
                session,
                parsed=group,
                load_mode="on_demand",
                source=source,
                source_ref=source_ref,
                overwrite=payload.overwrite,
            )
        except ValueError as exc:
            preview.errors.append(f"{name}: {exc}")
            continue
        if is_new:
            created += 1
        elif conflicts.get(name) and not payload.overwrite:
            # 重名 + 不覆盖：repository 是「跳过」（未写库），不能报 updated；
            # 更不能刷新快照 / 失效图缓存——那会白白打掉引用方已编译的图
            skipped += 1
        else:
            updated += 1
            # 覆盖会重置 pending：已绑定该技能的 Agent 快照需刷新并失效图缓存
            touched_skill_ids.append(skill.id)
            for agent in await repo.agents_using_skill(session, skill.id):
                touched_agent_ids.add(agent.id)
    if touched_skill_ids:
        await repo.refresh_agents_for_skills(session, touched_skill_ids)
    for agent_id in touched_agent_ids:
        await invalidate_agent(agent_id)
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": [i.error for i in preview.items if i.error] + preview.errors,
        "items": [item.model_dump() for item in preview.items],
    }


@router.post("/skills/{skill_id}/review", response_model=SkillOut)
async def review_skill(
    skill_id: int,
    data: SkillReviewRequest,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """审核状态变更：必须触发快照刷新 + 图缓存失效（否则已发布 Agent 继续用旧技能）。"""
    skill = await repo.get_skill(session, skill_id)
    if skill is None:
        raise HTTPException(404, "Skill 不存在")
    affected = await repo.agents_using_skill(session, skill_id)
    updated = await repo.set_skill_review(
        session, skill, SkillReviewStatus(data.status), data.note
    )
    await repo.refresh_agents_for_skills(session, [skill_id])
    for agent in affected:
        await invalidate_agent(agent.id)
    return updated
