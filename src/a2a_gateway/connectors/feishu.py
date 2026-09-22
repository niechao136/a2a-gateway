"""飞书适配器：verification_token 校验 + 可选 AES-256-CBC 解密 + im/v1 收发。

群聊过滤依赖机器人 open_id（经 GET /open-apis/bot/v3/info 获取，进程内缓存）。
tenant_access_token 同样进程内缓存、过期前刷新。
"""

import base64
import hashlib
import json
import logging
import os
import time
from collections.abc import Mapping

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .base import InboundMessage, PlatformAdapter, VerifyError, chunk_text, normalize_headers

logger = logging.getLogger(__name__)

_API_BASE = "https://open.feishu.cn/open-apis"
_CHUNK_LIMIT = 4000
_TIMEOUT = 15.0

# 进程内缓存：{app_id: (tenant_access_token, 过期时间戳)}
_token_cache: dict[str, tuple[str, float]] = {}
# 进程内缓存：{app_id: bot_open_id}
_bot_open_id_cache: dict[str, str] = {}


def decrypt_feishu_payload(encrypt_key: str, blob: str | bytes) -> dict:
    """解密飞书加密事件体：AES-256-CBC，key = sha256(encrypt_key)，IV 为密文前 16 字节，PKCS7 填充。"""
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    data = base64.b64decode(blob)
    cipher = Cipher(algorithms.AES(key), modes.CBC(data[:16]))
    decryptor = cipher.decryptor()
    padded = decryptor.update(data[16:]) + decryptor.finalize()
    pad_len = padded[-1]
    plain = padded[:-pad_len]
    return json.loads(plain)


def encrypt_feishu_payload(encrypt_key: str, payload: dict) -> str:
    """测试辅助：按飞书规范加密事件体（随机 IV）。"""
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = os.urandom(16)
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pad_len = 16 - (len(raw) % 16)
    padded = raw + bytes([pad_len]) * pad_len
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode()


def _unwrap(body: bytes, credentials: dict) -> dict:
    """解析事件体：配置了 encrypt_key 时先解密；校验 verification_token。"""
    token = (credentials or {}).get("verification_token") or ""
    if not token:
        raise VerifyError("飞书 verification_token 未配置")
    payload = json.loads(body)
    if payload.get("encrypt"):
        encrypt_key = (credentials or {}).get("encrypt_key") or ""
        if not encrypt_key:
            raise VerifyError("收到加密事件但未配置 encrypt_key")
        payload = decrypt_feishu_payload(encrypt_key, payload["encrypt"])
    header = payload.get("header") or {}
    if header.get("token") != token:
        raise VerifyError("飞书 verification_token 校验失败")
    return payload


class FeishuAdapter(PlatformAdapter):
    platform = "feishu"

    async def build_challenge(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> dict | None:
        payload = _unwrap(body, credentials)
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}
        return None

    async def _bot_open_id(self, credentials: dict) -> str:
        """机器人 open_id（群聊 @ 过滤用）；进程内缓存，获取失败返回空串。"""
        app_id = (credentials or {}).get("app_id") or ""
        if not app_id:
            return ""
        cached = _bot_open_id_cache.get(app_id)
        if cached:
            return cached
        token = await self._tenant_access_token(credentials)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{_API_BASE}/bot/v3/info",
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                bot = resp.json().get("bot") or {}
                open_id = str(bot.get("open_id") or "")
        except Exception as exc:
            logger.warning("获取飞书机器人信息失败 app_id=%s: %s", app_id, exc)
            return ""
        _bot_open_id_cache[app_id] = open_id
        return open_id

    async def _tenant_access_token(self, credentials: dict) -> str:
        app_id = (credentials or {}).get("app_id") or ""
        app_secret = (credentials or {}).get("app_secret") or ""
        cached = _token_cache.get(app_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_API_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            resp.raise_for_status()
            data = resp.json()
        token = data.get("tenant_access_token") or ""
        if not token:
            raise RuntimeError(f"获取 tenant_access_token 失败: {data.get('msg')}")
        _token_cache[app_id] = (token, time.time() + float(data.get("expire", 3600)))
        return token

    async def verify_and_parse(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> list[InboundMessage]:
        _ = normalize_headers(headers)  # 飞书不依赖请求头验签（token 在事件体内）
        payload = _unwrap(body, credentials)
        header = payload.get("header") or {}
        if header.get("event_type") != "im.message.receive_v1":
            return []
        event = payload.get("event") or {}
        message = event.get("message") or {}
        content = json.loads(message.get("content") or "{}")
        text = (content.get("text") or "").strip()
        if not text:
            return []
        sender_id = (event.get("sender") or {}).get("sender_id") or {}
        user_id = str(sender_id.get("open_id") or sender_id.get("user_id") or "")
        mentions = message.get("mentions") or []
        # mention 占位符替换为可读名字
        for mention in mentions:
            text = text.replace(mention.get("key") or "", f"@{mention.get('name') or ''}").strip()
        chat_type = "private" if message.get("chat_type") == "p2p" else "group"
        if chat_type == "group":
            bot_open_id = await self._bot_open_id(credentials)
            if not bot_open_id or not any(
                (m.get("id") or {}).get("open_id") == bot_open_id for m in mentions
            ):
                return []
        return [
            InboundMessage(
                platform=self.platform,
                chat_id=str(message.get("chat_id")),
                chat_type=chat_type,
                user_id=user_id,
                user_name=user_id,  # 事件不含用户名，open_id 即展示名
                text=text,
                event_id=str(header.get("event_id") or message.get("message_id") or ""),
            )
        ]

    async def send(self, credentials: dict, chat_id: str, text: str) -> None:
        token = await self._tenant_access_token(credentials)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for chunk in chunk_text(text, _CHUNK_LIMIT):
                resp = await client.post(
                    f"{_API_BASE}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "receive_id": chat_id,
                        "msg_type": "text",
                        "content": json.dumps({"text": chunk}, ensure_ascii=False),
                    },
                )
                resp.raise_for_status()
