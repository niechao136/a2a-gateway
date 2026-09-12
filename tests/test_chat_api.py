"""公开对话 API 测试：路由解析、历史读取、SSE 事件契约。"""

from types import SimpleNamespace

from a2a_gateway.models import AgentStatus
from a2a_gateway.routes import chat as chat_mod


async def _fake_notify(*args, **kwargs):
    return None


async def test_chat_404_when_agent_missing(anon_client, monkeypatch):
    async def fake_get(session, slug):
        return None

    monkeypatch.setattr(chat_mod, "get_agent_by_slug", fake_get)

    resp = await anon_client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 404


async def test_chat_404_when_agent_is_draft(anon_client, monkeypatch, make_agent):
    async def fake_get(session, slug):
        return make_agent(status=AgentStatus.DRAFT)

    monkeypatch.setattr(chat_mod, "get_agent_by_slug", fake_get)

    resp = await anon_client.post("/api/chat/demo", json={"message": "hi"})
    assert resp.status_code == 404


async def test_chat_streams_expected_sse_events(anon_client, monkeypatch, make_agent):
    async def fake_get(session, slug):
        return make_agent(status=AgentStatus.PUBLISHED)

    class FakeGraph:
        async def astream_events(self, *args, **kwargs):
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="你好")},
            }
            yield {"event": "on_tool_start", "name": "a2a_call", "data": {}}
            yield {"event": "on_tool_end", "name": "a2a_call", "data": {"output": "pong"}}

    async def fake_instance(agent):
        return FakeGraph()

    monkeypatch.setattr(chat_mod, "get_agent_by_slug", fake_get)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post("/api/chat", json={"message": "hi", "thread_id": "t1"})

    assert resp.status_code == 200
    body = resp.text
    assert "event: token" in body
    assert "你好" in body
    assert "event: tool_start" in body
    assert "event: tool_end" in body
    assert "event: done" in body
    assert '"thread_id": "t1"' in body


async def test_chat_stream_error_event_is_friendly(anon_client, monkeypatch, make_agent):
    async def fake_get(session, slug):
        return make_agent(status=AgentStatus.PUBLISHED)

    class BrokenGraph:
        async def astream_events(self, *args, **kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    async def fake_instance(agent):
        return BrokenGraph()

    monkeypatch.setattr(chat_mod, "get_agent_by_slug", fake_get)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)
    monkeypatch.setattr(chat_mod, "notify_alert", _fake_notify)

    resp = await anon_client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 200
    assert "event: error" in resp.text
    assert "对话处理失败" in resp.text
    # 不向前端暴露底层异常细节
    assert "boom" not in resp.text


async def test_history_returns_empty_without_checkpoint(anon_client, monkeypatch):
    class FakeCheckpointer:
        async def aget_tuple(self, config):
            return None

    async def fake_checkpointer():
        return FakeCheckpointer()

    monkeypatch.setattr(chat_mod, "get_checkpointer", fake_checkpointer)

    resp = await anon_client.get("/api/chat/history", params={"thread_id": "nope"})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_history_maps_messages(anon_client, monkeypatch):
    class FakeMessage:
        def __init__(self, type_, content, name=None):
            self.type = type_
            self.content = content
            if name is not None:
                self.name = name

    class FakeCheckpointer:
        async def aget_tuple(self, config):
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "messages": [
                            FakeMessage("human", "hi"),
                            FakeMessage("ai", ""),
                            FakeMessage("tool", '{"ok": true}', name="a2a_call"),
                            FakeMessage("ai", "hello"),
                        ]
                    }
                }
            )

    async def fake_checkpointer():
        return FakeCheckpointer()

    monkeypatch.setattr(chat_mod, "get_checkpointer", fake_checkpointer)

    resp = await anon_client.get("/api/chat/history", params={"thread_id": "t1"})
    assert resp.status_code == 200
    assert resp.json() == [
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "", "toolName": "a2a_call", "toolOutput": '{"ok": true}'},
        {"role": "assistant", "content": "hello"},
    ]
