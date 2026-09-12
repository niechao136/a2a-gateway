"""图构建与对话历史压缩测试。

回归背景：
1. state_schema 曾继承 MessagesState（缺少 remaining_steps），
   导致任何 Agent 构建图时报
   `ValueError: Missing required key(s) {'remaining_steps'} in state_schema`
   （表现为对话页「Agent 加载失败」）。
2. 历史压缩 hook 需要保证：窗口内不动、超窗口按批摘要、
   且不会产生以 ToolMessage 开头的非法消息序列。
"""

from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from a2a_gateway import graph as graph_mod
from a2a_gateway.graph import (
    KEEP_RECENT,
    SUMMARIZE_BATCH,
    _make_history_hook,
    _safe_recent,
    build_graph,
)


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


def _graph_llm() -> BindeableFakeChatModel:
    return BindeableFakeChatModel(messages=iter([AIMessage(content="ok")]))


def test_build_graph_requires_remaining_steps_in_state_schema(monkeypatch):
    """回归：图必须能成功构建（state_schema 含 prebuilt 要求的 remaining_steps）。"""
    monkeypatch.setattr(graph_mod, "build_llm", _graph_llm)

    agent = SimpleNamespace(system_prompt=None)
    graph = build_graph(agent, [], checkpointer=InMemorySaver())

    assert graph is not None


def test_build_graph_uses_agent_system_prompt(monkeypatch):
    monkeypatch.setattr(graph_mod, "build_llm", _graph_llm)

    agent = SimpleNamespace(system_prompt="你是测试助手")
    graph = build_graph(agent, [], checkpointer=InMemorySaver())

    assert graph is not None


async def test_graph_runs_a_turn(monkeypatch):
    """图能完整跑一轮（同时验证 pre_model_hook 在真实图内可执行）。"""
    monkeypatch.setattr(graph_mod, "build_llm", _graph_llm)
    graph = build_graph(
        SimpleNamespace(system_prompt=None), [], checkpointer=InMemorySaver()
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
