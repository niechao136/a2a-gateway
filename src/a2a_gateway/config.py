"""应用配置：环境变量 + 可选的环境变量文件（python-dotenv）。

加载策略（真实环境变量优先，不会被文件覆盖）：
1. 进程环境变量：shell export / docker run -e / docker compose environment / K8s env 等
2. 环境变量文件（可选，通过 load_dotenv 载入）：
   - 默认自动查找 .env（从本文件所在目录逐级向上，找到才加载，找不到静默跳过）
   - 可用 DOTENV_PATH 指定文件；多个文件用 os.pathsep 分隔（Windows ';' / POSIX ':'）

数据库配置有两种方式（完整连接串优先级更高）：
1. 组件式：POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB / POSTGRES_HOST / POSTGRES_PORT，
   应用会自动拼接出 DATABASE_URL（asyncpg）与 CHECKPOINT_DB_URL（psycopg）。
2. 完整连接串：DATABASE_URL / CHECKPOINT_DB_URL（如连接外部已有数据库）。
"""

import os
from functools import lru_cache
from typing import ClassVar
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_env_files() -> None:
    """加载环境变量文件（可选）。

    - 未设置 DOTENV_PATH 时：自动查找 .env（向上逐级查找，找不到不报错）
    - 设置 DOTENV_PATH 时：按 os.pathsep 分隔加载一个或多个文件
    - override=False：已存在的真实环境变量优先，不会被文件覆盖
    """
    dotenv_path = os.getenv("DOTENV_PATH")
    if not dotenv_path:
        _ = load_dotenv(override=False)
        return
    for path in dotenv_path.split(os.pathsep):
        path = path.strip()
        if path:
            _ = load_dotenv(path, override=False)


# 模块导入时即完成环境变量装载（幂等，重复调用安全）
_load_env_files()


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        # 文件已由 load_dotenv 载入到进程环境变量，这里统一从环境变量读取
        extra="ignore",
    )

    # 数据库（组件式配置）
    postgres_user: str = Field(default="a2a", alias="POSTGRES_USER")
    postgres_password: str = Field(default="a2a_secret", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="a2a_gateway", alias="POSTGRES_DB")
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")

    # 完整连接串（可选；若设置则优先于上面的组件式配置）
    database_url: str = Field(default="", alias="DATABASE_URL")
    checkpoint_db_url: str = Field(default="", alias="CHECKPOINT_DB_URL")

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

    # 语音服务（onnx-hub）：ASR 语音识别 / TTS 语音合成
    # 网关代理请求并注入 API Key，前端无需接触真实 Key
    onnx_hub_base_url: str = Field(
        default="http://43.156.187.79:10100", alias="ONNX_HUB_BASE_URL"
    )
    onnx_hub_api_key: str = Field(default="", alias="ONNX_HUB_API_KEY")
    onnx_hub_asr_model: str = Field(
        default="zipformer-streaming-bilingual-zh-en", alias="ONNX_HUB_ASR_MODEL"
    )
    onnx_hub_tts_model: str = Field(default="vits-zh-aishell3", alias="ONNX_HUB_TTS_MODEL")

    # 告警（可选）：配置后把 A2A 调用失败 / Agent 加载失败等推送到 Webhook
    alert_webhook_url: str = Field(default="", alias="ALERT_WEBHOOK_URL")
    alert_webhook_token: str = Field(default="", alias="ALERT_WEBHOOK_TOKEN")

    @model_validator(mode="after")
    def _fill_db_urls(self) -> "Settings":
        """未显式提供完整连接串时，用组件式配置拼接。"""
        if not self.database_url:
            self.database_url = self._build_db_url("postgresql+asyncpg")
        if not self.checkpoint_db_url:
            self.checkpoint_db_url = self._build_db_url("postgresql")
        return self

    def _build_db_url(self, scheme: str) -> str:
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return (
            f"{scheme}://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_db_url(self) -> str:
        """psycopg 同步连接串（建表 / Checkpointer 用）。"""
        return self.checkpoint_db_url

    @property
    def migration_db_url(self) -> str:
        """Alembic 用的同步连接串（SQLAlchemy 的 psycopg v3 方言）。

        checkpoint_db_url 默认是裸 `postgresql://`（供 psycopg3 直连），
        而 SQLAlchemy 需要 `postgresql+psycopg://` 才会选用 psycopg v3 驱动。
        """
        url = self.checkpoint_db_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
