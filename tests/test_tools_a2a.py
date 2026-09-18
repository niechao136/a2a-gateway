"""A2A 工具层测试：中断写挂起、正常路径不写、无会话兜底。"""

from a2a_gateway import tools as tools_mod
from a2a_gateway.a2a_client import Completed, InputRequired, TextChunk
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
    """替身 wrapper：按脚本产出事件，并记录调用参数。"""

    def __init__(self, target, events):
        self.target = target
        self._events = events
        self.calls = []

    async def stream_message_events(self, text, *, task_id=None, context_id=None, **kwargs):
        self.calls.append({"text": text, "task_id": task_id})
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
