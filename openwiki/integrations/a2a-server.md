---
type: integration-guide
title: 对外 A2A 服务端
description: 说明 Agent Card 发布与 X-Forwarded 派生公网 URL、JSON-RPC 方法别名分发、流式任务帧协议（WORKING→COMPLETED/INPUT_REQUIRED）、API Key 鉴权与无状态任务语义。
tags: [a2a-server, json-rpc, agent-card, api-key, stateless, sse]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-21cd86d1835d8fa9e2e76fca
    resource: repo://nginx/default.conf
  - id: openwiki-source-43725894c7c3e0df0d15d0b5
    resource: repo://src/a2a_gateway/config.py
  - id: openwiki-source-069aef3b450adb88d536a8b2
    resource: repo://src/a2a_gateway/pending_store.py
  - id: openwiki-source-ba9c8ddd38a929ac68fef375
    resource: repo://src/a2a_gateway/public_url.py
  - id: openwiki-source-d38fe19aaef9124307badd98
    resource: repo://src/a2a_gateway/routes/a2a_server.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 对外 A2A 服务端

`src/a2a_gateway/routes/a2a_server.py` 把管理中心配置的 Agent 以 A2A 协议对外发布：发现（Agent Card）+ 调用（JSON-RPC）。网关本身**不持久化 A2A 任务**——`GetTask`/`CancelTask` 恒返回 `TaskNotFound`；`input-required` 恢复依赖挂起表 `pending_store`（见 [input-required 恢复](/openwiki/workflows/input-required-resume.md)）。

## 路由与发现

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/a2a`、`/a2a/.well-known/agent-card.json` | 默认 Agent（slug 归一为 `/`）卡片，公开 |
| GET | `/a2a/{slug}/.well-known/agent-card.json` | 标准 well-known 卡片，公开 |
| GET | `/a2a/{slug}` | 卡片根路径，便于人工调试 |
| POST | `/a2a`、`/a2a/{slug}` | JSON-RPC 入口，**需该 Agent 自己的 API Key** |

slug 归一：`"" | "/" | "default"` → 默认 Agent（`_normalize_slug`，`a2a_server.py:96-105`）；对外路径 `a2a_path_for_slug`：默认 Agent 为 `/a2a`，否则 `/a2a/{slug}`（`a2a_server.py:99-101`）。仅 `status=PUBLISHED` 可解析，否则 404（`a2a_server.py:138-145`）。

卡片内容（`build_agent_card`，`a2a_server.py:154-179`）：单 `supported_interfaces`（JSONRPC、protocol_version 1.0）、`capabilities.streaming=true`、输入输出 `text/plain`、单个 `AgentSkill`（id=`agent-{id}`）。

## Agent Card 公网 URL（X-Forwarded 派生）

`public_url.public_base_url`（`public_url.py:29-47`）入口自适应：

- 协议：`X-Forwarded-Proto` 优先（多级代理逗号列表取首个，`first_header_value`），否则请求实际 scheme；
- 主机：`X-Forwarded-Host` 优先，否则 `Host`；同样取首个值；
- 端口：主机自带端口原样保留；不带端口且 `X-Forwarded-Port` 非协议默认端口（http/80、https/443）才补上；
- IPv6 字面量经 `split_host_port` 兼容（`public_url.py:17-26`）。

nginx 反代配合（`nginx/default.conf:17-44`）：`map` 保证上游声明过 `X-Forwarded-*` 则透传、缺失按本层补全；直连入口用 `$http_host` **原样保留端口**（`$host` 会剥掉端口）。`/a2a`（等值）与 `/a2a/` 两个 location 必须都转发后端，否则落入前端产生 301 重定向循环（`nginx/default.conf:63-85`）；SSE 关闭缓冲、`proxy_read_timeout 3600s`。详见 [部署架构](/openwiki/architecture/deployment.md)。

## API Key 鉴权

`validate_api_key`（`a2a_server.py:111-132`）：取 `X-Api-Key` 头，缺失则回退 `Authorization: Bearer <key>`；Key 必须**存在、已启用、且 `agent_id` 等于被调用 Agent**，否则 401（`WWW-Authenticate: Bearer`）。GET 卡片端点不鉴权（公开发现）；POST 前先解析已发布 Agent 再验 Key（`a2a_server.py:267-269`）。密钥管理见 [认证与安全面](/openwiki/concepts/security.md)。

## JSON-RPC 方法别名（1.0 / 0.3）

`METHOD_ALIASES`（`a2a_server.py:82-93`）：

| 1.0 | 0.3 别名 | 实现 |
|---|---|---|
| `SendMessage` | `message/send` | 非流式 `_rpc_send_message` |
| `SendStreamingMessage` | `message/stream` | SSE 流 `_rpc_stream`（`STREAMING_METHODS`） |
| `GetTask` | `tasks/get` | 恒 `TaskNotFound` |
| `CancelTask` | `tasks/cancel` | 恒 `TaskNotFound` |

别名未命中 → `MethodNotFound`；`jsonrpc != "2.0"` / body 非 dict / params 非 dict → `InvalidRequest`（`a2a_server.py:275-284`）。

## 无状态任务语义

- **GetTask / CancelTask 恒返回 `TaskNotFoundError`**：参数仍经 `ParseDict` 校验形状（`a2a_server.py:286-292`）。网关不做 A2A 任务持久化——对外 task_id 只在响应帧与挂起表中短暂存在。
- 文本提取兜底：先取消息文本；为空再扫 data parts 的 `query`/`text`（网关作为客户端时用 `{"query": ...}` 格式，`a2a_server.py:304-318`）。
- **task_id = context_id 生成规则**（`a2a_server.py:320-328`）：请求带 `task_id` → 沿用（`context_id` 缺省回填 task_id）；新任务 → `task_id = context_id = 请求 context_id 或 uuid4().hex`。标识统一使**挂起表主键（thread_id）可被恢复请求的 task_id 命中**（链路 B 以对外 task_id 为键，链路 A 以会话 thread_id 为键，`pending_store.py:1-6`）。
- 带 `task_id` 的请求进入**恢复分支**（见下节）；查不到挂起记录或 `pending.agent_id != agent.id` → `TaskNotFound`（`a2a_server.py:330-337`）。

## 流式任务帧协议（SendStreamingMessage）

`_rpc_stream`（`a2a_server.py:423-494`）按 A2A 1.0 任务式语义发 SSE 帧：

1. **首帧** `StreamResponse.task`：`WORKING` 快照——标准客户端收到裸 message 事件会视为「最终完整回复」并立刻关流（a2a-sdk `base_client._process_stream`），故增量文本不能直接发 message；
2. **中间帧** `status_update`：`WORKING` + `message` 携带增量文本（`new_text_status_update_event`）；
3. **轮末复查挂起表**：有挂起 → `status_update` 状态 `INPUT_REQUIRED`、文本为追问；无挂起 → `status_update` 状态 `COMPLETED`（挂起表查询异常降级为 COMPLETED，保证无中断场景行为不变，`a2a_server.py:453-482`）；
4. 流内异常 → `event: error` 帧（`build_error_response` + `InvalidRequestError`，`a2a_server.py:483-494`）。

**非流式 `_rpc_send_message`**（`a2a_server.py:384-408`）：等图跑完聚合全部 chunk；复查挂起表——有挂起返回 `SendMessageResponse(task)` 状态 `INPUT_REQUIRED` + 追问文本，否则返回普通 `message`。

增量文本由 `_stream_agent_text` 驱动 LangGraph `astream_events(version="v2")`，只取 `on_chat_model_stream` 事件的 chunk（`a2a_server.py:367-381`）；`thread_id` 即 `context_id`，与对话链路共用 Checkpointer。

## 恢复分支（wrapper 匹配、pending 结算、历史回写）

带 `task_id` 的请求不跑本地图，而是**透明转发下游**（`a2a_server.py:330-350`）：

1. **wrapper 匹配** `_match_wrapper`（`a2a_server.py:500-507`）：`get_agent_wrappers(agent)` 后按 `w.target.url == pending.target_url` 找到挂起时的下游包装器；找不到（配置已变化）→ 删除挂起记录并报「下游目标配置已变化」错误。
2. **转发** `wrapper.stream_message_events(text, task_id=pending.task_id, context_id=pending.context_id or None)`：`TextChunk` 转成 `WORKING` 增量帧（流式）或聚合（非流式）；`InputRequired` 记为 `again`。
3. **失败**（`A2ATargetError`）：告警 `notify_alert` + 删除挂起 + 流内/响应级错误帧（`a2a_server.py:580-588`、`644-653`）。
4. **pending 结算** `_settle_pending`（`a2a_server.py:528-541`）：再次中断 → `upsert` 刷新记录（可能携带新 task_id/question）；完成 → `delete`。挂起表 TTL 默认 86400s（`config.py:96`），过期读即删（`pending_store.py:107-115`）。
5. **历史回写** `_append_resume_history`（`a2a_server.py:510-525`）：把恢复轮的「用户补充 `HumanMessage` + 下游回复 `AIMessage`」经 `graph.aupdate_state(..., as_node="agent")` 追加进 LangGraph 线程，使 Checkpointer 中的会话历史与对外任务进度一致；失败仅记日志、不影响主流程。
6. **收尾帧**：`again` 存在 → `INPUT_REQUIRED` + 追问文本；否则 → `COMPLETED`（流式 `a2a_server.py:593-615`；非流式返回 `SendMessageResponse(task)` `a2a_server.py:658-671`）。流式恢复**不重发 Task 首帧**（`a2a_server.py:552`）。

## 挂起表（pending_store）

`DbPendingStore` 落 PostgreSQL `pending_a2a_tasks` 表，`PendingStore` 协议允许测试注入替身；`default_pending_store` 为路由层与工具层共用单例（`pending_store.py:47-132`）。记录含 `thread_id/agent_id/target_url/target_name/task_id/context_id/question`（`PendingRecord` 快照）。链路 A（对话界面 `a2a_call`/`a2a_resume` 工具）与链路 B（对外 A2A Server）共用同一存储，键在新任务时同值（`pending_store.py:1-6`）。

## 不变量

- 卡片地址必须由 `public_base_url` 推导，不得硬编码；nginx 必须保留直连端口并透传 `X-Forwarded-*`。
- POST 恒鉴权且 Key 必须属于目标 Agent；GET 卡片恒公开。
- 网关不持久化 A2A 任务：GetTask/CancelTask 恒 TaskNotFound；无状态语义靠挂起表 + Checkpointer 线程补足。
- 新任务 `task_id == context_id`，保证挂起表主键可被恢复请求命中。
- 流式必须首帧 Task(WORKING)、增量走 status_update、轮末 COMPLETED/INPUT_REQUIRED 二选一。

## 代表性测试

`tests/test_a2a_server.py`：API Key 鉴权三则（无 Key/停用/跨 Agent）、卡片公开与 404、`public_base_url` 六则（host 端口、Forwarded host/proto/port、IPv6）、卡片 URL 保端口、流式 `input-required` 首帧与恢复、未知 task 404、task/context 同 id 规则、非流式 INPUT_REQUIRED 快照。

相关页：[A2A 上游客户端](/openwiki/integrations/a2a-client.md)、[input-required 恢复流程](/openwiki/workflows/input-required-resume.md)、[聊天生命周期](/openwiki/workflows/chat-lifecycle.md)、[部署架构](/openwiki/architecture/deployment.md)。
