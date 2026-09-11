"""工具层：将 A2A / MCP 调用封装为标准 LangChain 工具。

- `a2a_call`：调用绑定的 A2A 目标（核心工具，所有 Agent 默认启用）
- `mcp_call`：调用 Agent 已启用的 MCP 服务上的工具（仅当勾选了 MCP 服务时出现）
- 其它可选工具（网页搜索等）按 enabled_tools 勾选启用
"""

from collections.abc import Callable
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .a2a_client import A2AClientWrapper, A2ATargetError
from .mcp_client import call_tool as mcp_invoke
from .mcp_client import connection_from_snapshot, format_tools_for_prompt
from .notifier import notify_alert


class A2ACallArgs(BaseModel):
    message: str = Field(description="要发送给 A2A 目标 Agent 的消息内容")


def make_a2a_tool(wrapper: A2AClientWrapper) -> StructuredTool:
    """构造绑定到指定 A2A 目标的调用工具。"""

    async def _acall(message: str) -> str:
        """向绑定的 A2A 目标发送消息并聚合返回文本。"""
        chunks: list[str] = []
        try:
            async for chunk in wrapper.stream_message(message):
                chunks.append(chunk)
        except A2ATargetError as e:
            await notify_alert(
                "A2A 调用失败",
                f"target={wrapper.target.url} kind={e.kind} error={e.detail}",
            )
            return f"A2A 调用失败：{e}"
        return "".join(chunks) if chunks else "（A2A 目标未返回内容）"

    def _call(message: str) -> str:
        raise RuntimeError("a2a_call 仅支持异步调用")

    return StructuredTool.from_function(
        coroutine=_acall,
        func=_call,
        name="a2a_call",
        description="调用绑定的远端 A2A Agent（如 Hermes）处理用户请求，返回其回复内容。当问题需要远端 Agent 能力时调用。",
        args_schema=A2ACallArgs,
    )


# ---------------------------------------------------------------------------
# MCP（Registry 中勾选的服务）
# ---------------------------------------------------------------------------
class McpCallArgs(BaseModel):
    server: str = Field(description="MCP 服务名称，必须取自下方「可用 MCP 服务」清单")
    tool: str = Field(description="要调用的 MCP 工具名称")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="工具入参，JSON 对象；无入参时传 {}"
    )


def make_mcp_call_tool(
    servers: list[dict], tool_index: dict[str, list[dict]] | None = None
) -> StructuredTool:
    """构造 MCP 调用工具。

    @param servers     Agent 已勾选的 MCP 服务快照（repository.mcp_server_snapshot 的列表）
    @param tool_index  服务名 → 工具清单（构建时尽力探测得到，用于把可用工具写进说明）
    """
    tool_index = tool_index or {}
    known = [s.get("name") or "" for s in servers if s.get("name")]

    def _description() -> str:
        if not known:
            return "调用 MCP 服务上的工具（当前 Agent 未启用任何 MCP 服务）。"
        head = "调用已启用的 MCP 服务上的工具。server 必须取自下列清单："
        body = [format_tools_for_prompt(s.get("name") or "", tool_index.get(s.get("name") or "", [])) for s in servers]
        tail = (
            "用法：server 指定服务，tool 指定工具名，arguments 为工具入参（JSON 对象）。"
            "若不确定入参结构，可根据工具描述尝试调用，失败后会返回错误信息。"
        )
        return "\n".join([head, *body, tail])

    async def _acall(server: str, tool: str, arguments: dict[str, Any] | None = None) -> str:
        match = next((s for s in servers if (s.get("name") or "") == server), None)
        if match is None:
            return f"未找到 MCP 服务「{server}」。当前可用：{', '.join(known) or '（无）'}"
        conn = connection_from_snapshot(match)
        return await mcp_invoke(conn, tool, arguments)

    def _call(server: str, tool: str, arguments: dict[str, Any] | None = None) -> str:
        raise RuntimeError("mcp_call 仅支持异步调用")

    return StructuredTool.from_function(
        coroutine=_acall,
        func=_call,
        name="mcp_call",
        description=_description(),
        args_schema=McpCallArgs,
    )


# 可选工具注册表（按待讨论问题 6：每 Agent 可勾选）
class WebSearchArgs(BaseModel):
    query: str = Field(description="搜索关键词")


def make_web_search_tool() -> StructuredTool:
    """占位网页搜索工具（MVP 阶段返回提示，二期接入真实搜索）。"""

    async def _acall(query: str) -> str:
        return f"（网页搜索工具未接入，查询：{query}）"

    return StructuredTool.from_function(
        coroutine=_acall,
        name="web_search",
        description="在网页上搜索信息并返回结果（MVP 阶段占位）。",
        args_schema=WebSearchArgs,
    )


# 可选工具名 → 构造函数
OPTIONAL_TOOLS: dict[str, Callable[[], StructuredTool]] = {
    "web_search": make_web_search_tool,
}
