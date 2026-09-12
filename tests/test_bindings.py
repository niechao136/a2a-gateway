"""Agent 绑定合并逻辑测试：注册表勾选 + 手动绑定并存。"""

from a2a_gateway.repository import _merge_manual_items


def test_manual_none_preserves_existing():
    """未显式提供手动列表（老客户端仅改勾选）时，保留既有手动条目。"""
    existing = [
        {"url": "http://registered/", "token": "a"},
        {"url": "http://manual/", "token": "b"},
    ]
    resolved = [{"url": "http://registered/", "token": "a"}]

    merged = _merge_manual_items(existing, resolved, None, key="url")

    assert merged == [{"url": "http://manual/", "token": "b"}]


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

    merged = _merge_manual_items(existing, resolved, manual, key="url")

    assert merged == [
        {"url": "http://new1/", "token": "1"},
        {"url": "http://new2/", "token": "2"},
    ]


def test_manual_mcp_keyed_by_name():
    """MCP 绑定按 name 去重。"""
    resolved = [{"name": "registered", "transport": "sse", "url": "http://r/"}]
    manual = [
        {"name": "registered", "transport": "sse", "url": "http://dup/"},
        {"name": "manual-mcp", "transport": "stdio", "command": "uvx mcp"},
    ]

    merged = _merge_manual_items([], resolved, manual, key="name")

    assert merged == [{"name": "manual-mcp", "transport": "stdio", "command": "uvx mcp"}]


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
