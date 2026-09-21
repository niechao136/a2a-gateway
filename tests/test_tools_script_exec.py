"""run_skill_script 工具测试：挂载门禁 + 执行链路（client monkeypatch，零网络）。"""

import pytest

from a2a_gateway import sandbox_client
from a2a_gateway.config import get_settings
from a2a_gateway.sandbox_client import SandboxTimeout, SandboxUnavailable
from a2a_gateway.tools import make_script_exec_tools

SKILL = {
    "name": "gen-skill",
    "description": "d",
    "content": "c",
    "load_mode": "on_demand",
    "allow_scripts": True,
    "files": [
        {
            "path": "scripts/gen.py",
            "size": 11,
            "content": "cHJpbnQoMSk=",
            "entry_type": "script",
            "encoding": "base64",
        },
        {"path": "refs/a.md", "size": 4, "content": "文本", "entry_type": "text", "encoding": "utf-8"},
    ],
}

SKILL_NO_SCRIPTS = {
    "name": "plain",
    "description": "d",
    "content": "c",
    "load_mode": "on_demand",
    "allow_scripts": False,
    "files": [],
}


@pytest.fixture
def sandbox_on(monkeypatch):
    monkeypatch.setenv("SANDBOX_URL", "http://sandbox-gate:8100")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_not_mounted_without_sandbox_url(monkeypatch):
    monkeypatch.delenv("SANDBOX_URL", raising=False)
    get_settings.cache_clear()
    assert make_script_exec_tools([SKILL]) == []
    get_settings.cache_clear()


def test_not_mounted_without_allow_scripts(sandbox_on):
    assert make_script_exec_tools([SKILL_NO_SCRIPTS]) == []


def test_mounted_with_allow_scripts(sandbox_on):
    tools = make_script_exec_tools([SKILL, SKILL_NO_SCRIPTS])
    assert len(tools) == 1
    assert tools[0].name == "run_skill_script"
    assert "scripts/gen.py" in tools[0].description  # 工具描述静态列出可执行脚本


async def test_run_success_and_files_passthrough(sandbox_on, monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {
            "exit_code": 0,
            "stdout": "1\n",
            "stderr": "",
            "truncated": False,
            "timeout": False,
            "duration_ms": 3,
        }

    monkeypatch.setattr(sandbox_client, "run_script", fake_run)
    tool = make_script_exec_tools([SKILL])[0]
    out = await tool.ainvoke(
        {"skill_name": "gen-skill", "script_path": "scripts/gen.py", "argv": ["--n", "1"], "stdin": ""}
    )
    assert "exit_code: 0" in out and "1\n" in out
    # 文件组装：技能包全部附件原样透传（script=b64 / text=utf-8）
    assert captured["entry"] == "scripts/gen.py"
    assert {f["path"]: f["encoding"] for f in captured["files"]} == {
        "scripts/gen.py": "base64",
        "refs/a.md": "utf-8",
    }


async def test_run_wrong_path_lists_available(sandbox_on, monkeypatch):
    async def fake_run(**kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("不应发起执行")

    monkeypatch.setattr(sandbox_client, "run_script", fake_run)
    tool = make_script_exec_tools([SKILL])[0]
    out = await tool.ainvoke(
        {"skill_name": "gen-skill", "script_path": "scripts/missing.py"}
    )
    assert "脚本不存在或不可执行" in out and "scripts/gen.py" in out


async def test_run_unknown_skill_lists_catalog(sandbox_on):
    tool = make_script_exec_tools([SKILL])[0]
    out = await tool.ainvoke({"skill_name": "no-such", "script_path": "scripts/gen.py"})
    assert "未找到该技能" in out and "gen-skill" in out


async def test_run_sandbox_errors_converge_to_text(sandbox_on, monkeypatch):
    async def fake_timeout(**kwargs):
        raise SandboxTimeout("脚本执行超时")

    async def fake_unavailable(**kwargs):
        raise SandboxUnavailable("沙箱服务不可达：ConnectError")

    monkeypatch.setattr(sandbox_client, "run_script", fake_timeout)
    tool = make_script_exec_tools([SKILL])[0]
    args = {"skill_name": "gen-skill", "script_path": "scripts/gen.py"}
    assert "超时" in await tool.ainvoke(args)

    monkeypatch.setattr(sandbox_client, "run_script", fake_unavailable)
    assert "不可达" in await tool.ainvoke(args)
