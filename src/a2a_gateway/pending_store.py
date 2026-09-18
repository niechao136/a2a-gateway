"""挂起任务存储：网关会话 / 对外 task_id → 下游任务（task_id）的映射。

链路 A（对话界面）以会话 thread_id 为键；链路 B（A2A Server）以对外 task_id
为键（创建新任务时两者同值，见 routes/a2a_server.py）。

存储走 PostgreSQL（pending_a2a_tasks 表）；``PendingStore`` 协议允许测试注入替身。
"""

import logging
from datetime import datetime, timezone
from typing import NamedTuple, Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from .config import get_settings
from .database import AsyncSessionLocal
from .repository import (
    delete_pending_a2a_task,
    get_pending_a2a_task,
    upsert_pending_a2a_task,
)

logger = logging.getLogger(__name__)


class PendingRecord(NamedTuple):
    """脱离 ORM 会话的只读快照。"""

    thread_id: str
    agent_id: int
    target_url: str
    target_name: str
    task_id: str
    context_id: str
    question: str


def _is_expired(
    updated_at: datetime, ttl_seconds: int, *, now: datetime | None = None
) -> bool:
    """挂起是否已超过 TTL；naive 时间按 UTC 处理。"""
    current = now or datetime.now(timezone.utc)
    stamp = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    return (current - stamp).total_seconds() > ttl_seconds


class PendingStore(Protocol):
    """挂起存储协议（生产走 DB，测试注入替身）。"""

    async def upsert(
        self,
        *,
        thread_id: str,
        agent_id: int,
        target_url: str,
        target_name: str,
        task_id: str,
        context_id: str,
        question: str,
    ) -> None: ...

    async def get(self, thread_id: str) -> PendingRecord | None: ...

    async def delete(self, thread_id: str) -> None: ...


class DbPendingStore:
    """PostgreSQL 实现（按需开启短会话）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker = AsyncSessionLocal,
        ttl_seconds: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._ttl_seconds = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        if self._ttl_seconds is not None:
            return self._ttl_seconds
        return get_settings().pending_a2a_ttl_seconds

    async def upsert(
        self,
        *,
        thread_id: str,
        agent_id: int,
        target_url: str,
        target_name: str,
        task_id: str,
        context_id: str,
        question: str,
    ) -> None:
        async with self._session_factory() as session:
            await upsert_pending_a2a_task(
                session,
                thread_id=thread_id,
                agent_id=agent_id,
                target_url=target_url,
                target_name=target_name,
                task_id=task_id,
                context_id=context_id,
                question=question,
            )

    async def get(self, thread_id: str) -> PendingRecord | None:
        async with self._session_factory() as session:
            row = await get_pending_a2a_task(session, thread_id)
            if row is None:
                return None
            if _is_expired(row.updated_at, self.ttl_seconds):
                await delete_pending_a2a_task(session, thread_id)
                logger.info("挂起任务已超时清理 thread=%s", thread_id)
                return None
            return PendingRecord(
                thread_id=row.thread_id,
                agent_id=row.agent_id,
                target_url=row.target_url,
                target_name=row.target_name,
                task_id=row.task_id,
                context_id=row.context_id,
                question=row.question,
            )

    async def delete(self, thread_id: str) -> None:
        async with self._session_factory() as session:
            await delete_pending_a2a_task(session, thread_id)


# 路由层与工具层共用（测试 monkeypatch 本模块变量或注入替身）
default_pending_store: PendingStore = DbPendingStore()
