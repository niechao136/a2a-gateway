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
