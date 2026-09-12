"""LangGraph 图定义：所有 Agent 共用同一套 ReAct 图结构。

节点（由 create_react_agent 内置）：
- pre_model_hook：对话历史压缩（摘要 + 最近消息截取，见 _make_history_hook）
- agent 对话节点：调用 LLM，决定是否调用工具
- 工具调用节点：执行工具（a2a_call / MCP 工具等）

差异点通过 AgentConfig 注入：A2A 目标、MCP 服务、system_prompt。
"""

import logging
from typing import Any

from langchain_core.messages import (
    SystemMessage,
    ToolMessage,
    get_buffer_string,
)
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent
from langgraph.prebuilt.chat_agent_executor import AgentState

from .llm import build_llm
from .models import AgentConfig
from .tools import make_mcp_call_tool, make_mcp_tools

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
)

# ---------------------------------------------------------------------------
# 对话历史压缩
# ---------------------------------------------------------------------------
# 模型可见的最近消息条数；更早的消息滚动压缩为摘要
KEEP_RECENT = 20
# 「最近窗口之外」累计新增多少条未压缩消息时，触发一次摘要生成
SUMMARIZE_BATCH = 12


class AgentChatState(AgentState):
    """在 AgentState（messages + remaining_steps）之上增加压缩摘要字段。

    注意：必须继承 AgentState 而非 MessagesState —— prebuilt 要求
    state_schema 含 remaining_steps，否则构建图时报
    ValueError: Missing required key(s) {'remaining_steps'} in state_schema。
    """

    # 早期对话的滚动摘要（模型不可见时也会保留在状态里）
    summary: str
    # 已被摘要覆盖的前缀消息条数
    summarized_count: int


def _safe_recent(messages: list[Any], keep: int) -> list[Any]:
    """取最近 keep 条消息，并保证首条不是 ToolMessage。

    OpenAI 接口要求 ToolMessage 前必须有带 tool_calls 的 AI 消息；
    简单截断可能从 ToolMessage 开头，导致请求被拒绝。
    """
    recent = list(messages[-keep:])
    while recent and isinstance(recent[0], ToolMessage):
        recent.pop(0)
    return recent


def _make_history_hook(llm: Any):
    """构造 pre_model_hook：超出窗口的历史滚动摘要为一段文字。"""

    async def history_compression_hook(state: Any) -> dict[str, Any]:
        messages: list[Any] = state.get("messages") or []
        summary: str = state.get("summary") or ""
        covered: int = state.get("summarized_count") or 0

        recent = _safe_recent(messages, KEEP_RECENT)

        def build_llm_input(cur_summary: str) -> list[Any]:
            if cur_summary:
                system = SystemMessage(
                    content=f"以下是与用户的早期对话摘要（供参考上下文）：\n{cur_summary}"
                )
                return [system, *recent]
            return recent

        overflow = len(messages) - KEEP_RECENT
        pending = overflow - covered
        if len(messages) <= KEEP_RECENT or pending < SUMMARIZE_BATCH:
            return {"llm_input_messages": build_llm_input(summary)}

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
            return {"llm_input_messages": build_llm_input(summary)}

        return {
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
):
    """根据 Agent 配置构建 LangGraph 图实例（共用同一套图结构）。

    @param a2a_tools 已按目标构造好的 A2A 工具（含各自描述）
    """
    llm = build_llm()
    tools = build_tools(a2a_tools, mcp_servers=mcp_servers, mcp_tool_index=mcp_tool_index)
    prompt = agent.system_prompt or DEFAULT_SYSTEM_PROMPT
    return create_react_agent(
        llm,
        tools,
        prompt=prompt,
        checkpointer=checkpointer,
        state_schema=AgentChatState,
        # 历史压缩：进入模型前做「摘要 + 最近窗口」裁剪（状态里的完整历史保留，
        # 压缩只影响模型可见的 llm_input_messages，time travel 不受影响）
        pre_model_hook=_make_history_hook(llm),
    )
