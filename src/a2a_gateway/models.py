"""ORM 数据模型。

- AgentConfig：自定义/默认 Agent 的配置（路由、A2A 目标、system_prompt、工具集、状态）
- AdminUser：管理中心登录账号（JWT 认证）
"""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AgentStatus(str, PyEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class BaseMixin:
    """公共时间戳字段。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentConfig(Base, BaseMixin):
    __tablename__ = "agent_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 路由标识；"/" 为默认 Agent 保留
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    # 绑定的 A2A 目标列表：[{"url": "...", "token": "..."}]
    a2a_targets: Mapped[list] = mapped_column(JSONB, default=list)
    # 可选覆盖 system prompt
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 启用的工具名称集合（按待讨论问题 6：每 Agent 可勾选）
    enabled_tools: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(
            AgentStatus,
            name="agentstatus",
            # 关键：SQLAlchemy 默认用「成员名」(DRAFT/PUBLISHED) 建 PG 枚举，
            # 而业务代码与 server_default 用的是「成员值」(draft/published)，
            # 这里改为按成员值建，避免 invalid input value for enum agentstatus。
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=AgentStatus.DRAFT,
        server_default=AgentStatus.DRAFT.value,
    )


class AdminUser(Base, BaseMixin):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(default=False)
