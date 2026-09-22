"""Slack 适配器：Events API HMAC 验签 + chat.postMessage。"""

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Mapping

import httpx

from .base import InboundMessage, PlatformAdapter, VerifyError, chunk_text, normalize_headers

logger = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"
_CHUNK_LIMIT = 39000
_TIMEOUT = 15.0
_MAX_TS_SKEW = 300  # 签名时间戳最大偏移（秒），防重放


def slack_signature(signing_secret: str, timestamp: str, body: str) -> str:
    """按 Slack 规范计算请求签名：v0=HMAC-SHA256(secret, "v0:{ts}:{body}")。"""
    basestring = f"v0:{timestamp}:{body}"
    digest = hmac.new(
        signing_secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"v0={digest}"


def _verify(headers: Mapping[str, str], body: bytes, credentials: dict) -> None:
    secret = (credentials or {}).get("signing_secret") or ""
    normalized = normalize_headers(headers)
    timestamp = normalized.get("x-slack-request-timestamp") or ""
    signature = normalized.get("x-slack-signature") or ""
    if not secret or not timestamp or not signature:
        raise VerifyError("Slack 签名头缺失")
    try:
        if abs(time.time() - int(timestamp)) > _MAX_TS_SKEW:
            raise VerifyError("Slack 签名时间戳过期")
    except ValueError as exc:
        raise VerifyError("Slack 签名时间戳非法") from exc
    expected = slack_signature(secret, timestamp, body.decode("utf-8", "replace"))
    if not hmac.compare_digest(expected, signature):
        raise VerifyError("Slack 签名校验失败")


class SlackAdapter(PlatformAdapter):
    platform = "slack"

    async def build_challenge(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> dict | None:
        _verify(headers, body, credentials)
        payload = json.loads(body)
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}
        return None

    async def verify_and_parse(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> list[InboundMessage]:
        _verify(headers, body, credentials)
        payload = json.loads(body)
        if payload.get("type") != "event_callback":
            return []
        event = payload.get("event") or {}
        event_type = event.get("type")
        # bot 自己 / 其他 app 的消息：bot_id 非空即忽略
        if event.get("bot_id"):
            return []
        text = (event.get("text") or "").strip()
        if not text:
            return []
        user_id = str(event.get("user") or "")
        event_id = str(payload.get("event_id") or "")
        if event_type == "app_mention":
            # 群聊 @机器人：去掉 <@Uxxx> 引用占位
            cleaned = " ".join(
                part for part in text.split() if not part.startswith("<@")
            ).strip()
            if not cleaned:
                return []
            return [
                InboundMessage(
                    platform=self.platform,
                    chat_id=str(event.get("channel")),
                    chat_type="group",
                    user_id=user_id,
                    user_name=user_id,
                    text=cleaned,
                    event_id=event_id,
                )
            ]
        if event_type == "message" and event.get("channel_type") == "im":
            # 私聊；编辑/删除等带 subtype 的不作为新消息处理
            if event.get("subtype"):
                return []
            return [
                InboundMessage(
                    platform=self.platform,
                    chat_id=str(event.get("channel")),
                    chat_type="private",
                    user_id=user_id,
                    user_name=user_id,
                    text=text,
                    event_id=event_id,
                )
            ]
        return []

    async def send(self, credentials: dict, chat_id: str, text: str) -> None:
        token = (credentials or {}).get("bot_token") or ""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for chunk in chunk_text(text, _CHUNK_LIMIT):
                resp = await client.post(
                    f"{_API_BASE}/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"channel": chat_id, "text": chunk},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Slack 发送失败: {data.get('error')}")
