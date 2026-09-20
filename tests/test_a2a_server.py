"""A2A Server 端点测试（不依赖数据库：repository 层全部 monkeypatch）。"""

import json
from types import SimpleNamespace
from typing import cast

from fastapi import Request

from a2a_gateway.a2a_client import Completed, TextChunk
from a2a_gateway.models import AgentStatus
from a2a_gateway.routes import a2a_server as a2a_server_mod
from a2a_gateway.routes import admin as admin_mod

TEST_KEY = "a2a-test-key"


# ---------------------------------------------------------------------------
# API Key 管理后台（按 Agent 维度）
# ---------------------------------------------------------------------------
async def test_api_key_requires_auth(anon_client):
    resp = await anon_client.get("/api/admin/agents/1/api-keys")
    assert resp.status_code == 401


async def test_list_agent_api_keys(auth_client, monkeypatch, make_agent, make_api_key):
    async def fake_get_agent(session, agent_id):
        return make_agent(id=agent_id)

    async def fake_list(session, agent_id):
        return [make_api_key(agent_id=agent_id)]

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get_agent)
    monkeypatch.setattr(admin_mod, "_list_agent_api_keys", fake_list)

    resp = await auth_client.get("/api/admin/agents/1/api-keys")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["key"] == "a2a-test-key"
    assert data[0]["agent_id"] == 1
    assert data[0]["is_default"] is True


async def test_create_agent_api_key_conflict(auth_client, monkeypatch, make_api_key):
    from types import SimpleNamespace

    async def fake_get(session, agent_id):
        return SimpleNamespace(id=agent_id)

    async def fake_list(session, agent_id):
        return [make_api_key(name="默认 Key", agent_id=agent_id)]

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)
    monkeypatch.setattr(admin_mod, "_list_agent_api_keys", fake_list)

    resp = await auth_client.post(
        "/api/admin/agents/1/api-keys", json={"name": "默认 Key"}
    )
    assert resp.status_code == 409


async def test_create_agent_api_key_generates_secret(
    auth_client, monkeypatch, make_agent, make_api_key
):
    async def fake_create(session, agent, data):
        return make_api_key(
            id=2, agent_id=agent.id, name=data.name, key="a2a-new", is_default=False
        )

    async def fake_list(session, agent_id):
        return []

    monkeypatch.setattr(admin_mod, "get_agent_by_id", _fake_agent_get)
    monkeypatch.setattr(admin_mod, "_list_agent_api_keys", fake_list)
    monkeypatch.setattr(admin_mod, "create_api_key", fake_create)

    resp = await auth_client.post("/api/admin/agents/1/api-keys", json={"name": "ci"})
    assert resp.status_code == 201
    assert resp.json()["key"].startswith("a2a-")
    assert resp.json()["agent_id"] == 1


async def test_delete_default_agent_api_key_forbidden(
    auth_client, monkeypatch, make_api_key
):
    async def fake_get(session, agent_id):
        from types import SimpleNamespace

        return SimpleNamespace(id=agent_id)

    async def fake_key(session, key_id):
        return make_api_key(agent_id=1, is_default=True)

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)
    monkeypatch.setattr(admin_mod, "get_api_key", fake_key)

    resp = await auth_client.delete("/api/admin/agents/1/api-keys/1")
    assert resp.status_code == 400


async def test_delete_api_key_of_other_agent_404(
    auth_client, monkeypatch, make_api_key
):
    async def fake_get(session, agent_id):
        from types import SimpleNamespace

        return SimpleNamespace(id=agent_id)

    async def fake_key(session, key_id):
        return make_api_key(agent_id=999, is_default=False)

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)
    monkeypatch.setattr(admin_mod, "get_api_key", fake_key)

    resp = await auth_client.delete("/api/admin/agents/1/api-keys/1")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------
async def test_agent_card_404_when_unpublished(anon_client, monkeypatch, make_agent):
    async def fake_get(session, slug):
        return make_agent(slug=slug, status=AgentStatus.DRAFT)

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", fake_get)

    resp = await anon_client.get("/a2a/demo/.well-known/agent-card.json")
    assert resp.status_code == 404


async def test_agent_card_public(anon_client, monkeypatch, make_agent):
    async def fake_get(session, slug):
        return make_agent(slug=slug, status=AgentStatus.PUBLISHED, name="Demo")

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", fake_get)

    resp = await anon_client.get("/a2a/demo/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "Demo"
    # 回连地址应为对外可达地址 + /a2a/{slug}
    assert card["supportedInterfaces"][0]["url"].endswith("/a2a/demo")


def _fake_card_request(headers: dict[str, str], scheme: str = "http") -> Request:
    """构造仅含 headers / url.scheme 的请求替身（_public_base_url 只读这两处）。"""
    return cast(
        Request, SimpleNamespace(headers=headers, url=SimpleNamespace(scheme=scheme))
    )


def test_public_base_url_keeps_host_port():
    """直连入口：Host 自带端口（:10099）时必须保留，不能丢。"""
    req = _fake_card_request({"host": "43.156.187.79:10099"})
    assert a2a_server_mod._public_base_url(req) == "http://43.156.187.79:10099"


def test_public_base_url_prefers_forwarded_host_and_proto():
    """域名反代：X-Forwarded-Host / Proto 优先，https 不能被内层 http 覆盖。"""
    req = _fake_card_request(
        {
            "host": "127.0.0.1:10099",
            "x-forwarded-host": "chat-niechao.duckdns.org",
            "x-forwarded-proto": "https",
        }
    )
    assert (
        a2a_server_mod._public_base_url(req) == "https://chat-niechao.duckdns.org"
    )


def test_public_base_url_appends_forwarded_port():
    """Host 不带端口时，用 X-Forwarded-Port 补上非默认端口。"""
    req = _fake_card_request({"host": "43.156.187.79", "x-forwarded-port": "10099"})
    assert a2a_server_mod._public_base_url(req) == "http://43.156.187.79:10099"


def test_public_base_url_skips_default_forwarded_port():
    """https/443 为默认端口，不应显式拼进地址。"""
    req = _fake_card_request(
        {
            "host": "chat-niechao.duckdns.org",
            "x-forwarded-proto": "https",
            "x-forwarded-port": "443",
        }
    )
    assert (
        a2a_server_mod._public_base_url(req) == "https://chat-niechao.duckdns.org"
    )


def test_public_base_url_takes_first_forwarded_value():
    """多级代理会把 X-Forwarded-* 追加为逗号列表，取首个值。"""
    req = _fake_card_request(
        {
            "host": "inner:80",
            "x-forwarded-host": "a.example.com, b.internal",
            "x-forwarded-proto": "https, http",
        }
    )
    assert a2a_server_mod._public_base_url(req) == "https://a.example.com"


def test_public_base_url_keeps_ipv6_port():
    """IPv6 字面量 [::1]:8000 的端口也要正确保留。"""
    req = _fake_card_request({"host": "[::1]:8000"})
    assert a2a_server_mod._public_base_url(req) == "http://[::1]:8000"


async def test_agent_card_url_keeps_port(anon_client, monkeypatch, make_agent):
    """回归：直连入口（Host 带端口）下卡片回连地址必须带 :10099。"""

    async def fake_get(session, slug):
        return make_agent(slug=slug, status=AgentStatus.PUBLISHED, name="Demo")

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", fake_get)

    resp = await anon_client.get(
        "/a2a/demo/.well-known/agent-card.json",
        headers={"Host": "43.156.187.79:10099"},
    )
    assert resp.status_code == 200
    assert (
        resp.json()["supportedInterfaces"][0]["url"]
        == "http://43.156.187.79:10099/a2a/demo"
    )


# ---------------------------------------------------------------------------
# JSON-RPC：鉴权与消息处理
# ---------------------------------------------------------------------------
def _patch_agent(monkeypatch, make_agent, chunks: list[str]):
    async def fake_get_agent(session, slug):
        return make_agent(slug=slug, status=AgentStatus.PUBLISHED)

    async def fake_stream(agent, text, thread_id):
        for c in chunks:
            yield c

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", fake_get_agent)
    monkeypatch.setattr(a2a_server_mod, "_stream_agent_text", fake_stream)


async def _fake_agent_get(session, agent_id):
    """替身：按 id 返回一个最小 Agent 存根。"""
    from types import SimpleNamespace

    return SimpleNamespace(id=agent_id)


def _patch_api_key(monkeypatch, make_api_key, **overrides):
    """默认返回属于 agent_id=1（即 make_agent 的 id）的 Key。"""
    async def fake_get(session, key):
        return make_api_key(**overrides)

    monkeypatch.setattr(a2a_server_mod, "get_api_key_by_key", fake_get)


async def test_rpc_requires_api_key(anon_client, monkeypatch, make_agent):
    _patch_agent(monkeypatch, make_agent, ["hi"])
    body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage",
            "params": {"message": {"messageId": "m1", "parts": [{"text": "hello"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body)
    assert resp.status_code == 401


async def test_rpc_rejects_disabled_key(anon_client, monkeypatch, make_agent, make_api_key):
    _patch_api_key(monkeypatch, make_api_key, enabled=False)
    _patch_agent(monkeypatch, make_agent, ["hi"])
    body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage",
            "params": {"message": {"messageId": "m1", "parts": [{"text": "hello"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})
    assert resp.status_code == 401


async def test_rpc_rejects_key_of_other_agent(anon_client, monkeypatch, make_agent, make_api_key):
    # Key 存在且启用，但属于别的 Agent（agent_id 不匹配）→ 401
    _patch_api_key(monkeypatch, make_api_key, agent_id=999)
    _patch_agent(monkeypatch, make_agent, ["hi"])
    body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage",
            "params": {"message": {"messageId": "m1", "parts": [{"text": "hello"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})
    assert resp.status_code == 401


async def test_send_message_returns_full_text(anon_client, monkeypatch, make_agent, make_api_key):
    _patch_api_key(monkeypatch, make_api_key)
    _patch_agent(monkeypatch, make_agent, ["你", "好"])

    body = {"jsonrpc": "2.0", "id": 7, "method": "SendMessage",
            "params": {"message": {"messageId": "m1", "contextId": "ctx-1",
                                   "parts": [{"text": "hello"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 7
    message = data["result"]["message"]
    texts = "".join(p["text"] for p in message["parts"] if "text" in p)
    assert texts == "你好"
    assert message["contextId"] == "ctx-1"


async def test_send_streaming_message_sse(anon_client, monkeypatch, make_agent, make_api_key):
    """流式响应必须为任务式事件流：Task → working(status_update)×N → completed。

    回归背景：曾把每个增量 chunk 包装成裸 message 事件发送，
    而 a2a-sdk 客户端收到 message 即视为最终回复并终止流，
    导致每次调用只能拿到第一个分块（/news 调 devops 拿到碎片）。
    """
    _patch_api_key(monkeypatch, make_api_key)
    _patch_agent(monkeypatch, make_agent, ["a", "b"])

    body = {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage",
            "params": {"message": {"messageId": "m1", "parts": [{"text": "hello"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})
    assert resp.status_code == 200

    events = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    # Task + 2 个增量 status_update + 1 个 completed
    assert len(events) == 4

    # 首事件是 Task（不含 message，客户端不会提前终止）
    assert "task" in events[0]["result"]
    assert "message" not in events[0]["result"]

    # 增量文本经 statusUpdate.message 携带
    first_chunk = events[1]["result"]["statusUpdate"]["status"]["message"]["parts"]
    assert "".join(p["text"] for p in first_chunk if "text" in p) == "a"
    second_chunk = events[2]["result"]["statusUpdate"]["status"]["message"]["parts"]
    assert "".join(p["text"] for p in second_chunk if "text" in p) == "b"

    # 结束事件为 completed，且不带 message（避免客户端重复输出全文）
    final_status = events[3]["result"]["statusUpdate"]["status"]
    assert final_status["state"] == "TASK_STATE_COMPLETED"
    assert "message" not in final_status


async def test_rpc_unknown_method(anon_client, monkeypatch, make_agent, make_api_key):
    _patch_api_key(monkeypatch, make_api_key)
    _patch_agent(monkeypatch, make_agent, ["hi"])

    body = {"jsonrpc": "2.0", "id": 1, "method": "Nope", "params": {}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# input-required：中断映射与任务恢复
# ---------------------------------------------------------------------------
class FakePendingStore:
    def __init__(self, pending=None):
        self.pending = pending
        self.upserts = []
        self.deleted = []

    async def get(self, thread_id):
        return self.pending

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        self.pending = _pending(**kwargs)

    async def delete(self, thread_id):
        self.deleted.append(thread_id)
        self.pending = None


def _pending(**overrides):
    base = {
        "thread_id": "task-1",
        "agent_id": 1,
        "target_url": "http://h:9900/",
        "target_name": "travel",
        "task_id": "task-1",
        "context_id": "ctx-1",
        "question": "请补充目的地",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeResumeWrapper:
    def __init__(self, url, events):
        self.target = SimpleNamespace(url=url)
        self._events = events
        self.calls = []

    async def stream_message_events(self, text, *, task_id=None, context_id=None, **kwargs):
        self.calls.append({"text": text, "task_id": task_id, "context_id": context_id})
        for event in self._events:
            yield event


def _parse_sse(text: str) -> list:
    return [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def _published(make_agent):
    async def fake_get_agent(session, slug):
        return make_agent(slug=slug, status=AgentStatus.PUBLISHED)

    return fake_get_agent


async def test_streaming_new_task_emits_input_required(
    anon_client, monkeypatch, make_agent, make_api_key
):
    """下游在本次调用中进入 input-required → 终帧应为 INPUT_REQUIRED 且带追问。"""
    _patch_api_key(monkeypatch, make_api_key)
    store = FakePendingStore()

    async def fake_stream(agent, text, thread_id):
        store.pending = _pending(thread_id=thread_id)
        yield "请补充目的地"

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", _published(make_agent))
    monkeypatch.setattr(a2a_server_mod, "_stream_agent_text", fake_stream)
    monkeypatch.setattr(a2a_server_mod, "default_pending_store", store)

    body = {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage",
            "params": {"message": {"messageId": "m1", "parts": [{"text": "帮我规划"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})

    events = _parse_sse(resp.text)
    final_status = events[-1]["result"]["statusUpdate"]["status"]
    assert final_status["state"] == "TASK_STATE_INPUT_REQUIRED"
    texts = "".join(p["text"] for p in final_status["message"]["parts"] if "text" in p)
    assert texts == "请补充目的地"


async def test_resume_streaming_with_task_id(
    anon_client, monkeypatch, make_agent, make_api_key
):
    """带 task_id 的请求命中挂起 → 透明转发恢复，不经 LLM。"""
    _patch_api_key(monkeypatch, make_api_key)
    store = FakePendingStore(pending=_pending())
    wrapper = FakeResumeWrapper(
        "http://h:9900/", [TextChunk("行程"), Completed(task_id="task-1")]
    )

    async def fake_wrappers(agent):
        return [wrapper]

    async def fake_stream(agent, text, thread_id):
        raise AssertionError("恢复轮不应经过 LLM 图")
        yield  # pragma: no cover

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", _published(make_agent))
    monkeypatch.setattr(a2a_server_mod, "default_pending_store", store)
    monkeypatch.setattr(a2a_server_mod, "get_agent_wrappers", fake_wrappers)
    monkeypatch.setattr(a2a_server_mod, "_stream_agent_text", fake_stream)

    body = {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage",
            "params": {"message": {"messageId": "m2", "taskId": "task-1",
                                   "parts": [{"text": "杭州 10/1-10/3 预算3000"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})

    events = _parse_sse(resp.text)
    assert events[-1]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert wrapper.calls == [
        {"text": "杭州 10/1-10/3 预算3000", "task_id": "task-1", "context_id": "ctx-1"}
    ]
    assert store.deleted == ["task-1"]


async def test_resume_unknown_task_returns_error(
    anon_client, monkeypatch, make_agent, make_api_key
):
    _patch_api_key(monkeypatch, make_api_key)
    store = FakePendingStore(pending=None)

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", _published(make_agent))
    monkeypatch.setattr(a2a_server_mod, "default_pending_store", store)

    body = {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage",
            "params": {"message": {"messageId": "m2", "taskId": "gone",
                                   "parts": [{"text": "补充"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})

    assert "error" in resp.json()


async def test_new_task_uses_same_id_for_task_and_context(
    anon_client, monkeypatch, make_agent, make_api_key
):
    """标识统一：新任务的对外 task_id 与 context_id 相同（挂起表主键可被恢复命中）。"""
    _patch_api_key(monkeypatch, make_api_key)

    async def fake_stream(agent, text, thread_id):
        yield "ok"

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", _published(make_agent))
    monkeypatch.setattr(a2a_server_mod, "_stream_agent_text", fake_stream)

    body = {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage",
            "params": {"message": {"messageId": "m1", "parts": [{"text": "hi"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})

    events = _parse_sse(resp.text)
    task = events[0]["result"]["task"]
    assert task["id"] == task["contextId"]


async def test_send_message_nonstream_returns_input_required_task(
    anon_client, monkeypatch, make_agent, make_api_key
):
    _patch_api_key(monkeypatch, make_api_key)
    store = FakePendingStore()

    async def fake_stream(agent, text, thread_id):
        store.pending = _pending(thread_id=thread_id)
        yield "请补充预算"

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", _published(make_agent))
    monkeypatch.setattr(a2a_server_mod, "_stream_agent_text", fake_stream)
    monkeypatch.setattr(a2a_server_mod, "default_pending_store", store)

    body = {"jsonrpc": "2.0", "id": 7, "method": "SendMessage",
            "params": {"message": {"messageId": "m1", "parts": [{"text": "帮我规划"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})

    task = resp.json()["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert task["id"] == task["contextId"]
