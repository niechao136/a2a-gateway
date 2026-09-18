"""链路 A（对话界面）input-required 测试：挂起上下文注入后的正常轮行为、interrupt 事件与清理。"""

from types import SimpleNamespace

import pytest

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


class FakeGraph:
    """正常轮替身：记录被调用次数并按脚本产出 token 事件。"""

    def __init__(self, on_stream=None):
        self.on_stream = on_stream
        self.updated = []
        self.stream_calls = 0

    async def astream_events(self, *args, **kwargs):
        self.stream_calls += 1
        if self.on_stream is not None:
            self.on_stream()
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="已转述追问")},
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


async def test_chat_does_not_intercept_pending(anon_client, monkeypatch, make_agent):
    """挂起存在时对话仍走正常 graph 流程（不再由路由层拦截转发）。"""
    store = FakeStore(pending=_record())
    graph = FakeGraph()

    async def fake_instance(agent):
        return graph

    _patch_agent(monkeypatch, make_agent)
    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post(
        "/api/chat", json={"message": "杭州 10/1-10/3 预算3000", "thread_id": "t1"}
    )

    assert resp.status_code == 200
    assert graph.stream_calls == 1
    assert store.deleted == []       # 路由层不再自行清理挂起
    assert graph.updated == []       # 不再手工补历史
    assert "event: token" in resp.text
    assert "event: done" in resp.text


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
