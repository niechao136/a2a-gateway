"""Agent 绑定合并逻辑测试：注册表勾选 + 手动绑定并存。

回归背景（_merge_bindings 曾名 _merge_manual_items）：
旧实现只返回手动条目、丢弃注册表解析结果，导致每次保存 Agent
都会把勾选的注册表绑定从 a2a_targets / mcp_servers 快照中清空
（/news 绑定 2 个 A2A 后快照为空，Agent 没有任何 a2a 工具可用）。
"""

from a2a_gateway.repository import _merge_bindings


def test_resolved_always_kept_with_empty_manual():
    """显式传空手动列表（前端正常保存路径）时，注册表绑定必须保留。"""
    resolved = [
        {"url": "http://news/", "token": "", "name": "news"},
        {"url": "http://devops/", "token": "t", "name": "devops"},
    ]

    merged = _merge_bindings(resolved, [], existing=[], key="url")

    assert merged == resolved


def test_manual_none_preserves_existing():
    """未显式提供手动列表（老客户端仅改勾选）时，保留既有手动条目。"""
    existing = [
        {"url": "http://registered/", "token": "a"},
        {"url": "http://manual/", "token": "b"},
    ]
    resolved = [{"url": "http://registered/", "token": "a"}]

    merged = _merge_bindings(resolved, None, existing, key="url")

    assert merged == [
        {"url": "http://registered/", "token": "a"},
        {"url": "http://manual/", "token": "b"},
    ]


def test_manual_list_replaces_and_dedupes():
    """显式提供手动列表时整体替换，且与注册表条目重复的以注册表为准。"""
    existing = [{"url": "http://old/", "token": "x"}]
    resolved = [{"url": "http://registered/", "token": "a"}]
    manual = [
        {"url": "http://registered/", "token": "dup"},  # 与注册表重复 → 丢弃
        {"url": "http://new1/", "token": "1"},
        {"url": "http://new1/", "token": "dup-in-manual"},  # 手动内部重复 → 去重
        {"url": "http://new2/", "token": "2"},
    ]

    merged = _merge_bindings(resolved, manual, existing, key="url")

    assert merged == [
        {"url": "http://registered/", "token": "a"},
        {"url": "http://new1/", "token": "1"},
        {"url": "http://new2/", "token": "2"},
    ]


def test_manual_mcp_keyed_by_name():
    """MCP 绑定按 name 去重，注册表结果保留在前。"""
    resolved = [{"name": "registered", "transport": "sse", "url": "http://r/"}]
    manual = [
        {"name": "registered", "transport": "sse", "url": "http://dup/"},
        {"name": "manual-mcp", "transport": "stdio", "command": "uvx mcp"},
    ]

    merged = _merge_bindings(resolved, manual, [], key="name")

    assert merged == [
        {"name": "registered", "transport": "sse", "url": "http://r/"},
        {"name": "manual-mcp", "transport": "stdio", "command": "uvx mcp"},
    ]


def test_manual_mcp_requires_name():
    import pytest
    from pydantic import ValidationError

    from a2a_gateway.schemas import ManualMcpServer

    with pytest.raises(ValidationError):
        ManualMcpServer(name="  ", transport="sse", url="http://x/")

    # stdio 必须有命令、远程必须有 URL
    with pytest.raises(ValidationError):
        ManualMcpServer(name="a", transport="stdio", command="")
    with pytest.raises(ValidationError):
        ManualMcpServer(name="a", transport="streamable_http", url="")
