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
    skill_status = sa.Enum(
        "pending", "approved", "rejected", name="skillreviewstatus", create_type=False
    )
    skill_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("frontmatter", JSONB(), nullable=False, server_default="{}"),
        sa.Column("files", JSONB(), nullable=False, server_default="[]"),
        sa.Column("load_mode", sa.String(length=16), nullable=False, server_default="on_demand"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("review_status", skill_status, nullable=False, server_default="pending"),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_skills_name"),
    )
    op.create_index(op.f("ix_skills_name"), "skills", ["name"], unique=True)
    op.add_column(
        "agent_configs", sa.Column("skill_ids", JSONB(), nullable=False, server_default="[]")
    )
    op.add_column(
        "agent_configs", sa.Column("skills", JSONB(), nullable=False, server_default="[]")
    )


def downgrade() -> None:
    op.drop_column("agent_configs", "skills")
    op.drop_column("agent_configs", "skill_ids")
    op.drop_index(op.f("ix_skills_name"), table_name="skills")
    op.drop_table("skills")
    sa.Enum(name="skillreviewstatus").drop(op.get_bind(), checkfirst=True)
