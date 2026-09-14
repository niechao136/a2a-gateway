"""create conversations: 对话会话目录（thread_id → 身份归属）

Revision ID: 0008_conversations
Revises: 0007_api_keys_agent_name_uq
Create Date: 2026-09-14

说明：
- 对话消息本体仍在 LangGraph Checkpointer 的 checkpoints 系列表里按 thread_id 存储；
  本表只维护「谁有哪些会话」这一层目录，因此匿名 → 登录的归并只需改 owner_* 字段。
- owner_kind：visitor（匿名 uuid）/ user（admin_users.username）。
- 历史库中的 thread 不在本表里（未登记），读写一律放行，由回填脚本按需补登记。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_conversations"
down_revision: str | None = "0007_api_keys_agent_name_uq"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("agent_slug", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("owner_kind", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", name="uq_conversations_thread"),
    )
    op.create_index(op.f("ix_conversations_thread_id"), "conversations", ["thread_id"], unique=True)
    op.create_index(op.f("ix_conversations_agent_slug"), "conversations", ["agent_slug"])
    op.create_index(op.f("ix_conversations_owner_kind"), "conversations", ["owner_kind"])
    op.create_index(op.f("ix_conversations_owner_id"), "conversations", ["owner_id"])
    op.create_index(
        "ix_conversations_owner", "conversations", ["owner_kind", "owner_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_owner", table_name="conversations")
    op.drop_index(op.f("ix_conversations_owner_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_owner_kind"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_agent_slug"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_thread_id"), table_name="conversations")
    op.drop_table("conversations")
