"""执行器用例：纯逻辑跨平台；真实子进程冒烟仅 POSIX（runner 只跑在 Linux 容器）。"""

import sys

import pytest

from sandbox.protocol import RunFile, RunRequest, SUFFIX_INTERPRETER
from sandbox.runner import build_command

IS_POSIX = sys.platform != "win32"


def test_build_command():
    assert build_command("scripts/gen.py", ["--x", "1"]) == [
        "python3",
        "scripts/gen.py",
        "--x",
        "1",
    ]


@pytest.mark.skipif(not IS_POSIX, reason="runner 仅运行在 Linux 容器")
async def test_execute_python_stdout(monkeypatch):
    # 用当前解释器替身 python3（venv 内必有），不依赖系统 python3
    monkeypatch.setitem(SUFFIX_INTERPRETER, ".py", [sys.executable])
    from sandbox.runner import execute

    result = await execute(
        RunRequest(
            files=[RunFile(path="main.py", content="print('hello sandbox')")],
            entry="main.py",
        )
    )
    assert result.exit_code == 0
    assert "hello sandbox" in result.stdout
    assert not result.timeout and not result.truncated


@pytest.mark.skipif(not IS_POSIX, reason="runner 仅运行在 Linux 容器")
async def test_execute_timeout_kills_process(monkeypatch):
    monkeypatch.setitem(SUFFIX_INTERPRETER, ".py", [sys.executable])
    from sandbox.runner import execute

    result = await execute(
        RunRequest(
            files=[RunFile(path="main.py", content="import time; time.sleep(30)")],
            entry="main.py",
            timeout_s=1,
        )
    )
    assert result.timeout is True
    assert result.exit_code is None


@pytest.mark.skipif(not IS_POSIX, reason="runner 仅运行在 Linux 容器")
async def test_execute_clean_env_and_sibling_files(monkeypatch):
    monkeypatch.setitem(SUFFIX_INTERPRETER, ".py", [sys.executable])
    monkeypatch.setenv("EVIL_VAR", "should-not-leak")
    from sandbox.runner import execute

    script = (
        "import os, pathlib\n"
        "assert 'EVIL_VAR' not in os.environ, 'env leaked'\n"
        "print(pathlib.Path('refs/data.csv').read_text())\n"
    )
    result = await execute(
        RunRequest(
            files=[
                RunFile(path="main.py", content=script),
                RunFile(path="refs/data.csv", content="a,b"),
            ],
            entry="main.py",
        )
    )
    assert result.exit_code == 0
    assert "a,b" in result.stdout


@pytest.mark.skipif(not IS_POSIX, reason="runner 仅运行在 Linux 容器")
async def test_execute_truncates_large_output(monkeypatch):
    monkeypatch.setitem(SUFFIX_INTERPRETER, ".py", [sys.executable])
    from sandbox.runner import execute

    result = await execute(
        RunRequest(
            files=[RunFile(path="main.py", content="print('x' * 40000)")],
            entry="main.py",
        )
    )
    assert result.truncated is True
    assert len(result.stdout) == 32768


async def test_execute_rejects_bad_entry():
    from sandbox.runner import execute

    result = await execute(RunRequest(files=[RunFile(path="a.exe", content="x")], entry="a.exe"))
    assert result.error and "不支持" in result.error
