"""链路 A（对话界面）input-required 测试：拦截恢复、interrupt 事件、历史追加与清理。"""

from types import SimpleNamespace

import pytest

from a2a_gateway.a2a_client import A2ATargetError, Completed, InputRequired, TextChunk
from a2a_gateway.models import AgentStatus
from a2a_gateway.routes import chat as chat_mod


def _record(**overrides):
    base = {
        "thread_id": "t1",
        "agent_id": 1,
        "target_url": "http://h:9900/",
        "target_name": "travel",
        "task_id": "task-1",
        "context_id": "ctx-1",
        "question": "请补充目的地",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeStore:
    def __init__(self, pending=None):
        self.pending = pending
        self.upserts = []
        self.deleted = []

    async def get(self, thread_id):
        return self.pending

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        self.pending = _record(**kwargs)

    async def delete(self, thread_id):
        self.deleted.append(thread_id)
        self.pending = None


class FakeWrapper:
    def __init__(self, url, events, error=None):
        self.target = SimpleNamespace(url=url)
        self._events = events
        self._error = error
        self.calls = []

    async def stream_message_events(self, text, *, task_id=None, context_id=None, **kwargs):
        self.calls.append({"text": text, "task_id": task_id, "context_id": context_id})
        if self._error is not None:
            raise self._error
        for event in self._events:
            yield event


class FakeGraph:
    """恢复轮用：禁止调用 LLM 流，只允许追加历史。"""

    def __init__(self, on_stream=None):
        self.on_stream = on_stream
        self.updated = []

    async def astream_events(self, *args, **kwargs):
        if self.on_stream is None:
            raise AssertionError("恢复轮不应调用 LLM 图")
        self.on_stream()
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="请补充目的地")},
            "metadata": {"langgraph_node": "agent"},
        }

    async def aupdate_state(self, config, values, as_node=None):
        self.updated.append({"config": config, "values": values, "as_node": as_node})


@pytest.fixture(autouse=True)
def stub_conversations(monkeypatch):
    async def _no_conversation(session, thread_id):
        return None

    async def _ignore(*args, **kwargs):
        return None

    monkeypatch.setattr(chat_mod, "get_conversation", _no_conversation)
    monkeypatch.setattr(chat_mod, "upsert_conversation", _ignore)


def _patch_agent(monkeypatch, make_agent):
    async def fake_get(session, slug):
        return make_agent(status=AgentStatus.PUBLISHED)

    monkeypatch.setattr(chat_mod, "get_agent_by_slug", fake_get)


async def test_resume_forwards_with_task_id_and_skips_llm(anon_client, monkeypatch, make_agent):
    store = FakeStore(pending=_record())
    wrapper = FakeWrapper(
        "http://h:9900/", [TextChunk("行程内容"), Completed(task_id="task-1")]
    )
    graph = FakeGraph()

    async def fake_wrappers(agent):
        return [wrapper]

    async def fake_instance(agent):
        return graph

    _patch_agent(monkeypatch, make_agent)
    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "get_agent_wrappers", fake_wrappers)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post(
        "/api/chat", json={"message": "杭州 10/1-10/3 预算3000", "thread_id": "t1"}
    )

    body = resp.text
    assert "event: token" in body and "行程内容" in body
    assert "event: done" in body
    assert "event: interrupt" not in body
    assert wrapper.calls == [
        {"text": "杭州 10/1-10/3 预算3000", "task_id": "task-1", "context_id": "ctx-1"}
    ]
    assert store.deleted == ["t1"]
    # 历史追加：Human + AI 两条
    assert len(graph.updated) == 1
    assert [m.content for m in graph.updated[0]["values"]["messages"]] == [
        "杭州 10/1-10/3 预算3000",
        "行程内容",
    ]


async def test_resume_updates_pending_on_repeat_interrupt(anon_client, monkeypatch, make_agent):
    store = FakeStore(pending=_record(question="旧追问"))
    wrapper = FakeWrapper(
        "http://h:9900/",
        [InputRequired(task_id="task-1", context_id="ctx-1", question="请补充预算")],
    )

    async def fake_wrappers(agent):
        return [wrapper]

    async def fake_instance(agent):
        return FakeGraph()

    _patch_agent(monkeypatch, make_agent)
    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "get_agent_wrappers", fake_wrappers)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post("/api/chat", json={"message": "只有日期", "thread_id": "t1"})

    assert "event: interrupt" in resp.text
    assert "请补充预算" in resp.text
    assert store.upserts[0]["question"] == "请补充预算"
    assert store.upserts[0]["task_id"] == "task-1"


async def test_normal_flow_emits_interrupt_when_pending_appears(anon_client, monkeypatch, make_agent):
    """正常轮（走 LLM）轮末复查挂起表：工具层刚写入 → 发 interrupt 事件。"""
    store = FakeStore(pending=None)

    def on_stream():
        store.pending = _record()

    graph = FakeGraph(on_stream=on_stream)

    async def fake_instance(agent):
        return graph

    _patch_agent(monkeypatch, make_agent)
    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post("/api/chat", json={"message": "帮我规划南昌", "thread_id": "t1"})

    body = resp.text
    assert "event: token" in body
    assert "event: interrupt" in body
    assert "请补充目的地" in body


async def test_resume_clears_pending_on_target_error(anon_client, monkeypatch, make_agent):
    store = FakeStore(pending=_record())
    wrapper = FakeWrapper("http://h:9900/", [], error=A2ATargetError("network", "down"))

    async def fake_wrappers(agent):
        return [wrapper]

    async def fake_instance(agent):
        return FakeGraph()

    async def fake_notify(*args, **kwargs):
        return None

    _patch_agent(monkeypatch, make_agent)
    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "get_agent_wrappers", fake_wrappers)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)
    monkeypatch.setattr(chat_mod, "notify_alert", fake_notify)

    resp = await anon_client.post("/api/chat", json={"message": "补充", "thread_id": "t1"})

    assert "event: error" in resp.text
    assert store.deleted == ["t1"]


async def test_delete_conversation_clears_pending(anon_client, monkeypatch):
    store = FakeStore()

    async def fake_delete(session, identity, thread_id):
        return True

    class FakeCheckpointer:
        async def adelete_thread(self, thread_id):
            return None

    async def fake_checkpointer():
        return FakeCheckpointer()

    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "delete_conversation", fake_delete)
    monkeypatch.setattr(chat_mod, "get_checkpointer", fake_checkpointer)

    resp = await anon_client.delete("/api/chat/conversations/t1")

    assert resp.status_code == 200
    assert store.deleted == ["t1"]
