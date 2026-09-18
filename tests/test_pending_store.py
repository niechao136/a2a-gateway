"""挂起任务存储测试：TTL 判定、记录转换与委托行为（不连数据库）。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Self, cast

from sqlalchemy.ext.asyncio import async_sessionmaker

from a2a_gateway import pending_store as ps_mod
from a2a_gateway.pending_store import DbPendingStore, PendingRecord, _is_expired


class _FakeSession:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _factory():
    return _FakeSession()


def _store(ttl_seconds: int = 3600) -> DbPendingStore:
    """用替身会话工厂构造存储（不连数据库）。

    存储层只要求 session_factory 可调用且返回值支持 `async with`，这里把替身函数
    标注成 `async_sessionmaker`，与生产端类型保持一致。
    """
    return DbPendingStore(
        session_factory=cast(async_sessionmaker, _factory), ttl_seconds=ttl_seconds
    )


def _row(**overrides):
    base = {
        "thread_id": "th-1",
        "agent_id": 7,
        "target_url": "http://h:9900/",
        "target_name": "travel",
        "task_id": "t-1",
        "context_id": "c-1",
        "question": "请补充目的地",
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_is_expired_boundaries():
    now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    assert _is_expired(now - timedelta(seconds=30), 60, now=now) is False
    assert _is_expired(now - timedelta(seconds=61), 60, now=now) is True
    # 数据库返回 naive 时间时按 UTC 处理，不抛异常
    naive = datetime(2026, 9, 18, 10, 0)
    assert _is_expired(naive, 3600, now=now) is True


async def test_store_get_returns_record_for_fresh_row(monkeypatch):
    async def fake_get(session, thread_id):
        return _row()

    monkeypatch.setattr(ps_mod, "get_pending_a2a_task", fake_get)
    store = _store()

    record = await store.get("th-1")

    assert record == PendingRecord(
        thread_id="th-1",
        agent_id=7,
        target_url="http://h:9900/",
        target_name="travel",
        task_id="t-1",
        context_id="c-1",
        question="请补充目的地",
    )


async def test_store_get_deletes_expired_row(monkeypatch):
    calls: dict = {}

    async def fake_get(session, thread_id):
        return _row(updated_at=datetime.now(timezone.utc) - timedelta(hours=25))

    async def fake_delete(session, thread_id):
        calls["deleted"] = thread_id
        return True

    monkeypatch.setattr(ps_mod, "get_pending_a2a_task", fake_get)
    monkeypatch.setattr(ps_mod, "delete_pending_a2a_task", fake_delete)
    store = _store(ttl_seconds=86400)

    assert await store.get("th-1") is None
    assert calls["deleted"] == "th-1"


async def test_store_get_returns_none_for_missing_row(monkeypatch):
    async def fake_get(session, thread_id):
        return None

    monkeypatch.setattr(ps_mod, "get_pending_a2a_task", fake_get)
    store = _store()

    assert await store.get("nope") is None


async def test_store_upsert_and_delete_delegate(monkeypatch):
    captured: dict = {}

    async def fake_upsert(session, **kwargs):
        captured.update(kwargs)

    async def fake_delete(session, thread_id):
        captured["deleted"] = thread_id
        return True

    monkeypatch.setattr(ps_mod, "upsert_pending_a2a_task", fake_upsert)
    monkeypatch.setattr(ps_mod, "delete_pending_a2a_task", fake_delete)
    store = _store()

    await store.upsert(
        thread_id="th-1",
        agent_id=7,
        target_url="http://h:9900/",
        target_name="travel",
        task_id="t-1",
        context_id="c-1",
        question="请补充",
    )
    await store.delete("th-1")

    assert captured["thread_id"] == "th-1"
    assert captured["agent_id"] == 7
    assert captured["question"] == "请补充"
    assert captured["deleted"] == "th-1"
