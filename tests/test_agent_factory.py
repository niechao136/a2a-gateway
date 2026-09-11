"""Agent 工厂测试：图实例缓存复用、按 updated_at 自动失效、显式失效。"""

from datetime import datetime, timedelta, timezone

from a2a_gateway import agent_factory


async def test_graph_cache_reuse_and_invalidation(monkeypatch, make_agent):
    agent_factory._cache.clear()
    calls = {"n": 0}

    async def fake_checkpointer():
        return object()

    def fake_build_graph(agent, wrapper, checkpointer=None, **kwargs):
        calls["n"] += 1
        return f"graph-{calls['n']}"

    monkeypatch.setattr(agent_factory, "get_checkpointer", fake_checkpointer)
    monkeypatch.setattr(agent_factory, "build_graph", fake_build_graph)

    now = datetime.now(timezone.utc)
    first = make_agent(updated_at=now)

    graph1 = await agent_factory.get_agent_instance(first)
    graph2 = await agent_factory.get_agent_instance(first)
    assert graph1 is graph2
    assert calls["n"] == 1

    # updated_at 变化（配置被修改）→ 缓存自动失效并重建
    updated = make_agent(updated_at=now + timedelta(seconds=1))
    graph3 = await agent_factory.get_agent_instance(updated)
    assert graph3 != graph1
    assert calls["n"] == 2

    # 显式失效
    await agent_factory.invalidate_agent(updated.id)
    graph4 = await agent_factory.get_agent_instance(updated)
    assert graph4 != graph3
    assert calls["n"] == 3

    await agent_factory.close_all()
    assert agent_factory._cache == {}
