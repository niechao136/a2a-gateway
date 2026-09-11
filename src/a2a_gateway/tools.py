"""工具层：将 A2A 调用封装为标准 LangChain 工具。

- `a2a_call`：调用绑定的 A2A 目标（核心工具，所有 Agent 默认启用）
- 其它可选工具（网页搜索等）按 enabled_tools 勾选启用
"""

from collections.abc import Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .a2a_client import A2AClientWrapper, A2ATargetError
from .notifier import notify_alert


class A2ACallArgs(BaseModel):
    message: str = Field(description="要发送给 A2A 目标 Agent 的消息内容")


def make_a2a_tool(wrapper: A2AClientWrapper) -> StructuredTool:
    """构造绑定到指定 A2A 目标的调用工具。"""

    async def _acall(message: str) -> str:
        """向绑定的 A2A 目标发送消息并聚合返回文本。"""
        chunks: list[str] = []
        try:
            async for chunk in wrapper.stream_message(message):
                chunks.append(chunk)
        except A2ATargetError as e:
            await notify_alert(
                "A2A 调用失败",
                f"target={wrapper.target.url} kind={e.kind} error={e.detail}",
            )
            return f"A2A 调用失败：{e}"
        return "".join(chunks) if chunks else "（A2A 目标未返回内容）"

    def _call(message: str) -> str:
        raise RuntimeError("a2a_call 仅支持异步调用")

    return StructuredTool.from_function(
        coroutine=_acall,
        func=_call,
        name="a2a_call",
        description="调用绑定的远端 A2A Agent（如 Hermes）处理用户请求，返回其回复内容。当问题需要远端 Agent 能力时调用。",
        args_schema=A2ACallArgs,
    )


# 可选工具注册表（按待讨论问题 6：每 Agent 可勾选）
class WebSearchArgs(BaseModel):
    query: str = Field(description="搜索关键词")


def make_web_search_tool() -> StructuredTool:
    """占位网页搜索工具（MVP 阶段返回提示，二期接入真实搜索）。"""

    async def _acall(query: str) -> str:
        return f"（网页搜索工具未接入，查询：{query}）"

    return StructuredTool.from_function(
        coroutine=_acall,
        name="web_search",
        description="在网页上搜索信息并返回结果（MVP 阶段占位）。",
        args_schema=WebSearchArgs,
    )


# 可选工具名 → 构造函数
OPTIONAL_TOOLS: dict[str, Callable[[], StructuredTool]] = {
    "web_search": make_web_search_tool,
}
