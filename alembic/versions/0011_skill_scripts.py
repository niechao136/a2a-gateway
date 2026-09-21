"""skills.allow_scripts: 是否允许沙箱执行捆绑脚本

Revision ID: 0011_skill_scripts
Revises: 0010_skills
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_skill_scripts"
down_revision: str | None = "0010_skills"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # 幂等加列（与 0010 的原生 SQL 风格一致，避免重复执行报错）
    op.execute(
        sa.text(
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS "
            "allow_scripts BOOLEAN NOT NULL DEFAULT FALSE;"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE skills DROP COLUMN IF EXISTS allow_scripts;"))
