"""配置层单元测试：连接串拼装、覆盖优先级、迁移连接串。"""

from a2a_gateway.config import Settings


def _settings(**env) -> Settings:
    # _env_file=None：不读取本地 .env，保证测试可复现
    return Settings(_env_file=None, **env)


def test_default_db_urls():
    s = _settings()
    assert s.database_url == "postgresql+asyncpg://a2a:a2a_secret@localhost:5432/a2a_gateway"
    assert s.checkpoint_db_url == "postgresql://a2a:a2a_secret@localhost:5432/a2a_gateway"


def test_component_env_builds_urls():
    s = _settings(
        POSTGRES_USER="u",
        POSTGRES_PASSWORD="p",
        POSTGRES_DB="mydb",
        POSTGRES_HOST="postgres",
        POSTGRES_PORT=5555,
    )
    assert s.database_url == "postgresql+asyncpg://u:p@postgres:5555/mydb"
    assert s.checkpoint_db_url == "postgresql://u:p@postgres:5555/mydb"


def test_explicit_url_takes_precedence():
    s = _settings(
        POSTGRES_HOST="postgres",
        DATABASE_URL="postgresql+asyncpg://x:y@external:5432/db",
    )
    assert s.database_url == "postgresql+asyncpg://x:y@external:5432/db"
    # 未显式提供 checkpoint 时，仍按组件式配置拼接
    assert s.checkpoint_db_url == "postgresql://a2a:a2a_secret@postgres:5432/a2a_gateway"


def test_password_is_url_encoded():
    s = _settings(POSTGRES_PASSWORD="p@ss/w:rd")
    assert "p%40ss%2Fw%3Ard" in s.database_url


def test_migration_db_url_switches_to_psycopg_driver():
    s = _settings(POSTGRES_HOST="postgres")
    assert s.migration_db_url == "postgresql+psycopg://a2a:a2a_secret@postgres:5432/a2a_gateway"


def test_migration_db_url_keeps_explicit_driver():
    s = _settings(CHECKPOINT_DB_URL="postgresql+psycopg://u:p@h:5432/d")
    assert s.migration_db_url == "postgresql+psycopg://u:p@h:5432/d"


def test_alert_webhook_defaults_to_empty():
    s = _settings()
    assert s.alert_webhook_url == ""
    assert s.alert_webhook_token == ""
