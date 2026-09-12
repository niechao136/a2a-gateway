"""公开对话 API 测试：路由解析、历史读取、SSE 事件契约。"""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

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
            # 摘要 hook 里的模型调用（pre_model_hook 节点）必须被过滤
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="摘要片段")},
                "metadata": {"langgraph_node": "pre_model_hook"},
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="你好")},
                "metadata": {"langgraph_node": "agent"},
            }
            yield {
                "event": "on_tool_start",
                "name": "a2a_call",
                "data": {},
                "metadata": {"langgraph_node": "agent"},
            }
            yield {
                "event": "on_tool_end",
                "name": "a2a_call",
                "data": {"output": "pong"},
                "metadata": {"langgraph_node": "agent"},
            }

    async def fake_instance(agent):
        return FakeGraph()

    monkeypatch.setattr(chat_mod, "get_agent_by_slug", fake_get)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post("/api/chat", json={"message": "hi", "thread_id": "t1"})

    assert resp.status_code == 200
    body = resp.text
    assert "event: token" in body
    assert "你好" in body
    # 摘要调用的 token 不应透传给前端
    assert "摘要片段" not in body
    assert "event: tool_start" in body
    assert "event: tool_end" in body
    assert "event: done" in body
    assert '"thread_id": "t1"' in body


async def test_retry_replays_from_last_human_checkpoint(anon_client, monkeypatch, make_agent):
    async def fake_get(session, slug):
        return make_agent(status=AgentStatus.PUBLISHED)

    captured: dict = {}

    class FakeSnapshot:
        def __init__(self, message, next_, checkpoint_id):
            self.values = {"messages": [message]}
            self.next = next_
            self.config = {"configurable": {"thread_id": "t1", "checkpoint_id": checkpoint_id}}

    class FakeGraph:
        async def aget_state_history(self, config):
            # 最新在前：最后一次 AI 回复已完成 → 上一个是待重放的检查点
            yield FakeSnapshot(AIMessage(content="上次回复"), (), "ckpt-new")
            yield FakeSnapshot(HumanMessage(content="hi"), ("agent",), "ckpt-old")

        async def astream_events(self, graph_input, config=None, **kwargs):
            captured["input"] = graph_input
            captured["config"] = config
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="重试回复")},
                "metadata": {"langgraph_node": "agent"},
            }

    async def fake_instance(agent):
        return FakeGraph()

    monkeypatch.setattr(chat_mod, "get_agent_by_slug", fake_get)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post("/api/chat/retry", json={"thread_id": "t1"})

    assert resp.status_code == 200
    body = resp.text
    # time travel：输入为 None，从「最后一次人类消息」的检查点重放
    assert captured["input"] is None
    assert captured["config"]["configurable"]["checkpoint_id"] == "ckpt-old"
    assert captured["config"]["configurable"]["thread_id"] == "t1"
    assert "重试回复" in body
    assert "event: done" in body


async def test_retry_without_retryable_message_returns_error(
    anon_client, monkeypatch, make_agent
):
    async def fake_get(session, slug):
        return make_agent(status=AgentStatus.PUBLISHED)

    class FakeGraph:
        async def aget_state_history(self, config):
            # 没有以人类消息结尾的检查点 → 无可重试
            yield SimpleNamespace(
                values={"messages": [AIMessage(content="hi")]},
                next=(),
                config={"configurable": {"thread_id": "t1", "checkpoint_id": "c1"}},
            )

    async def fake_instance(agent):
        return FakeGraph()

    monkeypatch.setattr(chat_mod, "get_agent_by_slug", fake_get)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post("/api/chat/retry", json={"thread_id": "t1"})
    assert resp.status_code == 200
    assert "没有可重试的对话" in resp.text


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
