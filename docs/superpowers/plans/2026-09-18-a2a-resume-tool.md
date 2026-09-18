# A2A 恢复轮工具化（`a2a_resume`）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增 `a2a_resume` 工具，把 input-required 恢复轮的驱动权从对话路由层交还给网关 LLM，删除路由层的透明转发拦截。

**架构：** 挂起的下游任务由 `pending_store` 按会话 `thread_id` 记录；`pre_model_hook` 在模型可见输入（`llm_input_messages`）里注入一条「有任务等待补充」的 `SystemMessage`，模型据此调用 `a2a_resume` 工具带 `task_id` 转发补充内容，工具输出回到模型后由模型决定转述追问或整理作答；对话路由不再拦截恢复轮。

**技术栈：** Python 3.11+ / FastAPI / LangGraph `create_react_agent` + `pre_model_hook` / LangChain `StructuredTool` / SQLAlchemy async（`pending_a2a_tasks` 表）/ pytest + 全离线替身 / basedpyright standard。

**规格：** `docs/superpowers/specs/2026-09-18-a2a-resume-tool-design.md`（执行前必读，本计划的全部论证来自该规格）

## 全局约束

- 恢复工具名固定为 `a2a_resume`，入参固定为 `answer: str`。
- `PendingStore` 协议（`upsert` / `get` / `delete`）与 `pending_a2a_tasks` 表结构**不得修改**。
- 链路 B（`routes/a2a_server.py` 的 `_rpc_resume_stream` / `_rpc_resume_message`）**不得修改**；`agent_factory.get_agent_wrappers` 必须保留（链路 B 依赖）。
- 前端（`web/`）**零改动**；SSE 事件类型不变。
- 测试全部离线：不连数据库、不发真实网络请求、不调用真实 LLM（沿用 `tests/conftest.py` 替身风格）。
- 每个任务结束时 `uv run pytest` 必须全绿，且 basedpyright standard 无新增错误。
- 每个任务一次 commit，commit 信息用中文 Conventional Commits（如 `feat: 新增 a2a_resume 恢复工具`）。

---

## 文件结构

| 文件 | 职责 | 变更 |
| --- | --- | --- |
| `src/a2a_gateway/tools.py` | A2A / MCP 工具构造 | 新增 `A2AResumeArgs`、`_build_a2a_resume_tool`，`make_a2a_tools` 追加注册 |
| `src/a2a_gateway/graph.py` | 图构建、历史压缩 hook、默认提示词 | hook 支持 `config` + 挂起上下文注入，`build_graph` 透传 `pending_store`，提示词追加 |
| `src/a2a_gateway/routes/chat.py` | 对话 SSE 路由 | 删除恢复拦截分支、`_resume_pending`、`_append_history` 与失效 import |
| `tests/test_tools_a2a.py` | 工具层测试 | 新增 resume 工具用例 + `FakePendingStore` |
| `tests/test_graph.py` | 图与 hook 测试 | 新增挂起上下文注入用例 |
| `tests/test_chat_input_required.py` | 链路 A 测试 | 删除已被工具层取代的恢复分支用例，新增「恢复轮走 graph」用例 |
| `docs/superpowers/specs/2026-09-18-a2a-resume-tool-design.md` | 规格 | 收尾时把状态改为「已实现」 |
| `TODO.md` | 待办 | 收尾时更新 |

---

### 任务 0：验证 `pre_model_hook` 的 `(state, config)` 签名

**文件：** 一次性脚本 `/tmp/verify_hook.py`（验证后删除，不进仓库）

规格第 7 节的两个假设必须先证实：`pre_model_hook` 是否按函数签名传 `config`、以及往 `llm_input_messages` 前置 `SystemMessage` 能否被图正常消费。

- [ ] **步骤 1：编写验证脚本**

```python
import asyncio
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent


class FakeChatModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


seen: list[Any] = []


async def hook(state: Any, config: Any):
    seen.append(config)
    return {"llm_input_messages": [SystemMessage(content="NOTICE"), *state["messages"]]}


async def main():
    llm = FakeChatModel(messages=iter([AIMessage(content="ok")]))
    graph = create_react_agent(llm, [], checkpointer=InMemorySaver(), pre_model_hook=hook)
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="hi")]},
        {"configurable": {"thread_id": "t1"}},
    )
    print("hook_configs:", seen)
    print("final:", result["messages"][-1].content)


asyncio.run(main())
```

- [ ] **步骤 2：运行并判定**

运行：`uv run python /tmp/verify_hook.py`

预期：`hook_configs` 为非空列表且元素含 `{"configurable": {"thread_id": "t1"}}`，末行 `final: ok`，无异常。

- [ ] **步骤 3：按结果决策**

  - 若 `hook_configs` 为空（langgraph 只传 state）：**停下并上报**，退路是把注入改到 `routes/chat.py` 构造 `graph_input` 时（代价：提示消息进 checkpoint 历史），并同步修改规格第 3.2 节与本计划任务 2。
  - 若通过：继续任务 1。

- [ ] **步骤 4：清理**

运行：`rm /tmp/verify_hook.py`。本任务不 commit（无仓库变更）。

---

### 任务 1：`tools.py` 新增 `a2a_resume` 工具

**文件：**
- 修改：`src/a2a_gateway/tools.py`
- 测试：`tests/test_tools_a2a.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_tools_a2a.py` 末尾追加（`FakeWrapper` / `_patch_wrapper` 复用文件顶部已有的替身）：

```python
class FakePendingStore:
    """带挂起记录的 store 替身。"""

    def __init__(self, pending=None):
        self.pending = pending
        self.upserts = []
        self.deleted = []

    async def get(self, thread_id):
        return self.pending

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    async def delete(self, thread_id):
        self.deleted.append(thread_id)


def _pending(**overrides):
    from types import SimpleNamespace

    base = {
        "thread_id": "th-1",
        "agent_id": 7,
        "target_url": "http://h:9900/",
        "target_name": "travel",
        "task_id": "t-1",
        "context_id": "c-1",
        "question": "请补充目的地",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _resume_tool(monkeypatch, events, store, error=None):
    """构造 tools 并返回最后一个工具（a2a_resume）与替身 wrapper。"""
    holder = {}

    def build(target):
        wrapper = FakeWrapper(target, events)
        wrapper._error = error
        holder["wrapper"] = wrapper
        return wrapper

    monkeypatch.setattr(tools_mod, "A2AClientWrapper", build)
    tools, _ = make_a2a_tools(
        [A2ATarget(url="http://h:9900/", name="travel")],
        agent_id=7,
        pending_store=store,
    )
    return tools[-1], holder["wrapper"]


async def test_resume_tool_forwards_answer_with_task_id(monkeypatch):
    store = FakePendingStore(pending=_pending())
    tool, wrapper = _resume_tool(
        monkeypatch, [TextChunk("行程内容"), Completed(task_id="t-1")], store
    )

    result = await tool.ainvoke(
        {"answer": "杭州 10/1-10/3"}, config={"configurable": {"thread_id": "th-1"}}
    )

    assert result == "行程内容"
    assert wrapper.calls == [{"text": "杭州 10/1-10/3", "task_id": "t-1"}]
    assert store.deleted == ["th-1"]


async def test_resume_tool_without_pending_returns_hint(monkeypatch):
    store = FakePendingStore(pending=None)
    tool, wrapper = _resume_tool(monkeypatch, [TextChunk("x")], store)

    result = await tool.ainvoke({"answer": "补充"}, config={"configurable": {"thread_id": "th-1"}})

    assert "没有等待补充" in result
    assert wrapper.calls == []
    assert store.deleted == []


async def test_resume_tool_refreshes_question_on_repeat_interrupt(monkeypatch):
    store = FakePendingStore(pending=_pending(question="旧追问"))
    tool, _ = _resume_tool(
        monkeypatch,
        [InputRequired(task_id="t-1", context_id="c-1", question="请补充预算")],
        store,
    )

    result = await tool.ainvoke({"answer": "只有日期"}, config={"configurable": {"thread_id": "th-1"}})

    assert result == "请补充预算"
    assert store.upserts == [
        {
            "thread_id": "th-1",
            "agent_id": 7,
            "target_url": "http://h:9900/",
            "target_name": "travel",
            "task_id": "t-1",
            "context_id": "c-1",
            "question": "请补充预算",
        }
    ]
    assert store.deleted == []


async def test_resume_tool_deletes_pending_on_target_mismatch(monkeypatch):
    store = FakePendingStore(pending=_pending(target_url="http://gone:1/"))
    tool, _ = _resume_tool(monkeypatch, [TextChunk("x")], store)

    result = await tool.ainvoke({"answer": "补充"}, config={"configurable": {"thread_id": "th-1"}})

    assert "重新描述需求" in result
    assert store.deleted == ["th-1"]


async def test_resume_tool_deletes_and_alerts_on_target_error(monkeypatch):
    notified = []

    async def fake_notify(title, detail):
        notified.append((title, detail))

    monkeypatch.setattr(tools_mod, "notify_alert", fake_notify)
    store = FakePendingStore(pending=_pending())
    tool, _ = _resume_tool(monkeypatch, [], store, error=A2ATargetError("network", "down"))

    result = await tool.ainvoke({"answer": "补充"}, config={"configurable": {"thread_id": "th-1"}})

    assert "暂时不可用" in result
    assert store.deleted == ["th-1"]
    assert notified and notified[0][0] == "挂起任务恢复失败"


async def test_resume_tool_without_thread_id_returns_hint(monkeypatch):
    store = FakePendingStore(pending=_pending())
    tool, wrapper = _resume_tool(monkeypatch, [TextChunk("x")], store)

    result = await tool.ainvoke({"answer": "补充"})

    assert "重新描述需求" in result
    assert wrapper.calls == []
```

同时在文件顶部补 import：`from a2a_gateway.a2a_client import A2ATargetError`。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_tools_a2a.py -v`

预期：FAIL —— `make_a2a_tools` 只返回 1 个工具，`tools[-1]` 是 `a2a_call`，resume 相关断言全部失败。

- [ ] **步骤 3：编写最少实现代码**

在 `src/a2a_gateway/tools.py` 中，`A2ACallArgs` 之后、`_build_a2a_tool` 之前新增：

```python
class A2AResumeArgs(BaseModel):
    answer: str = Field(description="用户对追问的补充信息，原样转发给下游 Agent")
```

在 `_build_a2a_tool` 之后新增：

```python
def _build_a2a_resume_tool(
    wrappers: list[A2AClientWrapper],
    *,
    agent_id: int,
    pending_store: PendingStore,
) -> StructuredTool:
    """构造恢复工具：把用户的补充信息转发给挂起的下游任务。

    目标由挂起记录锁定（不按目标拆分工具）；无挂起时返回提示文本，
    引导模型改用 a2a_call 发起新请求。
    """

    async def _acall(answer: str, config: RunnableConfig) -> str:
        thread_id = (config.get("configurable") or {}).get("thread_id") or ""
        if not thread_id:
            return "无法定位当前会话，请让用户重新描述需求。"
        pending = await pending_store.get(thread_id)
        if pending is None:
            return "当前没有等待补充的远端任务，请改用 a2a_call 发起新请求。"
        wrapper = next((w for w in wrappers if w.target.url == pending.target_url), None)
        if wrapper is None:
            await pending_store.delete(thread_id)
            return "下游目标配置已变化，请让用户重新描述需求。"

        chunks: list[str] = []
        again: InputRequired | None = None
        try:
            async for event in wrapper.stream_message_events(
                answer, task_id=pending.task_id, context_id=pending.context_id or None
            ):
                if isinstance(event, TextChunk):
                    chunks.append(event.text)
                elif isinstance(event, InputRequired):
                    again = event
        except A2ATargetError as e:
            await pending_store.delete(thread_id)
            await notify_alert(
                "挂起任务恢复失败",
                f"thread={thread_id} target={pending.target_url} kind={e.kind} error={e.detail}",
            )
            return f"目标暂时不可用（{e}），请稍后重试或重新描述需求。"

        if again is not None:
            await pending_store.upsert(
                thread_id=thread_id,
                agent_id=agent_id,
                target_url=pending.target_url,
                target_name=pending.target_name,
                task_id=again.task_id,
                context_id=again.context_id,
                question=again.question,
            )
            return again.question or "（需要用户继续补充信息）"
        await pending_store.delete(thread_id)
        return "".join(chunks) or "（A2A 目标未返回内容）"

    def _call(answer: str) -> str:
        raise RuntimeError("a2a_resume 仅支持异步调用")

    return StructuredTool.from_function(
        coroutine=_acall,
        func=_call,
        name="a2a_resume",
        description=(
            "把用户对追问的补充信息转发给正在等待补充的远端 A2A 任务，并让该任务继续执行。"
            "仅当上下文中出现「等待用户补充信息」的远端任务时使用；"
            "当前没有等待补充的任务时请改用 a2a_call。"
        ),
        args_schema=A2AResumeArgs,
    )
```

在 `make_a2a_tools` 的 `return tools, wrappers` 之前追加注册：

```python
    if usable:
        tools.append(
            _build_a2a_resume_tool(wrappers, agent_id=agent_id, pending_store=store)
        )
    return tools, wrappers
```

并在文件顶部 import 中补上 `A2ATargetError`（当前 `from .a2a_client import A2AClientWrapper, A2ATargetError, InputRequired, TextChunk` 已包含，确认即可）。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_tools_a2a.py -v`

预期：全部 PASS（含原有 3 条 `a2a_call` 用例——它们取 `tools[0]`，不受影响）。

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/tools.py tests/test_tools_a2a.py
git commit -m "feat: 新增 a2a_resume 工具转发用户补充信息"
```

---

### 任务 2：`graph.py` 注入挂起上下文

**文件：**
- 修改：`src/a2a_gateway/graph.py`
- 测试：`tests/test_graph.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_graph.py` 末尾追加：

```python
class FakePendingStore:
    def __init__(self, pending=None):
        self.pending = pending

    async def get(self, thread_id):
        return self.pending


class ExplodingPendingStore:
    async def get(self, thread_id):
        raise RuntimeError("db down")


def _pending_record(question="请补充目的地"):
    from types import SimpleNamespace

    return SimpleNamespace(
        thread_id="t1",
        agent_id=1,
        target_url="http://h:9900/",
        target_name="travel",
        task_id="task-1",
        context_id="c-1",
        question=question,
    )


async def test_history_hook_injects_pending_notice():
    hook = _make_history_hook(FakeLLM(), FakePendingStore(_pending_record()))
    messages = [HumanMessage(content="m0")]

    result = await hook(
        {"messages": messages}, {"configurable": {"thread_id": "t1"}}
    )

    first = result["llm_input_messages"][0]
    assert isinstance(first, SystemMessage)
    assert "请补充目的地" in first.content
    assert "a2a_resume" in first.content
    assert result["llm_input_messages"][1:] == messages


async def test_history_hook_without_pending_injects_nothing():
    hook = _make_history_hook(FakeLLM(), FakePendingStore(None))
    messages = [HumanMessage(content="m0")]

    result = await hook(
        {"messages": messages}, {"configurable": {"thread_id": "t1"}}
    )

    assert result["llm_input_messages"] == messages


async def test_history_hook_survives_pending_lookup_failure():
    hook = _make_history_hook(FakeLLM(), ExplodingPendingStore())
    messages = [HumanMessage(content="m0")]

    result = await hook(
        {"messages": messages}, {"configurable": {"thread_id": "t1"}}
    )

    assert result["llm_input_messages"] == messages


async def test_history_hook_keeps_notice_first_when_summarizing():
    fake = FakeLLM(summary_reply="摘要内容")
    hook = _make_history_hook(fake, FakePendingStore(_pending_record("请补充预算")))
    messages = [HumanMessage(content=f"m{i}") for i in range(KEEP_RECENT + SUMMARIZE_BATCH)]

    result = await hook(
        {"messages": messages}, {"configurable": {"thread_id": "t1"}}
    )

    llm_input = result["llm_input_messages"]
    assert "请补充预算" in llm_input[0].content
    assert "摘要内容" in llm_input[1].content
    assert llm_input[2:] == messages[-KEEP_RECENT:]
```

顶部补 import：`from langchain_core.messages import SystemMessage`（与现有 `AIMessage, HumanMessage, ToolMessage` 合并）。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_graph.py -v`

预期：FAIL —— `_make_history_hook()` 不接受第二个位置参数（`TypeError`）。

- [ ] **步骤 3：编写最少实现代码**

`src/a2a_gateway/graph.py` 修改四处：

1. 顶部 import 追加：

```python
from .pending_store import PendingStore, default_pending_store
```

2. `_make_history_hook` 改为：

```python
def _make_history_hook(llm: Any, pending_store: PendingStore | None = None):
    """构造 pre_model_hook：超出窗口的历史滚动摘要为一段文字。

    另按会话 thread_id 查询挂起任务，命中时在模型可见输入最前面插入一条
    提示（仅影响 llm_input_messages，不写入 checkpoint 历史）。
    """
    store = pending_store or default_pending_store

    async def _pending_notice(config: Any) -> list[Any]:
        try:
            thread_id = ((config or {}).get("configurable") or {}).get("thread_id") or ""
            if not thread_id:
                return []
            pending = await store.get(thread_id)
        except Exception:
            logger.warning("查询挂起任务失败，本轮跳过上下文注入", exc_info=True)
            return []
        if pending is None:
            return []
        return [
            SystemMessage(
                content=(
                    "当前有一个远端 Agent 任务正在等待用户补充信息"
                    f"（目标「{pending.target_name or pending.target_url}」，"
                    f"追问：{pending.question}）。"
                    "用户若已给出补充，请调用 a2a_resume 工具把补充内容转发出去；"
                    "不要自行编造结果。"
                )
            )
        ]

    async def history_compression_hook(state: Any, config: Any = None) -> dict[str, Any]:
        messages: list[Any] = state.get("messages") or []
        summary: str = state.get("summary") or ""
        covered: int = state.get("summarized_count") or 0

        recent = _safe_recent(messages, KEEP_RECENT)
        notice = await _pending_notice(config)

        def build_llm_input(cur_summary: str) -> list[Any]:
            if cur_summary:
                system = SystemMessage(
                    content=f"以下是与用户的早期对话摘要（供参考上下文）：\n{cur_summary}"
                )
                return [*notice, system, *recent]
            return [*notice, *recent]

        overflow = len(messages) - KEEP_RECENT
        pending_count = overflow - covered
        if len(messages) <= KEEP_RECENT or pending_count < SUMMARIZE_BATCH:
            return {"llm_input_messages": build_llm_input(summary)}
        ...
```

（函数体其余部分保持原样：摘要调用、`logger.warning`、`return {"summary": ..., "summarized_count": ..., "llm_input_messages": build_llm_input(new_summary)}`。注意原实现里局部变量叫 `pending`，为避免与本文件的「挂起」语义混淆，把它改名为 `pending_count`。）

3. `build_graph` 新增参数并透传：

```python
def build_graph(
    agent: AgentConfig,
    a2a_tools: list[StructuredTool],
    *,
    checkpointer,
    mcp_servers: list[McpServerConfig] | None = None,
    mcp_tool_index: McpToolIndex | None = None,
    pending_store: PendingStore | None = None,
):
    ...
        pre_model_hook=_make_history_hook(llm, pending_store),
```

4. `DEFAULT_SYSTEM_PROMPT` 追加：

```python
    "若上下文中出现等待用户补充信息的远端任务，请调用 a2a_resume 工具"
    "把用户的补充内容转发出去；拿到结果后整理作答，其中关键数据"
    "（行程、金额、日期、名称等）请原样保留，不要改写或编造。"
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_graph.py -v`

预期：全部 PASS（原有用例用单参数 `await hook({...})` 调用，靠 `config=None` 默认值保持兼容）。

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/graph.py tests/test_graph.py
git commit -m "feat: 对话前注入挂起任务上下文并引导调用 a2a_resume"
```

---

### 任务 3：删除对话路由的恢复拦截

**文件：**
- 修改：`src/a2a_gateway/routes/chat.py`
- 测试：`tests/test_chat_input_required.py`

- [ ] **步骤 1：改写测试（先让它们表达新行为）**

`tests/test_chat_input_required.py` 做如下调整：

- **删除** `test_resume_forwards_with_task_id_and_skips_llm`（其断言的「恢复轮不调用 LLM 图」已被本设计推翻）；
- **删除** `test_resume_updates_pending_on_repeat_interrupt` 与 `test_resume_clears_pending_on_target_error`（这两条行为已由任务 1 的工具层用例覆盖）；
- **保留** `test_normal_flow_emits_interrupt_when_pending_appears` 与 `test_delete_conversation_clears_pending`；
- **新增**下面这条（替换文件顶部的 `FakeGraph`，它原来是「禁止 astream_events」的替身）：

```python
class FakeGraph:
    """正常轮替身：记录被调用次数并按脚本产出 token 事件。"""

    def __init__(self, on_stream=None):
        self.on_stream = on_stream
        self.updated = []
        self.stream_calls = 0

    async def astream_events(self, *args, **kwargs):
        self.stream_calls += 1
        if self.on_stream is not None:
            self.on_stream()
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="已转述追问")},
            "metadata": {"langgraph_node": "agent"},
        }

    async def aupdate_state(self, config, values, as_node=None):
        self.updated.append({"config": config, "values": values, "as_node": as_node})


async def test_chat_does_not_intercept_pending(anon_client, monkeypatch, make_agent):
    """挂起存在时对话仍走正常 graph 流程（不再由路由层拦截转发）。"""
    store = FakeStore(pending=_record())
    graph = FakeGraph()

    async def fake_instance(agent):
        return graph

    _patch_agent(monkeypatch, make_agent)
    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post(
        "/api/chat", json={"message": "杭州 10/1-10/3 预算3000", "thread_id": "t1"}
    )

    assert resp.status_code == 200
    assert graph.stream_calls == 1
    assert store.deleted == []       # 路由层不再自行清理挂起
    assert graph.updated == []       # 不再手工补历史
    assert "event: token" in resp.text
    assert "event: done" in resp.text
```

同时把文件头 docstring 改为「链路 A（对话界面）input-required 测试：挂起上下文注入后的正常轮行为、interrupt 事件与清理。」

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_chat_input_required.py -v`

预期：FAIL —— 挂起存在时仍走 `_resume_pending`，`graph.stream_calls == 0`。

- [ ] **步骤 3：编写最少实现代码**

`src/a2a_gateway/routes/chat.py`：

1. 删除 `_stream_chat` 中的恢复拦截块（原第 130-138 行）：

```python
    try:
        pending = await default_pending_store.get(thread_id)
    except Exception:
        logger.exception("查询挂起任务失败 thread=%s", thread_id)
        pending = None
    if pending is not None:
        async for evt in _resume_pending(agent, pending, message, thread_id):
            yield evt
        return
```

2. 删除 `_resume_pending` 与 `_append_history` 两个函数（原第 146-238 行区间）。

3. 清理随之失效的 import / 符号，按 basedpyright 报告逐项删除：`A2ATargetError`、`InputRequired`、`TextChunk`、`get_agent_wrappers`、`PendingRecord`；若 `AIMessage` 仅被 `_append_history` 使用则一并删除。**保留**：`default_pending_store`（轮末复查与删除会话清理仍在用）、`get_agent_instance`、`get_checkpointer`、`HumanMessage`。

4. 确认 `agent_factory.get_agent_wrappers` 本身**不删除**（`routes/a2a_server.py:506` 仍在使用）。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_chat_input_required.py tests/test_chat_api.py -v`

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/routes/chat.py tests/test_chat_input_required.py
git commit -m "refactor: 对话路由移除恢复拦截，恢复轮交由 a2a_resume 工具驱动"
```

---

### 任务 4：全量回归与联调验收

**文件：**
- 修改：`docs/superpowers/specs/2026-09-18-a2a-resume-tool-design.md`、`TODO.md`

- [ ] **步骤 1：全量测试**

运行：`uv run pytest`

预期：全绿。若 `test_agent_factory.py` 或 `test_bindings.py` 因工具数量变化而失败，按实际断言修正（工具列表多了一个 `a2a_resume`）。

- [ ] **步骤 2：类型检查**

运行：`uv run basedpyright`（或项目 pyproject 中配置的命令）

预期：standard 模式无错误。

- [ ] **步骤 3：联调链路 A（devops-43）**

用 devops-43 MCP 在 `a2a-gateway` 项目执行：`git pull && docker compose up -d --build`（即 `update` action），然后：

1. 在网关聊天界面用 slug=news 的会话发送「帮我规划南昌的旅行」→ 应收到追问；
2. 回复「9/29-10/1，预算3000」→ 界面先出现 `a2a_resume` 工具卡片（含下游行程原文），随后出现模型整理后的回答；
3. 在 `travel-agent` 项目验证同一 task 恢复（替换 `<TID>` 为今天新增的 thread）：

```bash
cd /github/travel-agent && echo 'import sqlite3' > /tmp/chk.py; echo 'from langgraph.checkpoint.sqlite import SqliteSaver' >> /tmp/chk.py; echo 'con = sqlite3.connect("/app/data/checkpoints.db", check_same_thread=False)' >> /tmp/chk.py; echo 'for t in SqliteSaver(con).list({"configurable": {"thread_id": "<TID>"}}):' >> /tmp/chk.py; echo '    print(t.checkpoint.get("ts"), sorted(t.checkpoint.get("channel_values", {}).keys()))' >> /tmp/chk.py; docker cp /tmp/chk.py travel-agent:/tmp/chk.py; docker compose exec -T travel-agent python /tmp/chk.py; rm -f /tmp/chk.py
```

预期：checkpoint 时间线跨越两次用户消息，`messages` 数量递增，且**今天只新增一个 thread**。

- [ ] **步骤 4：连续追问场景**

补充不完整信息（例如只给日期不给预算）→ 界面等待提示仍在、追问更新 → 再次补充后完成。

- [ ] **步骤 5：文档与待办收尾**

把规格文件状态改为「已实现」，并在 `TODO.md` 中记录本次改动。

- [ ] **步骤 6：Commit**

```bash
git add docs/superpowers/specs/2026-09-18-a2a-resume-tool-design.md TODO.md
git commit -m "docs: 标记 a2a_resume 恢复工具化设计已实现"
```

---

## 自检结果

- **规格覆盖度**：规格 3.1（工具）→ 任务 1；3.2（hook 注入）→ 任务 2；3.3（提示词）→ 任务 2；3.4（路由简化）→ 任务 3；第 4 节错误边界 → 任务 1 全部有用例；第 5 节测试 → 任务 1/2/3；第 6 节验收 → 任务 4；第 7 节假设 → 任务 0。无遗漏。
- **类型一致性**：全计划统一使用 `a2a_resume` / `answer` / `pending_store` / `_make_history_hook(llm, pending_store)` / `PendingStore`；`FakePendingStore` 在任务 1 与任务 2 各自定义（分属不同测试文件，无跨文件引用）。
- **占位符扫描**：无「待定 / TODO / 后续实现 / 类似任务 N」；所有代码步骤均有可运行代码块。
