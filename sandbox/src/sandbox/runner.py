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
    ProtocolError,
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
    except ProtocolError as exc:
        # 协议校验失败（入口不在白名单等）：语义明确的拒绝，保留原文案
        result.error = str(exc)
        return result
    except Exception as exc:  # 兜底：执行器内部错误回传而非断连
        result.error = f"执行器内部错误：{type(exc).__name__}"
        return result
    finally:
        result.duration_ms = int((time.monotonic() - started) * 1000)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    # socket server 在任务 B3 实现；此入口先占位以防误启动
    raise SystemExit("runner socket server 将在任务 B3 提供（sandbox.runner:serve）")
