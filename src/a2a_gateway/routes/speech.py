"""语音路由：代理 onnx-hub 的 TTS / ASR 服务。

- POST /api/speech/tts  → 转发 onnx-hub `POST /api/tts/{tts_model}`，
  网关注入 X-API-Key 后把 audio/wav 流式回传前端。
- WS   /ws/asr          → 双向透传 onnx-hub `WS /ws/asr/{asr_model}`
  （sherpa-onnx 流式识别协议：上行 16kHz 单声道 int16 PCM 二进制帧，
  下行 JSON 文本帧 {"text","segment","start_time","is_final"}）。

前端不接触真实 API Key：密钥只保存在网关配置中，由本路由注入。
"""

import asyncio
import logging
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


class TtsRequest(BaseModel):
    """TTS 合成请求（与 onnx-hub 的 TtsRequest 保持一致）。"""

    text: str = Field(min_length=1)
    speaker_id: int = Field(default=0, ge=0)
    speed: float = Field(default=1.0, gt=0, le=3.0)


# ---------------------------------------------------------------------------
# TTS：HTTP 代理
# ---------------------------------------------------------------------------
@router.post("/api/speech/tts")
async def speech_tts(req: TtsRequest) -> Any:
    """文本转语音：请求 onnx-hub TTS 服务，流式返回 WAV 音频。"""
    settings = get_settings()
    if not settings.onnx_hub_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="语音服务未配置（缺少 ONNX_HUB_API_KEY）",
        )

    url = f"{settings.onnx_hub_base_url.rstrip('/')}/api/tts/{settings.onnx_hub_tts_model}"
    try:
        client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
    except Exception as exc:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="语音合成服务不可达",
        ) from exc

    try:
        resp = await client.post(
            url,
            headers={"X-API-Key": settings.onnx_hub_api_key},
            json=req.model_dump(),
        )
    except Exception as exc:
        await client.aclose()
        logger.exception("TTS 代理请求失败 url=%s", url)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"语音合成服务不可达：{exc.__class__.__name__}",
        ) from exc

    if resp.status_code != 200:
        await client.aclose()
        detail = "语音合成服务返回错误"
        try:
            data = resp.json()
            detail = data.get("detail") or detail
        except Exception:
            pass
        # 401/404/409 → 统一映射为 5xx，避免向前端泄露上游语义细节
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

    # 流式转发 WAV 字节流，避免大音频整体驻留内存
    async def stream():
        try:
            async for chunk in resp.aiter_bytes(8192):
                yield chunk
        finally:
            await client.aclose()

    return StreamingResponse(
        stream(),
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# ASR：WebSocket 双向透传
# ---------------------------------------------------------------------------
async def _pump(source: Any, sink: Any, label: str) -> None:
    """把 source 收到的消息原样转发给 sink，连接关闭即退出。"""
    try:
        while True:
            message = await source.receive()
            if isinstance(message, str):
                await sink.send_text(message)
            else:
                await sink.send_bytes(message)
    except (WebSocketDisconnect, Exception):
        logger.debug("ASR 透传任务结束 %s", label)


@router.websocket("/ws/asr")
@router.websocket("/ws/asr/{model_id}")
async def speech_asr(ws: WebSocket, model_id: str | None = None) -> None:
    """流式语音识别：把前端 WebSocket 透传到 onnx-hub 的 ASR 服务。

    上行：16kHz 单声道 int16 PCM 二进制帧（可先发 {"ExpectedSampleRate":16000} 握手）
    下行：JSON 文本帧 {"text","segment","start_time","is_final"}
    """
    settings = get_settings()
    await ws.accept()

    if not settings.onnx_hub_api_key:
        await ws.close(code=4503, reason="语音服务未配置")
        return

    upstream_model = model_id or settings.onnx_hub_asr_model
    base = settings.onnx_hub_base_url.rstrip("/")
    upstream_url = (
        base.replace("https://", "wss://").replace("http://", "ws://")
        + f"/ws/asr/{upstream_model}"
    )
    from urllib.parse import urlencode

    upstream_url += "?" + urlencode({"api_key": settings.onnx_hub_api_key})

    try:
        async with websockets.connect(
            upstream_url, max_size=None, open_timeout=10
        ) as upstream:
            client_to_hub = asyncio.create_task(_pump(ws, upstream, "client->hub"))
            hub_to_client = asyncio.create_task(_pump(upstream, ws, "hub->client"))
            done, pending = await asyncio.wait(
                {client_to_hub, hub_to_client}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        # 双向任一结束即终止：确保客户端连接也关闭
        try:
            await ws.close()
        except Exception:
            pass
    except websockets.exceptions.InvalidStatus as exc:
        # onnx-hub 网关用自定义关闭码表达认证/模型错误
        code = getattr(exc.response, "status_code", None)
        reason = {4401: "API Key 无效", 4404: "模型不存在", 4409: "模型未启动"}.get(
            code, f"上游连接被拒绝（{code}）"
        )
        logger.warning("ASR 上游握手失败 code=%s", code)
        await ws.close(code=4502, reason=reason)
    except Exception as exc:
        logger.exception("ASR 上游连接失败 %s", upstream_url)
        try:
            await ws.close(code=4502, reason=f"语音识别服务不可达：{exc.__class__.__name__}")
        except Exception:
            pass


@router.get("/api/speech/status")
async def speech_status() -> dict:
    """语音服务配置状态（不含敏感信息）。"""
    settings = get_settings()
    return {
        "configured": bool(settings.onnx_hub_api_key),
        "base_url": settings.onnx_hub_base_url,
        "asr_model": settings.onnx_hub_asr_model,
        "tts_model": settings.onnx_hub_tts_model,
    }
