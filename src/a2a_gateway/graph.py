"""LangGraph 图定义：所有 Agent 共用同一套 ReAct 图结构。

节点（由 create_react_agent 内置）：
- 对话节点：调用 LLM，决定是否调用工具
- 工具调用节点：执行工具（a2a_call / MCP 工具 / web_search 等）

差异点通过 AgentConfig 注入：A2A 目标、MCP 服务、system_prompt、启用工具集。
"""

from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent

from .llm import build_llm
from .models import AgentConfig
from .tools import OPTIONAL_TOOLS, RESERVED_TOOL_NAMES, make_mcp_call_tool, make_mcp_tools

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
    "若绑定了多个目标，请依据每个工具说明中的「能力与适用场景」选择最合适的一个。"
    "当用户的问题需要外部工具能力时，直接调用对应的 MCP 工具（工具说明里写明了各自用途）。"
    "对于一般性对话或你能直接回答的问题，直接回复即可。"
)


def build_tools(
    agent: AgentConfig,
    a2a_tools: list[StructuredTool],
    *,
    mcp_servers: list[McpServerConfig] | None = None,
    mcp_tool_index: McpToolIndex | None = None,
) -> list[StructuredTool]:
    """根据 Agent 配置构造工具集。

    组成：A2A 工具（每个目标一个，含描述） + 勾选的可选工具 + 绑定的 MCP 工具。
    """
    tools: list[StructuredTool] = list(a2a_tools)

    for name in agent.enabled_tools or []:
        if name in RESERVED_TOOL_NAMES:
            continue
        factory = OPTIONAL_TOOLS.get(name)
        if factory is not None:
            tools.append(factory())

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
    tools = build_tools(
        agent, a2a_tools, mcp_servers=mcp_servers, mcp_tool_index=mcp_tool_index
    )
    prompt = agent.system_prompt or DEFAULT_SYSTEM_PROMPT
    return create_react_agent(
        llm,
        tools,
        prompt=prompt,
        checkpointer=checkpointer,
    )
