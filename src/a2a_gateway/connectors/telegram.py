"""Telegram 适配器：secret-token 头验证 + Bot API 收发与 webhook 注册。"""

import hmac
import json
import logging
from collections.abc import Mapping

import httpx

from ..config import get_settings
from .base import InboundMessage, PlatformAdapter, VerifyError, chunk_text, normalize_headers

logger = logging.getLogger(__name__)

_settings = get_settings()

_API_BASE = "https://api.telegram.org"
_CHUNK_LIMIT = 4000
_TIMEOUT = 15.0


def _mentioned_bot(message: dict, bot_username: str) -> bool:
    """群聊消息是否 @了机器人（entities 中的 mention 文本匹配 @bot_username）。"""
    if not bot_username:
        return False
    text = message.get("text") or ""
    for entity in message.get("entities") or []:
        if entity.get("type") != "mention":
            continue
        offset, length = entity.get("offset", 0), entity.get("length", 0)
        if text[offset : offset + length].lower() == f"@{bot_username.lower()}":
            return True
    return False


class TelegramAdapter(PlatformAdapter):
    platform = "telegram"

    async def build_challenge(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> dict | None:
        return None  # Telegram 经 setWebhook 注册，无握手事件

    async def verify_and_parse(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> list[InboundMessage]:
        secret = (credentials or {}).get("secret_token") or ""
        header_token = normalize_headers(headers).get("x-telegram-bot-api-secret-token") or ""
        if not secret or not hmac.compare_digest(
            header_token.encode("utf-8"), secret.encode("utf-8")
        ):
            raise VerifyError("Telegram secret token 校验失败")

        update = json.loads(body)
        message = update.get("message") or update.get("edited_message")
        if not message:
            return []
        sender = message.get("from") or {}
        if sender.get("is_bot"):
            return []
        text = (message.get("text") or "").strip()
        if not text:
            return []
        chat = message.get("chat") or {}
        is_private = chat.get("type") == "private"
        if not is_private and not _mentioned_bot(
            message, (credentials or {}).get("bot_username") or ""
        ):
            return []
        return [
            InboundMessage(
                platform=self.platform,
                chat_id=str(chat.get("id")),
                chat_type="private" if is_private else "group",
                user_id=str(sender.get("id", "")),
                user_name=sender.get("first_name") or str(sender.get("id", "")),
                text=text,
                event_id=str(update.get("update_id", "")),
            )
        ]

    async def send(self, credentials: dict, chat_id: str, text: str) -> None:
        token = (credentials or {}).get("bot_token") or ""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for chunk in chunk_text(text, _CHUNK_LIMIT):
                resp = await client.post(
                    f"{_API_BASE}/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk},
                )
                resp.raise_for_status()


async def register_webhook(credentials: dict, connector_id: int) -> tuple[dict, str]:
    """启用/改凭据后自动注册 webhook 并经 getMe 补全 bot_username。

    返回 (更新后的凭据, 警告信息)；成功时警告为空串。
    失败不抛异常：注册失败只提示，不阻断保存。
    """
    base = _settings.public_base_url.rstrip("/")
    if not base:
        return credentials, "未配置 PUBLIC_BASE_URL，跳过自动注册（请在 Telegram 手动 setWebhook）"
    token = (credentials or {}).get("bot_token") or ""
    if not token:
        return credentials, "缺少 bot_token，无法自动注册"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            me = await client.get(f"{_API_BASE}/bot{token}/getMe")
            me.raise_for_status()
            username = (me.json().get("result") or {}).get("username") or ""
            resp = await client.post(
                f"{_API_BASE}/bot{token}/setWebhook",
                json={
                    "url": f"{base}/api/connectors/telegram/{connector_id}/webhook",
                    "secret_token": (credentials or {}).get("secret_token") or "",
                },
            )
            resp.raise_for_status()
        return {**credentials, "bot_username": username}, ""
    except Exception as exc:
        logger.warning("Telegram webhook 自动注册失败 connector=%s: %s", connector_id, exc)
        return credentials, f"Telegram 自动注册失败：{exc}"
