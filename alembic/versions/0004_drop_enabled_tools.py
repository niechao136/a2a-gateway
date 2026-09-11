"""drop enabled_tools: 可选工具集由 MCP 替代

Revision ID: 0004_drop_enabled_tools
Revises: 0003_auth_and_description
Create Date: 2026-09-11

说明：
- Agent 上的「工具集」勾选（enabled_tools）及预置的 web_search 占位工具已移除，
  能力扩展统一通过「MCP 管理」勾选服务、由服务端把工具绑定到 Agent 完成。
- 上线前已确认全库 enabled_tools 均为空数组，删除无数据损失。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_drop_enabled_tools"
down_revision: str | None = "0003_auth_and_description"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("agent_configs", "enabled_tools")


def downgrade() -> None:
    op.add_column(
        "agent_configs",
        sa.Column(
            "enabled_tools",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
