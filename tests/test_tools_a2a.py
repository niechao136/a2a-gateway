"""A2A 工具层测试：中断写挂起、正常路径不写、无会话兜底。"""

from a2a_gateway import tools as tools_mod
from a2a_gateway.a2a_client import A2ATargetError, Completed, InputRequired, TextChunk
from a2a_gateway.schemas import A2ATarget
from a2a_gateway.tools import make_a2a_tools


class FakeStore:
    def __init__(self):
        self.upserts = []

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    async def get(self, thread_id):
        return None

    async def delete(self, thread_id):
        return None


class FakeWrapper:
    """替身 wrapper：按脚本产出事件，并记录调用参数。

    ``error`` 非空时在首个事件前抛出，用于覆盖目标调用失败分支。
    """

    def __init__(self, target, events, error=None):
        self.target = target
        self._events = events
        self._error = error
        self.calls = []

    async def stream_message_events(self, text, *, task_id=None, context_id=None, **kwargs):
        self.calls.append({"text": text, "task_id": task_id})
        if self._error is not None:
            raise self._error
        for event in self._events:
            yield event


def _patch_wrapper(monkeypatch, events):
    """把 tools 模块里的 A2AClientWrapper 换成脚本化替身。"""

    def build(target):
        return FakeWrapper(target, events)

    monkeypatch.setattr(tools_mod, "A2AClientWrapper", build)


async def test_a2a_tool_writes_pending_on_input_required(monkeypatch):
    store = FakeStore()
    _patch_wrapper(
        monkeypatch,
        [
            TextChunk("请补充目的地"),
            InputRequired(task_id="t-1", context_id="c-1", question="请补充目的地"),
        ],
    )
    tools, _ = make_a2a_tools(
        [A2ATarget(url="http://h:9900/", name="travel")],
        agent_id=7,
        pending_store=store,
    )

    result = await tools[0].ainvoke(
        {"message": "去旅游"},
        config={"configurable": {"thread_id": "th-1"}},
    )

    assert result == "请补充目的地"
    assert store.upserts == [
        {
            "thread_id": "th-1",
            "agent_id": 7,
            "target_url": "http://h:9900/",
            "target_name": "travel",
            "task_id": "t-1",
            "context_id": "c-1",
            "question": "请补充目的地",
        }
    ]


async def test_a2a_tool_returns_joined_text_without_interrupt(monkeypatch):
    store = FakeStore()
    _patch_wrapper(monkeypatch, [TextChunk("a"), TextChunk("b"), Completed(task_id="t-1")])
    tools, _ = make_a2a_tools(
        [A2ATarget(url="http://h:9900/", name="travel")],
        agent_id=7,
        pending_store=store,
    )

    result = await tools[0].ainvoke(
        {"message": "去旅游"},
        config={"configurable": {"thread_id": "th-1"}},
    )

    assert result == "ab"
    assert store.upserts == []


async def test_a2a_tool_skips_pending_without_thread_id(monkeypatch):
    """拿不到会话 thread_id 时不登记挂起，但仍正常返回追问文本。"""
    store = FakeStore()
    _patch_wrapper(
        monkeypatch, [InputRequired(task_id="t-1", context_id="", question="请补充")]
    )
    tools, _ = make_a2a_tools(
        [A2ATarget(url="http://h:9900/", name="travel")],
        agent_id=7,
        pending_store=store,
    )

    result = await tools[0].ainvoke({"message": "去旅游"})

    assert result == "请补充"
    assert store.upserts == []


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
        wrapper = FakeWrapper(target, events, error)
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

    result = await tool.ainvoke(
        {"answer": "只有日期"}, config={"configurable": {"thread_id": "th-1"}}
    )

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
