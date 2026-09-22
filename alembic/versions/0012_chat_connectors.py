"""chat connectors: 连接器注册表 + 会话映射表

Revision ID: 0012_chat_connectors
Revises: 0011_skill_scripts
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_chat_connectors"
down_revision: str | None = "0011_skill_scripts"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # 1) 幂等创建枚举类型（原生 DO 块，等效 IF NOT EXISTS）
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n "
            "ON n.oid = t.typnamespace "
            "WHERE t.typname = 'connectorplatform' AND n.nspname = 'public') "
            "THEN CREATE TYPE connectorplatform AS ENUM ('feishu', 'telegram', 'slack'); "
            "END IF; END $$;"
        )
    )
    # 2) 连接器注册表
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS chat_connectors (
                id SERIAL PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                platform connectorplatform NOT NULL,
                credentials JSONB NOT NULL DEFAULT '{}'::jsonb,
                agent_id INTEGER NOT NULL REFERENCES agent_configs(id) ON DELETE CASCADE,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                CONSTRAINT uq_chat_connectors_name UNIQUE (name)
            );
            """
        )
    )
    op.execute(
        sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_chat_connectors_name ON chat_connectors (name);")
    )
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_chat_connectors_agent_id ON chat_connectors (agent_id);")
    )
    # 3) 会话映射表
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS chat_connector_conversations (
                id SERIAL PRIMARY KEY,
                connector_id INTEGER NOT NULL REFERENCES chat_connectors(id) ON DELETE CASCADE,
                chat_id VARCHAR(128) NOT NULL,
                chat_type VARCHAR(16) NOT NULL DEFAULT 'private',
                thread_id VARCHAR(128) NOT NULL,
                last_user_ref JSONB NOT NULL DEFAULT '{}'::jsonb,
                last_active_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                CONSTRAINT uq_connector_conversations_chat UNIQUE (connector_id, chat_id)
            );
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_connector_conversations_thread_id "
            "ON chat_connector_conversations (thread_id);"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_connector_conversations_last_active "
            "ON chat_connector_conversations (last_active_at);"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_connector_conversations_connector_id "
            "ON chat_connector_conversations (connector_id);"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS chat_connector_conversations;"))
    op.execute(sa.text("DROP TABLE IF EXISTS chat_connectors;"))
    sa.Enum(name="connectorplatform").drop(op.get_bind(), checkfirst=True)
