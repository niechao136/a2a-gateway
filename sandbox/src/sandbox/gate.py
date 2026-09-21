"""沙箱执行前门：Bearer 鉴权、请求体限流、并发信号量、经 unix socket 转发 runner。

gate 是唯一有网络的沙箱组件；与 runner 之间的 IPC 走共享 volume 上的
unix socket（runner 容器 network none，彻底无网络）。单连接一请求：
写请求 + write_eof → 读响应至 EOF。
"""

import asyncio
import os

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
        raise HTTPException(
            status_code=503, detail=f"runner 不可达：{type(exc).__name__}"
        ) from exc
    try:
        writer.write(req.model_dump_json().encode("utf-8"))
        await writer.drain()
        writer.write_eof()
        raw = await asyncio.wait_for(reader.read(), timeout=timeout_s + 5)
        return RunResult.model_validate_json(raw)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="runner 执行超时") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail=f"runner 通信失败：{type(exc).__name__}"
        ) from exc
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
