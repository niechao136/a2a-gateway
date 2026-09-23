"""平台适配器测试：公共工具 + 各平台验签/归一化/过滤/分段。"""

import hashlib
import hmac
import json
import time

import pytest

from a2a_gateway.connectors.base import VerifyError, chunk_text


# ---------------------------------------------------------------------------
# chunk_text（公共分段）
# ---------------------------------------------------------------------------
def test_chunk_text_short():
    assert chunk_text("你好", 100) == ["你好"]


def test_chunk_text_empty():
    assert chunk_text("   ", 100) == []


def test_chunk_text_splits_at_limit():
    text = "x" * 250
    chunks = chunk_text(text, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_chunk_text_prefers_newline():
    text = "a" * 60 + "\n" + "b" * 60
    chunks = chunk_text(text, 100)
    assert chunks[0] == "a" * 60
    assert chunks[1] == "b" * 60


def test_verify_error_exists():
    with pytest.raises(VerifyError):
        raise VerifyError("bad signature")


# ---------------------------------------------------------------------------
# Telegram 适配器
# ---------------------------------------------------------------------------
from a2a_gateway.connectors.telegram import TelegramAdapter, _mentioned_bot

TG = TelegramAdapter()
TG_CREDS = {"bot_token": "123:abc", "secret_token": "sec", "bot_username": "mybot"}


def _tg_headers(secret: str = "sec") -> dict:
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


def _tg_private_update(text: str = "你好") -> bytes:
    return json.dumps(
        {
            "update_id": 1001,
            "message": {
                "message_id": 11,
                "text": text,
                "from": {"id": 7, "is_bot": False, "first_name": "Tom"},
                "chat": {"id": 7, "type": "private"},
            },
        }
    ).encode()


def _tg_group_update(text: str, entities: list | None = None) -> bytes:
    return json.dumps(
        {
            "update_id": 1002,
            "message": {
                "message_id": 12,
                "text": text,
                "entities": entities or [],
                "from": {"id": 7, "is_bot": False, "first_name": "Tom"},
                "chat": {"id": -100, "type": "supergroup"},
            },
        }
    ).encode()


async def test_telegram_challenge_not_supported():
    assert await TG.build_challenge(_tg_private_update(), _tg_headers(), TG_CREDS) is None


async def test_telegram_verify_rejects_bad_secret():
    with pytest.raises(VerifyError):
        await TG.verify_and_parse(_tg_private_update(), _tg_headers("wrong"), TG_CREDS)


async def test_telegram_verify_rejects_empty_secret_config():
    with pytest.raises(VerifyError):
        await TG.verify_and_parse(_tg_private_update(), _tg_headers(), {"bot_token": "t"})


async def test_telegram_private_message_parsed():
    msgs = await TG.verify_and_parse(_tg_private_update(), _tg_headers(), TG_CREDS)
    assert len(msgs) == 1
    m = msgs[0]
    assert (m.platform, m.chat_id, m.chat_type) == ("telegram", "7", "private")
    assert m.text == "你好"
    assert m.user_name == "Tom"
    assert m.event_id == "1001"


async def test_telegram_bot_message_ignored():
    update = json.dumps(
        {
            "update_id": 1003,
            "message": {
                "message_id": 13,
                "text": "hi",
                "from": {"id": 99, "is_bot": True, "first_name": "B"},
                "chat": {"id": 7, "type": "private"},
            },
        }
    ).encode()
    assert await TG.verify_and_parse(update, _tg_headers(), TG_CREDS) == []


async def test_telegram_group_without_mention_ignored():
    assert await TG.verify_and_parse(_tg_group_update("大家好"), _tg_headers(), TG_CREDS) == []


async def test_telegram_group_with_mention_parsed():
    text = "@mybot 帮我查天气"
    entities = [{"type": "mention", "offset": 0, "length": len("@mybot")}]
    msgs = await TG.verify_and_parse(
        _tg_group_update(text, entities), _tg_headers(), TG_CREDS
    )
    assert len(msgs) == 1
    assert msgs[0].chat_type == "group"
    assert msgs[0].text == "@mybot 帮我查天气"


async def test_telegram_group_mention_requires_bot_username():
    # 缺少 bot_username（未启用自动注册）时，群聊 @ 也无法识别 → 忽略
    update = _tg_group_update("@mybot hi", [{"type": "mention", "offset": 0, "length": 6}])
    creds = {"bot_token": "123:abc", "secret_token": "sec"}
    assert await TG.verify_and_parse(update, _tg_headers(), creds) == []


def test_mentioned_bot_matches_case_insensitive():
    entities = [{"type": "mention", "offset": 0, "length": 6}]
    msg = {"text": "@MyBot hi", "entities": entities}
    assert _mentioned_bot(msg, "mybot") is True
    assert _mentioned_bot(msg, "other") is False


# ---------------------------------------------------------------------------
# Slack 适配器
# ---------------------------------------------------------------------------
from a2a_gateway.connectors.slack import SlackAdapter, slack_signature

SLACK = SlackAdapter()
SLACK_SECRET = "shhh"
SLACK_CREDS = {"bot_token": "xoxb-test", "signing_secret": "shhh"}
SLACK_BODY = {
    "event_id": "Ev007",
    "type": "event_callback",
    "event": {
        "type": "message",
        "channel_type": "im",
        "channel": "D123",
        "user": "U7",
        "text": "帮我总结",
        "bot_id": None,
    },
}


def _slack_headers(body: bytes, secret: str = SLACK_SECRET, ts: int | None = None) -> dict:
    timestamp = ts or int(time.time())
    basestring = f"v0:{timestamp}:{body.decode()}"
    digest = hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": str(timestamp), "X-Slack-Signature": f"v0={digest}"}


def _slack_bytes(payload: dict) -> bytes:
    return json.dumps(payload).encode()


async def test_slack_url_verification_challenge():
    body = _slack_bytes({"type": "url_verification", "challenge": "xyz"})
    result = await SLACK.build_challenge(body, _slack_headers(body), SLACK_CREDS)
    assert result == {"challenge": "xyz"}


async def test_slack_challenge_rejects_bad_signature():
    body = _slack_bytes({"type": "url_verification", "challenge": "xyz"})
    with pytest.raises(VerifyError):
        await SLACK.build_challenge(body, _slack_headers(body, secret="bad"), SLACK_CREDS)


async def test_slack_challenge_rejects_stale_timestamp():
    body = _slack_bytes({"type": "url_verification", "challenge": "xyz"})
    stale_ts = int(time.time()) - 600
    with pytest.raises(VerifyError):
        await SLACK.build_challenge(body, _slack_headers(body, ts=stale_ts), SLACK_CREDS)


async def test_slack_im_message_parsed():
    body = _slack_bytes(SLACK_BODY)
    msgs = await SLACK.verify_and_parse(body, _slack_headers(body), SLACK_CREDS)
    assert len(msgs) == 1
    m = msgs[0]
    assert (m.platform, m.chat_id, m.chat_type) == ("slack", "D123", "private")
    assert m.text == "帮我总结"
    assert m.event_id == "Ev007"


async def test_slack_app_mention_parsed_as_group():
    body = _slack_bytes(
        {
            "event_id": "Ev008",
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C456",
                "user": "U7",
                "text": "<@U0> 帮我总结",
                "bot_id": None,
            },
        }
    )
    msgs = await SLACK.verify_and_parse(body, _slack_headers(body), SLACK_CREDS)
    assert len(msgs) == 1
    assert msgs[0].chat_type == "group"
    assert msgs[0].chat_id == "C456"


async def test_slack_bot_message_ignored():
    payload = {**SLACK_BODY, "event": {**SLACK_BODY["event"], "bot_id": "B999"}}
    body = _slack_bytes(payload)
    assert await SLACK.verify_and_parse(body, _slack_headers(body), SLACK_CREDS) == []


async def test_slack_message_without_text_ignored():
    payload = {**SLACK_BODY, "event": {**SLACK_BODY["event"], "text": ""}}
    body = _slack_bytes(payload)
    assert await SLACK.verify_and_parse(body, _slack_headers(body), SLACK_CREDS) == []


async def test_slack_verify_rejects_bad_signature():
    body = _slack_bytes(SLACK_BODY)
    with pytest.raises(VerifyError):
        await SLACK.verify_and_parse(body, _slack_headers(body, secret="bad"), SLACK_CREDS)


def test_slack_signature_format():
    basestring = "v0:1:payload"
    digest = hmac.new(SLACK_SECRET.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    assert slack_signature(SLACK_SECRET, "1", "payload") == f"v0={digest}"


# ---------------------------------------------------------------------------
# 飞书适配器
# ---------------------------------------------------------------------------
import base64

from a2a_gateway.connectors.feishu import FeishuAdapter, encrypt_feishu_payload

FEISHU = FeishuAdapter()
FEISHU_CREDS = {"app_id": "cli_a", "app_secret": "s", "verification_token": "vt", "encrypt_key": ""}


def _feishu_event(token: str = "vt", encrypt_key: str = "") -> bytes:
    payload = {
        "header": {
            "event_id": "EvF1",
            "token": token,
            "event_type": "im.message.receive_v1",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_u1", "user_id": "u1"}},
            "message": {
                "chat_id": "oc_c1",
                "chat_type": "p2p",
                "message_id": "om_1",
                "content": json.dumps({"text": "你好飞书"}),
                "mentions": [],
            },
        },
    }
    body = json.dumps(payload).encode()
    if encrypt_key:
        # 真实飞书加密体是 {"encrypt": "<base64>"} 信封
        return json.dumps({"encrypt": encrypt_feishu_payload(encrypt_key, payload)}).encode()
    return body


async def test_feishu_url_verification_challenge():
    body = json.dumps(
        {"header": {"token": "vt"}, "type": "url_verification", "challenge": "cf_1"}
    ).encode()
    result = await FEISHU.build_challenge(body, {}, FEISHU_CREDS)
    assert result == {"challenge": "cf_1"}


async def test_feishu_challenge_rejects_bad_token():
    body = json.dumps(
        {"header": {"token": "bad"}, "type": "url_verification", "challenge": "cf_1"}
    ).encode()
    with pytest.raises(VerifyError):
        await FEISHU.build_challenge(body, {}, FEISHU_CREDS)


async def test_feishu_challenge_with_encryption():
    blob = encrypt_feishu_payload(
        "mykey",
        {"header": {"token": "vt"}, "type": "url_verification", "challenge": "cf_2"},
    )
    body = json.dumps({"encrypt": blob}).encode()
    creds = {**FEISHU_CREDS, "encrypt_key": "mykey"}
    result = await FEISHU.build_challenge(body, {}, creds)
    assert result == {"challenge": "cf_2"}


async def test_feishu_private_message_parsed():
    msgs = await FEISHU.verify_and_parse(_feishu_event(), {}, FEISHU_CREDS)
    assert len(msgs) == 1
    m = msgs[0]
    assert (m.platform, m.chat_id, m.chat_type) == ("feishu", "oc_c1", "private")
    assert m.text == "你好飞书"
    assert m.event_id == "EvF1"


async def test_feishu_encrypted_message_parsed():
    creds = {**FEISHU_CREDS, "encrypt_key": "mykey"}
    msgs = await FEISHU.verify_and_parse(_feishu_event(encrypt_key="mykey"), {}, creds)
    assert len(msgs) == 1
    assert msgs[0].text == "你好飞书"


async def test_feishu_verify_rejects_bad_token():
    with pytest.raises(VerifyError):
        await FEISHU.verify_and_parse(_feishu_event(token="bad"), {}, FEISHU_CREDS)


async def test_feishu_group_without_bot_mention_ignored(monkeypatch):
    async def fake_bot_open_id(credentials):
        return "ou_bot"

    monkeypatch.setattr(FEISHU, "_bot_open_id", fake_bot_open_id)
    event = {
        "header": {"event_id": "EvF2", "token": "vt", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_u1", "user_id": "u1"}},
            "message": {
                "chat_id": "oc_g1",
                "chat_type": "group",
                "message_id": "om_2",
                "content": json.dumps({"text": "@_user_1 大家好"}),
                "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_other"}, "name": "张三"}],
            },
        },
    }
    assert await FEISHU.verify_and_parse(json.dumps(event).encode(), {}, FEISHU_CREDS) == []


async def test_feishu_group_with_bot_mention_parsed(monkeypatch):
    async def fake_bot_open_id(credentials):
        return "ou_bot"

    monkeypatch.setattr(FEISHU, "_bot_open_id", fake_bot_open_id)
    event = {
        "header": {"event_id": "EvF3", "token": "vt", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_u1", "user_id": "u1"}},
            "message": {
                "chat_id": "oc_g2",
                "chat_type": "group",
                "message_id": "om_3",
                "content": json.dumps({"text": "@_user_1 帮我订机票"}),
                "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "助手"}],
            },
        },
    }
    msgs = await FEISHU.verify_and_parse(json.dumps(event).encode(), {}, FEISHU_CREDS)
    assert len(msgs) == 1
    assert msgs[0].chat_type == "group"
    # mention 占位符替换为可读名字
    assert msgs[0].text == "@助手 帮我订机票"


def test_encrypt_feishu_payload_roundtrip():
    from a2a_gateway.connectors.feishu import decrypt_feishu_payload

    key = "k" * 8
    payload = {"type": "url_verification", "challenge": "c"}
    blob = encrypt_feishu_payload(key, payload)
    assert decrypt_feishu_payload(key, blob) == payload


def test_encrypt_feishu_payload_uses_random_iv():
    key = "k" * 8
    payload = {"a": 1}
    b1 = encrypt_feishu_payload(key, payload)
    b2 = encrypt_feishu_payload(key, payload)
    assert base64.b64decode(b1)[:16] != base64.b64decode(b2)[:16]  # IV 随机


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
from a2a_gateway.connectors.registry import get_adapter


def test_registry_returns_all_platforms():
    for platform in ("feishu", "telegram", "slack"):
        assert get_adapter(platform).platform == platform


def test_registry_unknown_platform():
    with pytest.raises(KeyError):
        get_adapter("discord")


# ---------------------------------------------------------------------------
# Telegram webhook 自动注册：基址来源（入参优先，其次 settings）
# ---------------------------------------------------------------------------
from types import SimpleNamespace as _SimpleNamespace
from typing import ClassVar

from a2a_gateway.connectors import telegram as telegram_mod


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpxClient:
    """记录请求的 httpx.AsyncClient 替身（替换 telegram 模块内的引用，不影响全局）。"""

    calls: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        _FakeHttpxClient.calls.append(("GET", url, None))
        return _FakeResponse({"result": {"username": "mybot"}})

    async def post(self, url, **kwargs):
        _FakeHttpxClient.calls.append(("POST", url, kwargs.get("json")))
        return _FakeResponse({"ok": True})


async def test_register_webhook_uses_given_base_url(monkeypatch):
    _FakeHttpxClient.calls = []
    monkeypatch.setattr(
        telegram_mod, "httpx", _SimpleNamespace(AsyncClient=_FakeHttpxClient)
    )
    monkeypatch.setattr(telegram_mod, "_settings", _SimpleNamespace(public_base_url=""))
    creds, warning = await telegram_mod.register_webhook(
        {"bot_token": "123:abc", "secret_token": "sec"}, 7, "https://a2a.example.com/"
    )
    assert warning == ""
    assert creds["bot_username"] == "mybot"
    method, url, payload = _FakeHttpxClient.calls[-1]
    assert method == "POST"
    assert url == "https://api.telegram.org/bot123:abc/setWebhook"
    assert payload["url"] == "https://a2a.example.com/api/connectors/telegram/7/webhook"
    assert payload["secret_token"] == "sec"


async def test_register_webhook_without_any_base_url_returns_warning(monkeypatch):
    monkeypatch.setattr(telegram_mod, "_settings", _SimpleNamespace(public_base_url=""))
    creds, warning = await telegram_mod.register_webhook({"bot_token": "t"}, 1, "")
    assert creds == {"bot_token": "t"}
    assert "自动注册" in warning


async def test_register_webhook_falls_back_to_settings(monkeypatch):
    _FakeHttpxClient.calls = []
    monkeypatch.setattr(
        telegram_mod, "httpx", _SimpleNamespace(AsyncClient=_FakeHttpxClient)
    )
    monkeypatch.setattr(
        telegram_mod, "_settings", _SimpleNamespace(public_base_url="https://fixed.example.com")
    )
    _, warning = await telegram_mod.register_webhook({"bot_token": "t"}, 3, "")
    assert warning == ""
    assert _FakeHttpxClient.calls[-1][2]["url"] == (
        "https://fixed.example.com/api/connectors/telegram/3/webhook"
    )
