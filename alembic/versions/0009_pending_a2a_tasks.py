"""add pending_a2a_tasks: 挂起任务（input-required 中断 → 恢复映射）

Revision ID: 0009_pending_a2a_tasks
Revises: 0008_conversations
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_pending_a2a_tasks"
down_revision: str | None = "0008_conversations"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_a2a_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("target_url", sa.String(length=512), nullable=False),
        sa.Column("target_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("context_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", name="uq_pending_a2a_tasks_thread"),
    )
    op.create_index(
        op.f("ix_pending_a2a_tasks_thread_id"), "pending_a2a_tasks", ["thread_id"], unique=True
    )
    op.create_index(op.f("ix_pending_a2a_tasks_agent_id"), "pending_a2a_tasks", ["agent_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_pending_a2a_tasks_agent_id"), table_name="pending_a2a_tasks")
    op.drop_index(op.f("ix_pending_a2a_tasks_thread_id"), table_name="pending_a2a_tasks")
    op.drop_table("pending_a2a_tasks")
