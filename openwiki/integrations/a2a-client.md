---
type: integration-guide
title: A2A 上游客户端
description: 说明 A2AClientWrapper 的 Agent Card 发现、接口 URL 重写、流式事件抽取、三类错误分类与瞬态重试策略（有输出后不再重试）及连通性测试。
tags: [a2a, client, retries, error-classification, agent-card, outbound-auth]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-9c1ca7548fb268f534ce3334
    resource: repo://src/a2a_gateway/a2a_client.py
  - id: openwiki-source-cf26b275f836c4fa92ad8b8d
    resource: repo://src/a2a_gateway/auth_scheme.py
  - id: openwiki-source-7fd5ad639fdb57e093b58272
    resource: repo://src/a2a_gateway/routes/admin.py
  - id: openwiki-source-c728168ff3e83d7ca738448b
    resource: repo://src/a2a_gateway/routes/registry.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# A2A 上游客户端

`src/a2a_gateway/a2a_client.py` 基于 a2a-sdk 1.x 封装 `A2AClientWrapper`：向绑定的 A2A 目标发送消息、流式接收结果，并把失败归类为 `network` / `timeout` / `target_error`，供日志、前端与重试策略共用。上层消费方：`tools.py` 的 `a2a_call` / `a2a_resume` 工具与连通性测试端点。

## 错误三分类

`A2ATargetError(kind, detail)`（`a2a_client.py:94-100`）；`_classify` 映射规则（`a2a_client.py:181-191`）：

| kind | 触发异常 | 语义 |
|---|---|---|
| `network` | 卡片解析失败 / `httpx.HTTPError` | URL 不可达、Agent Card 未发布、连接失败（配置或网络问题） |
| `timeout` | `A2AClientTimeoutError` / `httpx.TimeoutException` | 调用超时 |
| `target_error` | `A2AClientError` 及其它未知异常 | 目标 Agent 内部错误 |

模块 docstring 同样把「连接失败 / Agent Card 解析失败」归为网络或配置问题（`a2a_client.py:1-8`）。仅 `network` 与 `timeout` 参与瞬态重试；`target_error` 一律不重试（`a2a_client.py:298`）。

## 重试策略（瞬态 only）

`stream_message_events` 循环 `for attempt in range(retries + 1)`，默认 `retries=2`、`backoff=0.5`（`a2a_client.py:234-241`）：

- 重试条件：**尚未产出任何输出**（`yielded == False`）且 `attempt < retries` 且 `kind in ("network", "timeout")`（`a2a_client.py:298`）。
- 延迟：`delay = backoff * (attempt + 1)` → 0.5s、1.0s（`a2a_client.py:300`），每次失败 `logger.warning` 记录 kind/第几次重试/详情。
- 一旦 `yielded`（已产出任意文本或状态事件）：即使网络错误也**直接抛出**，绝不对已有输出的流重放——避免下游收到重复内容。
- 已见终态 task 快照但流中无 artifact：记 `saw_final_task`，跳过补拉（`a2a_client.py:266-279`）。

代表性测试：`test_retries_transient_errors_then_succeeds`、`test_does_not_retry_target_error`、`test_gives_up_after_retries_exhausted`、`test_does_not_retry_after_partial_output`（`tests/test_a2a_client.py:59-127`）。

## Agent Card 发现与接口 URL 重写

1. **发现**：`_fetch_agent_card` 用 `A2ACardResolver` 解析；若带路径 URL 失败（典型误配：把 RPC 端点 `http://host:10101/a2a` 当服务地址，well-known 拼成 `/a2a/.well-known/...` 而 404），回退 `_origin_url`（取 `scheme://host[:port]`，本就不带路径则原样返回）再试一次（`a2a_client.py:103-144`）。
2. **接口改写**：A2A 1.0 的接口地址位于 `card.supported_interfaces[].url`，常是目标内部地址（如 `http://localhost:9901/a2a`）。`_merge_interface_url`（`a2a_client.py:111-127`）：
   - `target_url` 自带非 `/` 路径 → 视为用户显式端点，原样使用；
   - 否则以 `target_url` 的 scheme/host/port 为基准、**保留卡片声明的 RPC 路径**（否则请求打到根路径 404），并保留 `target_url` 上的查询串（query 鉴权参数挂在这里）。
3. **客户端构建**：`ClientConfig(streaming=True, polling=False, httpx_client=http, supported_protocol_bindings=[TransportProtocol.JSONRPC])` → `ClientFactory(config).create(card)`（`a2a_client.py:171-177`）。httpx 客户端 `timeout=60.0`（`a2a_client.py:159`）。
4. 每次 `_ensure_client` 惰性构建并缓存；`close()` 关闭 a2a-sdk 客户端与 httpx 客户端（异常仅 debug 日志，`a2a_client.py:377-396`）。

## 60s 超时与出站认证

- **超时**：`httpx.AsyncClient(..., timeout=60.0)` 一次性覆盖卡片发现与后续调用（`a2a_client.py:159`）。
- **共享出站认证**：`auth_scheme.py`（A2A 与 MCP 共用）。`token` 统一承载密钥，`auth_type` 决定放置位置（`auth_scheme.py:10-14`）：
  - `none`：不注入（`build_headers` 返回空，`auth_scheme.py:34-35`）；
  - `bearer`（默认，`DEFAULT_AUTH_TYPE`）：`Authorization: Bearer <token>`；
  - `header`：`<auth_name 或 Authorization>: <token>`；
  - `query`：`apply_query_auth` 拼 `?<auth_name 或 access_token>=<token>` 进 URL（不进请求头，`auth_scheme.py:47-57`）；
  - `basic`：`Authorization: Basic base64(<auth_name>:<token>)`。
- 包装器在 `_ensure_client` 中先 `build_headers` + `apply_query_auth` 再建卡（`a2a_client.py:157-158`），query 参数随 `_merge_interface_url` 保留到改写后的接口地址。stdio MCP 场景无 HTTP 头，改注入 `MCP_AUTH_*` 环境变量（`auth_scheme.py:26-73`）。

## 流式事件模型

三态事件（`a2a_client.py:53-76`）：

- `TextChunk(text)`：普通文本片段；
- `InputRequired(task_id, context_id, question)`：下游 `input-required`，带恢复句柄；
- `Completed(task_id)`：终态（`TERMINAL_STATES` = completed/failed/canceled/rejected，`a2a_client.py:43-50`）。

**事件顺序约定**：`_extract` 先产出文本，再由 `_state_events` 产出信号（「先文本后信号」，`a2a_client.py:193-197`）。`_state_events` 同时处理 `task` 快照与 `status_update` 两种响应形态（`a2a_client.py:194-232`）。

**文本抽取 `_extract`**（`a2a_client.py:318-350`）覆盖四种响应形态：

- `task`：`status.message` 文本 + 全部 `artifacts`（SDK 常把最终结果聚合成单个 task 快照，若跳过会丢掉全部内容）；
- `status_update`：`message` 文本；终态时不再继续产出；
- `artifact_update`：artifact 文本 + data part（`MessageToDict` 后 `_restore_integers` 还原浮点整数再 JSON 序列化，`a2a_client.py:79-91`、`352-361`）；
- `message`：直接取文本。

**发送消息**：`Message` 携带 data part `{"query": text}`（application/json）+ text part（text/plain）；传 `task_id` 即向任务追加用户输入恢复（A2A 协议，`a2a_client.py:250-261`）。

## 终态零产出兜底（GetTask 回取）

流结束后若 `terminal_task_id` 存在、但**既未见 artifact、也未见终态 task 快照**（`saw_artifact == False and saw_final_task == False`），补拉一次 `GetTask` 提取产物（`a2a_client.py:288-293`、`363-375`）：

- 目标只在最终快照放产物而流中无 artifact 事件时，避免「目标已跑完但调用方拿不到任何内容」；
- 补拉失败仅 `logger.debug`，不影响主流程（尽力而为，`a2a_client.py:366-372`）；
- 已见产物或已见终态快照则跳过（`test_does_not_refetch_when_artifact_already_received`）。

## 与工具层的集成

- `a2a_call`（`tools.py:43-96`）：聚合 `TextChunk`；遇 `InputRequired` 写入 `pending_store`（thread_id/agent_id/target/task_id/context_id/question）并把追问原样返回给模型转述；`A2ATargetError` 触发 `notify_alert` 并返回友好失败文案。
- `a2a_resume`（`tools.py:99-168`）：按 pending 记录锁定目标，用 `task_id`/`context_id` 恢复；无 pending 引导模型改用 `a2a_call`；恢复失败删除 pending 并告警。详见 [input-required 恢复流程](/openwiki/workflows/input-required-resume.md)。
- `stream_message` 为兼容旧调用方的纯文本迭代器（`a2a_client.py:310-316`）。

## 连通性测试

- `POST /api/admin/a2a-endpoints/{id}/test`（`registry.py:145-157`）与 `POST /api/admin/agents/test-connection`（`admin.py:238-254`）均调用 `wrapper.test_connection()`：`_ensure_client` 成功返回 `(True, "已成功连接 <url>")`，`A2ATargetError` 返回 `(False, str(e))`（`a2a_client.py:377-382`）。即只验证「卡片可解析 + 客户端可构建」，不发送消息。

## 代表性测试

- `tests/test_a2a_client.py`：分类（`test_classify_maps_error_kinds`）、重试四则、URL 改写三则、origin 回退、抽取与事件顺序、GetTask 回取有/无 artifact、整数还原、`test_connection` 失败上报。
- `tests/test_auth_scheme.py`：五种鉴权的 header/query/stdio 行为与校验。

相关页：[A2A 服务端](/openwiki/integrations/a2a-server.md)、[认证与安全面](/openwiki/concepts/security.md)、[失败与可观测性](/openwiki/operations/failure-and-observability.md)、[input-required 恢复流程](/openwiki/workflows/input-required-resume.md)。
