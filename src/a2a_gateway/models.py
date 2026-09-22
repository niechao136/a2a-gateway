"""ORM 数据模型。

- A2AEndpoint：「A2A 管理」中维护的 A2A 服务注册表
- McpServer：「MCP 管理」中维护的 MCP 服务注册表
- AgentConfig：自定义/默认 Agent 的配置（路由、绑定的 A2A/MCP 资源、system_prompt、工具集、状态）
- ApiKey：对外提供 A2A 服务的调用凭据（/a2a/* 端点鉴权）
- AdminUser：管理中心登录账号（JWT 认证）
- Conversation：对话会话目录（thread_id → 身份归属，支持匿名 → 登录归并）
- PendingA2ATask：挂起任务（input-required 中断 → 恢复映射）
- Skill：编排方法论技能注册表（SKILL.md）
"""

from datetime import datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AgentStatus(str, PyEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class ConnectorPlatform(str, PyEnum):
    FEISHU = "feishu"
    TELEGRAM = "telegram"
    SLACK = "slack"


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
    # 在「Skill 管理」中勾选的技能 id 列表（只有 approved 的技能可被勾选）
    skill_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    # 由 skill_ids 解析而来的运行时快照（正文全文，供注入与 load_skill 闭包使用）
    skills: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
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


class SkillReviewStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Skill(Base, BaseMixin):
    """编排方法论技能注册表（「Skill 管理」维护，Agent 勾选绑定）。

    正文与附件全量入库（快照即全部）：运行时零 DB 依赖，
    agent_factory / graph.py 直接从闭包读取。
    """

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")   # 进 prompt 清单，决定加载率
    content: Mapped[str] = mapped_column(Text, default="")       # SKILL.md 正文（已剥离 frontmatter）
    frontmatter: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # 附件：[{"path": "references/a.md", "size": 123, "content": "...", "entry_type": "text"|"script", "encoding": "utf-8"|"base64"}]
    # 存量数据缺 entry_type/encoding → 读取处兜底视为 text/utf-8
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    load_mode: Mapped[str] = mapped_column(String(16), default="on_demand")  # always | on_demand
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual|text|url|zip|dir
    source_ref: Mapped[str] = mapped_column(String(512), default="")
    # 是否允许在沙箱中执行捆绑脚本（Phase B 沙箱上线后生效；审核时决定，变更不重置审核）
    allow_scripts: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    review_status: Mapped[SkillReviewStatus] = mapped_column(
        Enum(
            SkillReviewStatus,
            name="skillreviewstatus",
            # 与 AgentStatus 同坑：必须按「成员值」建 PG 枚举
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        default=SkillReviewStatus.PENDING,
        server_default=SkillReviewStatus.PENDING.value,
    )
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiKey(Base, BaseMixin):
    """API Key（某个 Agent 对外提供 A2A 服务时的调用凭据）。

    - 每个 Agent 独立管理自己的 Key：创建 Agent 时自动生成默认 Key
      （is_default=True，不可删除），其余 Key 在 Agent 编辑页按需新增 / 删除
    - 对外 A2A 调用 /a2a/{slug} 时，Key 必须属于该 Agent
    """

    __tablename__ = "api_keys"
    # 名称在同一 Agent 内唯一（不同 Agent 可同名，如各自的「默认 Key」）
    __table_args__ = (
        UniqueConstraint("agent_id", "name", name="uq_api_keys_agent_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
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


class Conversation(Base, BaseMixin):
    """对话会话目录：把 LangGraph 的 thread_id 归属到某个身份。

    消息本体仍由 Checkpointer 按 thread_id 存放在 checkpoints 系列表中，与用户无关；
    本表只维护「谁有哪些会话 + 标题 / 时间」这一层目录。因此匿名 → 登录的归并
    只需改 owner_kind / owner_id 两个字段，消息数据零搬迁、历史可原地续聊。

    owner_kind 取值：
      - visitor：匿名访客，owner_id 为后端签发的 uuid
      - user：已登录管理员，owner_id 为 admin_users.username
    """

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("thread_id", name="uq_conversations_thread"),
        Index("ix_conversations_owner", "owner_kind", "owner_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # LangGraph thread_id（前端会话 id，由前端生成或后端 new_thread_id 兜底）
    thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 所属 Agent 路由；默认 Agent 为 "/"
    agent_slug: Mapped[str] = mapped_column(String(128), index=True, default="/")
    title: Mapped[str] = mapped_column(String(255), default="")
    owner_kind: Mapped[str] = mapped_column(String(16), index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)


class PendingA2ATask(Base, BaseMixin):
    """挂起任务：网关会话 / 对外 task_id → 下游任务（task_id）的映射。

    链路 A（对话界面）以会话 thread_id 为键；链路 B（A2A Server）以对外 task_id
    为键（创建新任务时两者取同一标识）。下游完成即删除；读取时按 TTL 过期清理。
    """

    __tablename__ = "pending_a2a_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="CASCADE"), index=True
    )
    target_url: Mapped[str] = mapped_column(String(512))
    target_name: Mapped[str] = mapped_column(String(128), default="")
    task_id: Mapped[str] = mapped_column(String(128))
    context_id: Mapped[str] = mapped_column(String(128), default="")
    question: Mapped[str] = mapped_column(Text, default="")


class ChatConnector(Base, BaseMixin):
    """聊天连接器注册表（「连接器管理」维护）。

    一个连接器 = 一个聊天平台机器人实例，1:1 绑定一个 Agent：
    平台消息 → 适配器归一化 → 绑定 Agent 处理 → 适配器回发。
    credentials 按 platform 存放不同结构（schemas.py 按平台校验），接口返回时脱敏。
    """

    __tablename__ = "chat_connectors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    platform: Mapped[ConnectorPlatform] = mapped_column(
        Enum(
            ConnectorPlatform,
            name="connectorplatform",
            # 与 AgentStatus 同坑：必须按「成员值」建 PG 枚举
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        )
    )
    credentials: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ConnectorConversation(Base, BaseMixin):
    """连接器会话映射：平台会话 (connector_id, chat_id) ↔ LangGraph thread_id。

    消息本体仍由 Checkpointer 按 thread_id 存放；本表只维护目录映射与
    最近发言人/活跃时间（群聊 @ 场景拼上下文、主动推送定位目标用）。
    """

    __tablename__ = "chat_connector_conversations"
    __table_args__ = (
        UniqueConstraint("connector_id", "chat_id", name="uq_connector_conversations_chat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    connector_id: Mapped[int] = mapped_column(
        ForeignKey("chat_connectors.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[str] = mapped_column(String(128))
    chat_type: Mapped[str] = mapped_column(String(16), default="private")  # private / group
    thread_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    last_user_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, server_default=func.now()
    )
