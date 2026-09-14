"""对话身份：匿名访客与登录用户共用一个 httpOnly cookie。

设计要点
--------
1. **一套令牌两种身份**：cookie ``a2a_identity`` 里是一个 JWT，
   ``typ=visitor`` 表示匿名访客（sub 为 uuid），``typ=user`` 表示已登录管理员
   （sub 为用户名）。后端只需解析这一个 cookie 就能确定会话归属。

2. **为什么不用 localStorage 存 visitor_id**：httpOnly + 后端签名意味着前端
   JS 既读不到也改不了，无法伪造他人 visitor_id 去读别人的会话。

3. **登录归并**：登录时把该 visitor 名下的会话 ``UPDATE`` 成 user 归属即可，
   消息本体（checkpoints 表）按 thread_id 存储、与用户无关，因此零数据搬迁。

4. **SSE 场景的坑**：``EventSourceResponse`` 是独立返回的 Response 对象，
   在 FastAPI 依赖里往注入的 ``Response`` 上 set_cookie **不会生效**。
   因此本模块不在依赖中自动签发 cookie，而是由路由显式调用
   ``set_identity_cookie(resp, identity)``。
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Request, Response
from jose import JWTError, jwt

from .config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# 身份 cookie 名称
IDENTITY_COOKIE = "a2a_identity"

# owner_kind 取值（与 models.Conversation.owner_kind 一致）
IdentityKind = Literal["visitor", "user"]
IDENTITY_KIND_VISITOR: IdentityKind = "visitor"
IDENTITY_KIND_USER: IdentityKind = "user"

_VISITOR_MAX_AGE = _settings.visitor_expire_days * 86400
_USER_MAX_AGE = _settings.identity_expire_days * 86400


@dataclass(frozen=True)
class Identity:
    """一个对话身份（匿名访客或登录用户）。"""

    kind: IdentityKind
    id: str

    @property
    def is_visitor(self) -> bool:
        return self.kind == IDENTITY_KIND_VISITOR

    @property
    def is_user(self) -> bool:
        return self.kind == IDENTITY_KIND_USER

    @property
    def max_age(self) -> int:
        return _VISITOR_MAX_AGE if self.is_visitor else _USER_MAX_AGE

    def owns(self, conversation) -> bool:
        """判断某条会话目录是否属于本身份。"""
        return (
            conversation.owner_kind == self.kind and conversation.owner_id == self.id
        )


def new_visitor_id() -> str:
    return str(uuid.uuid4())


def _encode(payload: dict) -> str:
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm)


def _token_for(identity: Identity) -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=identity.max_age)
    return _encode({"sub": identity.id, "typ": identity.kind, "exp": expire})


def set_identity_cookie(response: Response, identity: Identity) -> None:
    """把身份写入响应 cookie（httpOnly，前端 JS 不可读写）。"""
    response.set_cookie(
        IDENTITY_COOKIE,
        _token_for(identity),
        max_age=identity.max_age,
        httponly=True,
        samesite="lax",
        secure=_settings.cookie_secure,
        path="/",
    )


def decode_identity_token(token: str) -> dict | None:
    """解析身份令牌；无效或已过期返回 None。"""
    try:
        payload = jwt.decode(
            token, _settings.jwt_secret, algorithms=[_settings.jwt_algorithm]
        )
    except JWTError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def read_identity(request: Request) -> Identity | None:
    """从请求 cookie 解析身份；无 cookie / 令牌无效 / 已过期 → None。"""
    raw = request.cookies.get(IDENTITY_COOKIE)
    if not raw:
        return None
    payload = decode_identity_token(raw)
    if payload is None:
        return None
    kind = payload.get("typ")
    subject = payload.get("sub")
    if kind not in (IDENTITY_KIND_VISITOR, IDENTITY_KIND_USER) or not subject:
        return None
    return Identity(kind=kind, id=str(subject))


def ensure_identity(request: Request) -> tuple[Identity, bool]:
    """解析身份；不存在时新签一个匿名身份。

    @returns (身份, 是否为新签发) —— 新签发时调用方需要把 cookie 写到最终
    返回的 Response 上（SSE 场景见模块文档第 4 点）。
    """
    identity = read_identity(request)
    if identity is not None:
        return identity, False
    return Identity(IDENTITY_KIND_VISITOR, new_visitor_id()), True
