"""MCP 客户端纯函数、MCP 工具绑定与 A2A 工具构造测试（不发起真实连接）。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

from a2a_gateway import mcp_client as mc
from a2a_gateway.graph import DEFAULT_SYSTEM_PROMPT, build_tools
from a2a_gateway.mcp_client import _format_error, connection_from_snapshot, format_tools_for_prompt
from a2a_gateway.repository import mcp_server_snapshot
from a2a_gateway.schemas import A2ATarget
from a2a_gateway.tools import json_schema_to_model, make_a2a_tools, make_mcp_call_tool, make_mcp_tools


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
        "token": "tok",
        "auth_type": "bearer",
        "auth_name": "",
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
        "token": "tok",
        "auth_type": "bearer",
        "auth_name": "",
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
    assert conn.auth_type == "bearer"


def test_connection_from_snapshot_keeps_values():
    conn = connection_from_snapshot(
        {
            "name": "X",
            "transport": "sse",
            "url": "http://m/sse",
            "env": {"A": "B"},
            "token": "t",
            "auth_type": "header",
            "auth_name": "X-Key",
        }
    )
    assert conn.transport == "sse"
    assert conn.env == {"A": "B"}
    assert (conn.token, conn.auth_type, conn.auth_name) == ("t", "header", "X-Key")


# ---------------------------------------------------------------------------
# 说明文本
# ---------------------------------------------------------------------------
def test_format_tools_for_prompt_lists_tools():
    out = format_tools_for_prompt("S", [{"name": "echo", "description": "回声工具"}])
    assert "echo" in out and "回声工具" in out


def test_format_tools_for_prompt_when_empty():
    assert "暂无可用工具" in format_tools_for_prompt("S", [])


# ---------------------------------------------------------------------------
# MCP 工具绑定（把服务上的每个工具绑成 Agent 可用工具）
# ---------------------------------------------------------------------------
def test_json_schema_to_model_maps_types_and_required():
    model = json_schema_to_model(
        "t",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "文本"},
                "n": {"type": "integer"},
                "flag": {"type": "boolean"},
            },
            "required": ["text"],
        },
    )
    fields = model.model_fields
    assert fields["text"].annotation == str
    assert fields["text"].is_required() is True
    # 非必填项默认 None
    assert fields["n"].default is None
    assert fields["flag"].annotation == bool | None


def test_json_schema_to_model_falls_back_to_arguments():
    """无 properties 时退化为单个 arguments 字段，保证工具仍可调用。"""
    model = json_schema_to_model("t", {"type": "object"})
    assert set(model.model_fields) == {"arguments"}


def test_make_mcp_tools_binds_each_tool():
    tools = make_mcp_tools(
        {"name": "Local MCP", "transport": "stdio", "command": "python"},
        [
            {"name": "echo", "description": "回声", "inputSchema": {"type": "object"}},
            {
                "name": "sum",
                "description": "求和",
                "inputSchema": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}},
                    "required": ["a"],
                },
            },
        ],
    )
    assert [t.name for t in tools] == ["mcp_Local_MCP__echo", "mcp_Local_MCP__sum"]
    # 描述里带上来源服务，便于模型理解
    assert "回声" in tools[0].description and "Local MCP" in tools[0].description
    assert set(tools[1].args.keys()) == {"a"}


def test_make_mcp_tools_skips_unnamed_tools():
    tools = make_mcp_tools({"name": "S"}, [{"name": "  "}, {"name": "ok"}])
    assert [t.name for t in tools] == ["mcp_S__ok"]


# ---------------------------------------------------------------------------
# A2A 工具：单/多目标与描述注入
# ---------------------------------------------------------------------------
def test_make_a2a_tools_single_target_keeps_legacy_name():
    tools, wrappers = make_a2a_tools(
        [A2ATarget(url="http://h:9900/", token="t", name="Hermes", description="通用助手")]
    )
    assert [t.name for t in tools] == ["a2a_call"]
    # 关键：描述必须进入工具说明，模型才能判断该不该调它
    assert "通用助手" in tools[0].description
    assert len(wrappers) == 1


def test_make_a2a_tools_multiple_targets_are_distinct():
    tools, wrappers = make_a2a_tools(
        [
            A2ATarget(url="http://a/", name="weather", description="擅长查天气"),
            A2ATarget(url="http://b/", name="flight", description="擅长订机票"),
        ]
    )
    assert [t.name for t in tools] == ["a2a_call__weather", "a2a_call__flight"]
    assert "擅长查天气" in tools[0].description
    assert "擅长订机票" in tools[1].description
    # 描述必须各自独立，否则模型无法区分
    assert "擅长订机票" not in tools[0].description
    assert len(wrappers) == 2


def test_make_a2a_tools_guards_name_collisions():
    """中文名规整成 ASCII 后可能重名，必须用序号区分，否则工具会互相覆盖。"""
    tools, _ = make_a2a_tools(
        [
            A2ATarget(url="http://a/", name="天气服务", description="查天气"),
            A2ATarget(url="http://b/", name="机票服务", description="订机票"),
        ]
    )
    assert len(tools) == 2
    assert len({t.name for t in tools}) == 2


def test_make_a2a_tools_ignores_empty_url():
    tools, wrappers = make_a2a_tools([A2ATarget(url="  ", name="x")])
    assert tools == [] and wrappers == []


def test_make_a2a_tools_without_description_uses_default():
    tools, _ = make_a2a_tools([A2ATarget(url="http://h/")])
    assert "a2a_call" == tools[0].name
    assert tools[0].description  # 仍有可用说明


# ---------------------------------------------------------------------------
# 图工具集
# ---------------------------------------------------------------------------
def test_build_tools_without_a2a_or_mcp(make_agent):
    assert build_tools(make_agent(), []) == []


def test_build_tools_binds_mcp_tools_when_index_available(make_agent):
    tools = build_tools(
        make_agent(),
        [],
        mcp_servers=[{"name": "S1"}],
        mcp_tool_index={
            "S1": [{"name": "echo", "description": "回声", "inputSchema": {"type": "object"}}]
        },
    )
    assert [t.name for t in tools] == ["mcp_S1__echo"]


def test_build_tools_falls_back_to_mcp_call_when_no_tools_probed(make_agent):
    """服务不可达、拿不到工具清单时，退化为通用 mcp_call 保留可用能力。"""
    tools = build_tools(make_agent(), [], mcp_servers=[{"name": "S1"}])
    assert [t.name for t in tools] == ["mcp_call"]


def test_build_tools_combines_a2a_optional_and_mcp(make_agent):
    a2a_tools, _ = make_a2a_tools([A2ATarget(url="http://h/", name="H")])
    tools = build_tools(
        make_agent(enabled_tools=["web_search"]),
        a2a_tools,
        mcp_servers=[{"name": "S1"}],
        mcp_tool_index={
            "S1": [{"name": "echo", "description": "回声", "inputSchema": {"type": "object"}}]
        },
    )
    assert [t.name for t in tools] == ["a2a_call", "web_search", "mcp_S1__echo"]


def test_build_tools_ignores_reserved_core_tool_names(make_agent):
    """a2a_call / mcp_call 由本模块直接构造，enabled_tools 里出现也只构造一次。"""
    agent = make_agent(enabled_tools=["a2a_call", "mcp_call", "web_search"])
    names = [t.name for t in build_tools(agent, [])]
    assert names == ["web_search"]


def test_make_mcp_call_tool_args_schema():
    tool = make_mcp_call_tool([{"name": "S1"}])
    assert set(tool.args.keys()) == {"server", "tool", "arguments"}


# ---------------------------------------------------------------------------
# 传输层兼容性（回归）
# ---------------------------------------------------------------------------
async def test_streamable_http_accepts_two_item_streams(monkeypatch):
    """mcp 2.x 的 streamable_http_client 只 yield (read, write)。

    早期版本会 yield 三个值，若按三元组解包会在 2.x 下抛
    "ValueError: not enough values to unpack"，且被 anyio 包成 ExceptionGroup 难以定位。
    """
    captured: dict = {}

    @asynccontextmanager
    async def fake_streamable_http_client(url, http_client=None, **kwargs):
        yield ("read-stream", "write-stream")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def initialize(self):
            return SimpleNamespace(serverInfo=SimpleNamespace(name="Fake"))

        async def list_tools(self):
            return SimpleNamespace(tools=[])

    def fake_session_factory(read, write, **kwargs):
        captured["streams"] = (read, write)
        return FakeSession()

    monkeypatch.setattr(mc, "streamable_http_client", fake_streamable_http_client)
    monkeypatch.setattr(mc, "ClientSession", fake_session_factory)

    conn = mc.McpConnection(name="S", transport="streamable_http", url="http://m/mcp")
    ok, message = await mc.test_connection(conn)
    assert ok is True, message
    assert captured["streams"] == ("read-stream", "write-stream")


async def test_streamable_http_accepts_three_item_streams(monkeypatch):
    """旧版 mcp 会多 yield 一个 get_session_id，按下标取值同样兼容。"""
    captured: dict = {}

    @asynccontextmanager
    async def fake_streamable_http_client(url, http_client=None, **kwargs):
        yield ("read-stream", "write-stream", "get-session-id")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def initialize(self):
            return SimpleNamespace(serverInfo=SimpleNamespace(name="Fake"))

        async def list_tools(self):
            return SimpleNamespace(tools=[])

    def fake_session_factory(read, write, **kwargs):
        captured["streams"] = (read, write)
        return FakeSession()

    monkeypatch.setattr(mc, "streamable_http_client", fake_streamable_http_client)
    monkeypatch.setattr(mc, "ClientSession", fake_session_factory)

    conn = mc.McpConnection(name="S", transport="streamable_http", url="http://m/mcp")
    ok, message = await mc.test_connection(conn)
    assert ok is True, message
    assert captured["streams"] == ("read-stream", "write-stream")


def test_format_error_unwraps_exception_group():
    """异常必须展开子异常，否则只看到 "unhandled errors in a TaskGroup"。"""
    inner = ValueError("not enough values to unpack (expected 3, got 2)")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    text = _format_error(group)
    assert "ExceptionGroup" in text
    assert "not enough values to unpack" in text


def test_format_error_keeps_plain_exception():
    assert "boom" in _format_error(RuntimeError("boom"))


def test_default_prompt_mentions_selection_guidance():
    assert "a2a_call" in DEFAULT_SYSTEM_PROMPT
    assert "MCP" in DEFAULT_SYSTEM_PROMPT
