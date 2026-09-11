"""数据库迁移：启动时执行 Alembic，并自动接管由 create_all 建出的历史库。

- 新库：直接 `alembic upgrade head`（执行基线迁移建表）
- 历史库（已有业务表但无 alembic_version）：先 `stamp 0001_initial` 接管，再 upgrade
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from .config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# 基线版本（见 alembic/versions/0001_initial.py）
BASELINE_REVISION = "0001_initial"
# 应用自有表：用于判断当前库是否为「create_all 建出的历史库」
APP_TABLES = {"agent_configs", "admin_users"}
VERSION_TABLE = "alembic_version"

# 项目根目录（本地为仓库根；容器内包安装在 site-packages，会用 cwd 兜底）
_BASE_DIR = Path(__file__).resolve().parents[2]


def _resolve_paths() -> tuple[Path, Path]:
    """定位 alembic.ini 与脚本目录，兼容本地开发与容器运行。"""
    for base in (_BASE_DIR, Path.cwd()):
        ini_path = base / "alembic.ini"
        if ini_path.exists():
            return ini_path, base / "alembic"
    return Path.cwd() / "alembic.ini", Path.cwd() / "alembic"


def _build_config() -> Config:
    ini_path, script_location = _resolve_paths()
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("sqlalchemy.url", _settings.migration_db_url)
    # 由应用调用时不要重配日志
    cfg.attributes["skip_logging_config"] = True
    return cfg


def _existing_tables() -> set[str]:
    engine = create_engine(_settings.migration_db_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def adopt_legacy_database() -> bool:
    """若为历史库（create_all 建表、无版本表），打基线标记。

    @returns 是否执行了接管
    """
    tables = _existing_tables()
    if VERSION_TABLE not in tables and tables & APP_TABLES:
        logger.info(
            "检测到历史库（无 %s），标记基线 %s 以纳入版本管理",
            VERSION_TABLE,
            BASELINE_REVISION,
        )
        command.stamp(_build_config(), BASELINE_REVISION)
        return True
    return False


def run_migrations() -> None:
    """执行数据库迁移（`alembic upgrade head`）。"""
    adopt_legacy_database()
    command.upgrade(_build_config(), "head")
    logger.info("数据库迁移完成（alembic upgrade head）")
