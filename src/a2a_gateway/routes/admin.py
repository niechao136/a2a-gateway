"""管理中心路由：Agent 配置 CRUD + 发布/下线 + 测试对话 + A2A 连通性测试。

除 /login 外均需 JWT 管理员认证。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..a2a_client import A2AClientWrapper
from ..agent_factory import get_agent_instance, invalidate_agent
from ..auth import create_access_token, verify_password
from ..database import get_session
from ..deps import get_current_admin, new_thread_id
from ..models import AdminUser, AgentStatus
from ..repository import (
    create_agent,
    delete_agent,
    get_agent_by_id,
    get_agent_by_slug,
    list_agents,
    set_agent_status,
    update_agent,
)
from ..schemas import (
    AdminLoginRequest,
    AgentCreate,
    AgentOut,
    AgentUpdate,
    ChatRequest,
    Token,
)
from sse_starlette.sse import EventSourceResponse

from .chat import _stream_chat

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

# 禁止自定义 Agent 使用的保留 slug
RESERVED_SLUGS = {"/", ""}


@router.post("/login", response_model=Token)
async def login(
    req: AdminLoginRequest,
    session: AsyncSession = Depends(get_session),
):
    """管理员登录，返回 JWT。"""
    from ..repository import get_admin_by_username

    admin = await get_admin_by_username(session, req.username)
    if admin is None or not verify_password(req.password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    token = create_access_token(admin.username)
    return Token(access_token=token, expires_in=1440)


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
        raise HTTPException(400, "slug '/' 为默认 Agent 保留，禁止使用")
    existing = await get_agent_by_slug(session, data.slug)
    if existing is not None:
        raise HTTPException(409, f"slug '{data.slug}' 已被占用")
    return await create_agent(session, data)


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
    updated = await update_agent(session, agent, data)
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
