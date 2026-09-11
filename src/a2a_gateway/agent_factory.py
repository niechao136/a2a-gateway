"""Agent 实例工厂：根据 Agent 配置动态生成 LangGraph 图实例，含缓存与失效机制。

缓存键：agent.id + updated_at（配置变更后 updated_at 改变，旧缓存自动失效）。
"""

import asyncio
import logging
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from .a2a_client import A2AClientWrapper
from .config import get_settings
from .graph import build_graph
from .mcp_client import connection_from_snapshot, list_tools
from .models import AgentConfig
from .schemas import A2ATarget

logger = logging.getLogger(__name__)
_settings = get_settings()

# 单例 checkpointer 与其上下文管理器
_checkpointer: AsyncPostgresSaver | None = None
_checkpointer_cm = None  # _AsyncGeneratorContextManager

# agent 图实例缓存：{(agent_id, updated_at_iso): (wrapper, graph)}
_cache: dict[tuple[int, str], tuple[A2AClientWrapper, Any]] = {}


async def get_checkpointer() -> AsyncPostgresSaver:
    """获取（惰性初始化）共享的异步 Postgres Checkpointer。"""
    global _checkpointer, _checkpointer_cm
    if _checkpointer is not None:
        return _checkpointer
    _checkpointer_cm = AsyncPostgresSaver.from_conn_string(_settings.sync_db_url)
    _checkpointer = await _checkpointer_cm.__aenter__()
    await _checkpointer.setup()
    logger.info("LangGraph Postgres Checkpointer 已初始化")
    return _checkpointer


async def _probe_mcp_tools(snapshots: list[dict]) -> dict[str, list[dict]]:
    """并发探测各 MCP 服务的工具清单，用于把可用工具写进 mcp_call 的说明。

    尽力而为：任一服务探测失败只会导致该项没有工具清单，不影响图实例构建。
    内置超时由 mcp_client.list_tools 保证（并发执行，总耗时约等于单个超时）。
    """
    if not snapshots:
        return {}

    async def probe(snapshot: dict) -> tuple[str, bool, list[dict]]:
        name = snapshot.get("name") or ""
        try:
            ok, tools, _ = await list_tools(connection_from_snapshot(snapshot))
            return name, ok, tools
        except Exception:  # 兜底：探测绝不能影响主流程
            return name, False, []

    results = await asyncio.gather(*(probe(s) for s in snapshots))
    return {name: tools for name, ok, tools in results if ok and name}


async def get_agent_instance(agent: AgentConfig) -> Any:
    """根据 Agent 配置获取图实例（命中缓存则复用）。"""
    key = (agent.id, agent.updated_at.isoformat() if agent.updated_at else "")
    if key in _cache:
        return _cache[key][1]
    targets = agent.a2a_targets or []
    target_data = targets[0] if targets else {"url": "", "token": ""}
    wrapper = A2AClientWrapper(A2ATarget(**target_data))
    # MCP 服务快照（由 repository 解析 mcp_server_ids 得到），无 DB 依赖
    mcp_servers = list(getattr(agent, "mcp_servers", None) or [])
    mcp_tool_index = await _probe_mcp_tools(mcp_servers)
    checkpointer = await get_checkpointer()
    graph = build_graph(
        agent,
        wrapper,
        checkpointer=checkpointer,
        mcp_servers=mcp_servers,
        mcp_tool_index=mcp_tool_index,
    )
    _cache[key] = (wrapper, graph)
    # 清理同 id 但旧 updated_at 的缓存条目
    stale = [k for k in _cache if k[0] == agent.id and k != key]
    for k in stale:
        old_wrapper, _ = _cache.pop(k)
        await old_wrapper.close()
        logger.info("Agent %s 配置变更，已失效旧缓存", agent.slug)
    return graph


async def invalidate_agent(agent_id: int) -> None:
    """显式失效某个 Agent 的缓存（管理中心修改配置后调用）。"""
    stale = [k for k in _cache if k[0] == agent_id]
    for k in stale:
        wrapper, _ = _cache.pop(k)
        await wrapper.close()


async def close_all() -> None:
    """关闭所有缓存的 wrapper 与 checkpointer（应用关停时调用）。"""
    for wrapper, _ in list(_cache.values()):
        await wrapper.close()
    _cache.clear()
    global _checkpointer, _checkpointer_cm
    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
        _checkpointer = None
        _checkpointer_cm = None
