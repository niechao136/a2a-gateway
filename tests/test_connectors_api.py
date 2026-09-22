"""连接器管理 API 测试（零外部依赖：repository 层全部 monkeypatch）。"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from a2a_gateway.connectors import pipeline as pipeline_mod
from a2a_gateway.connectors.base import InboundMessage
from a2a_gateway.routes import connectors as connectors_mod


def _now():
    return datetime.now(timezone.utc)


class _Platform:
    def __init__(self, value: str):
        self.value = value


def make_connector(**overrides):
    base = {
        "id": 1,
        "name": "tg-1",
        "description": "",
        "platform": _Platform("telegram"),
        "credentials": {
            "bot_token": "123:abc",
            "secret_token": "sec",
            "bot_username": "mybot",
        },
        "agent_id": 1,
        "enabled": True,
        "created_at": _now(),
        "updated_at": _now(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def captured(monkeypatch):
    """monkeypatch 路由模块引用的 repository 函数，并捕获调用参数。"""
    store: dict = {"changes": None, "created": None, "sent": []}

    connector = make_connector()

    async def fake_get_connector(session, connector_id):
        return connector if connector_id == connector.id else None

    async def fake_get_connector_by_name(session, name):
        return None

    async def fake_get_agent_by_id(session, agent_id):
        return SimpleNamespace(id=agent_id, name="Demo Agent")

    async def fake_create_connector(session, data, credentials):
        store["created"] = credentials
        return make_connector(
            name=data.name,
            platform=_Platform(data.platform),
            credentials=credentials,
            agent_id=data.agent_id,
        )

    async def fake_update_connector(session, conn, changes):
        store["changes"] = changes
        for field, value in changes.items():
            setattr(conn, field, value)
        return conn

    async def fake_delete_connector(session, conn):
        store["deleted"] = conn.id

    async def fake_list_recent(session, connector_id, limit=20):
        return [
            SimpleNamespace(
                chat_id="777",
                chat_type="private",
                last_user_ref={},
                last_active_at=_now(),
            )
        ]

    async def fake_register_webhook(credentials, connector_id):
        return credentials, ""  # 测试中不触网

    monkeypatch.setattr(connectors_mod, "get_connector", fake_get_connector)
    monkeypatch.setattr(connectors_mod, "get_connector_by_name", fake_get_connector_by_name)
    monkeypatch.setattr(connectors_mod, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(connectors_mod, "create_connector", fake_create_connector)
    monkeypatch.setattr(connectors_mod, "update_connector", fake_update_connector)
    monkeypatch.setattr(connectors_mod, "delete_connector", fake_delete_connector)
    monkeypatch.setattr(
        connectors_mod, "list_recent_connector_conversations", fake_list_recent
    )
    monkeypatch.setattr(connectors_mod, "register_webhook", fake_register_webhook)
    store["connector"] = connector
    return store


# ---------------------------------------------------------------------------
# 管理端 CRUD
# ---------------------------------------------------------------------------
async def test_list_connectors_requires_auth(anon_client):
    resp = await anon_client.get("/api/admin/connectors")
    assert resp.status_code == 401


async def test_create_connector_generates_secret_and_masks(auth_client, captured):
    resp = await auth_client.post(
        "/api/admin/connectors",
        json={
            "name": "tg-1",
            "platform": "telegram",
            "agent_id": 1,
            "credentials": {"bot_token": "123:abc"},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["agent_name"] == "Demo Agent"
    assert data["webhook_url"].endswith("/api/connectors/telegram/1/webhook")
    assert data["credentials_masked"]["bot_token"] == "••••"
    # secret_token 留空时后端自动生成
    assert (captured["created"].get("secret_token") or "") != ""


async def test_create_connector_rejects_unknown_platform(auth_client, captured):
    resp = await auth_client.post(
        "/api/admin/connectors",
        json={"name": "x", "platform": "discord", "agent_id": 1, "credentials": {}},
    )
    assert resp.status_code == 422


async def test_create_connector_rejects_duplicate_name(auth_client, captured, monkeypatch):
    async def dup(session, name):
        return make_connector()

    monkeypatch.setattr(connectors_mod, "get_connector_by_name", dup)
    resp = await auth_client.post(
        "/api/admin/connectors",
        json={"name": "tg-1", "platform": "telegram", "agent_id": 1, "credentials": {}},
    )
    assert resp.status_code == 409


async def test_create_connector_rejects_missing_agent(auth_client, captured, monkeypatch):
    async def no_agent(session, agent_id):
        return None

    monkeypatch.setattr(connectors_mod, "get_agent_by_id", no_agent)
    resp = await auth_client.post(
        "/api/admin/connectors",
        json={"name": "tg-2", "platform": "telegram", "agent_id": 99, "credentials": {}},
    )
    assert resp.status_code == 400


async def test_update_keeps_blank_credentials(auth_client, captured):
    resp = await auth_client.put(
        "/api/admin/connectors/1",
        json={"credentials": {"bot_token": "", "secret_token": ""}},
    )
    assert resp.status_code == 200
    changes = captured["changes"]
    # 全部留空 = 不修改：合并结果与原凭据一致，不产生 credentials 变更
    assert "credentials" not in changes
    assert "name" not in changes


async def test_update_merges_partial_credentials(auth_client, captured):
    resp = await auth_client.put(
        "/api/admin/connectors/1",
        json={"credentials": {"bot_token": "456:def"}},
    )
    assert resp.status_code == 200
    merged = captured["changes"]["credentials"]
    assert merged["bot_token"] == "456:def"
    assert merged["secret_token"] == "sec"  # 未提供的字段保留原值
    assert merged["bot_username"] == "mybot"


async def test_update_rejects_missing_connector(auth_client, captured):
    resp = await auth_client.put("/api/admin/connectors/99", json={"name": "x"})
    assert resp.status_code == 404


async def test_delete_connector(auth_client, captured):
    resp = await auth_client.delete("/api/admin/connectors/1")
    assert resp.status_code == 204
    assert captured["deleted"] == 1


async def test_conversations_endpoint(auth_client, captured):
    resp = await auth_client.get("/api/admin/connectors/1/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["chat_id"] == "777"


# ---------------------------------------------------------------------------
# 主动推送
# ---------------------------------------------------------------------------
class _StubAdapter:
    platform = "telegram"

    def __init__(self, sink: list | None = None):
        self._sink = sink if sink is not None else []

    async def send(self, credentials, chat_id, text):
        self._sink.append((chat_id, text))


async def test_send_uses_most_recent_conversation(auth_client, captured, monkeypatch):
    sent: list = []
    monkeypatch.setattr(
        connectors_mod, "get_adapter", lambda platform: _StubAdapter(sent)
    )
    resp = await auth_client.post("/api/admin/connectors/1/send", json={"text": "hello"})
    assert resp.status_code == 200
    assert resp.json()["chat_id"] == "777"
    assert sent == [("777", "hello")]


async def test_send_with_explicit_chat_id(auth_client, captured, monkeypatch):
    sent: list = []
    monkeypatch.setattr(
        connectors_mod, "get_adapter", lambda platform: _StubAdapter(sent)
    )
    resp = await auth_client.post(
        "/api/admin/connectors/1/send", json={"chat_id": "888", "text": "hi"}
    )
    assert resp.status_code == 200
    assert sent == [("888", "hi")]


async def test_send_requires_enabled(auth_client, captured):
    captured["connector"].enabled = False
    resp = await auth_client.post("/api/admin/connectors/1/send", json={"text": "hello"})
    assert resp.status_code == 400


async def test_send_without_conversation_returns_404(auth_client, captured, monkeypatch):
    async def empty(session, connector_id, limit=20):
        return []

    monkeypatch.setattr(connectors_mod, "list_recent_connector_conversations", empty)
    resp = await auth_client.post("/api/admin/connectors/1/send", json={"text": "hello"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 平台 webhook 路由
# ---------------------------------------------------------------------------
def _tg_webhook_update(event_id: int = 1001) -> dict:
    return {
        "update_id": event_id,
        "message": {
            "message_id": event_id,
            "text": "你好",
            "from": {"id": 7, "is_bot": False, "first_name": "Tom"},
            "chat": {"id": 7, "type": "private"},
        },
    }


@pytest.fixture
def webhook_env(monkeypatch):
    """已注册的 telegram 连接器 + stub 适配器，捕获 enqueue 调用。"""
    connector = make_connector()
    calls: list = []

    class _WebhookStubAdapter:
        platform = "telegram"

        async def build_challenge(self, body, headers, credentials):
            return None

        async def verify_and_parse(self, body, headers, credentials):
            update = json.loads(body)
            message = update["message"]
            return [
                InboundMessage(
                    platform="telegram",
                    chat_id=str(message["chat"]["id"]),
                    chat_type="private",
                    user_id="7",
                    user_name="Tom",
                    text=message["text"],
                    event_id=str(update["update_id"]),
                )
            ]

        async def send(self, credentials, chat_id, text):
            pass

    async def fake_get_connector(session, connector_id):
        return connector if connector_id == 1 else None

    def fake_enqueue(connector_ref, message):
        calls.append(message.event_id)
        return True

    monkeypatch.setattr(connectors_mod, "get_connector", fake_get_connector)
    monkeypatch.setattr(
        connectors_mod, "get_adapter", lambda platform: _WebhookStubAdapter()
    )
    monkeypatch.setattr(connectors_mod, "enqueue_message", fake_enqueue)
    pipeline_mod._seen.clear()
    return {"calls": calls, "connector": connector}


@pytest.fixture
def feishu_connector(monkeypatch):
    """飞书连接器（使用真实适配器，验证握手与验签行为）。"""
    connector = make_connector(
        platform=_Platform("feishu"),
        credentials={
            "app_id": "a",
            "app_secret": "s",
            "verification_token": "vt",
            "encrypt_key": "",
        },
    )

    async def fake_get_connector(session, connector_id):
        return connector if connector_id == 1 else None

    monkeypatch.setattr(connectors_mod, "get_connector", fake_get_connector)
    return connector


async def test_webhook_unknown_platform_404(anon_client):
    resp = await anon_client.post("/api/connectors/discord/1/webhook", json={})
    assert resp.status_code == 404


async def test_webhook_unknown_connector_404(anon_client, webhook_env, monkeypatch):
    async def none_connector(session, connector_id):
        return None

    monkeypatch.setattr(connectors_mod, "get_connector", none_connector)
    resp = await anon_client.post(
        "/api/connectors/telegram/99/webhook",
        json=_tg_webhook_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec"},
    )
    assert resp.status_code == 404


async def test_webhook_disabled_connector_404(anon_client, webhook_env):
    webhook_env["connector"].enabled = False
    resp = await anon_client.post(
        "/api/connectors/telegram/1/webhook",
        json=_tg_webhook_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec"},
    )
    assert resp.status_code == 404


async def test_webhook_platform_mismatch_404(anon_client, webhook_env):
    resp = await anon_client.post(
        "/api/connectors/slack/1/webhook",
        json=_tg_webhook_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec"},
    )
    assert resp.status_code == 404


async def test_webhook_feishu_verify_failure_401(anon_client, feishu_connector):
    resp = await anon_client.post("/api/connectors/feishu/1/webhook", json={"header": {}})
    assert resp.status_code == 401


async def test_webhook_feishu_challenge(anon_client, feishu_connector):
    resp = await anon_client.post(
        "/api/connectors/feishu/1/webhook",
        json={"header": {"token": "vt"}, "type": "url_verification", "challenge": "cf"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "cf"}


async def test_webhook_enqueue_and_ack(anon_client, webhook_env):
    resp = await anon_client.post(
        "/api/connectors/telegram/1/webhook",
        json=_tg_webhook_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert webhook_env["calls"] == ["1001"]


async def test_webhook_dedups_retries(anon_client, webhook_env):
    body = _tg_webhook_update()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "sec"}
    await anon_client.post("/api/connectors/telegram/1/webhook", json=body, headers=headers)
    await anon_client.post("/api/connectors/telegram/1/webhook", json=body, headers=headers)
    assert webhook_env["calls"] == ["1001"]  # 重试被去重


async def test_webhook_queue_full_still_acks(anon_client, webhook_env, monkeypatch):
    def full(connector_ref, message):
        return False

    monkeypatch.setattr(connectors_mod, "enqueue_message", full)
    resp = await anon_client.post(
        "/api/connectors/telegram/1/webhook",
        json=_tg_webhook_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
