"""工具层：将 A2A / MCP 调用封装为标准 LangChain 工具。

- `a2a_call`：每个勾选的 A2A 目标各生成一个工具，说明里带上该目标的描述
  （关键：否则大模型无法判断该调用哪个目标）
- MCP：勾选的服务上的**每个工具**都绑定成独立工具，模型可直接调用
  （探测失败时退化为通用 mcp_call，至少保留可用能力）
- 其它可选工具（网页搜索等）按 enabled_tools 勾选启用
"""

import re
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from .a2a_client import A2AClientWrapper, A2ATargetError
from .mcp_client import call_tool as mcp_invoke
from .mcp_client import connection_from_snapshot, format_tools_for_prompt
from .notifier import notify_alert
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


def _build_a2a_tool(
    name: str, description: str, wrapper: A2AClientWrapper
) -> StructuredTool:
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
        name=name,
        description=description,
        args_schema=A2ACallArgs,
    )


def make_a2a_tools(
    targets: list[A2ATarget],
) -> tuple[list[StructuredTool], list[A2AClientWrapper]]:
    """为每个 A2A 目标各构造一个调用工具。

    - 仅一个目标时沿用历史名称 `a2a_call`；多个目标时按目标名区分
    - 目标描述写进工具说明，让大模型判断该调用哪一个

    @returns (工具列表, 需要由调用方关闭的 client wrapper 列表)
    """
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
        tools.append(_build_a2a_tool(name, description, wrapper))
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

        async def _acall(**kwargs: Any) -> str:
            arguments = _unwrap_arguments(kwargs, args_model)
            return await mcp_invoke(connection_from_snapshot(server), tool_name, arguments)

        def _call(**kwargs: Any) -> str:
            raise RuntimeError("MCP 工具仅支持异步调用")

        bound.append(
            StructuredTool(
                name=full_name,
                description=description,
                coroutine=_acall,
                func=_call,
                args_schema=args_model,
            )
        )
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
