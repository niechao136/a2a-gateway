"""MCP 工具绑定回归测试。

曾出现闭包晚绑定 bug：循环内定义的 `_acall` 捕获的是变量 `tool_name`
本身，循环结束后所有绑定工具都指向最后一个工具名——调用 A 工具时
实际执行了 B 工具（如调用 `get_node_overview` 却执行了 `query_audit_logs`）。
"""

from a2a_gateway import tools as tools_mod
from a2a_gateway.tools import make_mcp_tools


async def test_mcp_tools_bind_own_tool_name(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_invoke(conn, tool_name, arguments, timeout=60.0):
        calls.append((tool_name, arguments))
        return f"ok:{tool_name}"

    monkeypatch.setattr(tools_mod, "mcp_invoke", fake_invoke)

    server = {"name": "devops-43", "transport": "streamable_http", "url": "http://m/mcp"}
    tools = [
        {
            "name": "get_node_overview",
            "description": "节点概览",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "query_audit_logs",
            "description": "审计日志",
            "inputSchema": {
                "type": "object",
                "properties": {"project_name": {"type": "string"}},
                "required": ["project_name"],
            },
        },
    ]

    bound = make_mcp_tools(server, tools)
    assert [t.name for t in bound] == [
        "mcp_devops-43__get_node_overview",
        "mcp_devops-43__query_audit_logs",
    ]

    # 每个绑定工具必须转发自己的真实工具名，而不是循环末尾的那个
    assert await bound[0].ainvoke({}) == "ok:get_node_overview"
    assert await bound[1].ainvoke({"project_name": "a2a-gateway"}) == "ok:query_audit_logs"

    assert [c[0] for c in calls] == ["get_node_overview", "query_audit_logs"]
    assert calls[1][1] == {"project_name": "a2a-gateway"}


async def test_mcp_tool_forwarded_args_skip_none(monkeypatch):
    """未填写的可选参数（None）不应转发给 MCP 服务。"""
    calls: list[tuple[str, dict]] = []

    async def fake_invoke(conn, tool_name, arguments, timeout=60.0):
        calls.append((tool_name, arguments))
        return "ok"

    monkeypatch.setattr(tools_mod, "mcp_invoke", fake_invoke)

    server = {"name": "srv", "url": "http://m/mcp"}
    tools = [
        {
            "name": "echo",
            "inputSchema": {
                "type": "object",
                "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            },
        },
    ]

    (tool,) = make_mcp_tools(server, tools)
    await tool.ainvoke({"a": "x", "b": None})
    assert calls == [("echo", {"a": "x"})]
