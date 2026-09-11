"""注册表路由：A2A 目标与 MCP 服务的 CRUD、连通性测试与工具清单。

所有接口均位于 /api/admin 下，需要 JWT 管理员认证。

删除保护：注册表项被 Agent 引用时默认拒绝删除（409），
可用 `?force=true` 强制删除并自动从所有 Agent 上解绑。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import repository as repo
from ..a2a_client import A2AClientWrapper
from ..agent_factory import invalidate_agent
from ..database import get_session
from ..deps import get_current_admin
from ..mcp_client import connection_from_snapshot, list_tools, test_connection
from ..models import AdminUser
from ..schemas import (
    A2AEndpointCreate,
    A2AEndpointOut,
    A2AEndpointUpdate,
    A2ATarget,
    McpServerCreate,
    McpServerOut,
    McpServerUpdate,
    validate_auth,
    validate_mcp_transport,
)

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
