"""ORM 数据模型。

- A2AEndpoint：「A2A 管理」中维护的 A2A 服务注册表
- McpServer：「MCP 管理」中维护的 MCP 服务注册表
- AgentConfig：自定义/默认 Agent 的配置（路由、绑定的 A2A/MCP 资源、system_prompt、工具集、状态）
- ApiKey：对外提供 A2A 服务的调用凭据（/a2a/* 端点鉴权）
- AdminUser：管理中心登录账号（JWT 认证）
"""

from datetime import datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, String, Text, func
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
    # 由 a2a_target_ids 解析而来，是运行时的实际绑定（a2a_client 只读这里）
    a2a_targets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # 在「A2A 管理」中勾选的 A2A 目标 id 列表（Agent 侧只做选择，不再手填 url/token）
    a2a_target_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    # 可选覆盖 system prompt
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 在「MCP 管理」中勾选的 MCP 服务 id 列表
    mcp_server_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    # 由 mcp_server_ids 解析而来的连接快照，供运行时构造 MCP 工具（不对外暴露）
    mcp_servers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
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


class A2AEndpoint(Base, BaseMixin):
    """A2A 服务注册表（「A2A 管理」维护）。

    Agent 不再手填 url/token，而是在此注册后由 Agent 侧勾选绑定。
    与运行时绑定 `AgentConfig.a2a_targets`（解析后的快照）区分：
    这里是「可复用的服务定义」，那里是「某个 Agent 实际使用的连接」。
    """

    __tablename__ = "a2a_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(512))
    token: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # 鉴权方式：none / bearer / header / query / basic（密钥统一放在 token 字段）
    auth_type: Mapped[str] = mapped_column(String(32), default="bearer")
    # header 模式为请求头名、query 模式为查询参数名、basic 模式为用户名
    auth_name: Mapped[str] = mapped_column(String(128), default="")
    # 临时停用时不再参与解析（保留 Agent 上的勾选，重新启用即恢复）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class McpServer(Base, BaseMixin):
    """MCP 服务注册表（「MCP 管理」维护）。

    transport 取值：stdio / sse / streamable_http
      - stdio           → 用 command + args + env 启动本地进程
      - sse             → 连接 url（SSE 传输）
      - streamable_http → 连接 url（Streamable HTTP 传输，推荐）
    """

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    transport: Mapped[str] = mapped_column(String(32), default="streamable_http")
    url: Mapped[str] = mapped_column(String(512), default="")
    command: Mapped[str] = mapped_column(String(512), default="")
    args: Mapped[list[str]] = mapped_column(JSONB, default=list)
    env: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    # 验证凭据：远程传输（sse / streamable_http）走请求头或查询参数；
    # stdio 传输无法带 HTTP 头，改为注入环境变量 MCP_AUTH_TOKEN 供子进程读取
    token: Mapped[str] = mapped_column(String(512), default="")
    auth_type: Mapped[str] = mapped_column(String(32), default="bearer")
    auth_name: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiKey(Base, BaseMixin):
    """API Key（对外提供 A2A 服务时的调用凭据）。

    - 启动时自动生成一个默认 Key（is_default=True，不可删除，防止把自己锁在门外）
    - 其余 Key 可在「API Key 管理」中按需新增 / 删除
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # 默认 Key 不可删除
    is_default: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)


class AdminUser(Base, BaseMixin):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(default=False)
