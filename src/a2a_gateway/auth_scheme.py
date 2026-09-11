"""外部服务鉴权方式（A2A 目标与 MCP 服务共用）。

支持的鉴权方式：
- none   ：无鉴权
- bearer ：Authorization: Bearer <token>（默认，兼容历史配置）
- header ：<自定义头名>: <token>
- query  ：URL 查询参数 ?<参数名>=<token>
- basic  ：Authorization: Basic base64(<用户名>:<token>)

约定：`token` 字段统一承载密钥，不同方式只是"把密钥放到哪里"不同：
- bearer → 固定 Authorization 头
- header → auth_name 作为头名
- query  → auth_name 作为参数名
- basic  → auth_name 作为用户名
"""

import base64
from urllib.parse import urlencode, urlsplit, urlunsplit

AUTH_TYPES: tuple[str, ...] = ("none", "bearer", "header", "query", "basic")

DEFAULT_AUTH_TYPE = "bearer"
DEFAULT_HEADER_NAME = "Authorization"
DEFAULT_QUERY_PARAM = "access_token"

# stdio 传输无法携带 HTTP 头，改为注入子进程环境变量供本地 MCP 进程自行读取
STDIO_TOKEN_ENV = "MCP_AUTH_TOKEN"
STDIO_TYPE_ENV = "MCP_AUTH_TYPE"
STDIO_NAME_ENV = "MCP_AUTH_NAME"


def build_headers(auth_type: str, auth_name: str, token: str) -> dict[str, str]:
    """按鉴权方式生成 HTTP 请求头；none / query 返回空字典。"""
    if auth_type == "none" or not token:
        return {}
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {token}"}
    if auth_type == "header":
        return {auth_name.strip() or DEFAULT_HEADER_NAME: token}
    if auth_type == "basic":
        raw = f"{auth_name or ''}:{token}".encode("utf-8")
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}
    # query 方式走 URL 参数，不放在请求头
    return {}


def apply_query_auth(url: str, auth_type: str, auth_name: str, token: str) -> str:
    """query 方式：把密钥作为查询参数拼到 URL 上；其余方式原样返回。"""
    if auth_type != "query" or not token:
        return url
    param = auth_name.strip() or DEFAULT_QUERY_PARAM
    parts = urlsplit(url)
    extra = urlencode({param: token})
    separator = "&" if parts.query else ""
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, parts.query + separator + extra, parts.fragment)
    )


def build_stdio_env(
    auth_type: str,
    auth_name: str,
    token: str,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """stdio 传输：无法携带 HTTP 头，改为注入环境变量供本地进程读取。"""
    env = dict(base_env or {})
    if auth_type != "none" and token:
        env.setdefault(STDIO_TOKEN_ENV, token)
        env.setdefault(STDIO_TYPE_ENV, auth_type)
        if auth_name and auth_type in ("header", "query"):
            env.setdefault(STDIO_NAME_ENV, auth_name)
    return env


def describe_auth(auth_type: str, auth_name: str) -> str:
    """给界面/日志用的简短说明。"""
    if auth_type == "none":
        return "无鉴权"
    if auth_type == "bearer":
        return "Bearer Token"
    if auth_type == "header":
        return f"自定义头 {auth_name or DEFAULT_HEADER_NAME}"
    if auth_type == "query":
        return f"查询参数 {auth_name or DEFAULT_QUERY_PARAM}"
    if auth_type == "basic":
        return f"Basic（用户 {auth_name or '-'}）"
    return auth_type
