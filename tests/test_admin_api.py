"""管理中心 API 测试（不依赖数据库：repository 层全部 monkeypatch）。"""

from a2a_gateway import repository
from a2a_gateway.models import AgentStatus
from a2a_gateway.routes import admin as admin_mod


async def _noop(*args, **kwargs):
    return None


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------
async def test_list_agents_requires_auth(anon_client):
    resp = await anon_client.get("/api/admin/agents")
    assert resp.status_code == 401


async def test_login_success(anon_client, monkeypatch, make_admin):
    async def fake_get_admin(session, username):
        return make_admin(username=username)

    monkeypatch.setattr(repository, "get_admin_by_username", fake_get_admin)
    monkeypatch.setattr(admin_mod, "verify_password", lambda plain, hashed: True)

    resp = await anon_client.post(
        "/api/admin/login", json={"username": "admin", "password": "pw"}
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_login_failure(anon_client, monkeypatch, make_admin):
    async def fake_get_admin(session, username):
        return make_admin(username=username)

    monkeypatch.setattr(repository, "get_admin_by_username", fake_get_admin)
    monkeypatch.setattr(admin_mod, "verify_password", lambda plain, hashed: False)

    resp = await anon_client.post(
        "/api/admin/login", json={"username": "admin", "password": "bad"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 列表 / 创建 / 更新 / 删除
# ---------------------------------------------------------------------------
async def test_list_agents_serializes_status_by_value(auth_client, monkeypatch, make_agent):
    async def fake_list(session):
        return [
            make_agent(id=1, slug="a", status=AgentStatus.DRAFT),
            make_agent(id=2, slug="b", status=AgentStatus.PUBLISHED),
        ]

    monkeypatch.setattr(admin_mod, "list_agents", fake_list)

    resp = await auth_client.get("/api/admin/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert [a["slug"] for a in data] == ["a", "b"]
    assert data[0]["status"] == "draft"
    assert data[1]["status"] == "published"


async def test_create_agent_rejects_reserved_slug(auth_client):
    payload = {"slug": "/", "name": "x", "a2a_targets": []}
    resp = await auth_client.post("/api/admin/agents", json=payload)
    assert resp.status_code == 400


async def test_create_agent_rejects_duplicate_slug(auth_client, monkeypatch, make_agent):
    async def fake_get(session, slug):
        return make_agent(slug=slug)

    monkeypatch.setattr(admin_mod, "get_agent_by_slug", fake_get)

    payload = {"slug": "demo", "name": "x", "a2a_targets": []}
    resp = await auth_client.post("/api/admin/agents", json=payload)
    assert resp.status_code == 409


async def test_create_agent_ok(auth_client, monkeypatch, make_agent):
    async def fake_get(session, slug):
        return None

    async def fake_create(session, data):
        return make_agent(slug=data.slug, name=data.name)

    monkeypatch.setattr(admin_mod, "get_agent_by_slug", fake_get)
    monkeypatch.setattr(admin_mod, "create_agent", fake_create)

    payload = {"slug": "demo", "name": "Demo", "a2a_targets": []}
    resp = await auth_client.post("/api/admin/agents", json=payload)
    assert resp.status_code == 201
    assert resp.json()["slug"] == "demo"


async def test_update_agent_not_found(auth_client, monkeypatch):
    async def fake_get(session, agent_id):
        return None

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)

    resp = await auth_client.put("/api/admin/agents/999", json={"name": "n"})
    assert resp.status_code == 404


async def test_delete_default_agent_forbidden(auth_client, monkeypatch, make_agent):
    async def fake_get(session, agent_id):
        return make_agent(slug="/")

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)

    resp = await auth_client.delete("/api/admin/agents/1")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 发布 / 下线
# ---------------------------------------------------------------------------
async def test_publish_agent_ok(auth_client, monkeypatch, make_agent):
    async def fake_get(session, agent_id):
        return make_agent()

    async def fake_set(session, agent, status):
        return make_agent(status=status)

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)
    monkeypatch.setattr(admin_mod, "set_agent_status", fake_set)
    monkeypatch.setattr(admin_mod, "invalidate_agent", _noop)

    resp = await auth_client.post("/api/admin/agents/1/publish")
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"


async def test_unpublish_default_agent_forbidden(auth_client, monkeypatch, make_agent):
    async def fake_get(session, agent_id):
        return make_agent(slug="/")

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)

    resp = await auth_client.post("/api/admin/agents/1/unpublish")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# A2A 连通性测试
# ---------------------------------------------------------------------------
async def test_test_connection_rejects_bad_payload(auth_client):
    resp = await auth_client.post(
        "/api/admin/agents/test-connection", json={"message": "not-json"}
    )
    assert resp.status_code == 400


async def test_test_connection_returns_result(auth_client, monkeypatch):
    class FakeWrapper:
        def __init__(self, target):
            self.target = target

        async def test_connection(self):
            return True, f"已成功连接 {self.target.url}"

        async def close(self):
            return None

    monkeypatch.setattr(admin_mod, "A2AClientWrapper", FakeWrapper)

    resp = await auth_client.post(
        "/api/admin/agents/test-connection",
        json={"message": '{"url": "http://hermes.local:9900/", "token": "t"}'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "hermes.local" in body["message"]
