"""MCP 客户端纯函数与 mcp_call 工具构造测试（不发起真实连接）。"""

from types import SimpleNamespace

from a2a_gateway.graph import DEFAULT_SYSTEM_PROMPT, build_tools
from a2a_gateway.mcp_client import connection_from_snapshot, format_tools_for_prompt
from a2a_gateway.repository import mcp_server_snapshot
from a2a_gateway.tools import make_mcp_call_tool


def _server(**kw):
    base = {
        "id": 1,
        "name": "Local MCP",
        "description": "",
        "transport": "stdio",
        "url": "",
        "command": "python",
        "args": ["-m", "server"],
        "env": {"K": "V"},
        "enabled": True,
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# 快照与连接参数
# ---------------------------------------------------------------------------
def test_mcp_server_snapshot_fields():
    snap = mcp_server_snapshot(_server())
    assert snap == {
        "name": "Local MCP",
        "transport": "stdio",
        "url": "",
        "command": "python",
        "args": ["-m", "server"],
        "env": {"K": "V"},
    }


def test_mcp_server_snapshot_tolerates_none_collections():
    snap = mcp_server_snapshot(_server(args=None, env=None))
    assert snap["args"] == []
    assert snap["env"] == {}


def test_connection_from_snapshot_applies_defaults():
    conn = connection_from_snapshot({"name": "X"})
    assert conn.name == "X"
    assert conn.transport == "streamable_http"
    assert (conn.url, conn.command, conn.args, conn.env) == ("", "", [], {})


def test_connection_from_snapshot_keeps_values():
    conn = connection_from_snapshot(
        {"name": "X", "transport": "sse", "url": "http://m/sse", "env": {"A": "B"}}
    )
    assert conn.transport == "sse"
    assert conn.url == "http://m/sse"
    assert conn.env == {"A": "B"}


# ---------------------------------------------------------------------------
# 说明文本
# ---------------------------------------------------------------------------
def test_format_tools_for_prompt_lists_tools():
    out = format_tools_for_prompt("S", [{"name": "echo", "description": "回声工具"}])
    assert "S" in out
    assert "echo" in out
    assert "回声工具" in out


def test_format_tools_for_prompt_when_empty():
    assert "暂无可用工具" in format_tools_for_prompt("S", [])


# ---------------------------------------------------------------------------
# mcp_call 工具
# ---------------------------------------------------------------------------
def test_mcp_call_tool_description_lists_servers():
    tool = make_mcp_call_tool(
        [{"name": "S1", "transport": "streamable_http", "url": "http://m/mcp"}],
        {"S1": [{"name": "echo", "description": "回声"}]},
    )
    assert tool.name == "mcp_call"
    assert "S1" in tool.description
    assert "echo" in tool.description


def test_mcp_call_tool_description_without_servers():
    assert "未启用任何 MCP 服务" in make_mcp_call_tool([]).description


def test_mcp_call_tool_args_schema():
    tool = make_mcp_call_tool([{"name": "S1"}])
    assert set(tool.args.keys()) == {"server", "tool", "arguments"}


# ---------------------------------------------------------------------------
# 图工具集
# ---------------------------------------------------------------------------
def test_build_tools_omits_mcp_when_not_selected(make_agent):
    """未勾选 MCP 服务时不出现 mcp_call，避免给模型无意义的工具。"""
    names = [t.name for t in build_tools(make_agent(), object())]
    assert names == ["a2a_call"]


def test_build_tools_adds_mcp_when_selected(make_agent):
    names = [
        t.name
        for t in build_tools(make_agent(), object(), mcp_servers=[{"name": "S1"}])
    ]
    assert names == ["a2a_call", "mcp_call"]


def test_build_tools_ignores_reserved_core_tool_names(make_agent):
    """a2a_call / mcp_call 由 build_tools 直接构造，enabled_tools 里出现也只构造一次。"""
    agent = make_agent(enabled_tools=["a2a_call", "mcp_call", "web_search"])
    names = [t.name for t in build_tools(agent, object())]
    assert names == ["a2a_call", "web_search"]


def test_default_prompt_mentions_mcp():
    assert "mcp_call" in DEFAULT_SYSTEM_PROMPT
    assert "a2a_call" in DEFAULT_SYSTEM_PROMPT
