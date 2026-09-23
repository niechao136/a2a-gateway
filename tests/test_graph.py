"""图构建与对话历史压缩测试。

回归背景：
1. state_schema 曾继承 MessagesState（缺少 remaining_steps），
   导致任何 Agent 构建图时报
   `ValueError: Missing required key(s) {'remaining_steps'} in state_schema`
   （表现为对话页「Agent 加载失败」）。
2. 历史压缩 hook 需要保证：窗口内不动、超窗口按批摘要、
   且不会产生以 ToolMessage 开头的非法消息序列。
"""

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.memory import InMemorySaver

from a2a_gateway import graph as graph_mod
from a2a_gateway.graph import (
    KEEP_RECENT,
    SUMMARIZE_BATCH,
    _make_history_hook,
    _safe_recent,
    build_graph,
)
from a2a_gateway.models import AgentConfig


def _agent(system_prompt: str | None = None) -> AgentConfig:
    """最小 Agent 配置（构建图只用到 system_prompt）。"""
    return AgentConfig(slug="t", name="t", system_prompt=system_prompt)


class FakeLLM:
    """最小假模型：支持 bind_tools（构建图时会被调用），记录摘要调用。"""

    def __init__(self, summary_reply: str = "摘要内容"):
        self.summary_reply = summary_reply
        self.summarize_calls: list[str] = []

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, prompt, config=None, **kwargs):
        self.summarize_calls.append(str(prompt))
        return AIMessage(content=self.summary_reply)

    def invoke(self, prompt, config=None, **kwargs):  # pragma: no cover - 同步兜底
        return AIMessage(content=self.summary_reply)


class BindeableFakeChatModel(GenericFakeChatModel):
    """可 bind_tools 的假模型（create_react_agent 构建时会调用）。"""

    def bind_tools(self, tools, **kwargs):
        return self


class RecordingFakeChatModel(BindeableFakeChatModel):
    """记录每次送进模型的消息列表（用于验证真实图内的注入内容）。"""

    seen: list[list[BaseMessage]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _graph_llm(_snapshot: object = None) -> BindeableFakeChatModel:
    """resolve_llm 替身：接收 agent.model_snapshot 参数并忽略之。"""
    return BindeableFakeChatModel(messages=iter([AIMessage(content="ok")]))


class FakePendingStore:
    """挂起存储替身：固定返回一条（或没有）挂起记录。"""

    def __init__(self, pending=None):
        self.pending = pending

    async def get(self, thread_id):
        return self.pending

    async def upsert(self, **kwargs):
        return None

    async def delete(self, thread_id):
        return None


class ExplodingPendingStore:
    """挂起存储替身：查询即抛异常（模拟 DB 不可用）。"""

    async def get(self, thread_id):
        raise RuntimeError("db down")

    async def upsert(self, **kwargs):
        return None

    async def delete(self, thread_id):
        return None


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


def test_build_graph_requires_remaining_steps_in_state_schema(monkeypatch):
    """回归：图必须能成功构建（state_schema 含 prebuilt 要求的 remaining_steps）。"""
    monkeypatch.setattr(graph_mod, "resolve_llm", _graph_llm)

    graph = build_graph(_agent(), [], checkpointer=InMemorySaver())

    assert graph is not None


def test_build_graph_uses_agent_system_prompt(monkeypatch):
    monkeypatch.setattr(graph_mod, "resolve_llm", _graph_llm)

    graph = build_graph(_agent("你是测试助手"), [], checkpointer=InMemorySaver())

    assert graph is not None


async def test_graph_runs_a_turn(monkeypatch):
    """图能完整跑一轮（同时验证 pre_model_hook 在真实图内可执行）。"""
    monkeypatch.setattr(graph_mod, "resolve_llm", _graph_llm)
    # 必须显式注入替身：缺省时 hook 会用 default_pending_store（真实 DbPendingStore，会连库）
    graph = build_graph(
        _agent(),
        [],
        checkpointer=InMemorySaver(),
        pending_store=FakePendingStore(None),
    )

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="hi")]},
        {"configurable": {"thread_id": "t1"}},
    )

    assert result["messages"][-1].content == "ok"


def test_safe_recent_drops_leading_tool_messages():
    messages = [
        HumanMessage(content="q1"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
        ToolMessage(content="r1", tool_call_id="1"),
        AIMessage(content="a1"),
        HumanMessage(content="q2"),
    ]

    recent = _safe_recent(messages, keep=1)

    # 截断到只有最后 1 条（人类消息），不存在孤立工具消息
    assert recent == [messages[-1]]

    # 若窗口起点正好落在 ToolMessage 上，需要向后跳过（避免非法序列）
    recent2 = _safe_recent(messages, keep=2)
    assert not isinstance(recent2[0], ToolMessage)


async def test_history_hook_keeps_recent_without_summary():
    fake = FakeLLM()
    hook = _make_history_hook(fake)
    messages = [HumanMessage(content=f"m{i}") for i in range(KEEP_RECENT)]

    result = await hook({"messages": messages})

    assert result["llm_input_messages"] == messages
    assert fake.summarize_calls == []  # 未超窗口不调用摘要


async def test_history_hook_summarizes_overflow_batch():
    fake = FakeLLM(summary_reply="用户在做 A2A 网关")
    hook = _make_history_hook(fake)
    total = KEEP_RECENT + SUMMARIZE_BATCH
    messages = [HumanMessage(content=f"m{i}") for i in range(total)]

    result = await hook({"messages": messages})

    # 触发一次摘要，模型输入 = 摘要系统消息 + 最近窗口
    assert len(fake.summarize_calls) == 1
    llm_input = result["llm_input_messages"]
    assert "用户在做 A2A 网关" in str(llm_input[0].content)
    assert llm_input[1:] == messages[-KEEP_RECENT:]
    assert result["summarized_count"] == total - KEEP_RECENT
    assert result["summary"] == "用户在做 A2A 网关"


async def test_history_hook_skips_small_increment():
    """窗口外新增不足一批时不重复摘要。"""
    fake = FakeLLM()
    hook = _make_history_hook(fake)
    total = KEEP_RECENT + SUMMARIZE_BATCH - 1
    messages = [HumanMessage(content=f"m{i}") for i in range(total)]

    result = await hook({"messages": messages})

    assert fake.summarize_calls == []
    assert result["llm_input_messages"] == messages[-KEEP_RECENT:]


async def test_history_hook_survives_summary_failure():
    class BrokenLLM(FakeLLM):
        async def ainvoke(self, prompt, config=None, **kwargs):
            raise RuntimeError("boom")

    hook = _make_history_hook(BrokenLLM())
    messages = [HumanMessage(content=f"m{i}") for i in range(KEEP_RECENT + SUMMARIZE_BATCH)]

    result = await hook({"messages": messages})

    # 摘要失败不影响本轮对话：仍返回最近窗口
    assert result["llm_input_messages"] == messages[-KEEP_RECENT:]
    assert "summary" not in result


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
    # 注入只影响模型可见输入，不得写回 state 历史
    assert "messages" not in result
    assert messages == [HumanMessage(content="m0")]


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


async def test_graph_injects_pending_notice_into_model_input(monkeypatch):
    """图级回归：langgraph 真实调用 pre_model_hook 时会注入 config（含 thread_id）。

    上面几条用例是自行给 hook 传 config，证明不了真实链路会注入——注入与否取决于
    hook 的 config 形参注解：注解成 Any 时 langgraph 只告警并跳过，表现为
    「单测全绿但线上永不注入」。这条用例跑真实图，因此能捕捉到那种静默失效。
    """
    question = "请补充出行城市"
    recorder = RecordingFakeChatModel(messages=iter([AIMessage(content="ok")]))
    monkeypatch.setattr(graph_mod, "resolve_llm", lambda _snapshot=None: recorder)

    graph = build_graph(
        _agent(),
        [],
        checkpointer=InMemorySaver(),
        pending_store=FakePendingStore(_pending_record(question)),
    )

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="hi")]},
        {"configurable": {"thread_id": "t1"}},
    )

    assert result["messages"][-1].content == "ok"
    sent = recorder.seen[-1]
    notices = [
        m
        for m in sent
        if isinstance(m, SystemMessage) and question in m.content and "a2a_resume" in m.content
    ]
    assert notices, f"模型输入里没有挂起提示，实际收到：{[type(m).__name__ for m in sent]}"
