---
type: workflow-guide
title: input-required 与恢复流程
description: 解释下游 InputRequired 如何写入 pending 存储、SSE interrupt 事件、下一回合提示注入，以及聊天链（thread_id）与 A2A 链（external task_id）两条恢复路径与 TTL 过期语义。
tags: [input-required, pending-store, resume, ttl, dual-chain, interrupt]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-43725894c7c3e0df0d15d0b5
    resource: repo://src/a2a_gateway/config.py
  - id: openwiki-source-4febd71d669a7c84b1d2f5c0
    resource: repo://src/a2a_gateway/graph.py
  - id: openwiki-source-069aef3b450adb88d536a8b2
    resource: repo://src/a2a_gateway/pending_store.py
  - id: openwiki-source-d3e47f45c8a3dad144965b78
    resource: repo://src/a2a_gateway/repository.py
  - id: openwiki-source-d38fe19aaef9124307badd98
    resource: repo://src/a2a_gateway/routes/a2a_server.py
  - id: openwiki-source-96e2981cfaad30985f414a47
    resource: repo://src/a2a_gateway/routes/chat.py
  - id: openwiki-source-4df82bf1a4678a53b6438ca1
    resource: repo://src/a2a_gateway/tools.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# input-required 与恢复流程

下游 A2A Agent 追问（`InputRequired`）时，网关把挂起状态写入 `pending_a2a_tasks` 表，通过三条消费路径（hook 注入、SSE interrupt、A2A 终帧）通知上游，再沿**双链**（聊天链 / A2A 链）恢复任务。存储抽象见 [数据模型](/openwiki/architecture/data-model.md)。

## PendingStore：存储协议与 TTL

`pending_store.py`：

- **`PendingRecord`**（NamedTuple）：`thread_id / agent_id / target_url / target_name / task_id / context_id / question`——脱离 ORM 会话的只读快照（L26-35）。
- **`PendingStore` 协议**（L47-64）：`upsert(*, thread_id, agent_id, target_url, target_name, task_id, context_id, question)` / `get(thread_id)` / `delete(thread_id)`——生产走 DB，测试注入替身。
- **`DbPendingStore`**（L67-128）：每次操作按需开短会话；**TTL 默认 86400s（24h）**，`ttl_seconds` 优先用构造参数，否则读 `Settings.pending_a2a_ttl_seconds`（`config.py:96`，别名 `PENDING_A2A_TTL_SECONDS`）。
- **读时过期清理** `_is_expired` + `get`（L38-44、L107-115）：`updated_at` 与当前 UTC 差超过 TTL → **当场 `delete` 并 `logger.info` + 返回 `None`**——过期判定只发生在读路径，没有后台清理任务；naive 时间按 UTC 处理。
- 模块级单例 `default_pending_store`（L131-132），路由层与工具层共用；测试 monkeypatch 本变量或注入替身。
- Repository 层（`repository.py:1109-1162`）：`upsert` 按 `thread_id` 冲突更新（存在则全字段刷新 + `updated_at = now`，不存在则插入）；`get` 只查行，**TTL 判定由 store 层负责**（L1112 注释）。

## 写入方

### ① 工具层：`a2a_call` 首次中断

`tools.py:43-85` `_build_a2a_tool._acall`：

1. 遍历 `wrapper.stream_message_events(message)` 收集 `TextChunk`，遇 `InputRequired` 记下 `required`；
2. `A2ATargetError` → `notify_alert("A2A 调用失败")` + 返回友好错误字符串（不写 pending）；
3. **有 `required`**：从 `config.configurable.thread_id` 取键（空则跳过写入），`pending_store.upsert(...)` 登记下游 task_id/context_id/question；**工具返回值 = question 原文**——模型拿到追问去转述给用户（L72-84）；
4. 无中断 → 聚合 chunks 或 `（A2A 目标未返回内容）`。

`make_a2a_tools`（`tools.py:171+`）注入共享 `store`（缺省 `default_pending_store`），并把 `a2a_resume` 一并挂上（L214-220）。

### ② 工具层：`a2a_resume` 再次中断

`tools.py:99-168` `_build_a2a_resume_tool`：恢复调用中若 again 收到新的 `InputRequired` → **`upsert` 刷新**（question/task_id/context_id 更新、`updated_at` 重置使 TTL 续期）并返回新 question（L141-151）；无 again → `delete` + 聚合结果。

### ③ 服务端：`_settle_pending` 收尾

`a2a_server.py:528-541`：A2A 链恢复结束后统一收尾——`again is not None` → `upsert` 刷新（再次对外中断）；否则 `delete`（任务完成，挂起清除）。

### 删除时机

- 恢复成功 / `A2ATargetError` 失败分支（工具层 `tools.py:134`、`152`）；
- wrapper 匹配失败（配置变更，`tools.py:119-121`）；
- 会话删除时顺带清理（`routes/chat.py:264-268`）；
- TTL 读时过期。

## 消费方

### ① Hook 注入（下一回合提示）

`graph.py:190-211` `_pending_notice`：每轮 `pre_model_hook` 查 `store.get(thread_id)`，命中产一条 `SystemMessage`：

> 当前有一个远端 Agent 任务正在等待用户补充信息（目标「…」，追问：…）。用户若已给出补充，请调用 a2a_resume 工具把补充内容转发出去；不要自行编造结果。

- 查询失败 → `logger.warning` + **本轮跳过注入**（L196-198）；
- 注入位置 = `build_llm_input` **第一位**（notice → skill → summary → recent，`graph.py:231-240`）；
- 这使**恢复轮不在路由层拦截**成为可能（`routes/chat.py:121-123`）：用户下一条消息走正常 `POST /api/chat`，模型看到 notice 后自行调 `a2a_resume`。

### ② SSE `interrupt` 事件（聊天链轮末）

`routes/chat.py:104-111`：`_stream_graph_events` 流结束后复查 `pending_store.get(thread_id)`——本轮开始无挂起、结束时出现即下游刚进入中断 → yield `interrupt {question}`，随后恒发 `done`。查询失败降级为无 interrupt（L105-109）。

### ③ A2A 终帧（A2A 链轮末）

`routes/a2a_server.py`：

- **非流式** `_rpc_send_message`（L392-408）：跑完后 `pending.get(context_id)`，命中 → 返回 `SendMessageResponse(task=...)` 且 `task.status.state = TASK_STATE_INPUT_REQUIRED`、message 携带 `pending.question`；未命中 → 正常 message 完成帧。
- **流式** `_rpc_stream`（L453-482）：轮末复查 `pending.get(context_id)`，命中 → 最后一个 `TaskStatusUpdateEvent` 状态为 `input-required` + text=question；未命中 → `completed` 终帧。**挂起表不可用时降级为 completed**（L454 注释），保证无中断场景行为不变。

## 双链设计

`pending_store.py:1-4` 模块 docstring：

| | 链路 A（对话界面） | 链路 B（A2A Server） |
|---|---|---|
| **键** | 网关会话 `thread_id` | 对外 `task_id` |
| **首写** | 工具层 `a2a_call` 用 `configurable.thread_id` | 创建新任务时 `task_id = context_id = uuid`（`a2a_server.py:326-328`），挂起表主键即可被后续恢复请求的 `task_id` 命中 |
| **恢复入口** | 用户消息 → hook notice → 模型调 `a2a_resume` | 客户端带 `task_id` 的 `message/send` → 服务端查 pending → `_rpc_resume_*` |
| **轮末暴露** | SSE `interrupt` | input-required 终帧 / 非流式 task 快照 |

**标识统一**（`a2a_server.py:320-328`）：请求带 `incoming_task_id` → 沿用之（context 缺省回填 task_id）；不带 → 新建且 `task_id = context_id = incoming_context_id or uuid4.hex`——**新任务的对外 task_id 与 context_id 同值**，故两条链在键空间上对新任务是同值的。

### A2A 链恢复分派

`a2a_server.py:330-350`：带 `task_id` → `pending.get(incoming_task_id)`；查询异常降级 `pending=None`；**`pending is None` 或 `pending.agent_id != agent.id` → `TaskNotFoundError`**（防跨 Agent 误恢复）；命中则按 method 分流 `_rpc_resume_stream` / `_rpc_resume_message`。恢复上下文（question 等）另从 `pending.get(context_id)` 读作初始帧（L393-401、L456-468）。

## wrapper 匹配与配置变更安全性

- **聊天链** `tools.py:118-121`：`a2a_resume` 按 `pending.target_url` 在**当前** wrappers 里 `next(...)` 匹配；**匹配失败（目标被解绑/URL 变了）→ 立即 `delete` pending + 返回「下游目标配置已变化，请让用户重新描述需求」**——配置变更后不会拿着旧 task_id 对错误目标恢复。
- **A2A 链** `_match_wrapper`（`a2a_server.py:500-507`）：`get_agent_wrappers(agent)` 取当前 wrappers 后同样按 `target_url` 匹配；加载异常 `logger.exception` → None（恢复失败走错误分支）。
- `agent_id` 双重校验：A2A 链入口比对 `pending.agent_id != agent.id`（`a2a_server.py:336`），即使键撞上别的 Agent 的挂起也会拒绝。

配置变更整体安全性还依赖工厂缓存 `(id, updated_at)` 键变更（[Agent 图构建](/openwiki/workflows/agent-graph.md)）与 `invalidate_agent` 显式失效；pending 记录本身**不随配置删除**，靠匹配失败分支兜底清理。

## TTL 过期语义

- 默认 **86400 秒**（`config.py:96`）；每次 `upsert` 刷新 `updated_at`，**持续追问会续期**。
- **只在 `get` 时判定**：超限 → 删行 + info 日志 + 返回 `None` → hook 不注入、SSE 不发 interrupt、恢复入口视为无挂起（A2A 链进而 `TaskNotFoundError`）。
- 无后台 sweeper；长期无人读的过期行会残留到下次读取或会话删除。

## 端到端时序

```
下游 input-required
  ├─ 聊天链: a2a_call 遇 InputRequired → pending.upsert(thread_id) → 工具返回 question → 模型转述
  └─ A2A 链: 工具/服务端写入（键=thread_id 或 task_id=context_id）
        ↓ 轮末
  ├─ SSE: pending.get → interrupt{question} → done
  └─ A2A: 终帧状态 input-required + question
        ↓ 用户补充
  ├─ 聊天链: POST /api/chat → hook notice 注入 → 模型调 a2a_resume(answer)
  │     → wrapper 匹配 → stream_message_events(task_id, context_id)
  │     → again? upsert 刷新 : delete → 返回结果/新追问
  └─ A2A 链: message/send 带 task_id → agent_id 校验 → _rpc_resume_*
        → 下游转发 → _settle_pending(again) → _append_resume_history(失败仅记日志)
        ↓ TTL 超时且无人读
  get → delete → 一切消费方视为无挂起
```

## 不变量

- 挂起表键：聊天链 `thread_id`；A2A 链对外 `task_id`（新任务与 `context_id` 同值）。
- 写入方三处：`a2a_call` 首写、`a2a_resume` 刷新、服务端 `_settle_pending`；删除方：恢复完成/失败、wrapper 失配、会话删除、TTL 过期。
- TTL 默认 86400s，读时惰性清理，`upsert` 续期；无后台任务。
- 消费方三处独立降级：hook 注入失败跳本轮、SSE interrupt 查询失败照常 done、A2A 终帧查询失败降级 completed——**挂起表故障绝不阻断对话**。
- wrapper 按 `target_url` 匹配当前配置，失配即删记录并引导重述；A2A 链另校验 `agent_id`。
- 恢复轮不在路由拦截：hook notice + 模型自调 `a2a_resume` 是聊天链的唯一驱动。

## 代表性测试

- `tests/test_pending_store.py`：upsert 冲突更新、TTL 读时过期删除、协议替身注入。
- `tests/test_chat_input_required.py`：SSE interrupt、删会话清挂起、恢复分支走 graph。
- A2A 恢复：`tests/test_a2a_server.py` 中带 task_id 的 resume 分派、`agent_id` 不符 → TaskNotFoundError、input-required 终帧。
- 工具层：`a2a_call` 写 pending、`a2a_resume` 匹配失败删记录（`tests/test_tools.py` / 相关套件）。

相关页：[数据模型](/openwiki/architecture/data-model.md)、[Agent 图构建与编排](/openwiki/workflows/agent-graph.md)、[聊天生命周期](/openwiki/workflows/chat-lifecycle.md)、[A2A 客户端](/openwiki/integrations/a2a-client.md)、[A2A 服务端](/openwiki/integrations/a2a-server.md)。
