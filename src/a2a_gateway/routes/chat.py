"""公开对话路由：SSE 流式响应 + 会话历史 + 重试（time travel）。

- POST /api/chat              → 默认 Agent (slug=/)
- POST /api/chat/retry        → 默认 Agent 重试最后一次回复（time travel）
- POST /api/chat/{slug}       → 自定义 Agent（仅 published 可访问）
- POST /api/chat/{slug}/retry → 自定义 Agent 重试
- GET  /api/chat/history?thread_id=... → 会话历史
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ..agent_factory import get_agent_instance, get_checkpointer
from ..database import get_session
from ..deps import new_thread_id
from ..models import AgentConfig, AgentStatus
from ..notifier import notify_alert
from ..repository import get_agent_by_slug
from ..schemas import ChatRequest, RetryRequest

logger = logging.getLogger(__name__)
router = APIRouter()


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


def _sse(event: str, payload: dict) -> dict:
    return {"event": event, "data": json.dumps(payload, ensure_ascii=False)}


async def _stream_graph_events(
    graph, config: RunnableConfig, graph_input: dict | None, thread_id: str
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


@router.post("/api/chat")
async def chat_default(
    req: ChatRequest,
    session: AsyncSession = Depends(get_session),
):
    """默认 Agent 对话（slug=/）。"""
    agent = await _resolve_agent(session, "/")
    thread_id = req.thread_id or new_thread_id()
    return EventSourceResponse(_stream_chat(agent, req.message, thread_id))


@router.post("/api/chat/retry")
async def retry_chat_default(
    req: RetryRequest,
    session: AsyncSession = Depends(get_session),
):
    """默认 Agent 重试最后一次回复（time travel）。"""
    agent = await _resolve_agent(session, "/")
    return EventSourceResponse(_retry_stream(agent, req.thread_id))


# 注意：/api/chat/retry 必须注册在 /api/chat/{slug} 之前，避免 "retry" 被当作 slug
@router.post("/api/chat/{slug}")
async def chat_custom(
    slug: str,
    req: ChatRequest,
    session: AsyncSession = Depends(get_session),
):
    """自定义 Agent 对话。"""
    agent = await _resolve_agent(session, slug)
    thread_id = req.thread_id or new_thread_id()
    return EventSourceResponse(_stream_chat(agent, req.message, thread_id))


@router.post("/api/chat/{slug}/retry")
async def retry_chat_custom(
    slug: str,
    req: RetryRequest,
    session: AsyncSession = Depends(get_session),
):
    """自定义 Agent 重试最后一次回复（time travel）。"""
    agent = await _resolve_agent(session, slug)
    return EventSourceResponse(_retry_stream(agent, req.thread_id))


@router.get("/api/chat/history")
async def chat_history_default(thread_id: str):
    """默认 Agent 会话历史。"""
    return await _get_history(thread_id)


@router.get("/api/chat/{slug}/history")
async def chat_history_custom(slug: str, thread_id: str):
    """自定义 Agent 会话历史。slug 用于路由匹配，会话以 thread_id 为键。"""
    _ = slug  # 路由占位，会话历史按 thread_id 全局唯一
    return await _get_history(thread_id)


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
