---
type: operations-guide
title: 故障处理与可观测性
description: 汇总失败语义：全局 500 兜底、A2A 错误分类、SSE 错误不泄漏策略、告警 webhook 的降级、关键超时清单与故障弱化（fail-soft）模式。
tags: [failure, observability, alerting, timeouts, fail-soft, logging]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-9c1ca7548fb268f534ce3334
    resource: repo://src/a2a_gateway/a2a_client.py
  - id: openwiki-source-43725894c7c3e0df0d15d0b5
    resource: repo://src/a2a_gateway/config.py
  - id: openwiki-source-d165432686ca5f4c2a6f65ba
    resource: repo://src/a2a_gateway/connectors/pipeline.py
  - id: openwiki-source-4febd71d669a7c84b1d2f5c0
    resource: repo://src/a2a_gateway/graph.py
  - id: openwiki-source-5587127d632cfcdc010b44e9
    resource: repo://src/a2a_gateway/main.py
  - id: openwiki-source-452ba3d6ee67065515a01609
    resource: repo://src/a2a_gateway/mcp_client.py
  - id: openwiki-source-89f09f62f50e4b4e618e9e68
    resource: repo://src/a2a_gateway/notifier.py
  - id: openwiki-source-d38fe19aaef9124307badd98
    resource: repo://src/a2a_gateway/routes/a2a_server.py
  - id: openwiki-source-96e2981cfaad30985f414a47
    resource: repo://src/a2a_gateway/routes/chat.py
  - id: openwiki-source-5172018175a2fceb3121bd4d
    resource: repo://src/a2a_gateway/routes/speech.py
  - id: openwiki-source-1157ad22d08962e1ffa75962
    resource: repo://src/a2a_gateway/skills.py
  - id: openwiki-source-4df82bf1a4678a53b6438ca1
    resource: repo://src/a2a_gateway/tools.py
  - id: openwiki-source-e74227ee06b16f894c3e3826
    resource: repo://TODO.md
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 故障处理与可观测性

网关的失败语义可归纳为一条主线：**异常收敛为安全文案返回用户/调用方，完整堆栈只进服务端日志，关键事件经 `notify_alert` 外推，而告警本身失败绝不影响主流程**（fail-soft）。本文汇总各层兜底、错误分类、超时参数与日志/告警调用点。

## 全局 500 兜底

`main.py` 注册 `@app.exception_handler(Exception)`（`main.py:77-84`）：

- `logger.exception("未捕获异常 %s %s", method, path)` —— 完整堆栈进日志；
- 响应恒 `500 {"detail": "服务器内部错误，请稍后重试"}` —— **不暴露底层异常**（TODO.md:122 记录此为统一错误处理项）。

配套：启动期 Alembic 迁移失败 → `logger.exception` 后**回退 `create_all`**（仅建表、不做版本管理，`main.py:42-47`）；`/health` 恒 `{"status":"ok"}`。

## A2A 错误分类

`A2ATargetError(kind, detail)` 三分类（`a2a_client.py:94-100`、`_classify:181-191`）：

| kind | 触发 | 后续语义 |
|---|---|---|
| `network` | 卡片解析失败 / `httpx.HTTPError` | 可重试（`backoff=0.5`、最多 2 次），已产出内容则不重试 |
| `timeout` | `A2AClientTimeoutError` / `httpx.TimeoutException` | 同上；注意 `httpx.TimeoutException` 必须显式归 timeout，否则落入 `httpx.HTTPError` 分支被误标 network（TODO.md:211-212 为已修正缺陷） |
| `target_error` | `A2AClientError` 及未知异常 | **不重试** |

重试与「有输出不重放」详见 [A2A 上游客户端](/openwiki/integrations/a2a-client.md)。

## SSE 错误不泄漏策略

前端可见的流式错误一律**固定文案**，细节只进日志/告警：

| 位置 | 触发 | 用户可见 | 服务端 |
|---|---|---|---|
| 网页聊天 SSE（`chat.py:113-116`） | 图执行异常 | `{"detail":"对话处理失败，请稍后重试"}` | `logger.exception` + `notify_alert("对话流式失败")` |
| 网页聊天 SSE（`chat.py:127-130`） | Agent 实例构建失败 | `{"detail":"Agent 加载失败"}` | `logger.exception` + `notify_alert("Agent 加载失败")` |
| 网页重试（`chat.py:158-164`） | 检查点读取失败 / 无可重试消息 | `重试失败…` / `没有可重试的对话` | `logger.exception` |
| A2A 流式（`a2a_server.py:483-494`） | 流内异常 | `event: error` 帧 + `Agent 处理失败` | `logger.exception` |
| A2A 恢复（`a2a_server.py:580-588`、`644-653`） | `A2ATargetError` | `目标暂时不可用，请稍后重试`（流内 error 帧或 JSON-RPC error） | `logger.warning` + `notify_alert` + 删挂起 |
| A2A RPC 顶层（`a2a_server.py:346-364`） | 处理异常 | JSON-RPC `InvalidRequestError("Agent 处理失败")` | `logger.exception` |
| 语音 TTS/ASR（`speech.py`） | 上游非 200 / 连接失败 | 统一 502（detail 仅类名或上游 `detail` 字段）/ WS 4502+安全 reason | `logger.exception/warning` |
| 连接器管线（`pipeline.py:114-123`） | 120s 超时 / 处理异常 | `REPLY_FAILURE` 文案 | `logger.error/exception`，异常另 `notify_alert` |

原则：HTTP 层不回堆栈；JSON-RPC 层不回内部 message；SSE 层 `error` 事件只带友好 `detail`；WS 层用自定义关闭码（4502/4503）+ 短 reason。

## 告警 webhook 与降级

`notifier.notify_alert(title, detail, *, level="error")`（`notifier.py:23-40`）：

- **未配置 `ALERT_WEBHOOK_URL`** → 仅 `logger.error("[告警] %s | %s", ...)`（不依赖任何外部服务）；
- 配置后 → POST `{level, title, detail}`，可带 `Authorization: Bearer <ALERT_WEBHOOK_TOKEN>`；
- **超时 `_ALERT_TIMEOUT = 3.0s`**；
- **所有异常吞掉**：推送失败仅 `logger.warning(..., exc_info=True)`，**绝不因告警失败影响主流程**（`notifier.py:8`、`39-40`）。

### 调用点清单（8 处）

| 调用点 | 标题 | detail 要素 |
|---|---|---|
| `tools.py:66` | A2A 调用失败 | `target/kind/error` |
| `tools.py:135` | 挂起任务恢复失败 | `thread/target/kind/error` |
| `routes/chat.py:115` | 对话流式失败 | `thread/error` |
| `routes/chat.py:129` | Agent 加载失败 | `slug/error` |
| `routes/a2a_server.py:582` | A2A 恢复失败 | `task/target/error`（流式） |
| `routes/a2a_server.py:646` | A2A 恢复失败 | 同上（非流式） |
| `connectors/pipeline.py:120` | 连接器消息处理失败 | `connector/chat/error` |
| （连接器超时不告警，仅 `logger.error` + 失败文案；发送失败 `_safe_reply` 只记日志） | — | — |

**吞错语义**：`notify_alert` 内部全吞；调用方无需 try/except。但调用点自身对主流程的处置不同——有的删挂起（A2A 恢复失败）、有的仅记日志继续（历史回写、技能记账）。

## 关键超时 / 重试参数表

| 组件 | 参数 | 值 | 出处 |
|---|---|---|---|
| A2A 出站 httpx | `timeout` | **60s**（覆盖卡片发现+调用） | `a2a_client.py:159` |
| A2A 重试 | `retries` / `backoff` | **2 次 / 0.5**（延迟 0.5s、1.0s；仅 network/timeout 且未产出） | `a2a_client.py:240-241`、`298-300` |
| MCP 握手探测 | `PROBE_TIMEOUT` | **8s**（test_connection / list_tools） | `mcp_client.py:37` |
| MCP 调用 | `CALL_TIMEOUT` | **60s** | `mcp_client.py:39` |
| 告警 webhook | `_ALERT_TIMEOUT` | **3s** | `notifier.py:20` |
| 连接器单条处理 | `_PROCESS_TIMEOUT_SECONDS` | **120s**（`asyncio.wait_for`） | `pipeline.py:31` |
| 连接器队列 | `_QUEUE_LIMIT` | **5**（满则丢新消息回忙提示） | `pipeline.py:30` |
| 连接器去重 | 容量 / TTL | **4096 / 600s** | `pipeline.py:28-29` |
| 挂起任务 TTL | `PENDING_A2A_TTL_SECONDS` | **86400s**（过期读即删） | `config.py:96`、`pending_store.py:112-114` |
| 平台出站（Feishu/Telegram/Slack） | `_TIMEOUT` | **15s**（各自模块常量） | `feishu.py:24` 等 |
| 技能 URL 导入 | `URL_FETCH_TIMEOUT` | **10s**（+3 跳重定向、4MB 上限） | `skills.py:20`、`skill_import.py:352` |
| TTS 代理 | 总/连接 | **120s / 10s** | `speech.py:52` |
| ASR 上游 | `open_timeout` | **10s** | `speech.py:157` |
| 沙箱脚本 | 默认/上限 | **30s / 120s**（输出截断 32KB） | `skills.py:27-28`、`sandbox_client.py:67` |
| nginx | 读写超时（`/api/`、`/a2a/`、`/ws/`） | **3600s**（SSE/WS 长连接） | `default.conf` 各 location |
| MCP httpx（streamable_http） | client timeout | **60s** | `mcp_client.py:97` |

## 日志与结构化记录

日志格式为 `logging.basicConfig` 文本格式 `"%(asctime)s %(levelname)s %(name)s: %(message)s"`（`main.py:31-34`）——**非 JSON 结构化日志**，但消息体普遍采用 **`key=value` 准结构化约定**（`thread=`、`slug=`、`task=`、`connector=`、`chat=`、`kind=`、`error=`、`code=`），便于 grep/采集管道二次解析。级别约定：

- `exception`：本应成功的路径失败（迁移回退、历史回写、会话清理、TTS/ASR 连接、RPC 处理）——含堆栈；
- `error`：确定性失败（未捕获异常、连接器超时、未配置 webhook 时的告警）；
- `warning`：可恢复/预期内失败（A2A 重试、MCP 探测失败、恢复失败、队满、告警推送失败）；
- `info`：生命周期与降级决策（卡片 origin 回退、挂起过期清理、图缓存失效、告警已推送）。

关键采集字段：`request.method/path`（全局兜底）、`A2ATargetError.kind`、`MCP name+tool`、`connector id+chat`、`thread/task id`。

## 故障弱化（fail-soft）模式汇总

网关里「辅助能力失败绝不打断主对话/主请求」的模式成体系出现：

1. **告警**：`notify_alert` 全吞异常，3s 超时（`notifier.py:35-40`）。
2. **MCP**：探测/调用失败一律返回可读字符串，不抛出；`CancelledError` 例外透传（`mcp_client.py` 全模块）。
3. **技能 hook 重注入**：记账失败 → `logger.warning` + 本轮不注入，对话继续（`graph.py:225`）。
4. **历史摘要压缩**：失败 → 本轮跳过压缩（`graph.py:260`）。
5. **挂起查询**：网页 hook / A2A 响应 / 非流式路径查询失败 → 记日志降级为「本轮无挂起」（`graph.py:197`、`a2a_server.py:107-108` 等）。
6. **恢复历史回写** `aupdate_state`：失败仅 `logger.exception`，不阻断恢复响应（`a2a_server.py:524-525`）。
7. **A2A 终态 GetTask 补拉**：失败 `logger.debug` 静默返回（`a2a_client.py:366-372`）。
8. **连接器兜底回复** `_safe_reply` / 忙提示：失败只记日志（`pipeline.py:177-183`、`connectors.py:237-241`）。
9. **图缓存失效失败**、**会话目录登记失败**、**检查点/挂起清理失败**：`chat.py`/`agent_factory` 均只记日志。
10. **Alembic 失败回退 create_all**（`main.py:44-47`）。
11. **Telegram webhook 自动注册失败**：返回 warning 字符串，不阻断保存（`telegram.py:124-126`）。
12. **技能快照 `review_status` 纵深防御**：过滤失败 → 跳过该技能 + 告警日志，不阻断装配（设计规格 §7）。

反例边界（**不是** fail-soft）：API Key 鉴权失败 401、Agent 未发布 404、验签失败 401、队满丢弃（对**该条消息**失败但回忙提示）、`HTTPException` 透传——这些是安全/一致性硬失败，必须中断请求。

## 代表性测试

- `tests/test_notifier.py`：未配置退化为日志、配置后推送 payload、**推送失败被吞**（TODO.md:191）。
- `tests/test_a2a_client.py`：三分类、重试四则（含 `httpx.TimeoutException`→timeout 回归）。
- `tests/test_graph*.py`：hook 记账/摘要失败降级不打断。
- `tests/test_mcp_client.py`：失败文本化、`_format_error` 展开 ExceptionGroup。
- `tests/test_chat_api.py`：`test_chat_stream_error_event_is_friendly`（SSE 文案不泄漏）。
- `tests/test_connector_pipeline.py`：超时/异常回 `REPLY_FAILURE`。
- `tests/test_a2a_server.py`：恢复失败删挂起 + 错误帧。

相关页：[A2A 上游客户端](/openwiki/integrations/a2a-client.md)、[A2A 服务端](/openwiki/integrations/a2a-server.md)、[聊天平台连接器](/openwiki/integrations/connectors.md)、[MCP 集成](/openwiki/integrations/mcp.md)、[测试与质量门禁](/openwiki/operations/testing.md)。
