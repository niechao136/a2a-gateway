---
type: integration-guide
title: 语音代理（ASR/TTS）
description: 说明对 onnx-hub 的 TTS HTTP 流式代理与 ASR WebSocket 双向泵转发、API Key 注入与对前端隐藏、错误映射（502/503/4502/4503）。
tags: [speech, tts, asr, websocket, proxy, onnx-hub]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-21cd86d1835d8fa9e2e76fca
    resource: repo://nginx/default.conf
  - id: openwiki-source-5172018175a2fceb3121bd4d
    resource: repo://src/a2a_gateway/routes/speech.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 语音代理（ASR/TTS）

`src/a2a_gateway/routes/speech.py` 把 onnx-hub 的 TTS / ASR 服务代理给前端：**真实 API Key 只保存在网关配置（`ONNX_HUB_API_KEY`）中，由本路由注入，前端只接触代理后的相对接口、永不接触密钥**（`speech.py:1-10`）。

| 端点 | 上游 | 协议 |
|---|---|---|
| `POST /api/speech/tts` | `{ONNX_HUB_BASE_URL}/api/tts/{tts_model}` | HTTP，网关注入 `X-API-Key`，流式回传 `audio/wav` |
| `WS /ws/asr`、`WS /ws/asr/{model_id}` | `{base}/ws/asr/{asr_model}?api_key=...` | 双向透传 sherpa-onnx 流式识别协议 |
| `GET /api/speech/status` | — | 配置状态（`configured` 布尔 + base/模型名，**不含密钥**） |

配置项（`config.py:101-107`、`.env.example:62-65`）：`ONNX_HUB_BASE_URL` / `ONNX_HUB_API_KEY` / `ONNX_HUB_ASR_MODEL`（默认 `zipformer-streaming-bilingual-zh-en`）/ `ONNX_HUB_TTS_MODEL`（默认 `vits-zh-aishell3`）。

## TTS：HTTP 流式代理

`speech_tts`（`speech.py:40-96`），请求体 `TtsRequest{text(min_length=1), speaker_id(≥0), speed(0,3]}`（与 onnx-hub 一致，`speech.py:29-34`）：

1. **未配置密钥 → 503** `语音服务未配置（缺少 ONNX_HUB_API_KEY）`（`speech.py:44-48`）；
2. `httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))`——**总超时 120s、连接超时 10s**（`speech.py:52`）；
3. POST 上游，头注入 `X-API-Key`（`speech.py:60-64`）；
4. **上游非 200 → 统一 502**（`speech.py:73-82`）：detail 优先取上游 JSON 的 `detail` 字段，取不到用固定文案；**401/404/409 等语义一律映射为 502**——注释明示「避免向前端泄露上游语义细节」；连接失败同样 502 且 detail 只带异常类名（`exc.__class__.__name__`，不回显堆栈/URL 内密钥，`speech.py:65-71`）；
5. 成功 → `StreamingResponse(stream(), media_type="audio/wav", headers={"Cache-Control": "no-store"})`，按 8192 字节块 `aiter_bytes` 转发**避免大音频整体驻留内存**，`finally: client.aclose()`（`speech.py:84-96`）。

## ASR：WebSocket 双向泵

`speech_asr`（`speech.py:130-186`）协议：

- **上行**：16kHz 单声道 int16 PCM 二进制帧（可先发 `{"ExpectedSampleRate":16000}` 握手）；
- **下行**：JSON 文本帧 `{"text","segment","start_time","is_final"}`（`speech.py:133-136`、模块 docstring `speech.py:5-7`）。

连接流程：

1. `await ws.accept()` 后检查密钥——**未配置 → 关闭码 4503** `语音服务未配置`（`speech.py:141-143`）；
2. 模型 id：路径参数 `model_id` 优先，缺省用 `ONNX_HUB_ASR_MODEL`；base 的 `https://→wss://`、`http://→ws://` 改写后追加 `?api_key=<密钥>`（**密钥只在服务端→上游的 URL 中，不下发前端**，`speech.py:145-153`）；
3. `websockets.connect(upstream_url, max_size=None, open_timeout=10)`——**上游 open 超时 10s**、不限帧大小（`speech.py:156-158`）；
4. **双向泵**：
   - `_pump_client_to_hub`（`speech.py:102-114`）：`ws.receive()` 返回 ASGI 原始消息 dict，按 `websocket.disconnect` / `text` / `bytes` 分发转发；
   - `_pump_hub_to_client`（`speech.py:117-127`）：`upstream.recv()` 返回 str/bytes，对应 `send_text`/`send_bytes`；
   - `asyncio.wait(FIRST_COMPLETED)`——**任一方向结束即取消另一方向并关闭客户端连接**（`speech.py:161-171`），泵内异常仅 `logger.debug`（正常结束路径）。

### 关闭码映射

| 码 | 场景 |
|---|---|
| **4503** | 网关侧：`ONNX_HUB_API_KEY` 未配置（`speech.py:141-143`） |
| **4502** | 上游握手/连接失败（`speech.py:172-186`） |
| 4401 / 4404 / 4409 | 上游 `InvalidStatus` 响应中的自定义码 → 映射为 reason 文案「API Key 无效 / 模型不存在 / 模型未启动」后仍以 **4502** 关闭客户端（关闭码来自上游响应，取不到或非整数不参与映射，回退 `上游连接被拒绝（{code}）`，`speech.py:172-180`） |
| 4502（连接异常） | 非 `InvalidStatus` 异常 → `语音识别服务不可达：{异常类名}`（`speech.py:181-186`） |

上游连接失败会 `logger.exception`（含上游 URL——注意 URL query 中带 api_key，日志侧依赖日志配置脱敏，见 [失败与可观测性](/openwiki/operations/failure-and-observability.md)）；握手失败仅 `logger.warning` 与 code。

## 错误映射汇总（信息隐藏策略）

| 条件 | TTS（HTTP） | ASR（WS 关闭码） |
|---|---|---|
| 未配置 `ONNX_HUB_API_KEY` | **503** 固定文案 | **4503** `语音服务未配置` |
| 上游连接/超时失败 | **502** + 仅异常类名 | **4502** + 仅异常类名 |
| 上游非 200（401/404/409…） | **统一 502**，detail 尽量取上游 `detail` 字段 | —（握手阶段由 4401/4404/4409 映射 reason，关闭码仍 4502） |

信息隐藏原则：**不向前端透传上游 HTTP 语义（401/404/409）、异常堆栈、完整 URL**；前端只见 5xx 或 45xx 关闭码 + 安全文案。`GET /api/speech/status` 只回 `configured` 布尔与模型名，永不回显密钥（`speech.py:189-198`）。

## nginx `/ws/` 升级规则

`nginx/default.conf:87-96`：`/ws/` location 必须显式透传 WebSocket 升级——

```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
proxy_buffering off;
```

否则 ASR 长连接握手失败（浏览器 Upgrade 头被剥掉）。`/api/speech/*` 走 `/api/` location（已有 SSE/流式设置，`default.conf:52-61`）。宿主机如另有 Caddy 等上游代理，同样需放行 `/ws/` 升级（见 [部署架构](/openwiki/architecture/deployment.md)）。

## 密钥从不下发前端

- 前端 `web/src/lib/speech.ts` 只调相对路径 `POST /api/speech/tts` 与 `ws(s)://<网关>/ws/asr`——请求体/URL 中**无任何密钥**；
- 网关在 TTS 请求头注入 `X-API-Key`、在 ASR 上游 URL 追加 `?api_key=`，两处均发生在服务端；
- 配置状态经 `/api/speech/status` 以 `configured: bool` 暴露，避免前端探测密钥本身（`speech.py:189-198`）。

前端还负责长文本切句流水线合成与麦克风 PCM 采集（`speech.ts:41-105`、`206+`），与本代理解耦。

## 不变量

- `ONNX_HUB_API_KEY` 缺失：TTS 503、ASR 4503、status `configured=false`——三处一致。
- TTS 恒 120s/10s 超时、流式 8KB 转发；ASR open 恒 10s、双向泵 FIRST_COMPLETED 收尾。
- 上游错误一律收敛为 502/4502 + 安全文案；密钥只在服务端注入，永不出现在前端可见的响应/URL/状态里。
- `/ws/` 必须有 nginx Upgrade/Connection 透传。

## 代表性测试

`tests/test_speech_api.py`（或并入 `test_chat_api`/路由测试）：未配置 503、上游非 200→502 文案、流式 WAV 头、status 不含密钥、ASR 4503/4502 关闭码路径（可用 ws 客户端模拟）。

相关页：[配置参考](/openwiki/architecture/configuration.md)、[部署架构](/openwiki/architecture/deployment.md)、[认证与安全面](/openwiki/concepts/security.md)、[失败与可观测性](/openwiki/operations/failure-and-observability.md)。
