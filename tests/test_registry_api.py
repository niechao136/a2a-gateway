"""注册表 API 测试（A2A 目标 / MCP 服务）。

与既有测试一致：不依赖数据库，repository 层全部 monkeypatch。
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from a2a_gateway.routes import registry as registry_mod
from a2a_gateway.schemas import validate_mcp_transport

_NOW = datetime.now(timezone.utc)


def _endpoint(**kw):
    base = {
        "id": 1,
        "name": "Hermes",
        "url": "http://hermes:9900/",
        "token": "t",
        "description": "",
        "auth_type": "bearer",
        "auth_name": "",
        "enabled": True,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _server(**kw):
    base = {
        "id": 1,
        "name": "Local MCP",
        "description": "",
        "transport": "stdio",
        "url": "",
        "command": "python",
        "args": ["-m", "server"],
        "env": {},
        "token": "tok",
        "auth_type": "bearer",
        "auth_name": "",
        "enabled": True,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _agent(**kw):
    base = {"id": 7, "name": "测试 Agent", "slug": "demo"}
    base.update(kw)
    return SimpleNamespace(**base)


async def _noop(*args, **kwargs):
    return None


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------
async def test_registry_requires_auth(anon_client):
    assert (await anon_client.get("/api/admin/a2a-endpoints")).status_code == 401
    assert (await anon_client.get("/api/admin/mcp-servers")).status_code == 401


# ---------------------------------------------------------------------------
# A2A 目标
# ---------------------------------------------------------------------------
async def test_create_endpoint_rejects_empty_name(auth_client):
    resp = await auth_client.post(
        "/api/admin/a2a-endpoints", json={"name": "  ", "url": "http://x/", "auth_type": "none"}
    )
    assert resp.status_code == 400


async def test_create_endpoint_rejects_empty_url(auth_client):
    resp = await auth_client.post(
        "/api/admin/a2a-endpoints", json={"name": "x", "url": "  ", "auth_type": "none"}
    )
    assert resp.status_code == 400


async def test_create_endpoint_rejects_duplicate_name(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return _endpoint()

    monkeypatch.setattr(registry_mod.repo, "get_a2a_endpoint_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/a2a-endpoints",
        json={"name": "Hermes", "url": "http://x/", "auth_type": "none"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 鉴权方式
# ---------------------------------------------------------------------------
async def test_create_endpoint_rejects_bearer_without_token(auth_client):
    resp = await auth_client.post(
        "/api/admin/a2a-endpoints", json={"name": "x", "url": "http://x/", "auth_type": "bearer"}
    )
    assert resp.status_code == 422


async def test_create_endpoint_rejects_header_without_name(auth_client):
    resp = await auth_client.post(
        "/api/admin/a2a-endpoints",
        json={"name": "x", "url": "http://x/", "auth_type": "header", "token": "t"},
    )
    assert resp.status_code == 422


async def test_create_endpoint_accepts_query_auth(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return None

    async def fake_create(session, data):
        return _endpoint(name=data.name, auth_type=data.auth_type, auth_name=data.auth_name)

    monkeypatch.setattr(registry_mod.repo, "get_a2a_endpoint_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "create_a2a_endpoint", fake_create)

    resp = await auth_client.post(
        "/api/admin/a2a-endpoints",
        json={
            "name": "x",
            "url": "http://x/",
            "auth_type": "query",
            "auth_name": "key",
            "token": "v",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["auth_type"] == "query"
    assert resp.json()["auth_name"] == "key"


async def test_create_endpoint_ok(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return None

    async def fake_create(session, data):
        return _endpoint(name=data.name, url=data.url, token=data.token)

    monkeypatch.setattr(registry_mod.repo, "get_a2a_endpoint_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "create_a2a_endpoint", fake_create)

    resp = await auth_client.post(
        "/api/admin/a2a-endpoints",
        json={"name": "Hermes", "url": "http://hermes:9900/", "token": "tok"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Hermes"
    assert resp.json()["token"] == "tok"


async def test_update_endpoint_not_found(auth_client, monkeypatch):
    async def fake_get(session, endpoint_id):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_a2a_endpoint", fake_get)
    resp = await auth_client.put("/api/admin/a2a-endpoints/9", json={"name": "x"})
    assert resp.status_code == 404


async def test_update_endpoint_refreshes_bound_agents(auth_client, monkeypatch):
    """端点地址变更时，需刷新引用它的 Agent 的绑定快照并失效图缓存。"""
    refreshed: dict = {}
    invalidated: list[int] = []

    async def fake_get(session, endpoint_id):
        return _endpoint()

    async def fake_by_name(session, name):
        return None

    async def fake_using(session, endpoint_id):
        return [_agent(id=7)]

    async def fake_update(session, endpoint, data):
        endpoint.url = data.url or endpoint.url
        return endpoint

    async def fake_refresh(session, ids):
        refreshed["ids"] = list(ids)
        return 1

    async def fake_invalidate(agent_id):
        invalidated.append(agent_id)

    monkeypatch.setattr(registry_mod.repo, "get_a2a_endpoint", fake_get)
    monkeypatch.setattr(registry_mod.repo, "get_a2a_endpoint_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "agents_using_a2a_endpoint", fake_using)
    monkeypatch.setattr(registry_mod.repo, "update_a2a_endpoint", fake_update)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_a2a_endpoints", fake_refresh)
    monkeypatch.setattr(registry_mod, "invalidate_agent", fake_invalidate)

    resp = await auth_client.put("/api/admin/a2a-endpoints/1", json={"url": "http://new:9900/"})
    assert resp.status_code == 200
    assert resp.json()["url"] == "http://new:9900/"
    assert refreshed == {"ids": [1]}
    assert invalidated == [7]


async def test_delete_endpoint_blocked_when_referenced(auth_client, monkeypatch):
    async def fake_get(session, endpoint_id):
        return _endpoint()

    async def fake_using(session, endpoint_id):
        return [_agent()]

    monkeypatch.setattr(registry_mod.repo, "get_a2a_endpoint", fake_get)
    monkeypatch.setattr(registry_mod.repo, "agents_using_a2a_endpoint", fake_using)

    resp = await auth_client.delete("/api/admin/a2a-endpoints/1")
    assert resp.status_code == 409
    assert "Agent" in resp.json()["detail"]


async def test_delete_endpoint_force_detaches(auth_client, monkeypatch):
    calls: dict = {}

    async def fake_get(session, endpoint_id):
        return _endpoint()

    async def fake_using(session, endpoint_id):
        return [_agent()]

    async def fake_detach(session, endpoint_id):
        calls["detach"] = endpoint_id
        return 1

    async def fake_delete(session, endpoint):
        calls["deleted"] = True

    monkeypatch.setattr(registry_mod.repo, "get_a2a_endpoint", fake_get)
    monkeypatch.setattr(registry_mod.repo, "agents_using_a2a_endpoint", fake_using)
    monkeypatch.setattr(registry_mod.repo, "detach_a2a_endpoint_from_agents", fake_detach)
    monkeypatch.setattr(registry_mod.repo, "delete_a2a_endpoint", fake_delete)
    monkeypatch.setattr(registry_mod, "invalidate_agent", _noop)

    resp = await auth_client.delete("/api/admin/a2a-endpoints/1?force=true")
    assert resp.status_code == 204
    assert calls == {"detach": 1, "deleted": True}


async def test_test_a2a_endpoint(auth_client, monkeypatch):
    async def fake_get(session, endpoint_id):
        return _endpoint()

    class FakeWrapper:
        def __init__(self, target):
            self.target = target

        async def test_connection(self):
            return True, f"已连接 {self.target.url}"

        async def close(self):
            return None

    monkeypatch.setattr(registry_mod.repo, "get_a2a_endpoint", fake_get)
    monkeypatch.setattr(registry_mod, "A2AClientWrapper", FakeWrapper)

    resp = await auth_client.post("/api/admin/a2a-endpoints/1/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "hermes:9900" in resp.json()["message"]


# ---------------------------------------------------------------------------
# MCP 服务
# ---------------------------------------------------------------------------
async def test_create_mcp_server_ok(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return None

    async def fake_create(session, data):
        return _server(name=data.name, transport=data.transport)

    monkeypatch.setattr(registry_mod.repo, "get_mcp_server_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "create_mcp_server", fake_create)

    resp = await auth_client.post(
        "/api/admin/mcp-servers",
        json={
            "name": "Local MCP",
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "x"],
            "auth_type": "none",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["transport"] == "stdio"


async def test_create_mcp_server_rejects_duplicate(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return _server()

    monkeypatch.setattr(registry_mod.repo, "get_mcp_server_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/mcp-servers",
        json={
            "name": "Local MCP",
            "transport": "stdio",
            "command": "python",
            "auth_type": "none",
        },
    )
    assert resp.status_code == 409


async def test_create_mcp_server_rejects_mismatched_transport(auth_client):
    """stdio 缺 command → 422（Pydantic 校验）。"""
    resp = await auth_client.post(
        "/api/admin/mcp-servers", json={"name": "x", "transport": "stdio"}
    )
    assert resp.status_code == 422


async def test_update_mcp_server_requires_command_for_stdio(auth_client, monkeypatch):
    async def fake_get(session, server_id):
        return _server(transport="streamable_http", url="http://m/mcp", command="")

    monkeypatch.setattr(registry_mod.repo, "get_mcp_server", fake_get)
    resp = await auth_client.put("/api/admin/mcp-servers/1", json={"transport": "stdio"})
    assert resp.status_code == 400


async def test_update_endpoint_validates_auth(auth_client, monkeypatch):
    """改为 header 鉴权却没给头名 → 400（按合并后的值校验）。"""
    async def fake_get(session, endpoint_id):
        return _endpoint()

    monkeypatch.setattr(registry_mod.repo, "get_a2a_endpoint", fake_get)
    resp = await auth_client.put(
        "/api/admin/a2a-endpoints/1", json={"auth_type": "header", "token": "t"}
    )
    assert resp.status_code == 400


async def test_update_mcp_server_validates_auth(auth_client, monkeypatch):
    async def fake_get(session, server_id):
        return _server()

    monkeypatch.setattr(registry_mod.repo, "get_mcp_server", fake_get)
    resp = await auth_client.put(
        "/api/admin/mcp-servers/1", json={"auth_type": "query", "token": "t"}
    )
    assert resp.status_code == 400


async def test_update_mcp_server_requires_url_for_sse(auth_client, monkeypatch):
    async def fake_get(session, server_id):
        return _server(transport="stdio", url="", command="python")

    monkeypatch.setattr(registry_mod.repo, "get_mcp_server", fake_get)
    resp = await auth_client.put("/api/admin/mcp-servers/1", json={"transport": "sse"})
    assert resp.status_code == 400


async def test_delete_mcp_server_blocked_when_referenced(auth_client, monkeypatch):
    async def fake_get(session, server_id):
        return _server()

    async def fake_using(session, server_id):
        return [_agent()]

    monkeypatch.setattr(registry_mod.repo, "get_mcp_server", fake_get)
    monkeypatch.setattr(registry_mod.repo, "agents_using_mcp_server", fake_using)
    resp = await auth_client.delete("/api/admin/mcp-servers/1")
    assert resp.status_code == 409


async def test_mcp_server_test_endpoint(auth_client, monkeypatch):
    async def fake_get(session, server_id):
        return _server()

    async def fake_test(conn):
        return True, "连接成功：Local MCP（可用工具 2 个）"

    monkeypatch.setattr(registry_mod.repo, "get_mcp_server", fake_get)
    monkeypatch.setattr(registry_mod, "test_connection", fake_test)

    resp = await auth_client.post("/api/admin/mcp-servers/1/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_mcp_server_tools_endpoint(auth_client, monkeypatch):
    async def fake_get(session, server_id):
        return _server()

    async def fake_list(conn):
        return True, [{"name": "echo", "description": "回声工具"}], "共 1 个工具"

    monkeypatch.setattr(registry_mod.repo, "get_mcp_server", fake_get)
    monkeypatch.setattr(registry_mod, "list_tools", fake_list)

    resp = await auth_client.get("/api/admin/mcp-servers/1/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["tools"][0]["name"] == "echo"


async def test_mcp_server_tools_handles_failure(auth_client, monkeypatch):
    async def fake_get(session, server_id):
        return _server()

    async def fake_list(conn):
        return False, [], "获取工具列表失败：TimeoutError"

    monkeypatch.setattr(registry_mod.repo, "get_mcp_server", fake_get)
    monkeypatch.setattr(registry_mod, "list_tools", fake_list)

    resp = await auth_client.get("/api/admin/mcp-servers/1/tools")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["tools"] == []


# ---------------------------------------------------------------------------
# 纯函数：传输方式校验
# ---------------------------------------------------------------------------
def test_validate_mcp_transport_ok():
    validate_mcp_transport("streamable_http", "http://m/mcp", "")
    validate_mcp_transport("sse", "http://m/sse", "")
    validate_mcp_transport("stdio", "", "python")


def test_validate_mcp_transport_requires_url_for_remote():
    with pytest.raises(ValueError):
        validate_mcp_transport("sse", "", "")
    with pytest.raises(ValueError):
        validate_mcp_transport("streamable_http", "  ", "")


def test_validate_mcp_transport_requires_command_for_stdio():
    with pytest.raises(ValueError):
        validate_mcp_transport("stdio", "http://m/mcp", " ")


def test_validate_mcp_transport_rejects_unknown():
    with pytest.raises(ValueError):
        validate_mcp_transport("grpc", "http://m/", "")
