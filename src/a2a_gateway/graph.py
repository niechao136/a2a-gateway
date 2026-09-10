"""LangGraph 图定义：所有 Agent 共用同一套 ReAct 图结构。

节点（由 create_react_agent 内置）：
- 对话节点：调用 LLM，决定是否调用工具
- 工具调用节点：执行工具（a2a_call 等）

差异点通过 AgentConfig 注入：A2A 目标、system_prompt、启用工具集。
"""

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from .a2a_client import A2AClientWrapper
from .config import get_settings
from .models import AgentConfig
from .tools import OPTIONAL_TOOLS, make_a2a_tool

_settings = get_settings()

DEFAULT_SYSTEM_PROMPT = (
    "你是一个增强型对话 Agent。当用户的问题需要远端专家 Agent（通过 A2A 协议绑定的目标，"
    "如 Hermes）的能力时，调用 a2a_call 工具将请求委托给远端 Agent，并将其回复整理后返回给用户。"
    "对于一般性对话或你能直接回答的问题，直接回复即可。"
)


def build_llm() -> ChatOpenAI:
    """根据配置构造 LLM（OpenAI 兼容端点）。"""
    return ChatOpenAI(
        model=_settings.llm_model,
        api_key=_settings.llm_api_key,
        base_url=_settings.llm_base_url,
        streaming=True,
    )


def build_tools(agent: AgentConfig, wrapper: A2AClientWrapper) -> list:
    """根据 Agent 配置构造工具集：始终包含 a2a_call + 勾选的可选工具。"""
    tools = [make_a2a_tool(wrapper)]
    for name in agent.enabled_tools or []:
        if name == "a2a_call":
            continue
        factory = OPTIONAL_TOOLS.get(name)
        if factory is not None:
            tools.append(factory())
    return tools


def build_graph(
    agent: AgentConfig,
    wrapper: A2AClientWrapper,
    *,
    checkpointer,
):
    """根据 Agent 配置构建 LangGraph 图实例（共用同一套图结构）。"""
    llm = build_llm()
    tools = build_tools(agent, wrapper)
    prompt = agent.system_prompt or DEFAULT_SYSTEM_PROMPT
    return create_react_agent(
        llm,
        tools,
        prompt=prompt,
        checkpointer=checkpointer,
    )
