"""auth: A2A / MCP 统一鉴权方式

Revision ID: 0003_auth_and_description
Revises: 0002_registries
Create Date: 2026-09-11

背景：
- A2A 目标原先只支持 Bearer Token 一种鉴权
- MCP 服务原先完全没有鉴权字段

改造：两张注册表都增加 auth_type / auth_name，密钥统一放在 token 字段：
- none   ：无鉴权
- bearer ：Authorization: Bearer <token>
- header ：<auth_name>: <token>
- query  ：?<auth_name>=<token>
- basic  ：Authorization: Basic base64(<auth_name>:<token>)

回填：已有 A2A 目标按 token 是否为空设为 bearer / none，保证行为不变。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_auth_and_description"
down_revision: str | None = "0002_registries"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "a2a_endpoints",
        sa.Column("auth_type", sa.String(length=32), server_default="bearer", nullable=False),
    )
    op.add_column(
        "a2a_endpoints",
        sa.Column("auth_name", sa.String(length=128), server_default="", nullable=False),
    )

    op.add_column(
        "mcp_servers",
        sa.Column("token", sa.String(length=512), server_default="", nullable=False),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("auth_type", sa.String(length=32), server_default="bearer", nullable=False),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("auth_name", sa.String(length=128), server_default="", nullable=False),
    )

    # 历史数据回填：有 token 即视为 bearer，无 token 视为无鉴权（保持原有行为）
    op.execute(
        """
        UPDATE a2a_endpoints
           SET auth_type = CASE WHEN COALESCE(token, '') = '' THEN 'none' ELSE 'bearer' END
        """
    )
    op.execute("UPDATE mcp_servers SET auth_type = 'none'")


def downgrade() -> None:
    op.drop_column("mcp_servers", "auth_name")
    op.drop_column("mcp_servers", "auth_type")
    op.drop_column("mcp_servers", "token")
    op.drop_column("a2a_endpoints", "auth_name")
    op.drop_column("a2a_endpoints", "auth_type")
