"""处理管线纯逻辑测试：去重 / 串行队列 / 回复提取（不触 DB / 不联网）。"""

import pytest
from types import SimpleNamespace

from a2a_gateway.connectors import pipeline
from a2a_gateway.connectors.base import InboundMessage
from a2a_gateway.connectors.pipeline import ConnectorRef, enqueue_message, seen_recently


def _msg(chat_id: str = "c1", event_id: str = "e1") -> InboundMessage:
    return InboundMessage(
        platform="telegram",
        chat_id=chat_id,
        chat_type="private",
        user_id="u1",
        user_name="Tom",
        text="hi",
        event_id=event_id,
    )


def _conn() -> ConnectorRef:
    return ConnectorRef(
        id=1,
        name="t",
        platform="telegram",
        credentials={"bot_token": "x"},
        agent_id=1,
        enabled=True,
    )


@pytest.fixture(autouse=True)
def _reset_state():
    pipeline._seen.clear()
    pipeline._queues.clear()
    yield
    pipeline._seen.clear()
    pipeline._queues.clear()


def test_seen_recently_dedups():
    assert seen_recently("k1") is False  # 首次见到 → 待处理
    assert seen_recently("k1") is True   # 再次见到 → 重复
    assert seen_recently("k2") is False


def test_extract_reply_string():
    # LangGraph 返回的 messages 是带 .content 属性的消息对象
    assert pipeline._extract_reply({"messages": [SimpleNamespace(content="答案")]}) == "答案"


def test_extract_reply_empty():
    assert pipeline._extract_reply(None) == ""
    assert pipeline._extract_reply({}) == ""


def test_extract_reply_content_blocks():
    result = {
        "messages": [
            SimpleNamespace(content=[{"type": "text", "text": "a"}, {"type": "img"}])
        ]
    }
    assert pipeline._extract_reply(result) == "a"


def test_enqueue_queues_per_chat(monkeypatch):
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda key: None)
    assert enqueue_message(_conn(), _msg()) is True
    assert enqueue_message(_conn(), _msg(chat_id="c2")) is True
    assert pipeline._queues[(1, "c1")].qsize() == 1
    assert pipeline._queues[(1, "c2")].qsize() == 1


def test_enqueue_rejects_when_full(monkeypatch):
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda key: None)
    for i in range(pipeline._QUEUE_LIMIT):
        assert enqueue_message(_conn(), _msg(event_id=f"e{i}")) is True
    assert enqueue_message(_conn(), _msg(event_id="overflow")) is False
