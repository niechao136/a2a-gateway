"""公开对话路由：SSE 流式响应 + 会话历史。

- POST /api/chat        → 默认 Agent (slug=/)
- POST /api/chat/{slug} → 自定义 Agent（仅 published 可访问）
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
from ..schemas import ChatRequest

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


async def _stream_chat(agent: AgentConfig, message: str, thread_id: str):
    """生成 SSE 事件流。"""
    try:
        graph = await get_agent_instance(agent)
    except Exception as exc:
        logger.exception("Agent 实例构建失败 slug=%s", agent.slug)
        await notify_alert("Agent 加载失败", f"slug={agent.slug} error={exc}")
        yield {"event": "error", "data": json.dumps({"detail": "Agent 加载失败"}, ensure_ascii=False)}
        return

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    try:
        async for event in graph.astream_events(
            {"messages": [HumanMessage(content=message)]},
            config=config,
            version="v2",
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and chunk.content:
                    yield {
                        "event": "token",
                        "data": json.dumps(
                            {"content": chunk.content}, ensure_ascii=False
                        ),
                    }
            elif kind == "on_tool_start":
                yield {
                    "event": "tool_start",
                    "data": json.dumps(
                        {"name": event.get("name", "")}, ensure_ascii=False
                    ),
                }
            elif kind == "on_tool_end":
                output = event.get("data", {}).get("output")
                yield {
                    "event": "tool_end",
                    "data": json.dumps(
                        {"name": event.get("name", ""), "output": str(output)},
                        ensure_ascii=False,
                    ),
                }
        yield {"event": "done", "data": json.dumps({"thread_id": thread_id}, ensure_ascii=False)}
    except Exception as exc:
        logger.exception("对话流式失败 slug=%s thread=%s", agent.slug, thread_id)
        await notify_alert("对话流式失败", f"slug={agent.slug} thread={thread_id} error={exc}")
        yield {
            "event": "error",
            "data": json.dumps(
                {"detail": "对话处理失败，请稍后重试"}, ensure_ascii=False
            ),
        }


@router.post("/api/chat")
async def chat_default(
    req: ChatRequest,
    session: AsyncSession = Depends(get_session),
):
    """默认 Agent 对话（slug=/）。"""
    agent = await _resolve_agent(session, "/")
    thread_id = req.thread_id or new_thread_id()
    return EventSourceResponse(_stream_chat(agent, req.message, thread_id))


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
