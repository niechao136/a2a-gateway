"""Pydantic 数据契约（API 请求/响应）。"""

from datetime import datetime
from typing import Any, Literal

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
# 模型注册表（「模型管理」维护，Agent 单选绑定）
# ---------------------------------------------------------------------------
LLM_PROVIDERS = ("openai", "anthropic")


def validate_llm_model(provider: str, base_url: str, model: str) -> None:
    """按 provider 校验必填项；不合法抛 ValueError（中文）。"""
    if provider not in LLM_PROVIDERS:
        raise ValueError(f"不支持的模型供应商：{provider}")
    if not model.strip():
        raise ValueError("模型标识不能为空")
    if provider == "openai" and not base_url.strip():
        raise ValueError("OpenAI 兼容端点必须填写 base_url（通常以 /v1 结尾）")


def mask_secret(value: str) -> str:
    """API Key 出参脱敏：保留前 3 后 4，其余打码。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}****{value[-4:]}"


class LLMModelCreate(BaseModel):
    name: str = Field(description="展示名，全局唯一")
    provider: Literal["openai", "anthropic"] = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = Field(description="模型标识，如 deepseek-chat")
    description: str = ""


class LLMModelUpdate(BaseModel):
    name: str | None = None
    provider: Literal["openai", "anthropic"] | None = None
    base_url: str | None = None
    # 留空/不传 = 保持原值（配合出参脱敏：前端不回显明文 key）
    api_key: str | None = None
    model: str | None = None
    description: str | None = None


class LLMModelOut(BaseModel):
    """出参不含明文 api_key，只给脱敏形式。"""

    id: int
    name: str
    provider: str
    base_url: str
    model: str
    description: str
    api_key_masked: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Skill 注册表（「Skill 管理」维护）
# ---------------------------------------------------------------------------
SKILL_LOAD_MODES = ("always", "on_demand")
SKILL_REVIEW_STATUSES = ("pending", "approved", "rejected")


class SkillCreate(BaseModel):
    name: str = Field(description="技能标识，全局唯一；仅 ASCII 字母数字与 . _ -")
    description: str = Field(description="技能说明；模型依据它决定是否加载")
    content: str = Field(default="", description="技能正文（Markdown）")
    load_mode: Literal["always", "on_demand"] = "on_demand"


class SkillFileIn(BaseModel):
    """编辑提交的附件条目（全量替换语义）。"""

    path: str = Field(description="技能包内相对路径（posix 风格）")
    content: str = Field(default="", description="文本原文，或 base64（脚本）")
    entry_type: Literal["text", "script"] = "text"
    encoding: Literal["utf-8", "base64"] = "utf-8"


class SkillUpdate(BaseModel):
    description: str | None = None
    content: str | None = None
    load_mode: Literal["always", "on_demand"] | None = None
    enabled: bool | None = None
    allow_scripts: bool | None = Field(default=None, description="是否允许沙箱执行捆绑脚本")
    files: list[SkillFileIn] | None = Field(default=None, description="None=不改；列表=全量替换")


class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    content: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    files: list[dict[str, Any]] = Field(default_factory=list)
    load_mode: str = "on_demand"
    size_bytes: int = 0
    file_count: int = 0
    source: str = "manual"
    source_ref: str = ""
    review_status: str = "pending"
    review_note: str = ""
    reviewed_at: datetime | None = None
    enabled: bool = True
    allow_scripts: bool = False
    created_at: datetime
    updated_at: datetime


class SkillReviewRequest(BaseModel):
    status: Literal["approved", "rejected", "pending"]
    note: str = ""


class SkillScriptRunRequest(BaseModel):
    """管理端脚本试跑请求（审核辅助；不校验 review_status / allow_scripts）。"""

    path: str = Field(description="脚本相对路径（必须是该技能的脚本附件）")
    argv: list[str] = Field(default_factory=list)
    stdin: str = ""
    timeout_s: int = Field(default=30, ge=1, le=120)


class SkillScriptRunOut(BaseModel):
    """沙箱 RunResult 透传（字段与沙箱协议一致）。"""

    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timeout: bool = False
    duration_ms: int = 0
    error: str | None = None


class SkillImportDirFile(BaseModel):
    path: str = Field(description="浏览器上报的相对路径（posix 风格）")
    content: str = Field(default="", description="文件文本内容")


class SkillImportRequest(BaseModel):
    """导入预览请求：四种来源互斥，按 source 取对应字段。"""

    source: Literal["text", "url", "zip", "dir"]
    skill_md: str | None = Field(default=None, description="source=text 时的 SKILL.md 全文")
    url: str | None = Field(default=None, description="source=url 时的抓取地址（raw 或 zip）")
    zip_b64: str | None = Field(default=None, description="source=zip 时的 base64 包")
    dir_files: list[SkillImportDirFile] | None = Field(
        default=None, description="source=dir 时的文件数组"
    )
    overwrite: bool = Field(default=False, description="重名时覆盖（并把审核重置为 pending）")


class SkillImportPreviewItem(BaseModel):
    name: str = ""
    description: str = ""
    content_bytes: int = 0
    file_count: int = 0
    total_bytes: int = 0
    files: list[str] = Field(default_factory=list, description="文本附件相对路径")
    scripts: list[str] = Field(default_factory=list, description="脚本附件相对路径（Phase B 起可沙箱执行）")
    skipped_binary: list[str] = Field(default_factory=list, description="已跳过的非文本文件")
    conflict: bool = False
    error: str | None = Field(default=None, description="该条解析失败原因")


class SkillImportPreviewOut(BaseModel):
    items: list[SkillImportPreviewItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list, description="来源级错误（如 URL 不可达）")
    over_limit: bool = Field(default=False, description="是否存在解析失败条目")


class SkillImportCommitRequest(SkillImportRequest):
    names: list[str] = Field(
        default_factory=list, description="预览后勾选落库的技能名单；空 = 全部可落库项"
    )


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


class AgentModelBinding(BaseModel):
    """Agent 的模型绑定。

    更新时整体替换语义：字段对象为 null = 不修改绑定；
    提供对象且 model_id 为 null = 清除绑定（回落全局配置）。
    """

    model_id: int | None = Field(default=None, description="模型注册表 id；null = 清除绑定")
    temperature: float | None = Field(default=None, description="留空用运行时默认")
    max_tokens: int | None = Field(default=None, description="留空用运行时默认")


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
    skill_ids: list[int] = Field(
        default_factory=list, description="在「Skill 管理」中勾选的技能 id 列表"
    )
    system_prompt: str | None = None
    # 模型绑定（可空 = 回落全局 LLM_* 环境变量）
    model: AgentModelBinding | None = None


class AgentCreate(AgentBase):
    slug: str = Field(description="路由标识；禁止使用 '/' 或已占用 slug")


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    a2a_target_ids: list[int] | None = None
    mcp_server_ids: list[int] | None = None
    a2a_targets: list[A2ATarget] | None = None
    mcp_servers: list[ManualMcpServer] | None = None
    skill_ids: list[int] | None = None
    system_prompt: str | None = None
    status: Literal["draft", "published"] | None = None
    # 模型绑定：None = 不修改；提供对象则整体替换（model_id 为 null = 清除）
    model: AgentModelBinding | None = None


class AgentOut(AgentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    a2a_target_ids: list[int] = Field(default_factory=list)
    mcp_server_ids: list[int] = Field(default_factory=list)
    model_id: int | None = None
    model_snapshot: dict[str, Any] | None = None
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


# ---------------------------------------------------------------------------
# 聊天连接器（「连接器管理」维护）
# ---------------------------------------------------------------------------
CONNECTOR_PLATFORMS = ("feishu", "telegram", "slack")


class FeishuCredentials(BaseModel):
    """飞书开放平台「企业自建应用」凭据。"""

    model_config = ConfigDict(extra="forbid")

    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""
    encrypt_key: str = ""  # 未启用事件加密则留空


class TelegramCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_token: str = ""
    secret_token: str = ""  # webhook 验证密钥；创建时留空由后端自动生成
    bot_username: str = ""  # 启用时经 getMe 自动补全，群聊 @ 过滤用


class SlackCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_token: str = ""  # xoxb- 开头
    signing_secret: str = ""


_CREDENTIAL_MODELS: dict[str, type[BaseModel]] = {
    "feishu": FeishuCredentials,
    "telegram": TelegramCredentials,
    "slack": SlackCredentials,
}


def validate_connector_credentials(
    platform: str, credentials: dict[str, Any] | None
) -> dict[str, Any]:
    """按平台校验凭据结构；平台未知或字段非法抛 ValueError/ValidationError（路由转 400）。"""
    model = _CREDENTIAL_MODELS.get(platform)
    if model is None:
        raise ValueError(f"platform 仅支持 {CONNECTOR_PLATFORMS}")
    return model(**(credentials or {})).model_dump()


def merge_connector_credentials(
    platform: str, old: dict[str, Any] | None, new: dict[str, Any] | None
) -> dict[str, Any]:
    """更新合并：新值为空的字段保留原值（凭据回显脱敏，留空 = 不修改）。"""
    model = _CREDENTIAL_MODELS[platform]
    cleaned = {k: v for k, v in (new or {}).items() if v}
    merged = {**(old or {}), **cleaned}
    return model(**merged).model_dump()


def mask_connector_credentials(platform: str, credentials: dict[str, Any] | None) -> dict[str, str]:
    """凭据脱敏视图：已配置字段 → "••••"，空字段 → ""（仅提示配置状态，不含明文）。"""
    data = validate_connector_credentials(platform, credentials)
    return {k: ("••••" if v else "") for k, v in data.items()}


class ConnectorBase(BaseModel):
    name: str = Field(description="显示名称，全局唯一")
    platform: Literal["feishu", "telegram", "slack"]
    description: str = ""
    agent_id: int = Field(description="绑定的 Agent id")
    enabled: bool = True
    credentials: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_credentials(self) -> "ConnectorBase":
        self.credentials = validate_connector_credentials(self.platform, self.credentials)
        return self


class ConnectorCreate(ConnectorBase):
    pass


class ConnectorUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    agent_id: int | None = None
    enabled: bool | None = None
    credentials: dict[str, Any] | None = None


class ConnectorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    platform: str
    agent_id: int
    agent_name: str = ""
    enabled: bool
    webhook_url: str = ""
    credentials_masked: dict[str, str] = {}
    setup_warning: str = ""  # Telegram 自动注册失败等原因的提示（不阻断保存）
    last_active_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConnectorConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chat_id: str
    chat_type: str
    last_user_ref: dict[str, Any] = {}
    last_active_at: datetime


class ConnectorSendRequest(BaseModel):
    chat_id: str | None = Field(default=None, description="留空推送最近活跃会话")
    text: str = Field(min_length=1, description="消息文本")
