"""MCP 客户端封装：连通性测试、工具列表与工具调用。

设计要点：
- 采用「按需一次性会话」：每次调用建立连接 → 初始化 → 执行 → 关闭。
  这样无需在 Agent 图实例的生命周期内维护长连接，
  也不用处理连接保活、断线重建等复杂问题（代价是每次调用多一次握手）。
- 所有入口都带超时保护并收敛异常：MCP 失败不影响主对话流程。

传输方式（与 McpServer.transport 对应）：
- stdio           → 本地进程（command + args + env）
- sse             → 远端 SSE 端点
- streamable_http → 远端 Streamable HTTP 端点（推荐）
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from .auth_scheme import apply_query_auth, build_headers, build_stdio_env

logger = logging.getLogger(__name__)

# 连通性/工具列表（握手类）超时：短，避免拖慢图实例构建
PROBE_TIMEOUT = 8.0
# 单次工具调用超时：长，工具本身可能较慢
CALL_TIMEOUT = 60.0


@dataclass
class McpConnection:
    """一次 MCP 连接所需的全部参数。"""

    name: str = "MCP"
    transport: str = "streamable_http"
    url: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # 鉴权（与 A2A 目标共用同一套方式）
    token: str = ""
    auth_type: str = "bearer"
    auth_name: str = ""


def connection_from_snapshot(snapshot: dict[str, Any]) -> McpConnection:
    """由 Agent 上的 MCP 快照（repository.mcp_server_snapshot）构造连接参数。"""
    return McpConnection(
        name=snapshot.get("name") or "MCP",
        transport=snapshot.get("transport") or "streamable_http",
        url=snapshot.get("url") or "",
        command=snapshot.get("command") or "",
        args=list(snapshot.get("args") or []),
        env=dict(snapshot.get("env") or {}),
        token=snapshot.get("token") or "",
        auth_type=snapshot.get("auth_type") or "bearer",
        auth_name=snapshot.get("auth_name") or "",
    )


@asynccontextmanager
async def _open_session(conn: McpConnection):
    """按传输方式建立会话（退出时自动关闭连接与子进程）。

    鉴权按传输方式落到不同位置：
    - stdio           → 注入子进程环境变量（无法携带 HTTP 头）
    - sse             → headers 参数
    - streamable_http → 预置 headers 的 httpx 客户端（该传输不直接接受 headers）
    """
    headers = build_headers(conn.auth_type, conn.auth_name, conn.token)
    url = apply_query_auth(conn.url, conn.auth_type, conn.auth_name, conn.token)

    if conn.transport == "stdio":
        env = build_stdio_env(conn.auth_type, conn.auth_name, conn.token, conn.env)
        params = StdioServerParameters(command=conn.command, args=conn.args, env=env or None)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                yield session
    elif conn.transport == "sse":
        async with sse_client(url, headers=headers or None) as (read, write):
            async with ClientSession(read, write) as session:
                yield session
    else:
        # streamable_http 只接受 http_client，因此自建带鉴权头的 httpx 客户端
        http_client = httpx.AsyncClient(headers=headers or None, timeout=httpx.Timeout(60.0))
        try:
            async with streamable_http_client(url, http_client=http_client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    yield session
        finally:
            await http_client.aclose()


def _render_content(result: Any) -> str:
    """把 MCP 调用结果渲染为纯文本。"""
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
        else:
            try:
                parts.append(item.model_dump_json(exclude_none=True))
            except Exception:
                parts.append(str(item))
    output = "\n".join(parts).strip()
    if not output:
        return "（MCP 工具未返回内容）"
    if getattr(result, "isError", False):
        return f"MCP 工具返回错误：{output}"
    return output


async def test_connection(conn: McpConnection, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """测试连通性：握手 + 取一次工具列表。返回 (是否成功, 说明)。"""
    try:
        async with asyncio.timeout(timeout):
            async with _open_session(conn) as session:
                init = await session.initialize()
                server_name = (
                    getattr(getattr(init, "serverInfo", None), "name", None)
                    or conn.name
                )
                tools = await session.list_tools()
                return True, f"连接成功：{server_name}（可用工具 {len(tools.tools)} 个）"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("MCP 连通性测试失败 name=%s: %s", conn.name, exc)
        return False, f"连接失败：{type(exc).__name__}: {exc}"


async def list_tools(
    conn: McpConnection, timeout: float = PROBE_TIMEOUT
) -> tuple[bool, list[dict], str]:
    """列出工具。返回 (是否成功, [{"name","description"}], 说明)。"""
    try:
        async with asyncio.timeout(timeout):
            async with _open_session(conn) as session:
                await session.initialize()
                result = await session.list_tools()
                # 带上 inputSchema：用于把每个 MCP 工具真正绑定成 Agent 的可调用工具
                tools = [
                    {
                        "name": t.name,
                        "description": (getattr(t, "description", "") or "").strip(),
                        "inputSchema": getattr(t, "inputSchema", None) or {},
                    }
                    for t in result.tools
                ]
                return True, tools, f"共 {len(tools)} 个工具"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("MCP 工具列表获取失败 name=%s: %s", conn.name, exc)
        return False, [], f"获取工具列表失败：{type(exc).__name__}: {exc}"


async def call_tool(
    conn: McpConnection,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    timeout: float = CALL_TIMEOUT,
) -> str:
    """调用指定工具，返回渲染后的文本（失败时返回可读的错误说明）。"""
    try:
        async with asyncio.timeout(timeout):
            async with _open_session(conn) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments or {})
                return _render_content(result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "MCP 工具调用失败 name=%s tool=%s: %s", conn.name, tool_name, exc
        )
        return f"MCP 调用失败（{conn.name}·{tool_name}）：{type(exc).__name__}: {exc}"


def format_tools_for_prompt(server_name: str, tools: list[dict]) -> str:
    """把某个 MCP 服务的工具列表渲染进 mcp_call 工具的说明里。"""
    if not tools:
        return f"- {server_name}：暂无可用工具（服务可能未启动或工具列表获取失败）"
    lines = [f"- {server_name}："]
    for tool in tools:
        desc = tool.get("description") or "无描述"
        lines.append(f"    · {tool['name']} —— {desc}")
    return "\n".join(lines)
