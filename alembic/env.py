"""Alembic 运行环境：复用应用配置与 ORM metadata。

- 连接串来自 `settings.migration_db_url`（组件式 POSTGRES_* 自动拼接，或 DATABASE_URL 覆盖）
- `target_metadata` 指向应用 ORM，支持 `alembic revision --autogenerate`
- 由应用启动时调用不会重配日志（通过 attributes["skip_logging_config"] 标记）
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让 `src/` 可被导入（本地开发）；容器内包已安装，下面两行是幂等的
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from a2a_gateway.config import get_settings  # noqa: E402
from a2a_gateway.models import Base  # noqa: E402

config = context.config

# 仅在命令行调用时配置日志，避免影响应用（uvicorn / a2a_gateway）已配置的日志
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

_settings = get_settings()
config.set_main_option("sqlalchemy.url", _settings.migration_db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL（`alembic upgrade head --sql`），不连接数据库。"""
    context.configure(
        url=_settings.migration_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
