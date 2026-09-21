# Skill Phase B 实现计划：沙箱执行器服务（gate + runner）

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增 `sandbox/` 独立服务（一个镜像两个角色）：`gate`（有网络，鉴权/限流/转发）+ `runner`（`network_mode: none`，铺文件 → rlimit 子进程执行 → 截断回传），经共享 volume 上的 unix socket 通信。

**架构：** gate 与 runner 领域无关（只认「一组文件 + 一个入口脚本」，不知道 Skill 概念——文件组装由 Phase C 的 backend 完成）。容器级约束由 compose 保证（network none / read_only / cap_drop ALL / 非 root / pids+mem limit / tmpfs），进程级约束由 runner 保证（干净 env / rlimit / 超时 kill 进程组 / 输出截断 / 工作目录清理）。

**技术栈：** Python 3.12 + FastAPI + uvicorn（gate）；纯 asyncio（runner）；独立 uv 项目 `sandbox/`。

**规格：** `docs/superpowers/specs/2026-09-21-skill-preview-edit-script-execution-design.md` §3/§7/§9/§12/§13

## 全局约束

- 领域无关：`sandbox/` 内不出现 "skill" 字样；协议只认 files + entry
- 与 backend 的数值口径一致：超时默认 30s / 上限 120s、输出截断 32KB、总量 4MB、文件数 100
- runner 只在 Linux 容器内运行（rlimit/start_new_session/killpg 均为 POSIX-only）；涉及真实子进程的测试 `skipif not posix`，Windows 开发机自动跳过纯逻辑以外的用例
- 全部注释/报错文案用中文；遵循仓库现有「模块 docstring 写设计要点」的风格
- 不新增环境变量到 backend（`SANDBOX_URL`/`SANDBOX_TOKEN` 在 Phase C 接线）；本计划只新增 sandbox 自身与 compose 的变量
- 安全底线：干净 env（不透传任何宿主/容器环境变量）、白名单解释器、路径安全校验两端各自独立做（纵深防御）

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `sandbox/pyproject.toml` | 创建 | 独立 uv 项目：fastapi/uvicorn 运行依赖 + pytest/pytest-asyncio 开发依赖 |
| `sandbox/Dockerfile` | 创建 | python:3.12-slim + bash/nodejs；单镜像双角色（CMD=gate，compose 覆盖为 runner） |
| `sandbox/src/sandbox/__init__.py` | 创建 | 包标记 |
| `sandbox/src/sandbox/protocol.py` | 创建 | 请求/响应模型、路径安全、解释器映射、解码/截断/超时 clamp（两端共用纯函数） |
| `sandbox/src/sandbox/runner.py` | 创建 | 执行器（铺文件/子进程/rlimit/超时/截断）+ unix socket server |
| `sandbox/src/sandbox/gate.py` | 创建 | FastAPI 前门：Bearer 鉴权、请求体限流、并发信号量、socket 转发、/healthz |
| `sandbox/tests/conftest.py` | 创建 | tmp socket 路径 / token 环境替身 |
| `sandbox/tests/test_protocol.py` | 创建 | 协议纯函数用例 |
| `sandbox/tests/test_runner.py` | 创建 | 执行器用例（纯逻辑 + 真实解释器冒烟） |
| `sandbox/tests/test_gate.py` | 创建 | 鉴权/限流/转发/healthz 用例（转发层 monkeypatch） |
| `docker-compose.yml` | 修改 | 新增 sandbox-gate / sandbox-runner 两服务 + backend 环境变量与依赖 |
| `.env.example` | 修改 | 追加 `SANDBOX_TOKEN` |

---

### 任务 1：项目脚手架 + `protocol.py`

**文件：**
- 创建：`sandbox/pyproject.toml`、`sandbox/src/sandbox/__init__.py`、`sandbox/src/sandbox/protocol.py`
- 测试：`sandbox/tests/test_protocol.py`、`sandbox/tests/conftest.py`

- [ ] **步骤 1：创建脚手架**

`sandbox/pyproject.toml`：

```toml
[project]
name = "sandbox-exec"
version = "0.1.0"
description = "a2a-gateway 脚本沙箱执行器（gate + runner）"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.30.0",
]

[dependency-groups]
dev = ["pytest>=8.0.0", "pytest-asyncio>=0.24.0"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sandbox"]
```

`sandbox/src/sandbox/__init__.py`：

```python
"""a2a-gateway 脚本沙箱：gate（前门）+ runner（执行器），领域无关。"""
```

- [ ] **步骤 2：编写失败的测试**

`sandbox/tests/conftest.py`：

```python
"""沙箱服务测试公共夹具：临时 socket 路径与 token 环境替身（零外部依赖）。"""

import pytest


@pytest.fixture
def socket_path(tmp_path):
    return str(tmp_path / "sandbox.sock")
```

`sandbox/tests/test_protocol.py`：

```python
"""协议纯函数用例（跨平台，无子进程）。"""

import base64

import pytest

from sandbox.protocol import (
    ProtocolError,
    RunFile,
    assert_safe_rel_path,
    clamp_timeout,
    decode_files,
    resolve_interpreter,
    truncate_output,
)


def test_assert_safe_rel_path_rejects_escape():
    with pytest.raises(ProtocolError):
        assert_safe_rel_path("../escape.md")
    with pytest.raises(ProtocolError):
        assert_safe_rel_path("/abs.md")
    with pytest.raises(ProtocolError):
        assert_safe_rel_path("a\\b.md")
    with pytest.raises(ProtocolError):
        assert_safe_rel_path("  ")
    assert assert_safe_rel_path("scripts/gen.py") == "scripts/gen.py"


def test_resolve_interpreter_whitelist():
    assert resolve_interpreter("main.py") == ["python3"]
    assert resolve_interpreter("RUN.SH") == ["bash"]
    assert resolve_interpreter("x/tool.JS") == ["node"]
    with pytest.raises(ProtocolError):
        resolve_interpreter("noext")
    with pytest.raises(ProtocolError):
        resolve_interpreter("bin/run.exe")


def test_decode_files_caps():
    files = [RunFile(path="a.py", content="print(1)"), RunFile(path="d/b.bin", encoding="base64", content=base64.b64encode(b"\x00\x01").decode())]
    decoded = decode_files(files)
    assert decoded[0][1] == b"print(1)"
    assert decoded[1][1] == b"\x00\x01"
    with pytest.raises(ProtocolError):
        decode_files([RunFile(path="x.py", encoding="base64", content="!!!")])  # 非法 base64
    with pytest.raises(ProtocolError):
        decode_files([RunFile(path=f"f{i}.py", content="x") for i in range(101)])


def test_clamp_timeout():
    assert clamp_timeout(0) == 1
    assert clamp_timeout(30) == 30
    assert clamp_timeout(999) == 120


def test_truncate_output():
    text, truncated = truncate_output("短输出".encode())
    assert text == "短输出" and not truncated
    big = "x" * 40000
    text, truncated = truncate_output(big.encode())
    assert truncated and len(text) == 32768
    # 非法 UTF-8 容错解码不抛错
    text, truncated = truncate_output(b"\xff\xfe")
    assert not truncated
```

- [ ] **步骤 3：运行测试验证失败**

运行：`cd sandbox; uv sync; uv run pytest -q`
预期：FAIL，`ModuleNotFoundError: sandbox.protocol`

- [ ] **步骤 4：实现 `protocol.py`**

```python
"""沙箱执行协议：请求/响应模型与两端共用的纯函数（校验/解码/截断）。

领域无关：本模块与「Skill」概念零耦合，只认「一组文件 + 一个入口脚本」；
文件组装（技能包 → 运行时视图）由调用方（backend Phase C）完成。
"""

import base64
import binascii
import posixpath
from typing import Literal

from pydantic import BaseModel, Field

RUNTIME_TIMEOUT_DEFAULT_S = 30
RUNTIME_TIMEOUT_MAX_S = 120
MAX_RUN_BODY_BYTES = 8 * 1024 * 1024   # gate 请求体硬上限（4MB 附件 b64 后约 5.4MB，留余量）
MAX_RUN_FILES = 100
MAX_RUN_TOTAL_BYTES = 4 * 1024 * 1024  # 解码后总量
MAX_OUTPUT_BYTES = 32768               # stdout/stderr 各自截断（与 backend 约定一致）

# 入口脚本后缀 → 解释器命令（白名单；v1 只用镜像预置运行时 + 标准库）
SUFFIX_INTERPRETER: dict[str, list[str]] = {
    ".py": ["python3"],
    ".sh": ["bash"],
    ".js": ["node"],
}

# 子进程环境：显式白名单构造，绝不继承容器环境（backend 容器 env 含敏感凭据，
# runner 容器自身也无凭据，但仍以白名单为底线）
CHILD_ENV: dict[str, str] = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "TMPDIR": "/tmp",
    "LANG": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
}


class ProtocolError(ValueError):
    """协议/校验失败（message 面向调用方运维）。"""


class RunFile(BaseModel):
    path: str
    encoding: Literal["utf-8", "base64"] = "utf-8"
    content: str = ""


class RunRequest(BaseModel):
    files: list[RunFile] = Field(default_factory=list)
    entry: str = Field(description="入口脚本相对路径（后缀决定解释器）")
    argv: list[str] = Field(default_factory=list)
    stdin_b64: str | None = None
    timeout_s: int = RUNTIME_TIMEOUT_DEFAULT_S


class RunResult(BaseModel):
    exit_code: int | None = None  # None = 超时被杀 / 内部错误
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timeout: bool = False
    duration_ms: int = 0
    error: str | None = None


def assert_safe_rel_path(raw: str) -> str:
    """与 backend 导入管线同口径：空 / 绝对 / 含 .. / 反斜杠一律拒绝。"""
    path = raw.replace("\\", "/")
    if not path.strip():
        raise ProtocolError("文件路径为空")
    if path.startswith("/"):
        raise ProtocolError(f"文件路径非法：{raw}")
    normalized = posixpath.normpath(path)
    if normalized == ".." or normalized.startswith(("/", "../")):
        raise ProtocolError(f"文件路径非法：{raw}")
    return normalized


def resolve_interpreter(entry: str) -> list[str]:
    """入口脚本后缀 → 解释器命令；白名单外拒绝。"""
    dot = entry.rfind(".")
    suffix = entry[dot:].lower() if dot >= 0 else ""
    interpreter = SUFFIX_INTERPRETER.get(suffix)
    if interpreter is None:
        raise ProtocolError(f"不支持的脚本类型（仅支持 .py/.sh/.js）：{entry}")
    return interpreter


def decode_files(files: list[RunFile]) -> list[tuple[str, bytes]]:
    """解码 + 路径安全 + 上限校验；@returns [(相对路径, 原始字节)]。"""
    if len(files) > MAX_RUN_FILES:
        raise ProtocolError(f"文件数超过 {MAX_RUN_FILES} 上限")
    decoded: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        path = assert_safe_rel_path(f.path)
        if f.encoding == "base64":
            try:
                blob = base64.b64decode(f.content, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ProtocolError(f"base64 解码失败：{path}") from exc
        else:
            blob = f.content.encode("utf-8")
        total += len(blob)
        if total > MAX_RUN_TOTAL_BYTES:
            raise ProtocolError(f"文件总量超过 {MAX_RUN_TOTAL_BYTES} 字节上限")
        decoded.append((path, blob))
    return decoded


def clamp_timeout(timeout_s: int) -> int:
    return max(1, min(int(timeout_s), RUNTIME_TIMEOUT_MAX_S))


def truncate_output(blob: bytes) -> tuple[str, bool]:
    """字节截断 + 容错解码；@returns (文本, 是否截断)。"""
    if len(blob) > MAX_OUTPUT_BYTES:
        return blob[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"), True
    return blob.decode("utf-8", errors="replace"), False
```

- [ ] **步骤 5：运行测试验证通过**

运行：`cd sandbox; uv run pytest -q`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add sandbox/pyproject.toml sandbox/src/sandbox/__init__.py sandbox/src/sandbox/protocol.py sandbox/tests/conftest.py sandbox/tests/test_protocol.py
git commit -m "feat(sandbox): 沙箱协议模型与共享纯函数"
```

---

### 任务 2：runner 执行器（铺文件 / 子进程 / rlimit / 超时）

**文件：**
- 创建：`sandbox/src/sandbox/runner.py`
- 测试：`sandbox/tests/test_runner.py`

- [ ] **步骤 1：编写失败的测试**

`sandbox/tests/test_runner.py`：

```python
"""执行器用例：纯逻辑跨平台；真实子进程冒烟仅 POSIX（runner 只跑在 Linux 容器）。"""

import base64
import sys

import pytest

from sandbox.protocol import RunFile, RunRequest, SUFFIX_INTERPRETER
from sandbox.runner import build_command

IS_POSIX = sys.platform != "win32"


def test_build_command():
    assert build_command("scripts/gen.py", ["--x", "1"]) == ["python3", "scripts/gen.py", "--x", "1"]


@pytest.mark.skipif(not IS_POSIX, reason="runner 仅运行在 Linux 容器")
async def test_execute_python_stdout(socket_path, monkeypatch):
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
async def test_execute_timeout_kills_process(socket_path, monkeypatch):
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
async def test_execute_clean_env_and_sibling_files(monkeypatch, tmp_path):
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd sandbox; uv run pytest tests/test_runner.py -q`
预期：FAIL，`ModuleNotFoundError: sandbox.runner`

- [ ] **步骤 3：实现 `runner.py`**

```python
"""沙箱执行器：每请求独立工作目录 + rlimit 子进程 + 超时 kill 进程组。

容器级约束（network none / read_only rootfs / cap_drop ALL / 非 root / 资源上限）
由 compose 保证；本模块负责进程级约束。超时被杀时 stdout/stderr 不可靠，置空
并以 timeout=True 标记（不回传部分输出，避免半截结果误导模型）。
"""

import asyncio
import base64
import os
import shutil
import signal
import time
import uuid

from .protocol import (
    CHILD_ENV,
    RunRequest,
    RunResult,
    clamp_timeout,
    decode_files,
    resolve_interpreter,
    truncate_output,
)

WORK_ROOT = "/tmp"
RLIMIT_AS_BYTES = 256 * 1024 * 1024   # 与容器 mem_limit(256m) 同水位
RLIMIT_NPROC = 64
RLIMIT_FSIZE_BYTES = 16 * 1024 * 1024


def build_command(entry: str, argv: list[str]) -> list[str]:
    """解释器 + 入口脚本（相对路径，cwd=包根）+ argv。"""
    return [*resolve_interpreter(entry), entry, *argv]


def _apply_rlimits(timeout_s: int) -> None:  # pragma: no cover - 仅在子进程内执行（POSIX）
    import resource

    cpu = max(1, timeout_s)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_AS, (RLIMIT_AS_BYTES, RLIMIT_AS_BYTES))
    resource.setrlimit(resource.RLIMIT_NPROC, (RLIMIT_NPROC, RLIMIT_NPROC))
    resource.setrlimit(resource.RLIMIT_FSIZE, (RLIMIT_FSIZE_BYTES, RLIMIT_FSIZE_BYTES))


async def execute(req: RunRequest) -> RunResult:
    """执行一次脚本；任何内部错误都收敛进 RunResult.error，绝不向调用方抛异常。"""
    started = time.monotonic()
    result = RunResult()
    workdir = os.path.join(WORK_ROOT, f"exec-{uuid.uuid4().hex}")
    timeout_s = clamp_timeout(req.timeout_s)
    try:
        files = decode_files(req.files)
        command = build_command(req.entry, req.argv)
        os.makedirs(workdir)
        for path, blob in files:
            dest = os.path.join(workdir, path)
            os.makedirs(os.path.dirname(dest) or workdir, exist_ok=True)
            with open(dest, "wb") as fp:
                fp.write(blob)
        stdin_blob = base64.b64decode(req.stdin_b64) if req.stdin_b64 else None
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=workdir,
                env=CHILD_ENV,
                stdin=asyncio.subprocess.PIPE if stdin_blob else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # 独立进程组：超时可整组 kill
                preexec_fn=lambda: _apply_rlimits(timeout_s),
            )
        except (OSError, ValueError) as exc:
            result.error = f"无法启动解释器：{type(exc).__name__}"
            return result
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(input=stdin_blob), timeout=timeout_s
            )
            result.exit_code = proc.returncode
            result.stdout, truncated_out = truncate_output(out or b"")
            result.stderr, truncated_err = truncate_output(err or b"")
            result.truncated = truncated_out or truncated_err
        except asyncio.TimeoutError:
            result.timeout = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        return result
    except Exception as exc:  # 兜底：执行器内部错误回传而非断连
        result.error = f"执行器内部错误：{type(exc).__name__}"
        return result
    finally:
        result.duration_ms = int((time.monotonic() - started) * 1000)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    # socket server 在任务 3 实现；此入口先占位以防误启动
    raise SystemExit("runner socket server 将在任务 3 提供（sandbox.runner:serve）")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd sandbox; uv run pytest tests/test_runner.py -q`
预期：POSIX 上 PASS（Windows 上 4 个真实子进程用例 skip，`build_command`/`rejects_bad_entry` 跑通）

- [ ] **步骤 5：Commit**

```bash
git add sandbox/src/sandbox/runner.py sandbox/tests/test_runner.py
git commit -m "feat(sandbox): 执行器（rlimit 子进程 + 超时 kill + 输出截断）"
```

---

### 任务 3：runner socket server + gate 前门

**文件：**
- 修改：`sandbox/src/sandbox/runner.py`（追加 server 部分，替换 `__main__` 占位）
- 创建：`sandbox/src/sandbox/gate.py`
- 测试：`sandbox/tests/test_runner.py`（追加集成用例）、`sandbox/tests/test_gate.py`

- [ ] **步骤 1：编写失败的测试**

`sandbox/tests/test_runner.py` 追加：

```python
@pytest.mark.skipif(not IS_POSIX, reason="unix socket + 进程组仅 POSIX")
async def test_socket_roundtrip(socket_path, monkeypatch):
    monkeypatch.setitem(SUFFIX_INTERPRETER, ".py", [sys.executable])
    import asyncio

    from sandbox.protocol import RunResult
    from sandbox.runner import serve

    server_task = asyncio.create_task(serve(socket_path))
    try:
        # 等 socket 就绪
        for _ in range(50):
            if __import__("os").path.exists(socket_path):
                break
            await asyncio.sleep(0.02)
        reader, writer = await asyncio.open_unix_connection(socket_path)
        req = RunRequest(
            files=[RunFile(path="main.py", content="print('roundtrip')")],
            entry="main.py",
        )
        writer.write(req.model_dump_json().encode())
        await writer.drain()
        writer.write_eof()
        raw = await asyncio.wait_for(reader.read(), timeout=10)
        result = RunResult.model_validate_json(raw)
        assert "roundtrip" in result.stdout
        writer.close()
    finally:
        server_task.cancel()
```

`sandbox/tests/test_gate.py`：

```python
"""gate 用例：鉴权 / 限流体 / 转发（monkeypatch _forward，不依赖真 runner）。"""

import httpx
import pytest

from sandbox import gate
from sandbox.protocol import RunResult


@pytest.fixture
async def gate_client():
    transport = httpx.ASGITransport(app=gate.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _token(monkeypatch, value="tok-1"):
    monkeypatch.setattr(gate, "SANDBOX_TOKEN", value)


async def test_run_requires_token(gate_client, monkeypatch):
    _token(monkeypatch)
    resp = await gate_client.post("/v1/run", json={"files": [], "entry": "a.py"})
    assert resp.status_code == 401


async def test_run_forwards_to_runner(gate_client, monkeypatch):
    _token(monkeypatch)
    captured = {}

    async def fake_forward(req, timeout_s):
        captured["entry"] = req.entry
        return RunResult(exit_code=0, stdout="ok")

    monkeypatch.setattr(gate, "_forward", fake_forward)
    resp = await gate_client.post(
        "/v1/run",
        json={"files": [{"path": "a.py", "content": "print(1)"}], "entry": "a.py"},
        headers={"Authorization": "Bearer tok-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["stdout"] == "ok"
    assert captured["entry"] == "a.py"


async def test_run_rejects_oversized_body(gate_client, monkeypatch):
    _token(monkeypatch)
    gate.MAX_RUN_BODY_BYTES_LIMIT = 10  # 测试专用收窄（见实现：模块级可覆盖）
    resp = await gate_client.post(
        "/v1/run",
        json={"files": [{"path": "a.py", "content": "x" * 100}], "entry": "a.py"},
        headers={"Authorization": "Bearer tok-1"},
    )
    assert resp.status_code == 413


async def test_run_bad_json_400(gate_client, monkeypatch):
    _token(monkeypatch)
    resp = await gate_client.post(
        "/v1/run",
        content=b"{oops",
        headers={"Authorization": "Bearer tok-1", "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


async def test_forward_unreachable_503(gate_client, monkeypatch, tmp_path):
    _token(monkeypatch)
    monkeypatch.setattr(gate, "RUNNER_SOCKET", str(tmp_path / "missing.sock"))
    resp = await gate_client.post(
        "/v1/run",
        json={"files": [], "entry": "a.py"},
        headers={"Authorization": "Bearer tok-1"},
    )
    assert resp.status_code == 503


async def test_healthz_down(gate_client, monkeypatch, tmp_path):
    monkeypatch.setattr(gate, "RUNNER_SOCKET", str(tmp_path / "missing.sock"))
    resp = await gate_client.get("/healthz")
    assert resp.status_code == 503
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd sandbox; uv run pytest tests/test_runner.py::test_socket_roundtrip tests/test_gate.py -q`
预期：FAIL（`serve` 不存在 / `sandbox.gate` 不存在）

- [ ] **步骤 3：实现**

`runner.py` 尾部（替换 `__main__` 占位）：

```python
async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """每连接一请求：读至 EOF（调用方写完须 write_eof）→ 执行 → 回 JSON → 关闭。"""
    result_json: str
    try:
        raw = await asyncio.wait_for(reader.read(), timeout=300)
        req = RunRequest.model_validate_json(raw)
        result_json = (await execute(req)).model_dump_json()
    except Exception as exc:
        result_json = RunResult(error=f"请求处理失败：{type(exc).__name__}").model_dump_json()
    try:
        writer.write(result_json.encode("utf-8"))
        await writer.drain()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def serve(socket_path: str = "/ipc/sandbox.sock") -> None:
    """unix socket server；socket 由本进程创建（volume 首挂时已具备写权限）。"""
    parent = os.path.dirname(socket_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    server = await asyncio.start_unix_server(handle_client, path=socket_path)
    print(f"sandbox-runner listening on {socket_path}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
```

`sandbox/src/sandbox/gate.py`（完整文件）：

```python
"""沙箱执行前门：Bearer 鉴权、请求体限流、并发信号量、经 unix socket 转发 runner。

gate 是唯一有网络的沙箱组件；与 runner 之间的 IPC 走共享 volume 上的
unix socket（runner 容器 network none，彻底无网络）。单连接一请求：
写请求 + write_eof → 读响应至 EOF。
"""

import asyncio
import os
import time

from fastapi import FastAPI, HTTPException, Request

from .protocol import MAX_RUN_BODY_BYTES, RunRequest, RunResult, clamp_timeout

RUNNER_SOCKET = os.getenv("SANDBOX_RUNNER_SOCKET", "/ipc/sandbox.sock")
SANDBOX_TOKEN = os.getenv("SANDBOX_TOKEN", "")
MAX_CONCURRENCY = int(os.getenv("SANDBOX_MAX_CONCURRENCY", "3"))
# 测试可收窄的请求体上限（运行时恒为 MAX_RUN_BODY_BYTES）
MAX_RUN_BODY_BYTES_LIMIT = MAX_RUN_BODY_BYTES
FORWARD_CONNECT_TIMEOUT = 5.0

app = FastAPI(title="sandbox-gate", docs_url=None, redoc_url=None)
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


def _check_token(request: Request) -> None:
    auth = request.headers.get("authorization") or ""
    if not SANDBOX_TOKEN or auth != f"Bearer {SANDBOX_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


async def _forward(req: RunRequest, timeout_s: int) -> RunResult:
    """转发 runner：连接（5s）→ 写请求 + EOF → 读响应（timeout_s + 5s）。"""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(RUNNER_SOCKET), timeout=FORWARD_CONNECT_TIMEOUT
        )
    except (FileNotFoundError, ConnectionRefusedError, OSError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=503, detail=f"runner 不可达：{type(exc).__name__}") from exc
    try:
        writer.write(req.model_dump_json().encode("utf-8"))
        await writer.drain()
        writer.write_eof()
        raw = await asyncio.wait_for(reader.read(), timeout=timeout_s + 5)
        return RunResult.model_validate_json(raw)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="runner 执行超时") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"runner 通信失败：{type(exc).__name__}") from exc
    finally:
        writer.close()


@app.post("/v1/run", response_model=RunResult)
async def run(request: Request) -> RunResult:
    _check_token(request)
    body = await request.body()
    if len(body) > MAX_RUN_BODY_BYTES_LIMIT:
        raise HTTPException(status_code=413, detail="请求体过大")
    try:
        req = RunRequest.model_validate_json(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"请求格式非法：{type(exc).__name__}") from exc
    timeout_s = clamp_timeout(req.timeout_s)
    async with _semaphore:
        _ = time.monotonic()  # 计时由 runner 的 duration_ms 承担；此处仅占位保持结构清晰
        return await _forward(req, timeout_s)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(RUNNER_SOCKET), timeout=2
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"runner 不可达：{type(exc).__name__}") from exc
    writer.close()
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8100)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd sandbox; uv run pytest -q`
预期：PASS（POSIX 全部；Windows 上 socket 集成用例 skip）

- [ ] **步骤 5：Commit**

```bash
git add sandbox/src/sandbox/runner.py sandbox/src/sandbox/gate.py sandbox/tests/test_runner.py sandbox/tests/test_gate.py
git commit -m "feat(sandbox): runner socket server 与 gate 前门"
```

---

### 任务 4：镜像与 compose 编排

**文件：**
- 创建：`sandbox/Dockerfile`
- 修改：`docker-compose.yml`（services 末尾追加两个服务；backend 服务改 environment 与 depends_on）
- 修改：`.env.example`（追加 `SANDBOX_TOKEN`）

- [ ] **步骤 1：创建 `sandbox/Dockerfile`**

```dockerfile
# ===== 沙箱执行器镜像（单镜像双角色：gate / runner）=====
# - gate：compose 默认 CMD，接入 compose 网络，仅内网可达
# - runner：compose 以 command 覆盖 + network none + read_only + cap_drop ALL
FROM python:3.12-slim

# 预置运行时：脚本只用标准库，apt 版 bash/nodejs 即可
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.115.0" "uvicorn>=0.30.0"

# /ipc 预创建并赋属主：named volume 首次挂载会按镜像目录内容初始化权限，
# 使 runner 以非 root（uid 1000）创建 socket 成为可能
RUN mkdir -p /ipc && chown 1000:1000 /ipc \
    && useradd -u 1000 -m sandbox

COPY src /app/src
ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8100
USER 1000
# 默认启动 gate；runner 由 compose command 覆盖
CMD ["python", "-m", "sandbox.gate"]
```

- [ ] **步骤 2：修改 `docker-compose.yml`**

在 `services:` 下（backend 之前或之后均可）追加：

```yaml
  sandbox-gate:
    build:
      context: ./sandbox
      dockerfile: Dockerfile
    image: a2a-gateway-sandbox:latest
    container_name: a2a-gateway-sandbox-gate
    restart: unless-stopped
    environment:
      SANDBOX_TOKEN: ${SANDBOX_TOKEN:-dev-only-sandbox-token}
    volumes:
      - sandbox_ipc:/ipc
    expose:
      - "8100"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz')"]
      interval: 10s
      timeout: 5s
      retries: 6
    security_opt:
      - no-new-privileges:true

  sandbox-runner:
    image: a2a-gateway-sandbox:latest
    container_name: a2a-gateway-sandbox-runner
    restart: unless-stopped
    command: ["python", "-m", "sandbox.runner"]
    # 核心隔离：无网络 + 只读 rootfs + 去 capability + 资源上限 + 非 root
    network_mode: none
    read_only: true
    cap_drop: [ALL]
    pids_limit: 64
    mem_limit: 256m
    tmpfs:
      - /tmp:size=64m,mode=1777
    volumes:
      - sandbox_ipc:/ipc
    security_opt:
      - no-new-privileges:true
```

`backend` 服务追加环境变量与健康依赖：

```yaml
    environment:
      # ...（既有变量不动）
      SANDBOX_URL: ${SANDBOX_URL:-http://sandbox-gate:8100}
      SANDBOX_TOKEN: ${SANDBOX_TOKEN:-dev-only-sandbox-token}
    depends_on:
      postgres:
        condition: service_healthy
      sandbox-gate:
        condition: service_healthy
```

文件末尾（top-level）追加 volume：

```yaml
volumes:
  sandbox_ipc:
```

- [ ] **步骤 3：`.env.example` 追加**

```bash
# 沙箱执行器（backend ↔ sandbox-gate 共享的内部 token；留空则 backend 侧仍可用默认值）
SANDBOX_TOKEN=dev-only-sandbox-token
```

- [ ] **步骤 4：编排自检（不启动容器）**

运行：`docker compose -f d:\web\a2a-gateway\docker-compose.yml config --quiet`
预期：退出码 0（YAML 与引用变量合法）

- [ ] **步骤 5：Commit**

```bash
git add sandbox/Dockerfile docker-compose.yml .env.example
git commit -m "feat(sandbox): 镜像与 compose 编排（gate/runner 双容器）"
```

---

### 任务 5：真机冒烟 + 收尾回归

- [ ] **步骤 1：起全套并冒烟（需 Docker 环境）**

运行（PowerShell，仓库根）：

```powershell
docker compose up -d --build sandbox-gate sandbox-runner
docker compose exec backend python -c "import urllib.request,json,os;req=urllib.request.Request('http://sandbox-gate:8100/v1/run',data=json.dumps({'files':[{'path':'main.py','content':'print(1+1)'}],'entry':'main.py'}).encode(),headers={'Authorization':'Bearer '+os.getenv('SANDBOX_TOKEN',''),'Content-Type':'application/json'});print(urllib.request.urlopen(req).read().decode())"
```

预期：返回 JSON 含 `"stdout":"2\n"`、`"exit_code":0`。失败排查顺序：gate/runner 容器日志 → socket 文件权限 → token。

- [ ] **步骤 2：网络隔离验证（runner 不可达外网）**

运行：`docker compose exec sandbox-gate sh -c "python -c \"import socket; s=socket.socket(); s.settimeout(3); print(s.connect_ex(('1.1.1.1', 80)))\""`
预期：本机在 gate 上验证的是 gate 自身网络（可通）；runner 的隔离验证改为：
`docker compose exec sandbox-runner python -c "import socket; s=socket.socket(); s.settimeout(3); print(s.connect_ex(('1.1.1.1', 80)))"`
预期：非 0（`network_mode: none` 下无任何网络栈；此命令在 runner 内执行即证明无外网出口）

- [ ] **步骤 3：全量回归**

运行：`cd sandbox; uv run pytest -q` 然后 `uv run pytest -q`（主仓库）
预期：两边全绿（主仓库不受影响）

- [ ] **步骤 4：Commit（如有遗漏文件）**

```bash
git add -A
git commit -m "chore(sandbox): Phase B 收尾"
```

---

## 自检记录

- 规格覆盖：§3（gate/runner/unix socket/信号量/healthz）→ 任务 3；§7 协议数值口径（超时 30/120、截断 32KB、总量 4MB）→ 任务 1 常量；§9.1 compose（network none/read_only/cap_drop/pids/mem/tmpfs/非 root）→ 任务 4；§12 安全要求（干净 env/路径安全/白名单解释器）→ 任务 1/2；§13 残留风险对应容器硬ening → 任务 4
- 与规格的两处实现细化（更优且不违背意图）：构建上下文用 `./sandbox`（镜像更小）；非 root 落地方式为 named volume 首挂继承镜像目录属主 + `USER 1000`
- 类型一致性：`RunRequest.files[].encoding`（任务 1）↔ Phase C `build_run_files` 输出键；`RunResult` 字段（任务 1）↔ Phase C `SkillScriptRunOut`；`MAX_RUN_BODY_BYTES_LIMIT` 仅测试收窄用
- 无占位符；真实子进程用例在 Windows 开发机自动 skip，Linux/容器内全量执行
