"""数据库引擎与会话管理。

配置库使用 SQLAlchemy async（asyncpg），LangGraph Checkpointer 复用同一 PostgreSQL。
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

_settings = get_settings()

# 异步引擎：用于 ORM 会话（Agent 配置 CRUD）
async_engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：提供一个异步数据库会话。"""
    async with AsyncSessionLocal() as session:
        yield session
