"""A2A Server 端点：把管理中心配置的 Agent 以 A2A 协议对外发布。

路由：
- GET  /a2a/{slug}/.well-known/agent-card.json → Agent Card（公开，供调用方发现）
- GET  /a2a/{slug}                              → Agent Card（同样公开，便于调试）
- POST /a2a/{slug}                              → A2A JSON-RPC 入口（需 API Key）

协议支持：
- SendMessage / SendStreamingMessage（1.0 方法名），
  同时兼容 0.3 的 message/send、message/stream
- GetTask / CancelTask：网关不做任务持久化，统一返回 TaskNotFound
- 其余方法返回 MethodNotFound

鉴权：POST 需携带 ``X-Api-Key`` 请求头或 ``Authorization: Bearer <key>``，
key 必须是「API Key 管理」中已启用的密钥（默认 Key 随应用首次启动自动生成）。

Agent Card 中声明的回连地址由请求的 Host / X-Forwarded-* 头推导，
经 nginx 反代后即为对外可达地址；网关自身也可以在「A2A 管理」中
注册本网关地址（auth_type=header, auth_name=X-Api-Key）来实现
「Agent 之间互相调用」。
"""

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from google.protobuf.json_format import MessageToDict, ParseDict
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from a2a.helpers.proto_helpers import (
    get_data_parts,
    get_message_text,
    new_task,
    new_text_message,
    new_text_status_update_event,
)
from a2a.server.jsonrpc_models import InvalidRequestError, MethodNotFoundError
from a2a.server.request_handlers.response_helpers import (
    agent_card_to_dict,
    build_error_response,
)
from a2a.types.a2a_pb2 import (
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_WORKING,
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    CancelTaskRequest,
    GetTaskRequest,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, TransportProtocol
from a2a.utils.errors import TaskNotFoundError

from ..agent_factory import get_agent_instance
from ..database import get_session
from ..models import AgentConfig, AgentStatus
from ..repository import get_agent_by_slug, get_api_key_by_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["a2a-server"])

# 1.0 与 0.3 的 JSON-RPC 方法名兼容映射
METHOD_ALIASES: dict[str, str] = {
    "SendMessage": "SendMessage",
    "message/send": "SendMessage",
    "SendStreamingMessage": "SendStreamingMessage",
    "message/stream": "SendStreamingMessage",
    "GetTask": "GetTask",
    "tasks/get": "GetTask",
    "CancelTask": "CancelTask",
    "tasks/cancel": "CancelTask",
}
STREAMING_METHODS = {"SendStreamingMessage"}

# slug 归一化：default / 空 → 默认 Agent（slug="/"）
_DEFAULT_SLUGS = {"", "/", "default"}


def a2a_path_for_slug(slug: str) -> str:
    """Agent 的对外 A2A 地址路径（默认 Agent 为 /a2a）。"""
    return "/a2a" if slug in _DEFAULT_SLUGS else f"/a2a/{slug}"


def _normalize_slug(slug: str | None) -> str:
    return "/" if not slug or slug in ("default", "/") else slug


# ---------------------------------------------------------------------------
# 鉴权：API Key（每个 Agent 独立配置）
# ---------------------------------------------------------------------------
async def validate_api_key(
    request: Request,
    session: AsyncSession,
    agent: AgentConfig,
) -> None:
    """校验 X-Api-Key / Bearer 凭据；Key 必须属于被调用的 Agent 且已启用。"""
    key = (request.headers.get("x-api-key") or "").strip()
    if not key:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    api_key = await get_api_key_by_key(session, key)
    if (
        api_key is None
        or not api_key.enabled
        or api_key.agent_id != agent.id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效、已停用或不属于该 Agent 的 API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Agent 解析
# ---------------------------------------------------------------------------
async def _resolve_published_agent(session: AsyncSession, slug: str) -> AgentConfig:
    agent = await get_agent_by_slug(session, _normalize_slug(slug))
    if agent is None or agent.status != AgentStatus.PUBLISHED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="A2A Agent 不存在或未发布",
        )
    return agent


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------
def _public_base_url(request: Request) -> str:
    """从请求头推导对外可达的网关基础地址（支持经 nginx 反代）。"""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{proto}://{host}".rstrip("/")


def build_agent_card(agent: AgentConfig, base_url: str) -> AgentCard:
    url = base_url + a2a_path_for_slug(agent.slug)
    description = agent.description or agent.name
    return AgentCard(
        name=agent.name,
        description=description,
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=url,
                protocol_binding=TransportProtocol.JSONRPC,
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id=f"agent-{agent.id}",
                name=agent.name,
                description=description,
                tags=["chat"],
            )
        ],
    )


@router.get("/a2a")
async def get_agent_card_default(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """默认 Agent（slug=/）的 Agent Card。"""
    agent = await _resolve_published_agent(session, "/")
    card = build_agent_card(agent, _public_base_url(request))
    return JSONResponse(agent_card_to_dict(card))


@router.get("/a2a/.well-known/agent-card.json")
async def get_agent_card_well_known_default(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """默认 Agent（slug=/）的 well-known Agent Card。"""
    agent = await _resolve_published_agent(session, "/")
    card = build_agent_card(agent, _public_base_url(request))
    return JSONResponse(agent_card_to_dict(card))


@router.get("/a2a/{slug}/.well-known/agent-card.json")
async def get_agent_card_well_known(
    slug: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """标准 well-known 路径的 Agent Card（公开）。"""
    agent = await _resolve_published_agent(session, slug)
    card = build_agent_card(agent, _public_base_url(request))
    return JSONResponse(agent_card_to_dict(card))


@router.get("/a2a/{slug}")
async def get_agent_card(
    slug: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """GET 根路径也返回 Agent Card（便于人工/客户端直接访问）。"""
    agent = await _resolve_published_agent(session, slug)
    card = build_agent_card(agent, _public_base_url(request))
    return JSONResponse(agent_card_to_dict(card))


# ---------------------------------------------------------------------------
# JSON-RPC 入口
# ---------------------------------------------------------------------------
def _rpc_error(request_id: Any, error: Any) -> JSONResponse:
    return JSONResponse(build_error_response(request_id, error))


@router.post("/a2a")
async def a2a_rpc_default(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """默认 Agent 的 A2A JSON-RPC 入口。"""
    return await a2a_rpc("/", request, session)


@router.post("/a2a/{slug}")
async def a2a_rpc(
    slug: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """A2A JSON-RPC 协议入口（该 Agent 自己的 API Key 鉴权）。"""
    agent = await _resolve_published_agent(session, slug)
    await validate_api_key(request, session, agent)
    try:
        body = await request.json()
    except Exception:
        return _rpc_error(None, InvalidRequestError(message="请求体不是合法 JSON"))

    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        return _rpc_error(None, InvalidRequestError(message="jsonrpc 必须为 '2.0'"))
    request_id = body.get("id")
    method = METHOD_ALIASES.get(body.get("method") or "")
    if not method:
        return _rpc_error(request_id, MethodNotFoundError())

    params = body.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(request_id, InvalidRequestError(message="params 必须为对象"))

    if method in ("GetTask", "CancelTask"):
        # 网关为无状态转发，不持久化 A2A 任务
        if method == "GetTask":
            ParseDict(params, GetTaskRequest())
        else:
            ParseDict(params, CancelTaskRequest())
        return _rpc_error(request_id, TaskNotFoundError())

    if method != "SendMessage" and method not in STREAMING_METHODS:
        return _rpc_error(request_id, MethodNotFoundError())

    try:
        req = ParseDict(params, SendMessageRequest())
    except Exception as e:
        return _rpc_error(
            request_id, InvalidRequestError(message=f"params 解析失败：{e}")
        )

    text = get_message_text(req.message).strip()
    if not text:
        # 兜底：数据部件 {"query": ...}（网关自身作为客户端时使用的格式）
        for part in get_data_parts(req.message.parts):
            try:
                value = part.get("query") or part.get("text")
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
    if not text:
        return _rpc_error(
            request_id, InvalidRequestError(message="消息中未找到文本内容")
        )

    context_id = req.message.context_id or req.message.task_id or uuid.uuid4().hex
    task_id = req.message.task_id or uuid.uuid4().hex

    try:
        if method in STREAMING_METHODS:
            return EventSourceResponse(
                _rpc_stream(request_id, agent, text, context_id, task_id)
            )
        return JSONResponse(
            await _rpc_send_message(agent, text, context_id, task_id, request_id)
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("A2A RPC 处理失败 slug=%s", agent.slug)
        return _rpc_error(request_id, InvalidRequestError(message="Agent 处理失败"))


async def _stream_agent_text(
    agent: AgentConfig, text: str, thread_id: str
) -> AsyncGenerator[str, None]:
    """运行 LangGraph 图，产出模型增量文本。"""
    graph = await get_agent_instance(agent)
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    async for event in graph.astream_events(
        {"messages": [HumanMessage(content=text)]},
        config=config,
        version="v2",
    ):
        if event["event"] == "on_chat_model_stream":
            chunk = event["data"].get("chunk")
            if chunk and chunk.content:
                yield chunk.content


async def _rpc_send_message(
    agent: AgentConfig, text: str, context_id: str, task_id: str, request_id: Any
) -> dict[str, Any]:
    """非流式 SendMessage：等 Agent 跑完后返回完整消息。"""
    chunks: list[str] = []
    async for chunk in _stream_agent_text(agent, text, context_id):
        chunks.append(chunk)
    message = new_text_message("".join(chunks), context_id=context_id, task_id=task_id)
    response = SendMessageResponse(message=message)
    result = MessageToDict(response)
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _sse_result(request_id: Any, response: Any) -> dict[str, str]:
    """把流式事件包装为 JSON-RPC 响应的 SSE 帧。"""
    result = MessageToDict(response)
    return {
        "data": json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "result": result},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    }


async def _rpc_stream(
    request_id: Any, agent: AgentConfig, text: str, context_id: str, task_id: str
) -> AsyncGenerator[dict[str, str], None]:
    """流式 SendStreamingMessage：按 A2A 1.0 任务式语义发事件。

    - 首事件：Task（working）—— 标准客户端在收到裸 message 事件时会视为
      「最终完整回复」并立即终止流（a2a-sdk base_client._process_stream），
      因此增量文本必须经 status_update 携带，不能直接发 message
    - 中间事件：TaskStatusUpdateEvent（working，message 携带增量文本）
    - 结束事件：TaskStatusUpdateEvent（completed）
    """
    try:
        yield _sse_result(
            request_id,
            StreamResponse(
                task=new_task(task_id=task_id, context_id=context_id, state=TASK_STATE_WORKING)
            ),
        )
        async for chunk in _stream_agent_text(agent, text, context_id):
            yield _sse_result(
                request_id,
                StreamResponse(
                    status_update=new_text_status_update_event(
                        task_id=task_id,
                        context_id=context_id,
                        state=TASK_STATE_WORKING,
                        text=chunk,
                    )
                ),
            )
        yield _sse_result(
            request_id,
            StreamResponse(
                status_update=TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TASK_STATE_COMPLETED),
                )
            ),
        )
    except Exception:
        logger.exception("A2A 流式处理失败 slug=%s", agent.slug)
        yield {
            "event": "error",
            "data": json.dumps(
                build_error_response(
                    request_id, InvalidRequestError(message="Agent 处理失败")
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
