"""沙箱执行协议：请求/响应模型与两端共用的纯函数（校验/解码/截断）。

领域无关：本模块与「Skill」概念零耦合，只认「一组文件 + 一个入口脚本」；
文件组装（技能包 → 运行时视图）由调用方（backend）完成。
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
