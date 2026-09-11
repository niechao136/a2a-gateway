"""registries: a2a_endpoints / mcp_servers + agent 绑定列

Revision ID: 0002_registries
Revises: 0001_initial
Create Date: 2026-09-11

说明：
- 新增两张注册表：a2a_endpoints（A2A 目标）、mcp_servers（MCP 服务）
- agent_configs 增加「勾选关系」与 MCP 连接快照：
  - a2a_target_ids：在「A2A 管理」中勾选的目标 id
  - mcp_server_ids：在「MCP 管理」中勾选的服务 id
  - mcp_servers  ：由 mcp_server_ids 解析出的连接快照（运行时构造工具用）
已有行统一填 '[]'（应用启动时会为默认 Agent 回填关联，见 repository.ensure_default_agent）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_registries"
down_revision: str | None = "0001_initial"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
EMPTY_JSONB = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "a2a_endpoints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("token", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
    op.create_index("ix_a2a_endpoints_name", "a2a_endpoints", ["name"], unique=True)

    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("command", sa.String(length=512), nullable=False),
        sa.Column("args", JSONB, nullable=False),
        sa.Column("env", JSONB, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
    op.create_index("ix_mcp_servers_name", "mcp_servers", ["name"], unique=True)

    op.add_column(
        "agent_configs",
        sa.Column("a2a_target_ids", JSONB, server_default=EMPTY_JSONB, nullable=False),
    )
    op.add_column(
        "agent_configs",
        sa.Column("mcp_server_ids", JSONB, server_default=EMPTY_JSONB, nullable=False),
    )
    op.add_column(
        "agent_configs",
        sa.Column("mcp_servers", JSONB, server_default=EMPTY_JSONB, nullable=False),
    )


def downgrade() -> None:
    op.drop_column("agent_configs", "mcp_servers")
    op.drop_column("agent_configs", "mcp_server_ids")
    op.drop_column("agent_configs", "a2a_target_ids")
    op.drop_index("ix_mcp_servers_name", table_name="mcp_servers")
    op.drop_table("mcp_servers")
    op.drop_index("ix_a2a_endpoints_name", table_name="a2a_endpoints")
    op.drop_table("a2a_endpoints")
