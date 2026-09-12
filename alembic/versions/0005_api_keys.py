"""create api_keys: 对外 A2A 服务的 API Key

Revision ID: 0005_api_keys
Revises: 0004_drop_enabled_tools
Create Date: 2026-09-12

说明：
- 管理中心配置的 Agent 现在以 A2A 协议对外发布（/a2a/{slug}），
  调用方需凭 API Key 访问；默认 Key 由应用启动时自动生成。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_api_keys"
down_revision: str | None = "0004_drop_enabled_tools"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_api_keys_name"),
        sa.UniqueConstraint("key", name="uq_api_keys_key"),
    )
    op.create_index(op.f("ix_api_keys_name"), "api_keys", ["name"])
    op.create_index(op.f("ix_api_keys_key"), "api_keys", ["key"])


def downgrade() -> None:
    op.drop_index(op.f("ix_api_keys_key"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_name"), table_name="api_keys")
    op.drop_table("api_keys")
