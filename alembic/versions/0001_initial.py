"""baseline: agent_configs / admin_users

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-11

说明：本迁移与 `Base.metadata.create_all` 建出的结构完全一致，作为版本管理的基线。
历史库（已由 create_all 建表、且无 alembic_version）无需执行本迁移，
只需 `alembic stamp 0001_initial` 接管（`run_migrations()` 会自动完成这一步）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# 与 models.py 保持一致：PG 枚举按「成员值」创建（draft/published），
# 而非 SQLAlchemy Enum 默认的「成员名」（DRAFT/PUBLISHED）。
AGENT_STATUS = postgresql.ENUM("draft", "published", name="agentstatus")


def upgrade() -> None:
    op.create_table(
        "agent_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("a2a_targets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("enabled_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", AGENT_STATUS, server_default="draft", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_configs_slug", "agent_configs", ["slug"], unique=True)

    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_users_username", "admin_users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_admin_users_username", table_name="admin_users")
    op.drop_table("admin_users")
    op.drop_index("ix_agent_configs_slug", table_name="agent_configs")
    op.drop_table("agent_configs")
    AGENT_STATUS.drop(op.get_bind(), checkfirst=True)
