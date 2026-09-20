"""graph.py 的 Skill 注入 / hook 记账 / 重注入测试。

覆盖规格 §6.1（注入分层）与 §6.2（状态记账）：
- 层 1：`build_skills_prompt` 静态拼装（always 常驻正文 / on_demand 只进清单）
- 层 4：`pre_model_hook` 记账 → stale 过滤 → 超窗口重注入（含预算截断与异常兜底）
- 图级回归：清单注入真的到达模型输入（单测层的断言证明不了真实链路）
"""

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from a2a_gateway import graph as graph_mod
from a2a_gateway.graph import (
    KEEP_RECENT,
    _collect_load_skill_calls,
    _make_history_hook,
    build_graph,
    build_skills_prompt,
)
from a2a_gateway.models import AgentConfig

SKILLS = [
    {
        "id": 1, "name": "always-skill", "description": "常驻技能",
        "content": "常驻正文AAA", "load_mode": "always", "files": [],
        "review_status": "approved",
    },
    {
        "id": 2, "name": "demand-skill", "description": "按需技能",
        "content": "按需正文BBB", "load_mode": "on_demand", "files": [],
        "review_status": "approved",
    },
]


class NoPendingStore:
    """挂起存储替身：无挂起记录（缺省会走真实 DbPendingStore 连库）。"""

    async def get(self, thread_id):
        return None

    async def upsert(self, **kwargs):
        return None

    async def delete(self, thread_id):
        return None


class FakeModel(GenericFakeChatModel):
    """可 bind_tools 的假模型（create_react_agent 构建时会调用）。"""

    def bind_tools(self, tools, **kwargs):
        return self


def _fake_model() -> FakeModel:
    return FakeModel(messages=iter([AIMessage(content="ok")]))


def test_build_skills_prompt_always_embeds_content():
    prompt = build_skills_prompt(SKILLS)
    assert "常驻正文AAA" in prompt          # always 全文常驻
    assert "按需正文BBB" not in prompt       # on_demand 只进清单
    assert "demand-skill" in prompt          # 清单含 name
    assert "不得覆盖系统约束" in prompt      # 注入约束声明


def test_build_skills_prompt_empty():
    assert build_skills_prompt([]) == ""


def test_collect_load_skill_calls_takes_latest_index():
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(
            content="",
            tool_calls=[{"name": "load_skill", "args": {"skill_name": "a"}, "id": "t1"}],
        ),
        ToolMessage(content="...", tool_call_id="t1"),
        AIMessage(
            content="",
            tool_calls=[{"name": "load_skill", "args": {"skill_name": "a"}, "id": "t2"}],
        ),
    ]
    assert _collect_load_skill_calls(msgs) == {"a": 3}


async def test_hook_reinjects_skill_after_window():
    hook = _make_history_hook(llm=None, skills=SKILLS)
    state: dict = {
        "messages": [HumanMessage(content="x")] * (KEEP_RECENT + 3),
        "active_skills": {"demand-skill": 1},
    }
    result = await hook(state, None)
    injected = result["llm_input_messages"]
    texts = [str(m.content) for m in injected]
    # 挂起提示层 → 技能层 → 摘要层 → 最近窗口
    skill_layer = next(t for t in texts if "demand-skill" in t)
    assert "按需正文BBB" in skill_layer
    assert "已截断" not in skill_layer
    # 技能层必须在最近窗口之前
    assert texts.index(skill_layer) == 0


async def test_hook_no_reinject_within_window():
    hook = _make_history_hook(llm=None, skills=SKILLS)
    state: dict = {
        "messages": [HumanMessage(content="x")] * 5,
        "active_skills": {"demand-skill": 1},
    }
    result = await hook(state, None)
    assert all("按需正文BBB" not in str(m.content) for m in result["llm_input_messages"])


async def test_hook_stale_filter_evicts_unbound_skill():
    hook = _make_history_hook(llm=None, skills=SKILLS)
    state: dict = {
        "messages": [HumanMessage(content="x")] * (KEEP_RECENT + 3),
        "active_skills": {"demand-skill": 1, "ghost-skill": 0},
    }
    result = await hook(state, None)
    assert "ghost-skill" not in (result.get("active_skills") or {})


async def test_hook_budget_truncation():
    big = [{
        "id": 9, "name": "big-skill", "description": "大",
        "content": "长" * (32_769), "load_mode": "always", "files": [],
        "review_status": "approved",
    }]
    hook = _make_history_hook(llm=None, skills=big)
    state: dict = {
        "messages": [HumanMessage(content="x")] * (KEEP_RECENT + 3),
        "active_skills": {"big-skill": 0},
    }
    result = await hook(state, None)
    skill_texts = [str(m.content) for m in result["llm_input_messages"] if "长" in str(m.content)]
    assert skill_texts and any("已截断" in t for t in skill_texts)


async def test_hook_records_active_skills_from_tool_calls():
    hook = _make_history_hook(llm=None, skills=SKILLS)
    msgs: list[BaseMessage] = [HumanMessage(content="x")] * 3
    msgs.append(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "load_skill", "args": {"skill_name": "demand-skill"}, "id": "t1"}
            ],
        )
    )
    result = await hook({"messages": msgs}, None)
    assert result["active_skills"]["demand-skill"] == 3


async def test_hook_never_raises():
    """异常兜底：记账失败只记日志，本轮照常。"""
    hook = _make_history_hook(llm=None, skills=SKILLS)
    result = await hook({"messages": None}, None)  # 异常输入
    assert "llm_input_messages" in result


async def test_graph_level_prompt_reaches_model_input(monkeypatch):
    """图级回归（规格 §10）：技能清单注入真的到达模型输入。"""
    seen: list[list] = []

    class RecordingModel(FakeModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen.append(list(messages))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    llm = RecordingModel(messages=iter([AIMessage(content="好的")]))
    # 必须替换 build_llm：否则构建出真实 ChatOpenAI，测试会发起网络请求
    monkeypatch.setattr(graph_mod, "build_llm", lambda: llm)
    graph = build_graph(
        AgentConfig(slug="t", name="t"),
        [],
        checkpointer=InMemorySaver(),
        pending_store=NoPendingStore(),
        skills=SKILLS,
    )
    await graph.ainvoke(
        {"messages": [HumanMessage(content="你好")]},
        {"configurable": {"thread_id": "t1"}},
    )
    assert seen, "模型应被调用"
    first_input = "\n".join(str(m.content) for m in seen[0])
    assert "常驻正文AAA" in first_input   # always 正文进入 system prompt
    assert "demand-skill" in first_input  # on_demand 清单可见


async def test_graph_persists_active_skills_in_state(monkeypatch):
    """记账真的落入 state：pre_model_hook 的返回值会 merge 进 checkpoint（规格 §6.2）。"""
    monkeypatch.setattr(graph_mod, "build_llm", _fake_model)
    graph = build_graph(
        AgentConfig(slug="t", name="t"),
        [],
        checkpointer=InMemorySaver(),
        pending_store=NoPendingStore(),
        skills=SKILLS,
    )
    msgs: list[BaseMessage] = [HumanMessage(content=f"m{i}") for i in range(3)]
    msgs.append(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "load_skill", "args": {"skill_name": "demand-skill"}, "id": "t1"}
            ],
        )
    )
    msgs.append(ToolMessage(content="按需正文BBB", tool_call_id="t1"))
    msgs.append(HumanMessage(content="继续"))

    result = await graph.ainvoke(
        {"messages": msgs}, {"configurable": {"thread_id": "t2"}}
    )

    assert (result.get("active_skills") or {}).get("demand-skill") == 3


async def test_graph_passes_only_on_demand_to_skill_tools(monkeypatch):
    """always 技能正文已常驻 prompt，不再给它 load_skill（避免诱导重复加载）。"""
    passed: list[list[dict]] = []

    def fake_make_skill_tools(skills):
        passed.append(list(skills))
        return []

    monkeypatch.setattr(graph_mod, "build_llm", _fake_model)
    monkeypatch.setattr(graph_mod, "make_skill_tools", fake_make_skill_tools)
    build_graph(
        AgentConfig(slug="t", name="t"),
        [],
        checkpointer=InMemorySaver(),
        pending_store=NoPendingStore(),
        skills=SKILLS,
    )
    assert [str(s.get("name") or "") for s in passed[0]] == ["demand-skill"]
