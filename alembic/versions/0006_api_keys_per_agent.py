"""api_keys 归属到具体 Agent：每个 Agent 独立管理自己的 Key

Revision ID: 0006_api_keys_per_agent
Revises: 0005_api_keys
Create Date: 2026-09-12

说明：
- api_keys 新增 agent_id 外键；对外 A2A 调用时 Key 必须属于被调用的 Agent
- 存量 Key 统一回填到默认 Agent（slug='/'）名下
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_api_keys_per_agent"
down_revision: str | None = "0005_api_keys"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("agent_id", sa.Integer(), nullable=True))
    # 存量 Key 回填到默认 Agent
    op.execute(
        "UPDATE api_keys SET agent_id = (SELECT id FROM agent_configs WHERE slug = '/')"
    )
    op.alter_column("api_keys", "agent_id", nullable=False)
    op.create_index(
        op.f("ix_api_keys_agent_id"), "api_keys", ["agent_id"]
    )
    op.create_foreign_key(
        "fk_api_keys_agent_id",
        "api_keys",
        "agent_configs",
        ["agent_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_api_keys_agent_id", "api_keys", type_="foreignkey")
    op.drop_index(op.f("ix_api_keys_agent_id"), table_name="api_keys")
    op.drop_column("api_keys", "agent_id")
