"""api_keys.name 改为 Agent 内唯一（不同 Agent 可有同名 Key）

Revision ID: 0007_api_keys_agent_name_uq
Revises: 0006_api_keys_per_agent
Create Date: 2026-09-12

说明：
- 0005 建表时 name 是全局唯一（当时 Key 为全局资源）；
  Key 归属到 Agent 后，每个 Agent 都会有自己的「默认 Key」，
  全局唯一约束会导致启动补齐默认 Key 时冲突。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_api_keys_agent_name_uq"
down_revision: str | None = "0006_api_keys_per_agent"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_api_keys_name", "api_keys", type_="unique")
    op.drop_index(op.f("ix_api_keys_name"), table_name="api_keys")
    op.create_unique_constraint(
        "uq_api_keys_agent_name", "api_keys", ["agent_id", "name"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_api_keys_agent_name", "api_keys", type_="unique")
    op.create_index(op.f("ix_api_keys_name"), "api_keys", ["name"])
    op.create_unique_constraint("uq_api_keys_name", "api_keys", ["name"])
