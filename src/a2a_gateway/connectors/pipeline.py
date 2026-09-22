"""连接器消息处理管线：事件去重 → 会话级串行队列 → Agent 调用 → 回复推送。

- 平台 webhook 要求 ~3s 内确认，Agent 调用可能数十秒：路由层立即 200，
  处理在本管线后台进行（asyncio.create_task worker）
- 同一会话（connector_id, chat_id）串行处理，保序避免上下文交叉；
  队列上限 _QUEUE_LIMIT，队满丢弃新消息（由调用方回复忙提示）
- 单条处理超时 _PROCESS_TIMEOUT_SECONDS，超时/异常回复兜底文案并告警
"""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

from langchain_core.messages import HumanMessage

from ..agent_factory import get_agent_instance
from ..database import AsyncSessionLocal
from ..models import AgentConfig, AgentStatus
from ..notifier import notify_alert
from ..repository import upsert_connector_conversation
from .base import InboundMessage
from .registry import get_adapter

logger = logging.getLogger(__name__)

_DEDUP_CAPACITY = 4096
_DEDUP_TTL_SECONDS = 600.0
_QUEUE_LIMIT = 5
_PROCESS_TIMEOUT_SECONDS = 120.0

REPLY_UNPUBLISHED = "绑定的 Agent 未发布，暂时无法处理消息"
REPLY_BUSY = "消息较多，请稍后再试"
REPLY_FAILURE = "处理失败，请稍后重试"


@dataclass
class ConnectorRef:
    """连接器在后台任务中的最小引用（避免跨请求持有 ORM 实例）。"""

    id: int
    name: str
    platform: str
    credentials: dict
    agent_id: int
    enabled: bool


# ---------------------------------------------------------------------------
# 事件去重（进程内 TTL LRU：覆盖平台超时重试场景）
# ---------------------------------------------------------------------------
_seen: OrderedDict[str, float] = OrderedDict()


def seen_recently(key: str) -> bool:
    """登记事件并判断是否刚处理过；首次见到返回 False。"""
    now = time.monotonic()
    for k in [k for k, ts in _seen.items() if now - ts > _DEDUP_TTL_SECONDS]:
        _seen.pop(k, None)
    if key in _seen:
        _seen.move_to_end(key)
        return True
    _seen[key] = now
    while len(_seen) > _DEDUP_CAPACITY:
        _seen.popitem(last=False)
    return False


# ---------------------------------------------------------------------------
# 会话级串行队列
# ---------------------------------------------------------------------------
@dataclass
class _Job:
    connector: ConnectorRef
    message: InboundMessage


_queues: dict[tuple[int, str], asyncio.Queue] = {}


def _get_queue(key: tuple[int, str]) -> asyncio.Queue:
    queue = _queues.get(key)
    if queue is None:
        queue = asyncio.Queue(maxsize=_QUEUE_LIMIT)
        _queues[key] = queue
        _ensure_worker(key)
    return queue


def _ensure_worker(key: tuple[int, str]) -> None:
    asyncio.create_task(_worker(key))


def enqueue_message(connector: ConnectorRef, message: InboundMessage) -> bool:
    """投递消息到会话队列；队满返回 False（调用方负责回复忙提示）。"""
    key = (connector.id, message.chat_id)
    try:
        _get_queue(key).put_nowait(_Job(connector=connector, message=message))
        return True
    except asyncio.QueueFull:
        logger.warning("连接器会话队列已满 connector=%s chat=%s", key[0], key[1])
        return False


async def _worker(key: tuple[int, str]) -> None:
    queue = _queues[key]
    while True:
        job = await queue.get()
        try:
            await asyncio.wait_for(
                _process(job.connector, job.message), timeout=_PROCESS_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            logger.error("连接器消息处理超时 connector=%s chat=%s", key[0], key[1])
            await _safe_reply(job.connector, job.message, REPLY_FAILURE)
        except Exception as exc:
            logger.exception("连接器消息处理失败 connector=%s chat=%s", key[0], key[1])
            await _safe_reply(job.connector, job.message, REPLY_FAILURE)
            await notify_alert(
                "连接器消息处理失败",
                f"connector={job.connector.name} chat={key[1]} error={exc}",
            )
        finally:
            queue.task_done()


async def _process(connector: ConnectorRef, message: InboundMessage) -> None:
    """单条消息处理：映射会话 → 校验 Agent → 调用图 → 回复推送。"""
    async with AsyncSessionLocal() as session:
        conversation = await upsert_connector_conversation(
            session,
            connector.id,
            connector.platform,
            message.chat_id,
            message.chat_type,
            message.user_id,
            message.user_name,
        )
        agent = await session.get(AgentConfig, connector.agent_id)
        if agent is None or agent.status != AgentStatus.PUBLISHED:
            await _safe_reply(connector, message, REPLY_UNPUBLISHED)
            return
        content = (
            f"[{message.user_name}]: {message.text}"
            if message.chat_type == "group"
            else message.text
        )
        graph = await get_agent_instance(agent)
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=content)]},
            config={"configurable": {"thread_id": conversation.thread_id}},
        )
    reply = _extract_reply(result)
    if reply.strip():
        adapter = get_adapter(connector.platform)
        await adapter.send(connector.credentials, message.chat_id, reply)


def _extract_reply(result: dict | None) -> str:
    """从 graph.ainvoke 结果取最终回复文本（兼容 str 与内容块列表）。"""
    messages = (result or {}).get("messages") or []
    if not messages:
        return ""
    content = messages[-1].content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(part for part in parts if part)
    return str(content)


async def _safe_reply(connector: ConnectorRef, message: InboundMessage, text: str) -> None:
    """尽力回复兜底文案；失败只记日志，绝不影响主流程。"""
    try:
        adapter = get_adapter(connector.platform)
        await adapter.send(connector.credentials, message.chat_id, text)
    except Exception:
        logger.exception("连接器回复失败 connector=%s chat=%s", connector.id, message.chat_id)
