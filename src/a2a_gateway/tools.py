"""工具层：将 A2A / MCP 调用封装为标准 LangChain 工具。

- `a2a_call`：每个勾选的 A2A 目标各生成一个工具，说明里带上该目标的描述
  （关键：否则大模型无法判断该调用哪个目标）
- MCP：勾选的服务上的**每个工具**都绑定成独立工具，模型可直接调用
  （探测失败时退化为通用 mcp_call，至少保留可用能力）
- 其它可选工具（网页搜索等）按 enabled_tools 勾选启用
"""

import re
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from .a2a_client import A2AClientWrapper, A2ATargetError, InputRequired, TextChunk
from .mcp_client import call_tool as mcp_invoke
from .mcp_client import connection_from_snapshot, format_tools_for_prompt
from .notifier import notify_alert
from .pending_store import PendingStore, default_pending_store
from .schemas import A2ATarget


def _safe_identifier(raw: str, fallback: str, limit: int = 48) -> str:
    """把任意名称规整为合法的 LLM 工具名（仅保留字母、数字、- 和 _）。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return (cleaned or fallback)[:limit]


# ---------------------------------------------------------------------------
# A2A
# ---------------------------------------------------------------------------
class A2ACallArgs(BaseModel):
    message: str = Field(description="要发送给该 A2A 目标 Agent 的消息内容")


class A2AResumeArgs(BaseModel):
    answer: str = Field(description="用户对追问的补充信息，原样转发给下游 Agent")


def _build_a2a_tool(
    name: str,
    description: str,
    wrapper: A2AClientWrapper,
    *,
    agent_id: int,
    pending_store: PendingStore,
) -> StructuredTool:
    async def _acall(message: str, config: RunnableConfig) -> str:
        """向绑定的 A2A 目标发送消息并聚合返回文本。

        下游要求补充信息（input-required）时，把挂起状态写入 pending_store，
        并把追问原样返回给模型转述；用户的下一条消息由路由层直接恢复该任务。
        """
        chunks: list[str] = []
        required: InputRequired | None = None
        try:
            async for event in wrapper.stream_message_events(message):
                if isinstance(event, TextChunk):
                    chunks.append(event.text)
                elif isinstance(event, InputRequired):
                    required = event
        except A2ATargetError as e:
            await notify_alert(
                "A2A 调用失败",
                f"target={wrapper.target.url} kind={e.kind} error={e.detail}",
            )
            return f"A2A 调用失败：{e}"

        if required is not None:
            thread_id = (config.get("configurable") or {}).get("thread_id") or ""
            if thread_id:
                await pending_store.upsert(
                    thread_id=thread_id,
                    agent_id=agent_id,
                    target_url=wrapper.target.url,
                    target_name=wrapper.target.name or "",
                    task_id=required.task_id,
                    context_id=required.context_id,
                    question=required.question,
                )
            return required.question or "（需要用户补充信息）"
        return "".join(chunks) if chunks else "（A2A 目标未返回内容）"

    def _call(message: str) -> str:
        raise RuntimeError("a2a_call 仅支持异步调用")

    return StructuredTool.from_function(
        coroutine=_acall,
        func=_call,
        name=name,
        description=description,
        args_schema=A2ACallArgs,
    )


def _build_a2a_resume_tool(
    wrappers: list[A2AClientWrapper],
    *,
    agent_id: int,
    pending_store: PendingStore,
) -> StructuredTool:
    """构造恢复工具：把用户的补充信息转发给挂起的下游任务。

    目标由挂起记录锁定（不按目标拆分工具）；无挂起时返回提示文本，
    引导模型改用 a2a_call 发起新请求。
    """

    async def _acall(answer: str, config: RunnableConfig) -> str:
        thread_id = (config.get("configurable") or {}).get("thread_id") or ""
        if not thread_id:
            return "无法定位当前会话，请让用户重新描述需求。"
        pending = await pending_store.get(thread_id)
        if pending is None:
            return "当前没有等待补充的远端任务，请改用 a2a_call 发起新请求。"
        wrapper = next((w for w in wrappers if w.target.url == pending.target_url), None)
        if wrapper is None:
            await pending_store.delete(thread_id)
            return "下游目标配置已变化，请让用户重新描述需求。"

        chunks: list[str] = []
        again: InputRequired | None = None
        try:
            async for event in wrapper.stream_message_events(
                answer, task_id=pending.task_id, context_id=pending.context_id or None
            ):
                if isinstance(event, TextChunk):
                    chunks.append(event.text)
                elif isinstance(event, InputRequired):
                    again = event
        except A2ATargetError as e:
            await pending_store.delete(thread_id)
            await notify_alert(
                "挂起任务恢复失败",
                f"thread={thread_id} target={pending.target_url} kind={e.kind} error={e.detail}",
            )
            return f"目标暂时不可用（{e}），请稍后重试或重新描述需求。"

        if again is not None:
            await pending_store.upsert(
                thread_id=thread_id,
                agent_id=agent_id,
                target_url=pending.target_url,
                target_name=pending.target_name,
                task_id=again.task_id,
                context_id=again.context_id,
                question=again.question,
            )
            return again.question or "（需要用户继续补充信息）"
        await pending_store.delete(thread_id)
        return "".join(chunks) or "（A2A 目标未返回内容）"

    def _call(answer: str) -> str:
        raise RuntimeError("a2a_resume 仅支持异步调用")

    return StructuredTool.from_function(
        coroutine=_acall,
        func=_call,
        name="a2a_resume",
        description=(
            "把用户对追问的补充信息转发给正在等待补充的远端 A2A 任务，并让该任务继续执行。"
            "仅当上下文中出现「等待用户补充信息」的远端任务时使用；"
            "当前没有等待补充的任务时请改用 a2a_call。"
        ),
        args_schema=A2AResumeArgs,
    )


def make_a2a_tools(
    targets: list[A2ATarget],
    *,
    agent_id: int = 0,
    pending_store: PendingStore | None = None,
) -> tuple[list[StructuredTool], list[A2AClientWrapper]]:
    """为每个 A2A 目标各构造一个调用工具。

    - 仅一个目标时沿用历史名称 `a2a_call`；多个目标时按目标名区分
    - 目标描述写进工具说明，让大模型判断该调用哪一个
    - `agent_id` 用于挂起登记（0 表示未知，仅影响链路 B 恢复时的归属校验）
    - `pending_store` 缺省用全局单例；测试可注入替身

    @returns (工具列表, 需要由调用方关闭的 client wrapper 列表)
    """
    store = pending_store or default_pending_store
    usable = [t for t in targets if t.url.strip()]
    wrappers = [A2AClientWrapper(t) for t in usable]
    single = len(usable) == 1

    tools: list[StructuredTool] = []
    used: set[str] = set()
    for target, wrapper in zip(usable, wrappers):
        label = target.name or target.url
        if single:
            name = "a2a_call"
        else:
            candidate = f"a2a_call__{_safe_identifier(target.name or target.url, 'target')}"
            # 中文名规整后可能退化成同一个名字，用序号保证工具名唯一
            name, suffix = candidate, 1
            while name in used:
                suffix += 1
                name = f"{candidate}_{suffix}"
        used.add(name)
        description = f"调用远端 A2A 目标「{label}」处理用户请求，返回其回复内容。"
        if target.description:
            description += f"\n该目标的能力与适用场景：{target.description}"
        tools.append(
            _build_a2a_tool(
                name,
                description,
                wrapper,
                agent_id=agent_id,
                pending_store=store,
            )
        )

    if usable:
        tools.append(
            _build_a2a_resume_tool(wrappers, agent_id=agent_id, pending_store=store)
        )
    return tools, wrappers


# ---------------------------------------------------------------------------
# MCP：把服务上的每个工具绑定成 Agent 可直接调用的工具
# ---------------------------------------------------------------------------
_JSON_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def json_schema_to_model(tool_name: str, schema: dict[str, Any] | None) -> type[BaseModel]:
    """把 MCP 工具的 inputSchema 转成 pydantic 模型，供 StructuredTool 使用。

    无法解析（无 properties / 非 object）时退化为单个 arguments 字段，
    保证该工具仍可被正常调用（入参由调用方按 JSON 对象传入）。
    """
    schema = schema if isinstance(schema, dict) else {}
    props = schema.get("properties")
    props = props if isinstance(props, dict) else {}
    required_raw = schema.get("required") or []
    required = {str(item) for item in required_raw} if isinstance(required_raw, list) else set()

    fields: dict[str, Any] = {}
    for key, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        py_type = _JSON_TYPE_MAP.get(str(spec.get("type")), Any)
        description = str(spec.get("description") or "")
        field_name = str(key)
        if field_name in required:
            fields[field_name] = (py_type, Field(description=description))
        else:
            fields[field_name] = (py_type | None, Field(default=None, description=description))

    if not fields:
        fields["arguments"] = (
            dict[str, Any] | None,
            Field(default=None, description="工具入参（JSON 对象）"),
        )

    model_name = re.sub(r"[^A-Za-z0-9_]+", "_", tool_name).strip("_") or "McpToolArgs"
    if not model_name[0].isalpha():
        model_name = f"Mcp_{model_name}"
    return create_model(model_name[:80], **fields)


def _unwrap_arguments(kwargs: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    """整理实际入参：退化模式下取 arguments 本身，其余情况丢弃未填写的 None。"""
    if set(model.model_fields) == {"arguments"}:
        raw = kwargs.get("arguments")
        return raw if isinstance(raw, dict) else {}
    return {key: value for key, value in kwargs.items() if value is not None}


def _build_mcp_tool(
    full_name: str,
    description: str,
    args_model: type[BaseModel],
    server: dict[str, Any],
    tool_name: str,
) -> StructuredTool:
    """为单个 MCP 工具构造 StructuredTool。

    必须经由工厂函数固化 ``tool_name`` / ``args_model``：若在循环内直接定义
    闭包，Python 的晚绑定会让所有工具都指向最后一个 ``tool_name``，
    导致调用 A 工具时实际执行了 B 工具。
    """

    async def _acall(**kwargs: Any) -> str:
        arguments = _unwrap_arguments(kwargs, args_model)
        return await mcp_invoke(connection_from_snapshot(server), tool_name, arguments)

    def _call(**kwargs: Any) -> str:
        raise RuntimeError("MCP 工具仅支持异步调用")

    return StructuredTool(
        name=full_name,
        description=description,
        coroutine=_acall,
        func=_call,
        args_schema=args_model,
    )


def make_mcp_tools(
    server: dict[str, Any], tools: list[dict[str, Any]]
) -> list[StructuredTool]:
    """把一个 MCP 服务上的每个工具都绑定成 Agent 可直接调用的工具。

    工具名格式：`mcp_<服务名>__<工具名>`，避免与 A2A 工具及其它服务冲突。
    """
    server_name = str(server.get("name") or "MCP")
    bound: list[StructuredTool] = []
    used: set[str] = set()

    for tool in tools:
        tool_name = str(tool.get("name") or "").strip()
        if not tool_name:
            continue

        candidate = (
            f"mcp_{_safe_identifier(server_name, 'server')}__{_safe_identifier(tool_name, 'tool')}"
        )[:64]
        # 名称规整后可能重名，用序号保证唯一
        full_name, suffix = candidate, 1
        while full_name in used:
            suffix += 1
            full_name = f"{candidate[:60]}_{suffix}"
        used.add(full_name)
        base_desc = str(tool.get("description") or "").strip() or f"MCP 工具 {tool_name}"
        description = f"{base_desc}（来自 MCP 服务 {server_name}）"
        args_model = json_schema_to_model(full_name, tool.get("inputSchema"))
        bound.append(_build_mcp_tool(full_name, description, args_model, server, tool_name))
    return bound


class McpCallArgs(BaseModel):
    server: str = Field(description="MCP 服务名称，必须取自下方「可用 MCP 服务」清单")
    tool: str = Field(description="要调用的 MCP 工具名称")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="工具入参，JSON 对象；无入参时传 {}"
    )


def make_mcp_call_tool(
    servers: list[dict[str, Any]], tool_index: dict[str, list[dict[str, Any]]] | None = None
) -> StructuredTool:
    """兜底的通用 MCP 调用工具（仅在无法探测到工具清单时使用）。"""
    tool_index = tool_index or {}
    known = [str(s.get("name") or "") for s in servers if s.get("name")]

    def _description() -> str:
        if not known:
            return "调用 MCP 服务上的工具（当前 Agent 未启用任何 MCP 服务）。"
        head = "调用已启用的 MCP 服务上的工具。server 必须取自下列清单："
        body = [
            format_tools_for_prompt(str(s.get("name") or ""), tool_index.get(str(s.get("name") or ""), []))
            for s in servers
        ]
        tail = (
            "用法：server 指定服务，tool 指定工具名，arguments 为工具入参（JSON 对象）。"
            "若不确定入参结构，可根据工具描述尝试调用，失败后会返回错误信息。"
        )
        return "\n".join([head, *body, tail])

    async def _acall(server: str, tool: str, arguments: dict[str, Any] | None = None) -> str:
        match = next((s for s in servers if str(s.get("name") or "") == server), None)
        if match is None:
            return f"未找到 MCP 服务「{server}」。当前可用：{', '.join(known) or '（无）'}"
        return await mcp_invoke(connection_from_snapshot(match), tool, arguments)

    def _call(server: str, tool: str, arguments: dict[str, Any] | None = None) -> str:
        raise RuntimeError("mcp_call 仅支持异步调用")

    return StructuredTool.from_function(
        coroutine=_acall,
        func=_call,
        name="mcp_call",
        description=_description(),
        args_schema=McpCallArgs,
    )


# 注：原先的「可选工具集」（web_search 等）已移除，其功能由 MCP 服务替代。
# Agent 的能力扩展统一通过「MCP 管理」勾选服务后自动绑定工具完成。
