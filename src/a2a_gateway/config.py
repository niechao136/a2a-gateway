"""应用配置：从环境变量 / .env 加载。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 数据库
    database_url: str = Field(
        default="postgresql+asyncpg://a2a:a2a_secret@localhost:5432/a2a_gateway",
        alias="DATABASE_URL",
    )
    checkpoint_db_url: str = Field(
        default="postgresql://a2a:a2a_secret@localhost:5432/a2a_gateway",
        alias="CHECKPOINT_DB_URL",
    )

    # 默认 Agent 绑定的 Hermes A2A 目标
    hermes_a2a_url: str = Field(default="http://localhost:8080/", alias="HERMES_A2A_URL")
    hermes_a2a_token: str = Field(default="", alias="HERMES_A2A_TOKEN")

    # LangGraph Agent 的 LLM（OpenAI 兼容端点）
    llm_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")

    # 管理员认证 (JWT)
    jwt_secret: str = Field(default="dev-only-change-this", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=1440, alias="JWT_EXPIRE_MINUTES")
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="change-me", alias="ADMIN_PASSWORD")

    # 应用
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    frontend_origin: str = Field(default="http://localhost:3000", alias="FRONTEND_ORIGIN")

    @property
    def sync_db_url(self) -> str:
        """psycopg 同步连接串（建表 / Checkpointer 用）。"""
        return self.checkpoint_db_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
