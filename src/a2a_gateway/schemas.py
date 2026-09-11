"""Pydantic 数据契约（API 请求/响应）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import AgentStatus

# MCP 支持的传输方式
MCP_TRANSPORTS = ("stdio", "sse", "streamable_http")


class A2ATarget(BaseModel):
    """一个绑定的 A2A 目标（解析后的连接快照）。"""

    url: str = Field(description="A2A 目标 URL，如 http://host:port/")
    token: str = Field(default="", description="认证 token，可为空")


# ---------------------------------------------------------------------------
# A2A 目标注册表（「A2A 管理」维护）
# ---------------------------------------------------------------------------
class A2AEndpointBase(BaseModel):
    name: str = Field(description="显示名称，全局唯一")
    url: str = Field(description="A2A 服务地址，如 http://host:port/")
    token: str = Field(default="", description="Bearer token，可为空")
    description: str = Field(default="")
    enabled: bool = True


class A2AEndpointCreate(A2AEndpointBase):
    pass


class A2AEndpointUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    token: str | None = None
    description: str | None = None
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
    enabled: bool = True

    @model_validator(mode="after")
    def _check_transport(self) -> "McpServerBase":
        validate_mcp_transport(self.transport, self.url, self.command)
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
    enabled: bool | None = None


class McpServerOut(McpServerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
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
        description="解析后的 A2A 绑定快照（只读）。兼容历史客户端：未传 a2a_target_ids 时可直接传本字段",
    )
    system_prompt: str | None = None
    enabled_tools: list[str] = Field(default_factory=list)


class AgentCreate(AgentBase):
    slug: str = Field(description="路由标识；禁止使用 '/' 或已占用 slug")


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    a2a_target_ids: list[int] | None = None
    mcp_server_ids: list[int] | None = None
    a2a_targets: list[A2ATarget] | None = None
    system_prompt: str | None = None
    enabled_tools: list[str] | None = None
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


# 前端公开对话请求
class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = Field(
        default=None, description="会话标识；为空时由后端生成匿名 session"
    )


class AdminLoginRequest(BaseModel):
    username: str
    password: str
