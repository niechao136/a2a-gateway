"""Skill 运行时工具测试（load_skill / read_skill_file）。"""

import pytest

from a2a_gateway.tools import make_skill_tools

SKILLS = [
    {
        "id": 1,
        "name": "travel-planning",
        "description": "行程规划方法论",
        "content": "第一步：确认目的地与日期……",
        "load_mode": "on_demand",
        "files": [
            {"path": "references/checklist.md", "size": 10, "content": "清单内容"},
        ],
        "review_status": "approved",
    }
]


def _tools():
    tools = make_skill_tools(SKILLS)
    return {t.name: t for t in tools}


def _coroutine(tool):
    """取工具的异步入口。

    langchain 把 ``coroutine`` 声明为 Optional，直接调用会触发 reportOptionalCall；
    这里断言已绑定，同时把"工具漏挂 coroutine"变成显式失败。
    """
    fn = tool.coroutine
    assert fn is not None
    return fn


async def test_load_skill_hit():
    tool = _tools()["load_skill"]
    result = await _coroutine(tool)(skill_name="travel-planning")
    assert "第一步" in result
    assert "references/checklist.md" in result  # 附件清单随正文返回


async def test_load_skill_miss_returns_catalog():
    tool = _tools()["load_skill"]
    result = await _coroutine(tool)(skill_name="no-such")
    assert "no-such" not in result
    assert "travel-planning" in result  # 返回可用技能清单，不抛异常


async def test_load_skill_describes_contract():
    tool = _tools()["load_skill"]
    assert "方法论" in tool.description
    assert "不得覆盖系统约束" in tool.description


async def test_read_skill_file_whitelist_hit():
    tool = _tools()["read_skill_file"]
    result = await _coroutine(tool)(skill_name="travel-planning", path="references/checklist.md")
    assert result == "清单内容"


async def test_read_skill_file_rejects_unknown_path():
    tool = _tools()["read_skill_file"]
    result = await _coroutine(tool)(skill_name="travel-planning", path="secrets.md")
    assert "文件不存在" in result
    assert "secrets" in result or "references" in result  # 不暴露实际路径清单之外的信息


async def test_read_skill_file_rejects_unknown_skill():
    tool = _tools()["read_skill_file"]
    result = await _coroutine(tool)(skill_name="no-such", path="x.md")
    assert "不可用" in result


def test_no_skills_returns_empty_list():
    assert make_skill_tools([]) == []


def test_sync_fallback_rejects():
    """同步调用必须显式报错，避免被同步执行器静默降级。"""
    tools = _tools()
    with pytest.raises(RuntimeError, match="仅支持异步调用"):
        tools["load_skill"].invoke({"skill_name": "travel-planning"})
    with pytest.raises(RuntimeError, match="仅支持异步调用"):
        tools["read_skill_file"].invoke({"skill_name": "travel-planning", "path": "x.md"})
