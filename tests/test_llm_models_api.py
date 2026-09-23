"""模型注册表 API 测试（monkeypatch repository，无 DB、无真实网络）。"""
from datetime import UTC, datetime
from types import SimpleNamespace

from a2a_gateway.routes import models as models_mod


def _model(**overrides):
    base = {
        "id": 1,
        "name": "DeepSeek V3",
        "provider": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-abcdef123456",
        "model": "deepseek-chat",
        "description": "",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _async(value):
    async def _call(*args, **kwargs):
        return value

    return _call


async def test_list_masks_api_key(auth_client, monkeypatch):
    monkeypatch.setattr(models_mod.repo, "list_llm_models", _async([_model()]))
    resp = await auth_client.get("/api/admin/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["api_key_masked"].startswith("sk-")
    assert "api_key" not in body[0]
    assert "sk-abcdef123456" not in resp.text


async def test_create_requires_base_url_for_openai(auth_client):
    resp = await auth_client.post(
        "/api/admin/models",
        json={"name": "X", "provider": "openai", "base_url": "", "model": "m"},
    )
    assert resp.status_code == 400
    assert "base_url" in resp.json()["detail"]


async def test_create_duplicate_name_409(auth_client, monkeypatch):
    monkeypatch.setattr(
        models_mod.repo, "get_llm_model_by_name", _async(_model(name="重复"))
    )
    resp = await auth_client.post(
        "/api/admin/models",
        json={"name": "重复", "provider": "openai", "base_url": "https://x/v1", "model": "m"},
    )
    assert resp.status_code == 409


async def test_create_ok(auth_client, monkeypatch):
    monkeypatch.setattr(models_mod.repo, "get_llm_model_by_name", _async(None))
    monkeypatch.setattr(
        models_mod.repo, "create_llm_model", _async(_model(name="New", model="m1"))
    )
    resp = await auth_client.post(
        "/api/admin/models",
        json={"name": "New", "provider": "openai", "base_url": "https://x/v1", "model": "m1"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "New"


async def test_update_refreshes_snapshots_and_invalidates(auth_client, monkeypatch):
    refreshed: list[int] = []
    invalidated: list[int] = []

    async def fake_get(session, mid):
        return _model(id=mid)

    async def fake_update(session, m, data):
        return m

    async def fake_refresh(session, model_id):
        refreshed.append(model_id)
        return [SimpleNamespace(id=7, name="A")]

    async def fake_invalidate(agent_id):
        invalidated.append(agent_id)

    monkeypatch.setattr(models_mod.repo, "get_llm_model", fake_get)
    monkeypatch.setattr(models_mod.repo, "update_llm_model", fake_update)
    monkeypatch.setattr(models_mod.repo, "refresh_agents_for_model", fake_refresh)
    monkeypatch.setattr(models_mod, "invalidate_agent", fake_invalidate)

    resp = await auth_client.put(
        "/api/admin/models/1",
        json={"model": "deepseek-chat-v2"},
    )
    assert resp.status_code == 200
    assert refreshed == [1]
    assert invalidated == [7]


async def test_delete_referenced_409(auth_client, monkeypatch):
    monkeypatch.setattr(models_mod.repo, "get_llm_model", _async(_model()))
    monkeypatch.setattr(
        models_mod.repo,
        "agents_using_model",
        _async([SimpleNamespace(id=1, name="A")]),
    )
    resp = await auth_client.delete("/api/admin/models/1")
    assert resp.status_code == 409
    assert "force" in resp.json()["detail"]


async def test_delete_force_detaches(auth_client, monkeypatch):
    detached: list[int] = []
    monkeypatch.setattr(models_mod.repo, "get_llm_model", _async(_model()))
    monkeypatch.setattr(
        models_mod.repo,
        "agents_using_model",
        _async([SimpleNamespace(id=1, name="A")]),
    )
    monkeypatch.setattr(models_mod, "invalidate_agent", _async(None))

    async def fake_detach(session, model_id):
        detached.append(model_id)

    monkeypatch.setattr(models_mod.repo, "detach_model_from_agents", fake_detach)
    monkeypatch.setattr(models_mod.repo, "delete_llm_model", _async(None))

    resp = await auth_client.delete("/api/admin/models/1?force=true")
    assert resp.status_code == 204
    assert detached == [1]
