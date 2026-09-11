"""鉴权方式测试（A2A 目标与 MCP 服务共用同一套）。"""

import pytest

from a2a_gateway.auth_scheme import (
    apply_query_auth,
    build_headers,
    build_stdio_env,
    describe_auth,
)
from a2a_gateway.schemas import validate_auth


# ---------------------------------------------------------------------------
# 请求头
# ---------------------------------------------------------------------------
def test_bearer_uses_authorization_header():
    assert build_headers("bearer", "", "tok") == {"Authorization": "Bearer tok"}


def test_header_uses_custom_name():
    assert build_headers("header", "X-Api-Key", "tok") == {"X-Api-Key": "tok"}


def test_header_falls_back_to_authorization():
    assert build_headers("header", "  ", "tok") == {"Authorization": "tok"}


def test_basic_encodes_username_and_token():
    import base64

    headers = build_headers("basic", "alice", "secret")
    assert headers["Authorization"].startswith("Basic ")
    assert base64.b64decode(headers["Authorization"][6:]).decode() == "alice:secret"


def test_none_and_query_produce_no_header():
    assert build_headers("none", "", "tok") == {}
    assert build_headers("query", "token", "tok") == {}


def test_missing_token_produces_no_header():
    """没填密钥时不应生成任何鉴权头（等价于无鉴权）。"""
    assert build_headers("bearer", "", "") == {}


# ---------------------------------------------------------------------------
# query 参数
# ---------------------------------------------------------------------------
def test_query_appends_param():
    assert apply_query_auth("http://m/mcp", "query", "token", "abc") == "http://m/mcp?token=abc"


def test_query_uses_default_param_name():
    assert apply_query_auth("http://m/mcp", "query", "", "abc") == "http://m/mcp?access_token=abc"


def test_query_preserves_existing_params():
    assert apply_query_auth("http://m/mcp?a=1", "query", "k", "v") == "http://m/mcp?a=1&k=v"


def test_non_query_leaves_url_unchanged():
    assert apply_query_auth("http://m/mcp", "bearer", "", "abc") == "http://m/mcp"


# ---------------------------------------------------------------------------
# stdio 环境变量
# ---------------------------------------------------------------------------
def test_stdio_injects_token_env():
    env = build_stdio_env("bearer", "", "tok", {"PATH": "/bin"})
    assert env["PATH"] == "/bin"
    assert env["MCP_AUTH_TOKEN"] == "tok"
    assert env["MCP_AUTH_TYPE"] == "bearer"


def test_stdio_none_adds_nothing():
    assert build_stdio_env("none", "", "tok", {}) == {}


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------
def test_validate_auth_accepts_valid():
    validate_auth("none", "", "")
    validate_auth("bearer", "", "tok")
    validate_auth("header", "X-Key", "tok")
    validate_auth("query", "token", "tok")
    validate_auth("basic", "user", "pass")


def test_validate_auth_requires_name_for_header_query_basic():
    for auth_type in ("header", "query", "basic"):
        with pytest.raises(ValueError):
            validate_auth(auth_type, "  ", "tok")


def test_validate_auth_requires_token_when_not_none():
    with pytest.raises(ValueError):
        validate_auth("bearer", "", " ")


def test_validate_auth_rejects_unknown_type():
    with pytest.raises(ValueError):
        validate_auth("oauth2", "", "tok")


def test_describe_auth_readable():
    assert "无鉴权" in describe_auth("none", "")
    assert "Bearer" in describe_auth("bearer", "")
    assert "X-Key" in describe_auth("header", "X-Key")
