"""llm models: 模型注册表 + agent 模型绑定

Revision ID: 0013_llm_models
Revises: 0012_chat_connectors
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_llm_models"
down_revision: str | None = "0012_chat_connectors"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # 1) 幂等创建枚举类型（与 ORM 的 name="llmprovider" 一致）
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n "
            "ON n.oid = t.typnamespace "
            "WHERE t.typname = 'llmprovider' AND n.nspname = 'public') "
            "THEN CREATE TYPE llmprovider AS ENUM ('openai', 'anthropic'); "
            "END IF; END $$;"
        )
    )
    # 2) 模型注册表
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS llm_models (
                id SERIAL PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                provider llmprovider NOT NULL DEFAULT 'openai',
                base_url VARCHAR(512) NOT NULL DEFAULT '',
                api_key VARCHAR(512) NOT NULL DEFAULT '',
                model VARCHAR(128) NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                CONSTRAINT uq_llm_models_name UNIQUE (name)
            );
            """
        )
    )
    op.execute(
        sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_llm_models_name ON llm_models (name);")
    )
    # 3) Agent 侧绑定列（单选；NULL = 回落全局环境变量）
    op.execute(
        sa.text(
            "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS model_id INTEGER;"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS model_snapshot JSONB;"
        )
    )
    # 4) FK（幂等：仅当约束不存在时创建）；不设级联——删除走应用层解绑流程
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_agent_configs_model_id') "
            "THEN ALTER TABLE agent_configs ADD CONSTRAINT fk_agent_configs_model_id "
            "FOREIGN KEY (model_id) REFERENCES llm_models(id); "
            "END IF; END $$;"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE agent_configs DROP CONSTRAINT IF EXISTS fk_agent_configs_model_id;")
    )
    op.execute(sa.text("ALTER TABLE agent_configs DROP COLUMN IF EXISTS model_snapshot;"))
    op.execute(sa.text("ALTER TABLE agent_configs DROP COLUMN IF EXISTS model_id;"))
    op.execute(sa.text("DROP TABLE IF EXISTS llm_models;"))
    sa.Enum(name="llmprovider").drop(op.get_bind(), checkfirst=True)
