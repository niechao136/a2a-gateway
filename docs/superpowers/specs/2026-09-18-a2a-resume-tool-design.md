# A2A 恢复轮工具化设计（`a2a_resume`）

- 日期：2026-09-18
- 状态：已实现（2026-09-18 已在 devops-43 完成链路 A 联调验收，见第 6 节）
- 范围：`a2a-gateway` 后端（工具层 / graph / 对话路由）
- 关联：取代 `2026-09-17-a2a-input-required-design.md` 中「链路 A 恢复分支」的实现方式（3.4.1 / 3.4.2）
- 实测背景：2026-09-18 在 devops-43 用 news agent（下行目标 travel）验证，中断 → 补充 → 同一 task 恢复已跑通；但恢复轮的输出由路由层直接透传，未经过网关 LLM

## 1. 背景与动机

2026-09-17 的设计把恢复轮做成「路由拦截 · 透明转发」：`routes/chat.py` 的 `_resume_pending` 在会话开头拦截，把用户补充带 `task_id` 转发给下游，并把下游输出直接以 `token` 事件流给用户，网关 LLM 完全不参与。

实测暴露的问题：

1. **下游返回不一定是终态**：可能再次 `INPUT_REQUIRED`。现有代码只 `upsert` 刷新 `question` 再发一个 `interrupt` 事件，追问是裸文本，没有模型转述，多轮追问时体验割裂。
2. **无法编排**：补充信息后，网关 LLM 本可以继续调 MCP（天气、机票）或换目标，现在完全没机会。
3. **历史是"补"出来的**：恢复轮靠 `_append_history` + `graph.aupdate_state(as_node="agent")` 手工追加，与正常 graph 的 checkpoint 结构不一致（实测会话里第二轮只有 `HumanMessage → AIMessage`，中间没有 `ToolMessage`）。

因此把恢复轮的**驱动权交还给 LLM**：新增专用工具 `a2a_resume`，由模型决定何时转发补充信息、拿到结果后如何作答。

## 2. 目标与非目标

### 2.1 目标

- 新增 `a2a_resume` 工具：把用户补充信息带 `task_id` / `context_id` 转发给挂起的下游任务，结果作为工具输出回到模型上下文。
- 恢复轮回归正常 graph 流程：删除路由层拦截与手工补历史的代码。
- 挂起上下文以「模型可见但不入历史」的方式注入，保证 agent 级图缓存不受影响。

### 2.2 非目标（明确排除）

- **链路 B（`/a2a/{slug}` A2A Server 出口）不改**：该链路没有 LLM，继续沿用 3.5 的透明转发（`_rpc_resume_stream` / `_rpc_resume_message`）。
- `pending_a2a_tasks` 表结构与 `PendingStore` 协议不变。
- SSE 事件类型（`token` / `tool_end` / `interrupt` / `done` / `error`）与前端逻辑不变。
- `GetTask` / `CancelTask` 仍返回 `TaskNotFound`。
- 不改动 `a2a_client.stream_message_events` 的事件语义。

### 2.3 关键决策记录

| 决策点 | 结论 | 说明 |
| --- | --- | --- |
| 恢复轮由谁驱动 | **LLM（工具）** | 下游返回可能非终态；模型可继续编排 |
| 恢复工具粒度 | **每个 Agent 一个 `a2a_resume`** | 目标已由挂起记录锁定，再按目标拆分只会增加误用可能 |
| 工具注册时机 | **常驻注册** | 图实例按 agent 缓存，挂起是 thread 级；动态注册会破坏缓存 |
| 挂起上下文注入位置 | **`pre_model_hook`** | 不入 checkpoint 历史、每轮动态、无需改图结构 |
| 链路 B | **保持透明转发** | 无 LLM，语义分叉可接受，文档说明 |
| 前端 | **零改动** | `interrupt` 与等待提示条语义不变 |

## 3. 设计

### 3.1 `a2a_resume` 工具（`tools.py`）

在 `make_a2a_tools` 内构造：为每个目标建完 `a2a_call` 工具后，若目标列表非空，追加一个 `a2a_resume`。它闭包持有 `wrappers`（与目标同序）、`agent_id`、`pending_store`。

```python
class A2AResumeArgs(BaseModel):
    answer: str = Field(description="用户针对追问补充的信息，原样转发给下游 Agent")
```

工具 coroutine 注入 `RunnableConfig` 读 `config["configurable"]["thread_id"]`，流程：

1. 无 `thread_id` → 返回「无法定位当前会话，请让用户重新描述需求」；
2. `pending = await store.get(thread_id)`，为 `None` → 返回「当前没有等待补充的远端任务，请用 a2a_call 发起新请求」；
3. 按 `pending.target_url` 在 wrappers 中匹配，未命中 → `delete(thread_id)` + 返回「下游目标配置已变化，请让用户重新描述需求」；
4. `async for event in wrapper.stream_message_events(answer, task_id=pending.task_id, context_id=pending.context_id or None)`：
   - `TextChunk` → 累计；
   - `InputRequired` → 记为 `again`；
5. 收尾：
   - `again` 存在 → `upsert(..., task_id=again.task_id, context_id=again.context_id, question=again.question)`（task_id 不变，只刷新追问），返回 `again.question`；
   - 否则 → `delete(thread_id)`，返回拼接文本（为空时返回「下游未返回内容」）；
6. `A2ATargetError` → `delete(thread_id)` + `notify_alert("挂起任务恢复失败", ...)` + 返回「目标暂时不可用，请稍后重试或重新描述需求」（交由模型转述，路由层不再发 `error` 事件）。

工具描述（`description`）需写明：仅在存在等待补充的远端任务时使用；入参是用户补充的内容；返回下游的回复或新的追问。

### 3.2 挂起上下文注入（`graph.py`）

`_make_history_hook` 改为：

```python
def _make_history_hook(llm: Any, pending_store: PendingStore | None = None):
    store = pending_store or default_pending_store

    async def history_compression_hook(state: Any, config: RunnableConfig) -> dict[str, Any]:
        ...
        pending_notice = await _build_pending_notice(config, store)
        return {"llm_input_messages": [*pending_notice, *build_llm_input(summary)], ...}
```

- `_build_pending_notice(config, store)`：取 `config["configurable"]["thread_id"]` → `store.get(thread_id)` → 命中则返回长度 1 的列表：
  ```python
  SystemMessage(
      "当前有一个远端 Agent 任务正在等待用户补充信息"
      f"（目标「{pending.target_name or pending.target_url}」，追问：{pending.question}）。"
      "用户若已给出补充，请调用 a2a_resume 工具把补充内容转发出去；不要自行编造结果。"
  )
  ```
- 未命中 / `thread_id` 缺失 / store 抛异常 → 返回空列表（仅记日志，绝不打断本轮对话）；
- 摘要压缩的两条返回路径（`build_llm_input(summary)`、`build_llm_input(new_summary)`）都要带上 notice；
- `build_graph` 新增可选参数 `pending_store`，透传给 hook（测试注入替身）。

> 实施前需验证：`create_react_agent` 的 `pre_model_hook` 是否支持 `(state, config)` 双参数签名（langgraph 按函数签名传参）。见第 7 节。

### 3.3 提示词（`graph.py`）

`DEFAULT_SYSTEM_PROMPT` 追加一段：

> 若上下文中出现「等待用户补充信息」的远端任务，请调用 `a2a_resume` 工具把用户的补充内容转发出去；拿到结果后整理作答，其中的关键数据（行程、金额、日期、名称等）请原样保留，不要改写或编造。

### 3.4 路由层简化（`routes/chat.py`）

删除：

- `_stream_chat` 开头的挂起拦截分支（`pending is not None → _resume_pending`）；
- `_resume_pending` 与 `_append_history` 两个函数；
- 随之失效的 import（`A2ATargetError` / `InputRequired` / `TextChunk` / `get_agent_wrappers` / `PendingRecord`，按实际引用情况清理）。

保留：

- `_stream_graph_events` 轮末复查挂起表并发 `interrupt` 的逻辑（3.4.3）——它同时覆盖「首轮中断」与「恢复后再次中断」；
- `DELETE /api/chat/conversations/{thread_id}` 的挂起清理。

`agent_factory.get_agent_wrappers` **保留**：`routes/a2a_server.py` 的链路 B 仍在使用。

### 3.5 数据流（恢复轮）

```
用户补充 → POST /api/chat/{slug}
  → _stream_chat（无拦截）
  → graph.astream_events
      → pre_model_hook：注入「有挂起任务 + 追问」SystemMessage
      → agent 节点：模型决定调用 a2a_resume
      → 工具节点：带 task_id/context_id 转发补充内容
          ├ 完成   → 删挂起 → 返回下游文本
          └ 再中断 → upsert 刷新追问 → 返回新追问
      → agent 节点：整理作答 / 转述追问
      → tool_end / token 事件流出
  → 轮末复查挂起表：命中 → interrupt 事件
  → done
```

体感：下游结果先随 `tool_end` 事件到达，随后是模型整理后的 `token`。首字延迟比透明转发高一次模型往返，这是本次设计已接受的代价。

**界面副作用（已知并接受）**：`ChatPage.tsx` 会把 `tool_start` / `tool_end` 渲染成一张工具卡片，因此恢复轮界面上会出现「`a2a_resume` 工具卡片（含下游原文）+ 模型整理后的回答」两份内容，而透明转发时只有一份。若后续觉得冗余，可在前端对 `a2a_resume` 的工具卡片做默认折叠——不在本次范围。

## 4. 错误处理与边界

| 场景 | 行为 |
| --- | --- |
| 无挂起时模型误调 `a2a_resume` | 返回提示文本，模型改走 `a2a_call`；不报错、不删数据 |
| 挂起目标已从 Agent 配置移除 | 删挂起 + 返回「请让用户重新描述需求」 |
| 下游不可达 / 超时 | 删挂起 + `notify_alert` + 错误文本交模型转述 |
| 恢复后再次中断 | `upsert` 刷新 `question`（task_id 不变），工具返回新追问，轮末发 `interrupt` |
| 模型未调用 `a2a_resume` | 挂起保留，下一轮 hook 继续注入上下文（幂等，不会误删） |
| 挂起 TTL 过期 | `get` 视为无挂起，模型走 `a2a_call` 开新任务 |
| `config` 中无 `thread_id` | 返回提示文本，不抛异常 |
| 并发同会话请求 | last-write-wins，不额外加锁（与现状一致） |
| hook 查询挂起失败 | 记日志跳过注入，本轮降级为「模型不知道有挂起」 |

## 5. 测试策略

全部离线（沿用 `tests/conftest.py` 风格：mock DB / 网络 / LLM）。

| 模块 | 用例 |
| --- | --- |
| `test_tools_a2a.py` | resume 命中挂起 → 带 task_id 转发并返回文本；无挂起 → 提示文本；目标变更 → 删挂起 + 提示；再次中断 → upsert 刷新 question 且 task_id 不变；`A2ATargetError` → 删挂起 + 告警 + 错误文本；无 `thread_id` 兜底 |
| `test_graph.py`（新增或扩展） | hook 命中挂起 → `llm_input_messages` 含 SystemMessage 且 state 历史不含它；无挂起 → 不注入；store 抛异常 → 降级且不影响返回；注入与摘要压缩叠加时顺序正确 |
| `test_chat_input_required.py` | 现有恢复分支用例改写：恢复轮走 graph（mock `a2a_resume` 被调用）；轮末 `interrupt` 仍在；删除会话仍清挂起 |
| `test_chat_api.py` | 回归：无中断场景的普通对话、工具调用、MCP 行为不变 |
| 前端 | 不改动，无需新增用例 |

## 6. 验收标准

1. **联调链路 A**（本地网关 → devops-43 travel-agent）：「帮我规划南昌的旅行」→ 追问 → 补充「9/29-10/1，预算 3000」→ 下游**同一 task** 恢复（travel-agent 库验证：同一 thread 出现 2 条用户请求、checkpoint 继续增长）→ 网关 LLM 整理后输出，关键数据（景点、金额、日期）与下游原文一致。
2. **连续追问**：补充不完整导致下游再次中断 → 界面仍显示等待提示、追问更新 → 再次补充后完成；travel-agent 侧同一 task 的 checkpoint 继续增长。
3. **误用兜底**：无挂起时直接问「今天天气」→ 模型走 `a2a_call` / MCP，行为与现状一致。
4. **回归**：`uv run pytest` 全绿；无中断场景行为不变。

## 7. 需先验证的技术假设

| 假设 | 验证方式 | 结论 |
| --- | --- | --- |
| `create_react_agent` 的 `pre_model_hook` 支持 `(state, config)` 签名 | 本地脚本（langgraph 实装，打印 hook 收到的参数） | ✅ 已验证通过：**支持**，但 `config` 形参必须注解为 `RunnableConfig` / `RunnableConfig \| None`（注解为 `Any` 时 langgraph 不注入，报 `missing 1 required positional argument: 'config'`），无需走第 9 节的退路。实测见 `.superpowers/sdd/2026-09-18-a2a-resume-tool/task-0-report.md`，图级回归用例见 `tests/test_graph.py`（`test_history_hook_injects_pending_notice` 等） |
| `pre_model_hook` 返回 `llm_input_messages` 时前置 `SystemMessage` 能被模型正确消费 | 本地脚本（Fake store 注入 + 假的 chat model 记录入参） | ✅ 已验证通过：同一脚本的 `PregelTaskWrites.writes` 中出现 `('llm_input_messages', [SystemMessage(content='NOTICE'), HumanMessage(...)])`，图接受该返回值并写入通道，末行 `final: ok`。实测见 `.superpowers/sdd/2026-09-18-a2a-resume-tool/task-0-report.md`，图级回归用例见 `tests/test_graph.py`（`test_graph_injects_pending_notice_into_model_input`） |

## 8. 实施顺序与文件清单

1. 验证第 7 节两项假设；
2. `tools.py`：新增 `A2AResumeArgs` 与 `_build_a2a_resume_tool`，`make_a2a_tools` 追加注册（含单测）；
3. `graph.py`：`_make_history_hook` 支持 config + 挂起注入，`build_graph` 透传 `pending_store`，`DEFAULT_SYSTEM_PROMPT` 追加约束（含单测）；
4. `routes/chat.py`：删除恢复拦截分支、`_resume_pending`、`_append_history` 与失效 import（含单测改写）；
5. 联调验收（第 6 节）；
6. 更新 `TODO.md` 与本文件状态。

## 9. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 模型不调 `a2a_resume` 而自行作答 | 提示词强约束 + hook 每轮重复注入；挂起不因未调用而丢失 |
| 模型改写下游精确内容（金额 / POI 名称） | 提示词要求关键数据原样保留；验收第 1 项逐项比对 |
| 恢复轮多一次模型往返，首字变慢 | `tool_end` 事件可先展示下游原文；已确认为可接受代价 |
| `pre_model_hook` 签名不被支持 | 退路：改为在 `graph_input` 中注入提示消息（会进历史），或把注入放到 `_stream_chat` 构造 input 时 |
| 恢复轮界面出现「工具卡片 + 整理回答」两份内容 | 已确认前端会渲染 `tool_end`；本次接受，后续可按需折叠该工具卡片 |
| 两条链路恢复语义分叉（A 走模型、B 透明转发） | 文档明确说明；B 无 LLM，属客观约束 |
