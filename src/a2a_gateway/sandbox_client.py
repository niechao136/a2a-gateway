"""沙箱执行客户端：backend → sandbox-gate 的 HTTP 封装。

错误三分类（规格 §3.2）：
- SandboxUnavailable：未配置（SANDBOX_URL 空 = 功能关闭）或 gate 不可达
- SandboxTimeout：gate 504（runner 执行超时）
- SandboxRejected：鉴权失败 / 其它非 200

payload 组装遵循沙箱协议（sandbox 服务 protocol.py）：files 为「技能包运行时视图」，
script 条目 base64 原样透传，text 条目 utf-8。
"""

import base64
from typing import Any

import httpx

from .config import get_settings
from .skills import SCRIPT_TIMEOUT_DEFAULT_S, SCRIPT_TIMEOUT_MAX_S


class SandboxError(RuntimeError):
    """沙箱执行失败基类（message 面向最终用户/模型）。"""


class SandboxUnavailable(SandboxError):
    """沙箱未配置或不可达。"""


class SandboxTimeout(SandboxError):
    """脚本执行超时。"""


class SandboxRejected(SandboxError):
    """沙箱拒绝执行（鉴权 / 协议 / 5xx）。"""


def sandbox_enabled() -> bool:
    """SANDBOX_URL 是否已配置（未配置时 Agent 工具整体不挂载）。"""
    return bool(get_settings().sandbox_url)


def build_run_files(files: list[dict[str, Any]]) -> list[dict[str, str]]:
    """技能附件 → 沙箱请求 files；encoding 缺省按 utf-8（兼容存量数据）。"""
    return [
        {
            "path": str(f.get("path") or ""),
            "encoding": str(f.get("encoding") or "utf-8"),
            "content": str(f.get("content") or ""),
        }
        for f in files
    ]


async def run_script(
    *,
    files: list[dict[str, Any]],
    entry: str,
    argv: list[str] | None = None,
    stdin: str = "",
    timeout_s: int = SCRIPT_TIMEOUT_DEFAULT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """提交一次脚本执行；@returns gate 的 RunResult dict；失败抛 SandboxError 子类。"""
    settings = get_settings()
    if not settings.sandbox_url:
        raise SandboxUnavailable("沙箱执行未启用（未配置 SANDBOX_URL）")
    timeout_s = max(1, min(int(timeout_s), SCRIPT_TIMEOUT_MAX_S))
    payload = {
        "files": build_run_files(files),
        "entry": entry,
        "argv": list(argv or []),
        "stdin_b64": base64.b64encode(stdin.encode("utf-8")).decode("ascii") if stdin else None,
        "timeout_s": timeout_s,
    }
    async with httpx.AsyncClient(timeout=timeout_s + 10, transport=transport) as client:
        try:
            resp = await client.post(
                f"{settings.sandbox_url.rstrip('/')}/v1/run",
                json=payload,
                headers={"Authorization": f"Bearer {settings.sandbox_token}"},
            )
        except httpx.HTTPError as exc:
            raise SandboxUnavailable(f"沙箱服务不可达：{type(exc).__name__}") from exc
    if resp.status_code == 504:
        raise SandboxTimeout("脚本执行超时")
    if resp.status_code == 401:
        raise SandboxRejected("沙箱鉴权失败")
    if resp.status_code != 200:
        raise SandboxRejected(f"沙箱拒绝执行（HTTP {resp.status_code}）")
    return dict(resp.json())
