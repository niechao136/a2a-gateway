"""Pydantic 数据契约（API 请求/响应）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth_scheme import AUTH_TYPES
from .models import AgentStatus

# MCP 支持的传输方式
MCP_TRANSPORTS = ("stdio", "sse", "streamable_http")

# 需要「配套名称」的鉴权方式：header→请求头名、query→参数名、basic→用户名
AUTH_NAME_REQUIRED = ("header", "query", "basic")


def validate_auth(auth_type: str, auth_name: str, token: str) -> None:
    """校验鉴权参数完整性；不匹配时抛 ValueError（由路由转 400）。"""
    if auth_type not in AUTH_TYPES:
        raise ValueError(f"auth_type 仅支持 {AUTH_TYPES}")
    if auth_type in AUTH_NAME_REQUIRED and not auth_name.strip():
        raise ValueError(f"{auth_type} 鉴权需要填写名称（请求头名 / 参数名 / 用户名）")
    if auth_type != "none" and not token.strip():
        raise ValueError(f"{auth_type} 鉴权需要填写密钥")


class A2ATarget(BaseModel):
    """一个绑定的 A2A 目标（解析后的连接快照）。

    description 会写进工具说明，让大模型知道"该目标擅长什么"，从而在多个目标间做出选择。
    """

    url: str = Field(description="A2A 目标 URL，如 http://host:port/")
    token: str = Field(default="", description="认证 token，可为空")
    name: str = Field(default="", description="目标名称（用于生成工具名）")
    description: str = Field(default="", description="目标说明，会进入大模型提示词")
    auth_type: str = Field(default="bearer", description="鉴权方式")
    auth_name: str = Field(default="", description="请求头名 / 查询参数名 / basic 用户名")


# ---------------------------------------------------------------------------
# A2A 目标注册表（「A2A 管理」维护）
# ---------------------------------------------------------------------------
class A2AEndpointBase(BaseModel):
    name: str = Field(description="显示名称，全局唯一")
    url: str = Field(description="A2A 服务地址，如 http://host:port/")
    token: str = Field(default="", description="密钥（随 auth_type 决定放到哪里）")
    description: str = Field(
        default="", description="目标说明（会进入大模型提示词，用于判断该调用哪个目标）"
    )
    auth_type: str = Field(default="bearer", description="鉴权方式：none/bearer/header/query/basic")
    auth_name: str = Field(default="", description="请求头名 / 查询参数名 / basic 用户名")
    enabled: bool = True

    @model_validator(mode="after")
    def _check_auth(self) -> "A2AEndpointBase":
        validate_auth(self.auth_type, self.auth_name, self.token)
        return self


class A2AEndpointCreate(A2AEndpointBase):
    pass


class A2AEndpointUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    token: str | None = None
    description: str | None = None
    auth_type: str | None = None
    auth_name: str | None = None
    enabled: bool | None = None


class A2AEndpointOut(A2AEndpointBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# MCP 服务注册表（「MCP 管理」维护）
# ---------------------------------------------------------------------------
def validate_mcp_transport(transport: str, url: str, command: str) -> None:
    """校验传输方式与连接参数是否匹配；不匹配时抛 ValueError（由路由转 400）。"""
    if transport not in MCP_TRANSPORTS:
        raise ValueError(f"transport 仅支持 {MCP_TRANSPORTS}")
    if transport == "stdio":
        if not command.strip():
            raise ValueError("stdio 传输需要填写启动命令")
    elif not url.strip():
        raise ValueError(f"{transport} 传输需要填写服务 URL")


class McpServerBase(BaseModel):
    name: str = Field(description="显示名称，全局唯一")
    description: str = Field(default="")
    transport: Literal["stdio", "sse", "streamable_http"] = "streamable_http"
    url: str = Field(default="", description="sse / streamable_http 的服务地址")
    command: str = Field(default="", description="stdio 的启动命令")
    args: list[str] = Field(default_factory=list, description="stdio 的启动参数")
    env: dict[str, str] = Field(default_factory=dict, description="stdio 的进程环境变量")
    # 验证凭据：远程传输走请求头/查询参数，stdio 注入环境变量 MCP_AUTH_TOKEN
    token: str = Field(default="", description="密钥（随 auth_type 决定放到哪里）")
    auth_type: str = Field(default="bearer", description="鉴权方式：none/bearer/header/query/basic")
    auth_name: str = Field(default="", description="请求头名 / 查询参数名 / basic 用户名")
    enabled: bool = True

    @model_validator(mode="after")
    def _check_transport(self) -> "McpServerBase":
        validate_mcp_transport(self.transport, self.url, self.command)
        return self

    @model_validator(mode="after")
    def _check_auth(self) -> "McpServerBase":
        validate_auth(self.auth_type, self.auth_name, self.token)
        return self


class McpServerCreate(McpServerBase):
    pass


class McpServerUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    transport: Literal["stdio", "sse", "streamable_http"] | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    token: str | None = None
    auth_type: str | None = None
    auth_name: str | None = None
    enabled: bool | None = None


class McpServerOut(McpServerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
class ManualMcpServer(BaseModel):
    """Agent 上手动绑定的 MCP 服务（不经过「MCP 管理」注册表）。

    与注册表勾选可并存：运行时快照 = 勾选解析结果 + 手动条目。
    """

    name: str = Field(description="显示名称（用于生成工具说明），同一 Agent 内建议唯一")
    description: str = Field(default="", description="服务说明，会进入大模型提示词")
    transport: Literal["stdio", "sse", "streamable_http"] = "streamable_http"
    url: str = Field(default="", description="sse / streamable_http 的服务地址")
    command: str = Field(default="", description="stdio 的启动命令")
    args: list[str] = Field(default_factory=list, description="stdio 的启动参数")
    env: dict[str, str] = Field(default_factory=dict, description="stdio 的进程环境变量")
    token: str = Field(default="", description="密钥（随 auth_type 决定放到哪里）")
    auth_type: str = Field(default="bearer", description="鉴权方式：none/bearer/header/query/basic")
    auth_name: str = Field(default="", description="请求头名 / 查询参数名 / basic 用户名")

    @model_validator(mode="after")
    def _check(self) -> "ManualMcpServer":
        if not self.name.strip():
            raise ValueError("手动绑定的 MCP 服务需要填写名称")
        validate_mcp_transport(self.transport, self.url, self.command)
        validate_auth(self.auth_type, self.auth_name, self.token)
        return self


class AgentBase(BaseModel):
    name: str
    description: str = ""
    a2a_target_ids: list[int] = Field(
        default_factory=list, description="在「A2A 管理」中勾选的目标 id 列表"
    )
    mcp_server_ids: list[int] = Field(
        default_factory=list, description="在「MCP 管理」中勾选的服务 id 列表"
    )
    a2a_targets: list[A2ATarget] = Field(
        default_factory=list,
        description="手动绑定的 A2A 目标（无需在「A2A 管理」注册），可与勾选并存",
    )
    mcp_servers: list[ManualMcpServer] = Field(
        default_factory=list,
        description="手动绑定的 MCP 服务（无需在「MCP 管理」注册），可与勾选并存",
    )
    system_prompt: str | None = None


class AgentCreate(AgentBase):
    slug: str = Field(description="路由标识；禁止使用 '/' 或已占用 slug")


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    a2a_target_ids: list[int] | None = None
    mcp_server_ids: list[int] | None = None
    a2a_targets: list[A2ATarget] | None = None
    mcp_servers: list[ManualMcpServer] | None = None
    system_prompt: str | None = None
    status: Literal["draft", "published"] | None = None


class AgentOut(AgentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    a2a_target_ids: list[int] = Field(default_factory=list)
    mcp_server_ids: list[int] = Field(default_factory=list)
    status: AgentStatus
    created_at: datetime
    updated_at: datetime


class AdminUserCreate(BaseModel):
    username: str
    password: str


class ApiKeyCreate(BaseModel):
    name: str = Field(description="显示名称，全局唯一")


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    name: str
    key: str
    is_default: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    disabled: bool
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    # 登录时归并到账号的匿名会话数量（0 表示本次没有可归并的会话）
    claimed: int = 0


# 前端公开对话请求
class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = Field(
        default=None, description="会话标识；为空时由后端生成匿名 session"
    )


# 重试请求（time travel：从最后一次人类消息处重放）
class RetryRequest(BaseModel):
    thread_id: str = Field(description="要重试的会话标识")


class AdminLoginRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# 对话会话目录（thread_id → 身份归属）
# ---------------------------------------------------------------------------
class IdentityOut(BaseModel):
    """当前对话身份。前端只用于判断「是否已登录」。"""

    kind: Literal["visitor", "user"]
    id: str


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    thread_id: str
    agent_slug: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationCreate(BaseModel):
    """登记 / 刷新一条会话。"""

    thread_id: str = Field(description="会话 id（即 LangGraph thread_id）")
    slug: str = Field(default="", description="Agent 路由；空串表示默认 Agent")
    title: str | None = Field(default=None, description="标题；仅在原标题为空时写入")


class ConversationRename(BaseModel):
    title: str = Field(description="新的会话标题")


class ConversationImportItem(BaseModel):
    """前端 localStorage 里的一条历史会话。"""

    thread_id: str
    title: str = ""
    created_at: int | None = Field(default=None, description="毫秒时间戳，仅用于排序")
    updated_at: int | None = Field(default=None, description="毫秒时间戳，仅用于排序")


class ConversationImportRequest(BaseModel):
    slug: str = Field(default="", description="Agent 路由；空串表示默认 Agent")
    items: list[ConversationImportItem] = Field(default_factory=list)


class ConversationImportOut(BaseModel):
    imported: int
