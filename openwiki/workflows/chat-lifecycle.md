---
type: workflow-guide
title: 聊天请求生命周期（SSE）
description: 端到端追踪一次聊天回合：slug 解析、身份 cookie、会话 upsert、工厂取图、graph 事件到 SSE 事件契约、回合末 pending 检查、时间旅行重试与错误/告警路径。
tags: [sse, chat, lifecycle, time-travel, identity, event-contract]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-96e2981cfaad30985f414a47
    resource: repo://src/a2a_gateway/routes/chat.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 聊天请求生命周期（SSE）

`src/a2a_gateway/routes/chat.py` 定义公开对话路由：SSE 流式响应 + 会话历史 + time-travel 重试 + 会话目录。一次回合从 HTTP POST 进入到 SSE `done`/`error` 关闭，链路如下。

## 端到端回合流程

```
POST /api/chat[/{slug}]  (message, thread_id?)
  │
  ├─ _resolve_agent(slug)           → slug 归一 + PUBLISHED 校验，否则 404
  ├─ thread_id = req.thread_id or new_thread_id()
  ├─ ensure_identity(request)       → 匿名/登录身份；issued 时签发 cookie
  ├─ _register(...)                 → upsert_conversation 目录登记（失败只记日志）
  └─ EventSourceResponse(_stream_chat(agent, message, thread_id))
        │  （issued → set_identity_cookie(resp)）
        ▼
     get_agent_instance(agent)      → 工厂缓存 (id, updated_at)，失败 → error 事件 + 告警
        ▼
     _stream_graph_events(graph, config, graph_input, thread_id)
        │  astream_events(version="v2") 逐事件翻译（见事件契约）
        ├─ on_chat_model_stream (node=agent) → token
        ├─ on_tool_start / on_tool_end       → tool_start / tool_end
        ├─ （流结束）pending_store.get(thread_id) → 有挂起则 interrupt
        ├─                                   → 恒发 done
        └─ 任意异常 → error 事件 + logger.exception + notify_alert
```

### 各步细节

1. **slug 解析** `_resolve_agent`（`chat.py:62-71`）：`"" | "/" | "default"` → 默认 Agent（库内存 `"/"`，前端用空串表示，`chat.py:58-59`）；`get_agent_by_slug` 未找到或 `status != PUBLISHED` → **404 `对话 Agent 不存在或未发布`**。
2. **thread_id**：请求可携带续聊 `thread_id`，缺省 `new_thread_id()` 新建。
3. **身份 cookie** `ensure_identity`（`identity.py`）：匿名访客首次签发，登录用户绑定；`issued` 时 `set_identity_cookie`——普通 JSON 响应与 SSE 响应都可写（`chat.py:184-187`、`293-294`）。前端挂载时先调 `POST /api/chat/identity` 一次。
4. **会话 upsert** `_register`（`chat.py:379-395`）：`upsert_conversation(identity, thread_id, slug, message[:24] 作标题)`；**归属不成立（会话属于他人）时静默跳过——不阻断对话，只是不写目录**；异常 `logger.exception` 不回滚请求。
5. **取图**：`get_agent_instance` 见 [Agent 图构建](/openwiki/workflows/agent-graph.md)；失败 → `error {detail: Agent 加载失败}` + `notify_alert`（`chat.py:125-131`）。
6. **恢复轮不在路由拦截**：会话有挂起任务时上下文由 `pre_model_hook` 注入，模型自行调 `a2a_resume` 带 task_id 续跑（`chat.py:121-123` docstring）——详见 [input-required 恢复](/openwiki/workflows/input-required-resume.md)。

## SSE 事件契约

`_sse(event, payload)` = `{event, data: JSON}`（`chat.py:74-75`）。六种事件：

| event | payload | 触发 |
|---|---|---|
| **`token`** | `{content}` | `on_chat_model_stream` 且 `metadata.langgraph_node == "agent"`——**只透传主对话模型 token，过滤 pre_model_hook 里的摘要 LLM 调用**（`chat.py:89-95`） |
| **`tool_start`** | `{name}` | `on_tool_start` |
| **`tool_end`** | `{name, output: str}` | `on_tool_end`，output 经 `str()` |
| **`interrupt`** | `{question}` | **流结束后**复查 `pending_store.get(thread_id)` 命中——本轮开始无挂起、结束时出现即下游刚进入中断（`chat.py:104-111`） |
| **`done`** | `{thread_id}` | 正常轮末恒发（在 interrupt 之后）（`chat.py:112`） |
| **`error`** | `{detail}` | 图执行异常 → `对话处理失败，请稍后重试`；构图失败 → `Agent 加载失败`；重试无目标 → `没有可重试的对话`；历史读取失败 → `重试失败，请稍后重试`。**detail 恒为友好文案，不泄漏底层** |

顺序保证：token/tool_* 若干 →（可选 interrupt）→ done；异常路径以 error 结束且**不再发 done**（`_stream_graph_events` 的 try 包住全序列，except 分支 yield error 后函数结束）。

轮末 pending 查询失败 → `logger.exception` + `pending = None`（降级为无 interrupt，照常 done，`chat.py:105-109`）。

## 路由注册顺序约束

FastAPI 按注册顺序匹配路径参数，模块 docstring 与行内注释明确两处约束：

1. **带字面量的路径必须先注册**：`identity`、`conversations`（及 `retry`）注册在 `/api/chat/{slug}` **之前**，否则被 slug 通配吃掉（`chat.py:14-15`、`176`）。
2. **`/api/chat/retry` 必须先于 `/api/chat/{slug}`**——否则 `"retry"` 被当作 slug 解析（`chat.py:314` 行内注释）。

实际注册顺序（源码行序）：identity → conversations CRUD/import → `POST /api/chat` → `POST /api/chat/retry` → `POST /api/chat/{slug}` → `POST /api/chat/{slug}/retry` → `GET /api/chat/history` → `GET /api/chat/{slug}/history`。`{slug}/retry` 因有 `/retry` 后缀天然不与 `{slug}` 冲突，但仍放在其后依赖字面量优先规则保护 `retry`。

路由表：

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/chat/identity` | 确保身份 cookie |
| GET/POST | `/api/chat/conversations` | 列表 / 登记（归属冲突返回 `null` 不抢） |
| POST | `/api/chat/conversations/import` | localStorage 批量迁移 |
| PATCH/DELETE | `/api/chat/conversations/{thread_id}` | 重命名 / 删除 |
| POST | `/api/chat`、`/api/chat/{slug}` | 对话 SSE |
| POST | `/api/chat/retry`、`/api/chat/{slug}/retry` | 重试 SSE |
| GET | `/api/chat/history`、`/api/chat/{slug}/history` | 历史（slug 仅占位，thread_id 全局键，`chat.py:369-370`） |

**归属校验** `_ensure_owned`（`chat.py:398-406`）：retry/history 必经——会话存在且 `identity.owns(conv)` 为假 → **404 `会话不存在`**（不区分「存在但他人」）；**未登记会话放行**（回填前的历史数据不受影响）。删会话时顺带清 pending 与 `checkpointer.adelete_thread`，两者失败只记日志、**不回滚目录删除**（避免删不掉的幽灵会话，`chat.py:264-274`）。

## Time-travel 重试（checkpoint 回放）

`_retry_stream`（`chat.py:139-172`）：

1. `graph.aget_state_history(config)` **倒序**扫描 checkpoint 历史；
2. 找「最新一个**以 HumanMessage 结尾、且 `snapshot.next` 非空**（仍有待执行节点）」的快照作为 target（`chat.py:149-157`）——这是「用户说完话、模型还没（或没成功）回答」的分叉点；
3. 读不到历史 → `error {重试失败}`；找不到 target → `error {没有可重试的对话}`；
4. 取 `target.config.configurable.checkpoint_id`，构造 `retry_config = {configurable: {thread_id, checkpoint_id}}`；
5. 调 `_stream_graph_events(graph, retry_config, graph_input=None, thread_id)`——**`graph_input is None` 即 time travel 重放**：checkpoint 已含待处理输入，模型在该点重新生成，**其后的旧回复随之分叉丢弃**（`chat.py:83`、`149-150`）。

事件契约与新对话完全共用（同 `_stream_graph_events`），故重试同样产出 token/tool_*/interrupt/done/error。

`graph_input` 形参约定（`chat.py:78-84`）：非 None = 新输入追加；None = 重放。实现层 `astream_events(None, config_with_checkpoint_id)` 依赖 LangGraph 的 checkpoint 续跑语义。

## 错误与告警路径汇总

| 阶段 | 用户可见 | 服务端 |
|---|---|---|
| Agent 不存在/未发布 | HTTP 404 | 路由层直接抛 |
| 归属不符（retry/history） | HTTP 404 | `_ensure_owned` |
| 构图失败 | SSE `error {Agent 加载失败}` | `logger.exception` + `notify_alert("Agent 加载失败")` |
| 图执行异常 | SSE `error {对话处理失败，请稍后重试}` | `logger.exception` + `notify_alert("对话流式失败")` |
| 轮末 pending 查询失败 | 无 interrupt，照常 done | `logger.exception` |
| 目录登记失败 | 无感（对话继续） | `logger.exception` |
| 删会话时 pending/检查点清理失败 | 仍返回 `{ok:true}` | `logger.exception`（不回滚） |
| 重试历史读取失败 | SSE `error {重试失败，请稍后重试}` | `logger.exception` |
| 重试无目标 | SSE `error {没有可重试的对话}` | 无（正常业务分支） |
| 历史读取失败 | HTTP 500 `{读取会话历史失败}` | `logger.exception` |

完整 fail-soft 清单见 [故障处理与可观测性](/openwiki/operations/failure-and-observability.md)。

## 历史读取（刷新还原）

`_get_history`（`chat.py:409-454`）：`checkpointer.aget_tuple(thread_id)` → `channel_values.messages` → 映射为前端契约：

- `human` → `{role: "user", content}`；
- `tool` → `{role: "tool", content: "", toolName, toolOutput}`——**工具调用以 role=tool 返回，刷新后仍还原工具卡片而非折叠成助手文本**（`chat.py:411-414`）；
- `ai`/`system` 等 → 仅有文本内容时 `{role: "assistant", content}`。

元组为 `None` → `[]`；读取异常 → HTTP 500。

## 不变量

- SSE 契约六事件固定；`token` 只来自主 `agent` 节点（摘要 LLM 不上屏）；`done` 恒有、`error` 终止流。
- 字面量路由（identity/conversations/retry）必须注册在 `{slug}` 之前。
- 身份 cookie 在首次访问的响应上签发（JSON 与 SSE 均可）。
- retry = 找「人类消息结尾 + next 非空」的最新 checkpoint 回放，旧回复分叉丢弃。
- 归属不符 404、未登记放行；目录登记与清理失败均 fail-soft 不阻断主流程。
- 恢复轮不在路由层拦截，由 hook 注入 + 模型自调 `a2a_resume`。

## 代表性测试

- `tests/test_chat_api.py`：SSE 事件序列、retry（`test_retry_replays_from_last_human_checkpoint`、`without_retryable → error`）、**错误事件友好文案**、history 映射与 404。
- `tests/test_chat_input_required.py`：恢复分支走 graph、轮末 interrupt、删会话清挂起。
- `tests/test_conversations.py`：目录 upsert 归属不抢、import、rename/delete。
- 身份：`identity` 相关测试（cookie 签发/认领）并入 conversations/chat 套件。

相关页：[Agent 图构建与编排](/openwiki/workflows/agent-graph.md)、[身份与会话](/openwiki/concepts/identity-and-conversations.md)、[input-required 恢复](/openwiki/workflows/input-required-resume.md)、[失败与可观测性](/openwiki/operations/failure-and-observability.md)。
