"""A2A Server 端点测试（不依赖数据库：repository 层全部 monkeypatch）。"""

import json

from a2a_gateway import repository
from a2a_gateway.models import AgentStatus
from a2a_gateway.routes import a2a_server as a2a_server_mod
from a2a_gateway.routes import admin as admin_mod

TEST_KEY = "a2a-test-key"


# ---------------------------------------------------------------------------
# API Key 管理后台
# ---------------------------------------------------------------------------
async def test_api_key_requires_auth(anon_client):
    resp = await anon_client.get("/api/admin/api-keys")
    assert resp.status_code == 401


async def test_list_api_keys(auth_client, monkeypatch, make_api_key):
    async def fake_list(session):
        return [make_api_key()]

    monkeypatch.setattr(admin_mod, "list_api_keys", fake_list)

    resp = await auth_client.get("/api/admin/api-keys")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["key"] == "a2a-test-key"
    assert data[0]["is_default"] is True


async def test_create_api_key_conflict(auth_client, monkeypatch, make_api_key):
    async def fake_get(session, name):
        return make_api_key(name=name)

    monkeypatch.setattr(repository, "get_api_key_by_name", fake_get)

    resp = await auth_client.post("/api/admin/api-keys", json={"name": "默认 Key"})
    assert resp.status_code == 409


async def test_create_api_key_generates_secret(auth_client, monkeypatch, make_api_key):
    async def fake_get(session, name):
        return None

    async def fake_create(session, data):
        return make_api_key(id=2, name=data.name, key="a2a-new", is_default=False)

    monkeypatch.setattr(repository, "get_api_key_by_name", fake_get)
    monkeypatch.setattr(admin_mod, "create_api_key", fake_create)

    resp = await auth_client.post("/api/admin/api-keys", json={"name": "ci"})
    assert resp.status_code == 201
    assert resp.json()["key"].startswith("a2a-")


async def test_delete_default_api_key_forbidden(auth_client, monkeypatch, make_api_key):
    async def fake_get(session, key_id):
        return make_api_key(is_default=True)

    monkeypatch.setattr(admin_mod, "get_api_key", fake_get)

    resp = await auth_client.delete("/api/admin/api-keys/1")
    assert resp.status_code == 400


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


def _patch_api_key(monkeypatch, make_api_key, **overrides):
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
    assert len(events) == 2
    parts = events[0]["result"]["message"]["parts"]
    assert "".join(p["text"] for p in parts if "text" in p) == "a"


async def test_rpc_unknown_method(anon_client, monkeypatch, make_agent, make_api_key):
    _patch_api_key(monkeypatch, make_api_key)
    _patch_agent(monkeypatch, make_agent, ["hi"])

    body = {"jsonrpc": "2.0", "id": 1, "method": "Nope", "params": {}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32601
