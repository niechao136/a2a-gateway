# A2A input-required 支持设计（网关侧）

- 日期：2026-09-17
- 状态：已评审待实现
- 范围：`a2a-gateway`（后端 + 前端），不改动 `travel-agent` / `new-agent` / `hermes`
- 关联：`TODO.md` 二期功能「input-required 人工确认」

## 1. 背景

网关作为 A2A 客户端调用下游 Agent 时，`SendMessageRequest.message` 从不携带 `task_id` / `context_id`（`src/a2a_gateway/a2a_client.py` 第 168-176 行），因此每次调用都会在下游创建**新任务**。

当下游以 `TASK_STATE_INPUT_REQUIRED` 中断（要求用户补充信息）时，现状是：

- 追问文本被 `a2a_client._extract`（第 224-256 行）当作普通增量文本透传，由网关 LLM 转述给用户；
- 下游任务永久挂起，**协议的「中断 → 补充 → 恢复」闭环从未发生**。

2026-09-17 在 devops-43 的实测数据佐证了这一点：

- 用户「南昌之旅」会话中，"帮我规划南昌的旅行"（下游 task `c9139e63`）中断后，最终行程来自**另一个新任务** `3ff3bdc4`（输入是网关 LLM 整合后的完整需求）；
- `c9139e63` 至今停留在中断状态；travel-agent 库中累计 5 个中断未恢复的「僵尸线程」；
- 全库唯一的真实 resume（线程 `18329819`，2 条用户消息）由直连的测试客户端产生，与网关无关。

## 2. 目标与范围

### 2.1 目标

实现协议级 input-required 闭环，覆盖两条链路：

- **链路 A（对话界面）**：用户在网关聊天界面（`/api/chat`）对话；下游中断时界面展示追问，用户补充后网关**带 task_id 透明转发**，下游同一任务恢复并继续输出。
- **链路 B（A2A Server 出口）**：外部 A2A 客户端调用网关 `/a2a/{slug}`；网关正确对外暴露 `TASK_STATE_INPUT_REQUIRED`，并在调用方带 `task_id` 重发时恢复对应下游任务。

### 2.2 非目标（明确排除）

- `GetTask` / `CancelTask`：维持现状返回 `TaskNotFoundError`（网关不持久化任务快照）；
- 取消指令（挂起期间用户无法显式放弃任务，仅自动清理）；
- 网关自身任务的完整 TaskStore / 多实例横向扩展改造（挂起状态存 DB，天然可多实例读取；`A2AClientWrapper` 缓存仍为进程级）；
- 无中断场景的行为变化：下游不返回 `INPUT_REQUIRED` 时，两条链路行为与现状完全一致。

### 2.3 关键决策记录

| 决策点 | 结论 | 说明 |
| --- | --- | --- |
| 范围 | A + B 两条链路都做 | 底层能力共用，A 先行、B 紧随 |
| 恢复轮语义 | **路由拦截 · 透明转发** | 挂起后用户的下一条消息不经网关 LLM，直接带 `task_id` 转发给下游 |
| 挂起状态存储 | **新增 PostgreSQL 表**（含 Alembic 迁移） | 重启不丢、两条链路共用、删会话可清理 |
| 前端呈现 | SSE 新增 `interrupt` 事件 + 「等待补充」提示 | 用户发送下一条消息后提示消失 |
| GetTask / CancelTask | 保持 `TaskNotFound` | 只做 SendMessage 闭环 |
| 中断信号传递 | **结构化事件流**（`stream_message_events`） | 类型安全、可单测、两条链路共用同一信号源 |

## 3. 设计

### 3.1 结构化事件流（`a2a_client.py`）

新增事件类型（模块级 dataclass）：

```python
@dataclass(frozen=True)
class TextChunk:
    text: str

@dataclass(frozen=True)
class InputRequired:
    task_id: str
    context_id: str
    question: str

@dataclass(frozen=True)
class Completed:
    task_id: str

StreamEvent = TextChunk | InputRequired | Completed
```

新增方法：

```python
async def stream_message_events(
    self, text: str, *, task_id: str | None = None,
    context_id: str | None = None, retries: int = 2, backoff: float = 0.5,
) -> AsyncIterator[StreamEvent]
```

行为约定：

1. `task_id` / `context_id` 非空时分别写入 `message.task_id` / `message.context_id`（恢复语义）；为空时保持现状（不带）。
2. 逐响应处理（对 `Task` / `TaskStatusUpdateEvent` / `TaskArtifactUpdateEvent` / `Message` 四种形态）：
   - 先产出该响应携带的**文本**（`task.status.message`、`status_update.status.message`、artifact 文本与 data part，均按现有 `_extract` 逻辑，含整数还原）；
   - `Task.status.state == TASK_STATE_INPUT_REQUIRED` 或 `status_update.status.state == TASK_STATE_INPUT_REQUIRED` 时，追加产出 `InputRequired(task_id, context_id, question)`，`question` 取该响应的 message 文本（可能为空串）；
   - 终态（`COMPLETED` / `FAILED` / `CANCELED` / `REJECTED`）产出 `Completed(task_id)`。
3. 重试语义不变：仅网络/超时错误重试，且**已产出任何事件后不再重试**（避免重复输出）。
4. 旧 API `stream_message()` 保留为薄封装：消费 `stream_message_events`，只产出 `TextChunk.text`，聚合行为与现状一致（`tools.py` 迁移到新 API，旧 API 继续被现有测试覆盖）。

### 3.2 挂起表与存储

#### 3.2.1 数据模型（`models.py`）

```python
class PendingA2ATask(Base, BaseMixin):
    __tablename__ = "pending_a2a_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 两条链路统一标识：链路 A = 网关会话 thread_id；链路 B = 对外 task_id
    thread_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="CASCADE"), index=True
    )
    target_url: Mapped[str] = mapped_column(String(512))
    target_name: Mapped[str] = mapped_column(String(128), default="")
    task_id: Mapped[str] = mapped_column(String(128))      # 下游任务 id
    context_id: Mapped[str] = mapped_column(String(128), default="")  # 下游上下文 id
    question: Mapped[str] = mapped_column(Text, default="")
```

- `BaseMixin` 提供 `created_at` / `updated_at`；
- Alembic 新增一个迁移版本（`add_pending_a2a_tasks`）；
- TTL：`config.py` 新增 `pending_a2a_ttl_seconds: int = 86400`；**读取时**若 `updated_at` 超过 TTL，删除该行并视为无挂起。

#### 3.2.2 repository（`repository.py`）

```python
async def upsert_pending_a2a_task(session, *, thread_id, agent_id, target_url,
                                  target_name, task_id, context_id, question) -> None
async def get_pending_a2a_task(session, thread_id, *, ttl_seconds) -> PendingA2ATask | None
async def delete_pending_a2a_task(session, thread_id) -> bool
```

- `upsert` 按 `thread_id` 冲突更新（`question` / `task_id` / `updated_at` 刷新）；
- `get` 内含 TTL 判定与过期删除。

#### 3.2.3 store 抽象（新模块 `pending_store.py`）

```python
class PendingRecord(NamedTuple):   # 脱离 ORM 会话的只读快照
    thread_id: str; agent_id: int
    target_url: str; target_name: str
    task_id: str; context_id: str; question: str

class PendingStore(Protocol):
    async def upsert(self, *, thread_id, agent_id, target_url, target_name,
                     task_id, context_id, question) -> None: ...
    async def get(self, thread_id: str) -> PendingRecord | None: ...
    async def delete(self, thread_id: str) -> None: ...

class DbPendingStore:      # 生产实现：内部用 AsyncSessionLocal + repository
    ...

default_pending_store: PendingStore = DbPendingStore()
```

- 路由层与工具层**共用** `default_pending_store`；
- 测试通过 monkeypatch 模块变量或显式注入 Fake 实现（保持现有「测试无外部依赖」风格）。

### 3.3 工具层（`tools.py`）

- 签名扩展：`make_a2a_tools(targets, *, agent_id: int, pending_store: PendingStore | None = None)`；`_build_a2a_tool(..., agent_id, pending_store)`；
- `pending_store` 为空时取 `pending_store.default_pending_store`；`agent_factory.get_agent_instance` 调用处传 `agent_id=agent.id`；
- 工具 coroutine 增加 `config: RunnableConfig` 注入（**已验证** StructuredTool 支持），读取 `config["configurable"]["thread_id"]`；
- 工具逻辑改为消费事件流：

```python
chunks: list[str] = []
required: InputRequired | None = None
async for ev in wrapper.stream_message_events(message):
    if isinstance(ev, TextChunk):
        chunks.append(ev.text)
    elif isinstance(ev, InputRequired):
        required = ev
if required is not None:
    await pending_store.upsert(
        thread_id=thread_id, agent_id=agent_id,
        target_url=wrapper.target.url, target_name=wrapper.target.name or "",
        task_id=required.task_id, context_id=required.context_id,
        question=required.question,
    )
    return required.question or "（需要用户补充信息）"
return "".join(chunks) if chunks else "（A2A 目标未返回内容）"
```

- `DEFAULT_SYSTEM_PROMPT`（`graph.py`）追加约束：若工具结果表明需要用户补充信息，请**原样转述**并等待用户回复，不得自行编造答案。

### 3.4 链路 A：对话路由（`routes/chat.py`）

#### 3.4.1 分流

`_stream_chat(agent, message, thread_id)` 开头：

```python
pending = await default_pending_store.get(thread_id)
if pending is not None:
    async for evt in _resume_pending(agent, pending, message, thread_id):
        yield evt
    return
# 以下为现有 graph 流程（不变）
```

#### 3.4.2 恢复分支 `_resume_pending`

1. 目标匹配：`wrappers = await get_agent_wrappers(agent)`，按 `pending.target_url` 匹配；匹配失败 → 删除挂起 → `error` 事件（提示「下游目标配置已变化，请重新描述需求」）→ `done`；
2. 调 `wrapper.stream_message_events(message, task_id=pending.task_id, context_id=pending.context_id or None)`：
   - `TextChunk` → 累计并 `yield _sse("token", {"content": text})`；
   - `InputRequired` → 记录为 `again`；
3. `A2ATargetError` → 删除挂起 + `notify_alert` + `error` 事件 + `done`；
4. 收尾：
   - `again` 存在 → `upsert(..., task_id=again.task_id, context_id=again.context_id, question=again.question)`（同一任务 id 不变，`question` 刷新为最新追问）→ `yield _sse("interrupt", {"question": again.question})`；
   - 否则 → `delete(thread_id)`；
5. 历史追加（try/except，失败只记日志）：
   ```python
   graph = await get_agent_instance(agent)
   await graph.aupdate_state(
       {"configurable": {"thread_id": thread_id}},
       {"messages": [HumanMessage(message), AIMessage(collected_text)]},
       as_node="agent",
   )
   ```
   `collected_text` 为空时只追加 `HumanMessage`。**已验证**：该写法 `next` 为空、不触发意外执行、下一轮上下文与历史接口均完整；
6. `yield _sse("done", {"thread_id": thread_id})`。

#### 3.4.3 正常分支的轮末检查

`_stream_graph_events`（`_stream_chat` 与 `_retry_stream` 共用）在 `yield done` 之前：

```python
pending = await default_pending_store.get(thread_id)
if pending is not None:
    yield _sse("interrupt", {"question": pending.question})
yield _sse("done", {"thread_id": thread_id})
```

语义：本轮开始无挂起、结束时出现挂起 → 说明本轮下游刚进入中断（工具层已写入）。

#### 3.4.4 其他

- SSE 事件新增 `interrupt`（payload：`{"question": str}`）；
- `DELETE /api/chat/conversations/{thread_id}`：删除会话时同步 `default_pending_store.delete(thread_id)`（失败不影响主流程）；
- `agent_factory.py` 新增 `get_agent_wrappers(agent) -> list[A2AClientWrapper]`：命中缓存直接返回；未命中先走 `get_agent_instance(agent)` 完成构建后再返回。

### 3.5 链路 B：A2A Server 出口（`routes/a2a_server.py`）

#### 3.5.1 标识统一

`a2a_rpc` 中替换现有 `context_id` / `task_id` 生成逻辑（第 301-302 行）：

```python
incoming_task_id = req.message.task_id or ""
incoming_context_id = req.message.context_id or ""
if incoming_task_id:
    task_id = incoming_task_id
    context_id = incoming_context_id or incoming_task_id
else:
    task_id = context_id = incoming_context_id or uuid.uuid4().hex
```

效果：新任务时 `task_id == context_id == LangGraph thread_id`，挂起表主键（thread_id）即为对外 task_id，恢复请求可直接命中。

#### 3.5.2 恢复分流

解析出文本后：

```python
if incoming_task_id:
    pending = await default_pending_store.get(incoming_task_id)
    if pending is None or pending.agent_id != agent.id:
        return _rpc_error(request_id, TaskNotFoundError())
    if method in STREAMING_METHODS:
        return EventSourceResponse(_rpc_resume_stream(request_id, agent, pending, text, task_id, context_id))
    return JSONResponse(await _rpc_resume_message(request_id, agent, pending, text, task_id, context_id))
```

#### 3.5.3 新任务轮末的中断映射

- `_rpc_stream`：文本流结束后查 `default_pending_store.get(context_id)`：
  - 命中 → 终帧 `status_update(state=TASK_STATE_INPUT_REQUIRED, message=追问)`（复用 `new_text_status_update_event(state=..., text=pending.question)`）；
  - 未命中 → 维持现状发 `TASK_STATE_COMPLETED`；
- `_rpc_send_message`（非流式）：结束后同样查挂起表：
  - 命中 → 返回 `SendMessageResponse(task=Task(id=task_id, context_id=context_id, status=TaskStatus(state=TASK_STATE_INPUT_REQUIRED, message=new_text_message(pending.question, ...))))`；
  - 未命中 → 维持现状返回 `message` 响应。

#### 3.5.4 恢复流 `_rpc_resume_stream` / `_rpc_resume_message`

- 复用 3.4.2 的 wrapper 匹配逻辑（失败 → 删挂起 + JSON-RPC error 帧）；
- 不重发首帧 `Task`（任务已存在），首帧发 `status_update(working)`，文本以 `status_update(working, text=…)` 增量输出；
- 终帧：再次中断 → `INPUT_REQUIRED`（并 upsert）；完成 → `COMPLETED`（并 delete）；
- 收尾同样 `graph.aupdate_state` 追加 `(用户补充, 下游回复)` 历史（thread = `context_id`）；
- 非流式恢复：消费上述流并聚合：
  - 完成 → `SendMessageResponse(task=Task(state=TASK_STATE_COMPLETED, status.message=结果文本))`；
  - 再次中断 → `SendMessageResponse(task=Task(state=TASK_STATE_INPUT_REQUIRED, status.message=追问))`。

- `GetTask` / `CancelTask` 分支不变（继续返回 `TaskNotFoundError`）。

### 3.6 前端（Web）

- `web/src/lib/api.ts`：
  - `SSEEvent` 联合类型增加 `{ type: "interrupt"; question: string }`；
  - `consumeSSE` 增加 `case "interrupt"` 分发；
- `web/src/components/ChatPage.tsx`：
  - 新增状态 `waiting: boolean`；收到 `interrupt` 置 `true`；用户发送下一条消息时置 `false`；
  - UI：输入框上方显示固定文案的轻量提示条「等待你补充信息，请直接回复」——追问内容已在对话流中展示，不重复渲染；样式复用现有 MUI 组件。
- 测试：`web/src/lib/api.test.ts` 增补 `interrupt` 解析用例；ChatPage 相关测试（如已有组件测试）增补提示展示/消失用例。

## 4. 错误处理与边界

| 场景 | 行为 |
| --- | --- |
| 恢复轮下游不可达 / 超时 | 删除挂起 + `error` 事件（提示稍后重试）+ `notify_alert` |
| 下游任务已被清理（重启/过期） | 恢复调用将得到下游 TaskNotFound → `A2ATargetError` → 同上一行处理 |
| 目标配置变更（`target_url` 已不存在） | 删除挂起 + 提示「请重新描述需求」 |
| TTL 过期 | 读取时删除并视为无挂起，走正常流程 |
| 恢复轮产出为空 | 仍追加 `HumanMessage` 历史；`done` 正常结束 |
| 连续多轮中断 | 每次 `upsert` 刷新 `question`，任务保持挂起 |
| 链路 B 恢复请求 `task_id` 与 `agent_id` 不匹配 | 返回 `TaskNotFoundError` |
| 并发同会话请求 | last-write-wins，不额外加锁（现状语义） |

## 5. 测试策略

全部离线（沿用 `tests/conftest.py` 风格：mock DB / 网络 / LLM）：

| 模块 | 用例（新增或扩展） |
| --- | --- |
| `test_a2a_client.py` | 事件流：文本+中断（Task 快照形态）、纯中断（status_update 形态）、正常完成；`task_id` 透传；已产出后不重试 |
| `test_pending_store.py`（新） | `upsert/get/delete`；TTL 过期删除；Fake store 行为 |
| `test_tools_a2a.py`（新） | 中断时写 store 且返回追问文本；正常路径不写 store；`config` 注入缺失时的兜底 |
| `test_chat_api.py` | 命中挂起 → 走恢复分支（mock wrapper 与 store）；正常分支轮末 `interrupt` 事件；`aupdate_state` 被调用；删除会话清挂起 |
| `test_a2a_server.py` | 首次中断输出 `INPUT_REQUIRED`（流式/非流式）；带 `task_id` 恢复（流式/非流式）；未命中 → `TaskNotFound`；标识统一 |
| `web`（vitest） | `interrupt` 事件解析；等待提示展示与消失 |

## 6. 验收标准

1. **链路 A 联调**（本地网关 → devops-43 travel-agent）：在聊天界面问「帮我规划南昌的旅行」→ 收到追问 → 补充「9/27-10/1，预算3000」→ 下游**同一 task** 恢复（travel-agent 库内验证：同一 thread 出现 2 条用户消息、checkpoint 增长含 resume 段）→ 行程输出；刷新页面历史包含补充与回复。
2. **链路 B 联调**：外部 A2A 客户端（a2a-sdk 或 JSON-RPC 脚本）调 `/a2a/{slug}`：收到 `INPUT_REQUIRED`（含追问）→ 带 `task_id` 重发 → 收到 `COMPLETED` 与结果。
3. **回归**：无中断场景（普通对话、工具调用、MCP）行为不变；`uv run pytest` 与 `npm test` 全绿。

## 7. 实施顺序与文件清单

**阶段 1（链路 A 基础能力）**

1. `a2a_client.py`：事件流 + `task_id` 支持（含单测）
2. `models.py` / `repository.py` / `alembic/versions/*` / `config.py` / `pending_store.py`（含单测）
3. `tools.py` + `graph.py`（提示词）：消费事件流、写挂起（含单测）
4. `routes/chat.py` + `agent_factory.py`：拦截、恢复、`interrupt` 事件、历史追加、会话清理（含单测）
5. `web/src/lib/api.ts` + `ChatPage.tsx`（含前端测试）

**阶段 2（链路 B）**

6. `routes/a2a_server.py`：标识统一、中断映射、恢复流/恢复消息（含单测）

**阶段 3（验收与收尾）**

7. 联调验收（第 6 节 1、2 项）
8. 更新 `TODO.md`（勾选二期 input-required 条目）、必要时补 `README`

## 8. 已验证的技术假设

| 假设 | 验证方式 | 结论 |
| --- | --- | --- |
| `StructuredTool` coroutine 可注入 `RunnableConfig` 读取 `thread_id` | 本地脚本（langchain-core 实装） | ✅ 通过 |
| `graph.aupdate_state(..., as_node="agent")` 追加历史后 `next` 为空、下轮上下文完整 | 本地脚本（InMemorySaver + `create_react_agent`） | ✅ 通过 |
| `Message.task_id` / `SendMessageResponse.task` / `TaskStatus.message` 字段存在 | 本地脚本（a2a protobuf 实装） | ✅ 通过 |

## 9. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 下游（travel-agent）重启导致任务丢失 | 恢复失败 → 清挂起 + 明确提示，用户重新描述即可 |
| `aupdate_state` 在不同 langgraph 版本的 `as_node` 行为差异 | 单测覆盖「追加后 `next` 为空 + 下轮带历史」；异常时仅记日志不影响主流程 |
| 挂起表数据成为「僵尸」 | TTL 自动清理 + 删除会话联动清理 |
| 链路 B 标识统一改变既有调用方假设 | 兼容性评估：现有调用方均按「新会话」使用；恢复场景此前不可用，属新增能力 |
