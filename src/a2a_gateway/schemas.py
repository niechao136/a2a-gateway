"""Pydantic 数据契约（API 请求/响应）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import AgentStatus


class A2ATarget(BaseModel):
    """一个绑定的 A2A 目标。"""

    url: str = Field(description="A2A 目标 URL，如 http://host:port/")
    token: str = Field(default="", description="认证 token，可为空")


class AgentBase(BaseModel):
    name: str
    description: str = ""
    a2a_targets: list[A2ATarget] = Field(default_factory=list)
    system_prompt: str | None = None
    enabled_tools: list[str] = Field(default_factory=list)


class AgentCreate(AgentBase):
    slug: str = Field(description="路由标识；禁止使用 '/' 或已占用 slug")


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    a2a_targets: list[A2ATarget] | None = None
    system_prompt: str | None = None
    enabled_tools: list[str] | None = None
    status: Literal["draft", "published"] | None = None


class AgentOut(AgentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
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
