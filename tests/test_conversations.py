"""对话身份与会话归属测试。

覆盖本次改动的核心链路：
- 身份令牌：签发 / 解析 / 过期 / 伪造（必须全部走后端签名）
- 登录归并：匿名访客名下的会话过户到账号（幂等、只改归属不改 thread_id）
- 越权读取：他人会话的 history 必须 404，未登记的历史 thread 放行
"""

import time
from types import SimpleNamespace

import pytest
from jose import jwt

from a2a_gateway import repository
from a2a_gateway.config import get_settings
from a2a_gateway.identity import (
    IDENTITY_COOKIE,
    IDENTITY_KIND_USER,
    IDENTITY_KIND_VISITOR,
    Identity,
    decode_identity_token,
    ensure_identity,
    new_visitor_id,
    read_identity,
    set_identity_cookie,
)
from a2a_gateway.routes import admin as admin_mod
from a2a_gateway.routes import chat as chat_mod

_settings = get_settings()


def _make_request(cookies: dict | None = None):
    """构造一个最小 Request 替身（只需 cookies）。"""
    return SimpleNamespace(cookies=cookies or {})


def _make_response():
    """构造一个最小 Response 替身，记录被写入的 cookie。

    ``set_cookie`` 的调用形式是 ``set_cookie(key, value, **opts)``，
    所以这里用 *args 接收并把 value 记到 ``cookies["key"]`` 上。
    """
    store: dict = {}

    def _set_cookie(*args, **kwargs):
        name = args[0] if args else kwargs.get("key")
        opts = dict(kwargs)
        if len(args) >= 2:
            opts["key"] = args[1]  # set_cookie(key, value, ...)
        store[name] = opts
        return opts

    return SimpleNamespace(cookies=store, set_cookie=_set_cookie)


async def _no_conversation(session, thread_id):
    """替身：会话未登记。"""
    return None


def _sign(payload: dict) -> str:
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm)


# ---------------------------------------------------------------------------
# 身份令牌
# ---------------------------------------------------------------------------
def test_visitor_identity_roundtrip():
    identity = Identity(kind=IDENTITY_KIND_VISITOR, id=new_visitor_id())
    resp = _make_response()
    set_identity_cookie(resp, identity)

    assert resp.cookies[IDENTITY_COOKIE]["httponly"] is True
    assert resp.cookies[IDENTITY_COOKIE]["samesite"] == "lax"

    token = resp.cookies[IDENTITY_COOKIE]["key"]
    parsed = read_identity(_make_request({IDENTITY_COOKIE: token}))
    assert parsed == identity


def test_user_identity_cookie_lives_longer_than_admin_jwt():
    """身份 cookie 有效期必须长于管理中心 JWT（1440 分钟），否则会话会「无故消失」。"""
    resp = _make_response()
    set_identity_cookie(resp, Identity(kind=IDENTITY_KIND_USER, id="admin"))
    assert resp.cookies[IDENTITY_COOKIE]["max_age"] > _settings.jwt_expire_minutes * 60


def test_expired_identity_is_ignored():
    token = _sign({"sub": "v-1", "typ": IDENTITY_KIND_VISITOR, "exp": int(time.time()) - 10})
    assert read_identity(_make_request({IDENTITY_COOKIE: token})) is None


def test_tampered_identity_is_ignored():
    token = _sign({"sub": "v-1", "typ": IDENTITY_KIND_VISITOR, "exp": int(time.time()) + 600})
    forged = token[:-3] + "aaa"
    assert read_identity(_make_request({IDENTITY_COOKIE: forged})) is None


def test_identity_without_typ_is_rejected():
    """缺 typ（如管理员 Bearer token 被拿来当身份 cookie）不应被当成身份。"""
    token = _sign({"sub": "admin", "exp": int(time.time()) + 600})
    assert read_identity(_make_request({IDENTITY_COOKIE: token})) is None


def test_decode_identity_token_rejects_garbage():
    assert decode_identity_token("not-a-jwt") is None


def test_ensure_identity_issues_visitor_only_once():
    first, issued = ensure_identity(_make_request())
    assert issued is True and first.is_visitor

    resp = _make_response()
    set_identity_cookie(resp, first)
    token = resp.cookies[IDENTITY_COOKIE]["key"]

    second, issued_again = ensure_identity(_make_request({IDENTITY_COOKIE: token}))
    assert issued_again is False and second == first


def test_identity_owns_matches_kind_and_id():
    owner = Identity(kind=IDENTITY_KIND_USER, id="admin")
    conv = SimpleNamespace(owner_kind=IDENTITY_KIND_USER, owner_id="admin")
    assert owner.owns(conv) is True
    # 同名但不同身份类型（匿名 uuid 恰好叫 admin）不算归属
    assert owner.owns(SimpleNamespace(owner_kind=IDENTITY_KIND_VISITOR, owner_id="admin")) is False


# ---------------------------------------------------------------------------
# 登录归并
# ---------------------------------------------------------------------------
async def test_login_claims_visitor_conversations(anon_client, monkeypatch, make_admin):
    captured: dict = {}

    async def fake_get_admin(session, username):
        return make_admin(username=username)

    async def fake_claim(session, visitor_id, username):
        captured["visitor_id"] = visitor_id
        captured["username"] = username
        return 3

    monkeypatch.setattr(repository, "get_admin_by_username", fake_get_admin)
    monkeypatch.setattr(admin_mod, "verify_password", lambda plain, hashed: True)
    monkeypatch.setattr(admin_mod, "claim_conversations", fake_claim)

    visitor = Identity(kind=IDENTITY_KIND_VISITOR, id=new_visitor_id())
    resp_obj = _make_response()
    set_identity_cookie(resp_obj, visitor)

    # 用 Cookie 头而非 client.cookies，避免 httpx 的「按请求设置 cookie」弃用告警
    token = resp_obj.cookies[IDENTITY_COOKIE]["key"]
    resp = await anon_client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "pw"},
        headers={"Cookie": f"{IDENTITY_COOKIE}={token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["claimed"] == 3
    assert captured["visitor_id"] == visitor.id
    assert captured["username"] == "admin"
    # 身份 cookie 换成 user
    assert IDENTITY_COOKIE in resp.cookies
    assert read_identity(_make_request({IDENTITY_COOKIE: resp.cookies[IDENTITY_COOKIE]})).kind == (
        IDENTITY_KIND_USER
    )


async def test_login_without_visitor_cookie_claims_nothing(
    anon_client, monkeypatch, make_admin
):
    async def fake_get_admin(session, username):
        return make_admin(username=username)

    async def fake_claim(session, visitor_id, username):
        raise AssertionError("无匿名身份时不应调用归并")

    monkeypatch.setattr(repository, "get_admin_by_username", fake_get_admin)
    monkeypatch.setattr(admin_mod, "verify_password", lambda plain, hashed: True)
    monkeypatch.setattr(admin_mod, "claim_conversations", fake_claim)

    resp = await anon_client.post(
        "/api/admin/login", json={"username": "admin", "password": "pw"}
    )
    assert resp.status_code == 200
    assert resp.json()["claimed"] == 0


async def test_logout_issues_fresh_visitor_identity(anon_client):
    resp = await anon_client.post("/api/admin/logout")
    assert resp.status_code == 200
    identity = read_identity(_make_request({IDENTITY_COOKIE: resp.cookies[IDENTITY_COOKIE]}))
    assert identity is not None and identity.is_visitor


# ---------------------------------------------------------------------------
# 会话目录 API 与越权校验
# ---------------------------------------------------------------------------
@pytest.fixture
async def conv_client():
    """会话目录客户端：复用 conftest 的 anon_client（DB 会话已被替身）。"""
    from a2a_gateway.database import get_session
    from a2a_gateway.main import app
    import httpx

    async def _fake_session():
        yield None

    app.dependency_overrides[get_session] = _fake_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def test_identity_endpoint_issues_cookie(conv_client):
    resp = await conv_client.post("/api/chat/identity")
    assert resp.status_code == 200
    assert resp.json()["kind"] == IDENTITY_KIND_VISITOR
    assert IDENTITY_COOKIE in resp.cookies


async def test_list_conversations_returns_own_only(conv_client, monkeypatch):
    seen: dict = {}

    async def fake_list(session, identity, slug):
        seen["identity"] = identity
        seen["slug"] = slug
        return [
            SimpleNamespace(
                thread_id="t1",
                agent_slug="/",
                title="你好",
                created_at="2026-09-14T00:00:00+00:00",
                updated_at="2026-09-14T00:00:00+00:00",
            )
        ]

    monkeypatch.setattr(chat_mod, "list_conversations", fake_list)

    resp = await conv_client.get("/api/chat/conversations", params={"slug": "/"})
    assert resp.status_code == 200
    assert resp.json()[0]["thread_id"] == "t1"
    assert seen["identity"].is_visitor
    assert seen["slug"] == "/"


async def test_history_of_other_owner_is_404(conv_client, monkeypatch):
    async def fake_get_conversation(session, thread_id):
        return SimpleNamespace(owner_kind=IDENTITY_KIND_USER, owner_id="someone-else")

    monkeypatch.setattr(chat_mod, "get_conversation", fake_get_conversation)

    resp = await conv_client.get("/api/chat/history", params={"thread_id": "t1"})
    assert resp.status_code == 404


async def test_history_of_unregistered_thread_is_allowed(conv_client, monkeypatch):
    """未登记的 thread（回填前的历史数据）必须放行，否则升级会打断现有用户。"""

    class FakeCheckpointer:
        async def aget_tuple(self, config):
            return None

    async def fake_checkpointer():
        return FakeCheckpointer()

    monkeypatch.setattr(chat_mod, "get_conversation", _no_conversation)
    monkeypatch.setattr(chat_mod, "get_checkpointer", fake_checkpointer)
    monkeypatch.setattr(chat_mod, "upsert_conversation", lambda *a, **kw: None)

    resp = await conv_client.get("/api/chat/history", params={"thread_id": "legacy"})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_delete_conversation_requires_ownership(conv_client, monkeypatch):
    async def fake_delete(session, identity, thread_id):
        return False

    monkeypatch.setattr(chat_mod, "delete_conversation", fake_delete)

    resp = await conv_client.delete("/api/chat/conversations/t1")
    assert resp.status_code == 404


async def test_import_conversations_reports_count(conv_client, monkeypatch):
    async def fake_import(session, identity, slug, items):
        return len(items)

    monkeypatch.setattr(chat_mod, "import_conversations", fake_import)

    resp = await conv_client.post(
        "/api/chat/conversations/import",
        json={"slug": "/", "items": [{"thread_id": "t1", "title": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"imported": 1}
