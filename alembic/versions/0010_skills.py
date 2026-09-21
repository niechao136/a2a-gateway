"""add skills: 编排方法论技能注册表 + agent_configs 绑定字段

Revision ID: 0010_skills
Revises: 0009_pending_a2a_tasks
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010_skills"
down_revision: str | None = "0009_pending_a2a_tasks"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # 1) 幂等创建枚举类型。
    #    说明：本环境 SQLAlchemy 2.0.54 下 op.create_table 会忽略枚举列的 create_type=False，
    #    重复建类型导致 DuplicateObject，故改用原生 DO 块（等效 IF NOT EXISTS 写法）。
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n "
            "ON n.oid = t.typnamespace "
            "WHERE t.typname = 'skillreviewstatus' AND n.nspname = 'public') "
            "THEN CREATE TYPE skillreviewstatus AS ENUM ('pending', 'approved', 'rejected'); "
            "END IF; END $$;"
        )
    )
    # 2) 用原生 SQL 建表（IF NOT EXISTS），绕开 SQLAlchemy 枚举列的自动建类型行为。
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS skills (
                id SERIAL PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                frontmatter JSONB NOT NULL DEFAULT '{}'::jsonb,
                files JSONB NOT NULL DEFAULT '[]'::jsonb,
                load_mode VARCHAR(16) NOT NULL DEFAULT 'on_demand',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                file_count INTEGER NOT NULL DEFAULT 0,
                source VARCHAR(16) NOT NULL DEFAULT 'manual',
                source_ref VARCHAR(512) NOT NULL DEFAULT '',
                review_status skillreviewstatus NOT NULL DEFAULT 'pending',
                review_note TEXT NOT NULL DEFAULT '',
                reviewed_at TIMESTAMP WITH TIME ZONE,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                CONSTRAINT uq_skills_name UNIQUE (name)
            );
            """
        )
    )
    op.execute(
        sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_skills_name ON skills (name);")
    )
    # 用 ALTER COLUMN IF NOT EXISTS 保证幂等（避免重复加列报错）
    op.execute(
        sa.text(
            "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS "
            "skill_ids JSONB NOT NULL DEFAULT '[]'::jsonb;"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS "
            "skills JSONB NOT NULL DEFAULT '[]'::jsonb;"
        )
    )


def downgrade() -> None:
    op.drop_column("agent_configs", "skills")
    op.drop_column("agent_configs", "skill_ids")
    op.drop_index(op.f("ix_skills_name"), table_name="skills")
    op.drop_table("skills")
    sa.Enum(name="skillreviewstatus").drop(op.get_bind(), checkfirst=True)
