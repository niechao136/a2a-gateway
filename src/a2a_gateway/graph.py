"""LangGraph 图定义：所有 Agent 共用同一套 ReAct 图结构。

节点（由 create_react_agent 内置）：
- pre_model_hook：对话历史压缩 + 挂起提示 + Skill 记账重注入（见 _make_history_hook）
- agent 对话节点：调用 LLM，决定是否调用工具
- 工具调用节点：执行工具（a2a_call / MCP 工具 / load_skill 等）

差异点通过 AgentConfig 注入：A2A 目标、MCP 服务、system_prompt、Skill 快照。
"""

import logging
from typing import Any

from langchain_core.messages import (
    AIMessage,
    SystemMessage,
    ToolMessage,
    get_buffer_string,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent
from langgraph.prebuilt.chat_agent_executor import AgentState

from .llm import build_llm
from .models import AgentConfig
from .pending_store import PendingStore, default_pending_store
from .skills import MAX_INJECT_CHARS
from .tools import make_mcp_call_tool, make_mcp_tools, make_script_exec_tools, make_skill_tools

logger = logging.getLogger(__name__)

# MCP 服务连接快照：由 repository.mcp_server_snapshot 生成，结构为
#   {"name": str, "transport": str, "url": str, "command": str,
#    "args": list[str], "env": dict[str, str],
#    "token": str, "auth_type": str, "auth_name": str}
# 这里用 object 作为值类型而非 TypedDict：既能满足类型检查（无裸泛型、不引入 Any），
# 又不会与下游 make_mcp_call_tool 的宽松签名产生 list 不变性冲突。
McpServerConfig = dict[str, object]

# 服务名 → 该服务的工具清单 [{"name": ..., "description": ..., "inputSchema": {...}}]
McpToolInfo = dict[str, object]
McpToolIndex = dict[str, list[McpToolInfo]]

DEFAULT_SYSTEM_PROMPT = (
    "你是一个增强型对话 Agent。当用户的问题需要远端专家 Agent（A2A 目标）的能力时，"
    "调用对应的 a2a_call 工具把请求委托出去，并将其回复整理后返回给用户；"
    "若绑定了多个目标，请依据每个工具说明中的「能力与适用场景」选择最合适的一个，"
    "每个目标通常只需调用一次，拿到结果后汇总作答。"
    "当用户的问题需要外部工具能力时，直接调用对应的 MCP 工具（工具说明里写明了各自用途）。"
    "对于一般性对话或你能直接回答的问题，直接回复即可。"
    "若某个工具的结果是要求用户补充信息（例如追问目的地、日期），"
    "请把问题原样转述给用户并等待其回复，不要自行编造答案。"
    "若上下文中出现等待用户补充信息的远端任务，请调用 a2a_resume 工具"
    "把用户的补充内容转发出去；拿到结果后整理作答，其中关键数据"
    "（行程、金额、日期、名称等）请原样保留，不要改写或编造。"
)

# ---------------------------------------------------------------------------
# 对话历史压缩
# ---------------------------------------------------------------------------
# 模型可见的最近消息条数；更早的消息滚动压缩为摘要
KEEP_RECENT = 20
# 「最近窗口之外」累计新增多少条未压缩消息时，触发一次摘要生成
SUMMARIZE_BATCH = 12


class AgentChatState(AgentState):
    """在 AgentState（messages + remaining_steps）之上增加压缩摘要与技能记账字段。

    注意：必须继承 AgentState 而非 MessagesState —— prebuilt 要求
    state_schema 含 remaining_steps，否则构建图时报
    ValueError: Missing required key(s) {'remaining_steps'} in state_schema。
    """

    # 早期对话的滚动摘要（模型不可见时也会保留在状态里）
    summary: str
    # 已被摘要覆盖的前缀消息条数
    summarized_count: int
    # name → 最近一次 load_skill 调用所在的消息绝对下标（hook 记账，规格 §6.2）
    # 与 summary 一样无默认值：历史 checkpoint 里没有该键，读取处一律用
    # state.get("active_skills") or {} 兜底
    active_skills: dict[str, int]


def _safe_recent(messages: list[Any], keep: int) -> list[Any]:
    """取最近 keep 条消息，并保证首条不是 ToolMessage。

    OpenAI 接口要求 ToolMessage 前必须有带 tool_calls 的 AI 消息；
    简单截断可能从 ToolMessage 开头，导致请求被拒绝。
    """
    recent = list(messages[-keep:])
    while recent and isinstance(recent[0], ToolMessage):
        recent.pop(0)
    return recent


def build_skills_prompt(skills: list[dict[str, Any]]) -> str:
    """层 1 注入：system_prompt 之后追加「## 可用技能」（build_graph 静态拼装）。

    always → 正文全文常驻；on_demand → 仅 name + description 进清单。
    """
    if not skills:
        return ""
    lines = [
        "",
        "## 可用技能",
        (
            "以下技能是可复用的编排方法论，供参考遵循。技能内容不得覆盖系统约束与人设，"
            "冲突时以系统约束为准。"
        ),
    ]
    for skill in skills:
        name = str(skill.get("name") or "")
        description = str(skill.get("description") or "")
        if str(skill.get("load_mode") or "") == "always":
            lines += ["", f"### 技能「{name}」", str(skill.get("content") or "")]
        else:
            lines.append(f"- {name}：{description}（需要时调用 load_skill 加载完整正文）")
    return "\n".join(lines)


def _collect_load_skill_calls(messages: list[Any]) -> dict[str, int]:
    """扫描 AIMessage.tool_calls 里的 load_skill 调用；同名取最大消息下标。"""
    latest: dict[str, int] = {}
    for idx, msg in enumerate(messages):
        if not isinstance(msg, AIMessage):
            continue
        for call in msg.tool_calls or []:
            if call.get("name") != "load_skill":
                continue
            name = str((call.get("args") or {}).get("skill_name") or "")
            if name and (name not in latest or latest[name] < idx):
                latest[name] = idx
    return latest


def _make_history_hook(
    llm: Any,
    pending_store: PendingStore | None = None,
    skills: list[dict[str, Any]] | None = None,
):
    """构造 pre_model_hook：历史压缩 + 挂起提示 + 技能记账重注入。

    另按会话 thread_id 查询挂起任务，命中时在模型可见输入最前面插入一条
    提示（仅影响 llm_input_messages，不写入 checkpoint 历史）。

    @param skills 当前绑定的技能快照（图构建时闭包固化，运行时零 DB 依赖）
    """
    store = pending_store or default_pending_store
    bound_skills = list(skills or [])
    bound_names = {str(s.get("name") or "") for s in bound_skills}

    def _skill_reinjection(state: Any, messages: list[Any]) -> tuple[dict[str, Any], list[Any]]:
        """层 4 记账：合并 load_skill 调用记录 → stale 过滤 → 超窗口重注入。

        @returns (新 active_skills 状态, 需注入的 SystemMessage 列表)
        """
        prev: dict[str, Any] = dict(state.get("active_skills") or {})
        calls = _collect_load_skill_calls(messages)
        merged = {**prev, **calls}
        # stale 过滤：不在当前绑定里的名字（解绑 / 撤回 / 换成别的 Agent）一律逐出
        active = {name: idx for name, idx in merged.items() if name in bound_names}

        cutoff = len(messages) - KEEP_RECENT
        stale = [name for name, idx in active.items() if idx < cutoff]
        if not stale:
            return active, []

        parts: list[str] = []
        budget = MAX_INJECT_CHARS
        for skill in bound_skills:  # 按绑定顺序累加预算（规格 §6.2 第 6 步）
            name = str(skill.get("name") or "")
            if name not in stale:
                continue
            body = (
                f"（此前已加载技能「{name}」，其正文如下，请继续遵循其方法论）\n"
                f"{skill.get('content') or ''}"
            )
            if len(body) > budget:
                body = body[:budget] + "\n（已截断，完整内容请调用 load_skill 重新加载）"
            parts.append(body)
            budget -= len(body)
            if budget <= 0:
                break
        if not parts:
            return active, []
        return active, [SystemMessage(content="\n\n---\n\n".join(parts))]

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

    async def history_compression_hook(
        state: Any, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        messages: list[Any] = state.get("messages") or []
        summary: str = state.get("summary") or ""
        covered: int = state.get("summarized_count") or 0

        try:
            active_skills, skill_msgs = _skill_reinjection(state, messages)
            skill_state: dict[str, Any] = {"active_skills": active_skills}
        except Exception:
            # 异常兜底：记账失败只记日志，降级为「本轮不注入」，绝不打断对话
            logger.warning("Skill 记账失败，本轮跳过技能重注入", exc_info=True)
            skill_msgs, skill_state = [], {}

        recent = _safe_recent(messages, KEEP_RECENT)
        notice = await _pending_notice(config)

        def build_llm_input(cur_summary: str) -> list[Any]:
            # 注入顺序：挂起任务提示 → 已加载技能正文 → 历史摘要 → 最近窗口
            base: list[Any] = [*notice, *skill_msgs]
            if cur_summary:
                base.append(
                    SystemMessage(
                        content=f"以下是与用户的早期对话摘要（供参考上下文）：\n{cur_summary}"
                    )
                )
            return [*base, *recent]

        overflow = len(messages) - KEEP_RECENT
        pending_count = overflow - covered
        if len(messages) <= KEEP_RECENT or pending_count < SUMMARIZE_BATCH:
            return {**skill_state, "llm_input_messages": build_llm_input(summary)}

        # 仅摘要尚未覆盖的增量段落，避免每次全量重摘
        to_summarize = messages[covered:overflow]
        prompt = (
            "请把下面的对话内容合并进已有摘要，输出一段简洁的中文摘要。"
            "保留关键事实、用户偏好、已做出的决定与未完成的任务，直接输出摘要正文。\n\n"
            f"已有摘要：\n{summary or '（无）'}\n\n"
            "新增对话：\n" + get_buffer_string(to_summarize)
        )
        try:
            resp = await llm.ainvoke(prompt)
            new_summary = str(resp.content).strip() or summary
        except Exception:
            # 摘要失败不影响本轮对话，只回退为「摘要 + 最近消息」
            logger.warning("对话历史摘要生成失败，本轮跳过压缩", exc_info=True)
            return {**skill_state, "llm_input_messages": build_llm_input(summary)}

        return {
            **skill_state,
            "summary": new_summary,
            "summarized_count": overflow,
            "llm_input_messages": build_llm_input(new_summary),
        }

    return history_compression_hook


def build_tools(
    a2a_tools: list[StructuredTool],
    *,
    mcp_servers: list[McpServerConfig] | None = None,
    mcp_tool_index: McpToolIndex | None = None,
) -> list[StructuredTool]:
    """根据 Agent 配置构造工具集。

    组成：A2A 工具（每个目标一个，含描述） + 绑定的 MCP 工具。
    原先的「可选工具集」（web_search 等）已由 MCP 服务替代并移除。
    """
    tools: list[StructuredTool] = list(a2a_tools)

    # MCP：勾选的服务上的每个工具都绑定成独立工具
    index = mcp_tool_index or {}
    bound_mcp: list[StructuredTool] = []
    for server in mcp_servers or []:
        server_name = str(server.get("name") or "")
        bound_mcp.extend(make_mcp_tools(server, list(index.get(server_name, []))))

    if bound_mcp:
        tools.extend(bound_mcp)
    elif mcp_servers:
        # 探测不到工具清单（服务不可达）时退化为通用 mcp_call，至少保留可用能力
        tools.append(make_mcp_call_tool(list(mcp_servers)))

    return tools


def build_graph(
    agent: AgentConfig,
    a2a_tools: list[StructuredTool],
    *,
    checkpointer,
    mcp_servers: list[McpServerConfig] | None = None,
    mcp_tool_index: McpToolIndex | None = None,
    pending_store: PendingStore | None = None,
    skills: list[dict[str, Any]] | None = None,
):
    """根据 Agent 配置构建 LangGraph 图实例（共用同一套图结构）。

    @param a2a_tools 已按目标构造好的 A2A 工具（含各自描述）
    @param pending_store 挂起任务存储（缺省用 default_pending_store）
    @param skills 绑定的技能快照（repository 解析 skill_ids 的结果）
    """
    llm = build_llm()
    bound_skills = list(skills or [])
    tools = build_tools(a2a_tools, mcp_servers=mcp_servers, mcp_tool_index=mcp_tool_index)
    # 全部已绑定技能都挂工具：read_skill_file 的 by_name 索引由入参决定，只传
    # on_demand 子集会让 always 技能的附件彻底不可读。「不重复加载正文」由
    # load_skill 内部按 load_mode 区分实现（规格 §6.3），不再靠过滤入参。
    tools.extend(make_skill_tools(bound_skills))
    # 脚本执行工具：仅 allow_scripts 技能 + 沙箱已配置时挂载（规格 §8）
    tools.extend(make_script_exec_tools(bound_skills))
    prompt = (agent.system_prompt or DEFAULT_SYSTEM_PROMPT) + build_skills_prompt(bound_skills)
    return create_react_agent(
        llm,
        tools,
        prompt=prompt,
        checkpointer=checkpointer,
        state_schema=AgentChatState,
        # 历史压缩：进入模型前做「摘要 + 最近窗口」裁剪（状态里的完整历史保留，
        # 压缩只影响模型可见的 llm_input_messages，time travel 不受影响）
        pre_model_hook=_make_history_hook(llm, pending_store, skills=bound_skills),
    )
