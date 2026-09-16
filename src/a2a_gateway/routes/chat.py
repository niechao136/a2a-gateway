"""公开对话路由：SSE 流式响应 + 会话历史 + 重试（time travel）+ 会话目录。

- POST /api/chat/identity                 → 确保身份 cookie（匿名访客 / 登录用户）
- GET  /api/chat/conversations            → 当前身份的会话列表
- POST /api/chat/conversations            → 登记 / 刷新一条会话
- POST /api/chat/conversations/import     → 批量导入（前端 localStorage 迁移）
- PATCH/DELETE /api/chat/conversations/{thread_id}
- POST /api/chat              → 默认 Agent (slug=/)
- POST /api/chat/retry        → 默认 Agent 重试最后一次回复（time travel）
- POST /api/chat/{slug}       → 自定义 Agent（仅 published 可访问）
- POST /api/chat/{slug}/retry → 自定义 Agent 重试
- GET  /api/chat/history?thread_id=... → 会话历史

⚠️ 路由顺序：带字面量的路径（identity / conversations）必须注册在
``/api/chat/{slug}`` 之前，否则会被 slug 通配吃掉。
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ..agent_factory import get_agent_instance, get_checkpointer
from ..database import get_session
from ..deps import new_thread_id
from ..identity import Identity, ensure_identity, set_identity_cookie
from ..models import AgentConfig, AgentStatus
from ..notifier import notify_alert
from ..repository import (
    delete_conversation,
    get_agent_by_slug,
    get_conversation,
    import_conversations,
    list_conversations,
    rename_conversation,
    upsert_conversation,
)
from ..schemas import (
    ChatRequest,
    ConversationCreate,
    ConversationImportOut,
    ConversationImportRequest,
    ConversationOut,
    ConversationRename,
    IdentityOut,
    RetryRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# 前端用空串表示默认 Agent，库里统一存 "/"
DEFAULT_SLUG = "/"


async def _resolve_agent(session: AsyncSession, slug: str) -> AgentConfig:
    """根据 slug 解析已发布的 Agent；未找到/未发布返回 404。"""
    target_slug = "/" if slug in ("", "/", "default") else slug
    agent = await get_agent_by_slug(session, target_slug)
    if agent is None or agent.status != AgentStatus.PUBLISHED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="对话 Agent 不存在或未发布",
        )
    return agent


def _sse(event: str, payload: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(payload, ensure_ascii=False)}


async def _stream_graph_events(
    graph: Any, config: RunnableConfig, graph_input: dict[str, Any] | None, thread_id: str
):
    """把 graph.astream_events 转成对话 SSE 事件流（新对话 / 重试共用）。

    graph_input 为 None 时表示 time travel 重放（checkpoint 已含待处理的输入）。
    """
    try:
        async for event in graph.astream_events(graph_input, config=config, version="v2"):
            kind = event["event"]
            node = (event.get("metadata") or {}).get("langgraph_node")
            if kind == "on_chat_model_stream":
                # 只透传主对话模型的 token；过滤 pre_model_hook 里的摘要调用
                if node != "agent":
                    continue
                chunk = event["data"].get("chunk")
                if chunk and chunk.content:
                    yield _sse("token", {"content": chunk.content})
            elif kind == "on_tool_start":
                yield _sse("tool_start", {"name": event.get("name", "")})
            elif kind == "on_tool_end":
                output = event.get("data", {}).get("output")
                yield _sse(
                    "tool_end",
                    {"name": event.get("name", ""), "output": str(output)},
                )
        yield _sse("done", {"thread_id": thread_id})
    except Exception as exc:
        logger.exception("对话流式失败 thread=%s", thread_id)
        await notify_alert("对话流式失败", f"thread={thread_id} error={exc}")
        yield _sse("error", {"detail": "对话处理失败，请稍后重试"})


async def _stream_chat(agent: AgentConfig, message: str, thread_id: str):
    """生成新对话的 SSE 事件流。"""
    try:
        graph = await get_agent_instance(agent)
    except Exception as exc:
        logger.exception("Agent 实例构建失败 slug=%s", agent.slug)
        await notify_alert("Agent 加载失败", f"slug={agent.slug} error={exc}")
        yield _sse("error", {"detail": "Agent 加载失败"})
        return

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    graph_input = {"messages": [HumanMessage(content=message)]}
    async for evt in _stream_graph_events(graph, config, graph_input, thread_id):
        yield evt


async def _retry_stream(agent: AgentConfig, thread_id: str):
    """time travel 重试：从最后一次人类消息处重放生成回复。"""
    try:
        graph = await get_agent_instance(agent)
    except Exception as exc:
        logger.exception("Agent 实例构建失败 slug=%s", agent.slug)
        yield _sse("error", {"detail": "Agent 加载失败"})
        return

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    # 找「最新一个以人类消息结尾、且仍有待执行节点」的检查点 → 重放该点，
    # 模型将重新生成回复，其后的旧回复随之分叉丢弃
    target = None
    try:
        async for snapshot in graph.aget_state_history(config):
            msgs = (snapshot.values or {}).get("messages") or []
            if msgs and isinstance(msgs[-1], HumanMessage) and snapshot.next:
                target = snapshot
                break
    except Exception:
        logger.exception("读取检查点历史失败 thread=%s", thread_id)
        yield _sse("error", {"detail": "重试失败，请稍后重试"})
        return

    if target is None:
        yield _sse("error", {"detail": "没有可重试的对话"})
        return

    checkpoint_id = (target.config or {}).get("configurable", {}).get("checkpoint_id")
    retry_config: RunnableConfig = {
        "configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}
    }
    async for evt in _stream_graph_events(graph, retry_config, None, thread_id):
        yield evt


# ---------------------------------------------------------------------------
# 身份与会话目录（必须注册在 /api/chat/{slug} 之前）
# ---------------------------------------------------------------------------
@router.post("/api/chat/identity", response_model=IdentityOut)
async def ensure_chat_identity(request: Request, response: Response):
    """确保身份 cookie 存在；首次访问时签发匿名身份。

    前端在页面挂载时调用一次，之后所有请求由浏览器自动带上 cookie。
    """
    identity, issued = ensure_identity(request)
    if issued:
        set_identity_cookie(response, identity)
    return IdentityOut(kind=identity.kind, id=identity.id)


@router.get("/api/chat/conversations", response_model=list[ConversationOut])
async def list_chat_conversations(
    request: Request,
    response: Response,
    slug: str = "",
    session: AsyncSession = Depends(get_session),
):
    """当前身份名下的会话列表（按最近更新倒序）。"""
    identity, issued = ensure_identity(request)
    # 普通 JSON 响应可正常写 cookie；首次访问顺带把匿名身份落盘
    if issued:
        set_identity_cookie(response, identity)
    return await list_conversations(session, identity, slug)


@router.post("/api/chat/conversations", response_model=ConversationOut | None)
async def upsert_chat_conversation(
    data: ConversationCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """登记 / 刷新一条会话；会话已归属他人时返回 null（不抢归属）。"""
    identity, _ = ensure_identity(request)
    conv = await upsert_conversation(
        session, identity, data.thread_id, data.slug, data.title
    )
    if conv is None:
        return None
    return conv


@router.post(
    "/api/chat/conversations/import", response_model=ConversationImportOut
)
async def import_chat_conversations(
    data: ConversationImportRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """批量导入历史会话（前端 localStorage 一次性迁移）。"""
    identity, _ = ensure_identity(request)
    items = [item.model_dump() for item in data.items]
    imported = await import_conversations(session, identity, data.slug, items)
    return ConversationImportOut(imported=imported)


@router.patch("/api/chat/conversations/{thread_id}", response_model=ConversationOut | None)
async def rename_chat_conversation(
    thread_id: str,
    data: ConversationRename,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """重命名会话；不属于当前身份时返回 null。"""
    identity, _ = ensure_identity(request)
    conv = await rename_conversation(session, identity, thread_id, data.title)
    if conv is None:
        return None
    return conv


@router.delete("/api/chat/conversations/{thread_id}")
async def delete_chat_conversation(
    thread_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """删除会话：清目录 + 清 Checkpointer 中的消息本体。"""
    identity, _ = ensure_identity(request)
    removed = await delete_conversation(session, identity, thread_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在"
        )
    # 消息本体也一并清理（失败不回滚目录删除，避免产生删不掉的幽灵会话）
    try:
        checkpointer = await get_checkpointer()
        await checkpointer.adelete_thread(thread_id)
    except Exception:
        logger.exception("清理检查点失败 thread=%s", thread_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 对话（SSE）
# ---------------------------------------------------------------------------
@router.post("/api/chat")
async def chat_default(
    req: ChatRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """默认 Agent 对话（slug=/）。"""
    agent = await _resolve_agent(session, "/")
    thread_id = req.thread_id or new_thread_id()
    identity, issued = ensure_identity(request)
    await _register(session, identity, thread_id, DEFAULT_SLUG, req)
    resp = EventSourceResponse(_stream_chat(agent, req.message, thread_id))
    if issued:
        set_identity_cookie(resp, identity)
    return resp


@router.post("/api/chat/retry")
async def retry_chat_default(
    req: RetryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """默认 Agent 重试最后一次回复（time travel）。"""
    agent = await _resolve_agent(session, "/")
    identity, issued = ensure_identity(request)
    await _ensure_owned(session, identity, req.thread_id)
    resp = EventSourceResponse(_retry_stream(agent, req.thread_id))
    if issued:
        set_identity_cookie(resp, identity)
    return resp


# 注意：/api/chat/retry 必须注册在 /api/chat/{slug} 之前，避免 "retry" 被当作 slug
@router.post("/api/chat/{slug}")
async def chat_custom(
    slug: str,
    req: ChatRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """自定义 Agent 对话。"""
    agent = await _resolve_agent(session, slug)
    thread_id = req.thread_id or new_thread_id()
    identity, issued = ensure_identity(request)
    await _register(session, identity, thread_id, slug, req)
    resp = EventSourceResponse(_stream_chat(agent, req.message, thread_id))
    if issued:
        set_identity_cookie(resp, identity)
    return resp


@router.post("/api/chat/{slug}/retry")
async def retry_chat_custom(
    slug: str,
    req: RetryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """自定义 Agent 重试最后一次回复（time travel）。"""
    agent = await _resolve_agent(session, slug)
    identity, issued = ensure_identity(request)
    await _ensure_owned(session, identity, req.thread_id)
    resp = EventSourceResponse(_retry_stream(agent, req.thread_id))
    if issued:
        set_identity_cookie(resp, identity)
    return resp


@router.get("/api/chat/history")
async def chat_history_default(
    thread_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """默认 Agent 会话历史。"""
    identity, _ = ensure_identity(request)
    await _ensure_owned(session, identity, thread_id)
    return await _get_history(thread_id)


@router.get("/api/chat/{slug}/history")
async def chat_history_custom(
    slug: str,
    thread_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """自定义 Agent 会话历史。slug 用于路由匹配，会话以 thread_id 为键。"""
    _ = slug  # 路由占位，会话历史按 thread_id 全局唯一
    identity, _ = ensure_identity(request)
    await _ensure_owned(session, identity, thread_id)
    return await _get_history(thread_id)


# ---------------------------------------------------------------------------
# 归属校验辅助
# ---------------------------------------------------------------------------
async def _register(
    session: AsyncSession,
    identity: Identity,
    thread_id: str,
    slug: str,
    req: ChatRequest,
) -> None:
    """发消息时登记 / 刷新会话目录。

    归属不成立（会话属于他人）时静默跳过：不阻断对话，只是不写目录。
    """
    try:
        await upsert_conversation(
            session, identity, thread_id, slug, (req.message or "")[:24]
        )
    except Exception:
        logger.exception("登记会话目录失败 thread=%s", thread_id)


async def _ensure_owned(
    session: AsyncSession, identity: Identity, thread_id: str
) -> None:
    """校验会话归属；未登记的会话放行（回填前的历史数据不受影响）。"""
    conv = await get_conversation(session, thread_id)
    if conv is not None and not identity.owns(conv):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在"
        )


async def _get_history(thread_id: str) -> list[dict[str, str]]:
    """从 Checkpointer 提取会话历史消息。

    与前端流式渲染保持一致：工具调用以 ``role=tool`` 消息返回
    （``toolName`` / ``toolOutput``），刷新后仍能还原工具卡片，
    而不是折叠成普通助手文本。
    """
    checkpointer = await get_checkpointer()
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    try:
        tuple_ = await checkpointer.aget_tuple(config)
    except Exception:
        logger.exception("读取历史失败 thread=%s", thread_id)
        raise HTTPException(status_code=500, detail="读取会话历史失败")
    if tuple_ is None:
        return []
    messages = tuple_.checkpoint.get("channel_values", {}).get("messages", [])
    history = []
    for msg in messages:
        if msg.type == "human":
            history.append(
                {
                    "role": "user",
                    "content": msg.content
                    if isinstance(msg.content, str)
                    else str(msg.content),
                }
            )
        elif msg.type == "tool":
            history.append(
                {
                    "role": "tool",
                    "content": "",
                    "toolName": getattr(msg, "name", "") or "",
                    "toolOutput": msg.content
                    if isinstance(msg.content, str)
                    else str(msg.content),
                }
            )
        else:  # ai / system 等：仅输出有文本内容的助手消息
            content = (
                msg.content if isinstance(msg.content, str) else str(msg.content)
            )
            if content:
                history.append({"role": "assistant", "content": content})
    return history
