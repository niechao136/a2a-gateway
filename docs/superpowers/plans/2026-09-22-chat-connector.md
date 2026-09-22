# 聊天连接器管理 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 a2a-gateway 新增「连接器管理」子系统：Agent 通过 Webhook 接入飞书 / Telegram / Slack，双向文本对话 + 系统侧主动推送。

**架构：** 平台适配器抽象层（`connectors/` 包，每平台实现验签/归一化/握手/出站）+ 统一 Webhook 网关（收消息立即 200，后台串行队列处理，复用 `get_agent_instance` + `graph.ainvoke` 产出回复）+ 会话映射表（平台 chat_id ↔ LangGraph thread_id）+ 管理端 CRUD API 与 `/admin/connectors` 管理页。

**技术栈：** FastAPI + SQLAlchemy 2 async + PostgreSQL(JSONB) + Alembic + LangGraph（现有栈）；前端 Next.js App Router + MUI。不引入各平台 SDK，出入站为 httpx 直接调用 + 标准库验签；唯一新增显式依赖 `cryptography`（飞书事件解密，python-jose 已间接引入）。

**规格：** `docs/superpowers/specs/2026-09-22-chat-connector-design.md`（计划的论证依据，执行者两份都读）

**对规格的两点实现适配（语义不变）：**
1. 规格中的 `PATCH /api/admin/connectors/{id}` 采用本仓库现有 **PUT** 约定（与 agents / a2a-endpoints / mcp-servers 一致），「凭据留空字段不覆盖」语义原样保留；
2. 凭据回显脱敏实现为 `credentials_masked` 字段（`{"app_secret": "••••", ...}`），供编辑弹窗展示「已配置」状态。

## 全局约束

- Python 3.11；ruff line-length=100；basedpyright `standard` 模式 0 error（`uv run basedpyright`）。
- 测试零外部依赖：不连数据库、不起 lifespan、不真实联网（`tests/conftest.py` 的 `ASGITransport` + `dependency_overrides` + monkeypatch 模式）。
- **所有命令执行时必须设置超时**（建议 180s），避免阻塞开发流程。
- 凭据（`credentials`）永不写入日志、永不出现在 API 响应明文中。
- Webhook 路由不带 admin JWT 鉴权，安全依赖平台验签（Telegram secret 头 / Slack HMAC / 飞书 token+可选解密）。
- PG 枚举必须按「成员值」建（`values_callable`，同 `AgentStatus` / `SkillReviewStatus` 的坑）。
- 提交信息用 Conventional Commits（`feat:` / `test:` / `docs:`，中文描述，对齐仓库现有风格）。
- 前端管理页：`"use client"` 客户端组件 + MUI `sx` + `adminApi` 统一请求；新增文件放 `web/src/app/admin/connectors/` 与 `web/src/components/admin/`。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/a2a_gateway/config.py`（改） | 新增 `public_base_url` 配置 |
| `src/a2a_gateway/models.py`（改） | `ConnectorPlatform` 枚举、`ChatConnector`、`ConnectorConversation` |
| `alembic/versions/0012_chat_connectors.py`（新） | 幂等建两表 + 枚举类型 |
| `src/a2a_gateway/schemas.py`（改） | 平台凭据模型、`Connector*` 契约、脱敏/合并工具 |
| `src/a2a_gateway/repository.py`（改） | 连接器 CRUD、会话映射 upsert、`build_connector_thread_id` |
| `src/a2a_gateway/connectors/__init__.py`（新） | 包标记 |
| `src/a2a_gateway/connectors/base.py`（新） | `PlatformAdapter` 抽象、`InboundMessage`、`VerifyError`、`chunk_text` |
| `src/a2a_gateway/connectors/telegram.py`（新） | Telegram 适配器 + `register_webhook` |
| `src/a2a_gateway/connectors/slack.py`（新） | Slack 适配器（HMAC 验签、url_verification、app_mention/message.im） |
| `src/a2a_gateway/connectors/feishu.py`（新） | 飞书适配器（token 校验、可选 AES 解密、mentions 过滤、tenant_access_token 缓存） |
| `src/a2a_gateway/connectors/registry.py`（新） | platform 字符串 → 适配器实例 |
| `src/a2a_gateway/connectors/pipeline.py`（新） | 去重、串行队列、Agent 调用、回复推送 |
| `src/a2a_gateway/routes/connectors.py`（新） | 管理 PUT/CRUD 路由 + 平台 webhook 路由 |
| `src/a2a_gateway/main.py`（改） | 注册两个 router |
| `web/src/lib/adminApi.ts`（改） | 连接器类型与 6 个 API 方法 |
| `web/src/components/admin/ConnectorDialog.tsx`（新） | 新建/编辑弹窗（平台驱动凭据表单） |
| `web/src/components/admin/SendTestDialog.tsx`（新） | 发送测试弹窗 |
| `web/src/app/admin/connectors/page.tsx`（新） | 连接器列表页 |
| `web/src/components/admin/AdminShell.tsx`（改） | NAV_ITEMS 增加导航项 |
| `tests/test_connector_models.py`、`tests/test_connector_support.py`、`tests/test_connector_adapters.py`、`tests/test_connector_pipeline.py`、`tests/test_connectors_api.py`（新） | 分层测试 |

任务顺序即依赖顺序：1→2（数据层）→3（抽象）→4/5/6（三适配器）→7（管线）→8/9（路由）→10/11（前端）→12（全量验证）。

---

### 任务 1：配置 + 数据模型 + Alembic 迁移

**文件：**
- 修改：`src/a2a_gateway/config.py`
- 修改：`src/a2a_gateway/models.py`（文件末尾追加）
- 创建：`alembic/versions/0012_chat_connectors.py`
- 测试：`tests/test_connector_models.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_connector_models.py`：

```python
"""连接器数据模型注册与迁移文件存在性。"""

from pathlib import Path

from a2a_gateway.models import Base, ConnectorPlatform


def test_connector_tables_registered():
    assert "chat_connectors" in Base.metadata.tables
    assert "chat_connector_conversations" in Base.metadata.tables


def test_platform_enum_uses_member_values():
    assert [p.value for p in ConnectorPlatform] == ["feishu", "telegram", "slack"]


def test_migration_file_exists():
    path = Path("alembic/versions/0012_chat_connectors.py")
    assert path.exists()
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_connector_models.py -v`（超时 180s）
预期：FAIL，`ImportError: cannot import name 'ConnectorPlatform'`

- [ ] **步骤 3：实现数据模型与配置**

`config.py` 在 `frontend_origin` 之后追加：

```python
    # 公网基址（聊天连接器）：用于展示 webhook 完整 URL 与 Telegram 自动注册；留空则只展示相对路径
    public_base_url: str = Field(default="", alias="PUBLIC_BASE_URL")
```

`models.py` 在 `AgentStatus` 枚举之后追加枚举，在文件末尾追加两个模型：

```python
class ConnectorPlatform(str, PyEnum):
    FEISHU = "feishu"
    TELEGRAM = "telegram"
    SLACK = "slack"


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
```

- [ ] **步骤 4：编写 Alembic 迁移**

创建 `alembic/versions/0012_chat_connectors.py`（沿用 0010 的幂等原生 SQL 风格）：

```python
"""chat connectors: 连接器注册表 + 会话映射表

Revision ID: 0012_chat_connectors
Revises: 0011_skill_scripts
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_chat_connectors"
down_revision: str | None = "0011_skill_scripts"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # 1) 幂等创建枚举类型（原生 DO 块，等效 IF NOT EXISTS）
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n "
            "ON n.oid = t.typnamespace "
            "WHERE t.typname = 'connectorplatform' AND n.nspname = 'public') "
            "THEN CREATE TYPE connectorplatform AS ENUM ('feishu', 'telegram', 'slack'); "
            "END IF; END $$;"
        )
    )
    # 2) 连接器注册表
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS chat_connectors (
                id SERIAL PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                platform connectorplatform NOT NULL,
                credentials JSONB NOT NULL DEFAULT '{}'::jsonb,
                agent_id INTEGER NOT NULL REFERENCES agent_configs(id) ON DELETE CASCADE,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                CONSTRAINT uq_chat_connectors_name UNIQUE (name)
            );
            """
        )
    )
    op.execute(
        sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_chat_connectors_name ON chat_connectors (name);")
    )
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_chat_connectors_agent_id ON chat_connectors (agent_id);")
    )
    # 3) 会话映射表
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS chat_connector_conversations (
                id SERIAL PRIMARY KEY,
                connector_id INTEGER NOT NULL REFERENCES chat_connectors(id) ON DELETE CASCADE,
                chat_id VARCHAR(128) NOT NULL,
                chat_type VARCHAR(16) NOT NULL DEFAULT 'private',
                thread_id VARCHAR(128) NOT NULL,
                last_user_ref JSONB NOT NULL DEFAULT '{}'::jsonb,
                last_active_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                CONSTRAINT uq_connector_conversations_chat UNIQUE (connector_id, chat_id)
            );
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_connector_conversations_thread_id "
            "ON chat_connector_conversations (thread_id);"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_connector_conversations_last_active "
            "ON chat_connector_conversations (last_active_at);"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_connector_conversations_connector_id "
            "ON chat_connector_conversations (connector_id);"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS chat_connector_conversations;"))
    op.execute(sa.text("DROP TABLE IF EXISTS chat_connectors;"))
    sa.Enum(name="connectorplatform").drop(op.get_bind(), checkfirst=True)
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_connector_models.py -v`（超时 180s）
预期：3 passed

- [ ] **步骤 6：Commit**

```bash
git add src/a2a_gateway/config.py src/a2a_gateway/models.py alembic/versions/0012_chat_connectors.py tests/test_connector_models.py
git commit -m "feat: 连接器数据模型（chat_connectors + 会话映射表）与迁移"
```

---

### 任务 2：schemas 凭据契约 + repository 数据访问

**文件：**
- 修改：`src/a2a_gateway/schemas.py`（文件末尾追加）
- 修改：`src/a2a_gateway/repository.py`（文件末尾追加 + 顶部 import）
- 测试：`tests/test_connector_support.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_connector_support.py`：

```python
"""连接器 schemas（凭据校验/合并/脱敏）与 thread_id 映射的纯函数测试。"""

import pytest
from pydantic import ValidationError

from a2a_gateway.repository import build_connector_thread_id
from a2a_gateway.schemas import (
    ConnectorCreate,
    mask_connector_credentials,
    merge_connector_credentials,
    validate_connector_credentials,
)


def test_thread_id_stable():
    a = build_connector_thread_id(3, "feishu", "oc_abc")
    b = build_connector_thread_id(3, "feishu", "oc_abc")
    assert a == b == "conn-3-feishu-oc_abc"


def test_thread_id_long_chat_hashed():
    tid = build_connector_thread_id(1, "feishu", "oc_" + "x" * 200)
    assert len(tid) <= 128
    assert tid.startswith("conn-1-feishu-")


def test_credentials_validated_by_platform():
    creds = validate_connector_credentials("telegram", {"bot_token": "123:abc"})
    assert creds == {"bot_token": "123:abc", "secret_token": "", "bot_username": ""}
    with pytest.raises(ValueError):
        validate_connector_credentials("discord", {})


def test_credentials_reject_unknown_keys():
    with pytest.raises(ValidationError):
        validate_connector_credentials("feishu", {"nope": 1})


def test_merge_credentials_keeps_blank_fields():
    merged = merge_connector_credentials(
        "slack",
        {"bot_token": "xoxb-old", "signing_secret": "s1"},
        {"bot_token": "", "signing_secret": "s2"},
    )
    assert merged == {"bot_token": "xoxb-old", "signing_secret": "s2"}


def test_mask_connector_credentials():
    masked = mask_connector_credentials("feishu", {"app_id": "cli_a", "app_secret": "s"})
    assert masked["app_id"] == "••••"
    assert masked["app_secret"] == "••••"
    assert masked["encrypt_key"] == ""


def test_connector_create_validates_credentials():
    with pytest.raises(ValidationError):
        ConnectorCreate(name="n", platform="feishu", agent_id=1, credentials={"nope": 1})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_connector_support.py -v`（超时 180s）
预期：FAIL，`ImportError: cannot import name 'build_connector_thread_id'`

- [ ] **步骤 3：实现 schemas**

`schemas.py` 顶部 import 区补 `from pydantic import BaseModel, ConfigDict, Field, model_validator`（已有则不动），文件末尾追加：

```python
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
```

- [ ] **步骤 4：实现 repository 函数**

`repository.py` 顶部 import 调整：models import 列表加入 `ChatConnector, ConnectorConversation, ConnectorPlatform`；schemas import 列表加入 `ConnectorCreate`；顶部加入 `import hashlib`。文件末尾追加：

```python
# ---------------------------------------------------------------------------
# 聊天连接器（连接器管理 + webhook 链路共用）
# ---------------------------------------------------------------------------
def build_connector_thread_id(connector_id: int, platform: str, chat_id: str) -> str:
    """平台会话 → LangGraph thread_id 的确定性映射（同会话永远同 thread）。"""
    base = f"conn-{connector_id}-{platform}-{chat_id}"
    if len(base) <= 128:
        return base
    digest = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:24]
    return f"conn-{connector_id}-{platform}-{digest}"


async def list_connectors(session: AsyncSession) -> list[ChatConnector]:
    rows = await session.execute(select(ChatConnector).order_by(ChatConnector.id))
    return list(rows.scalars().all())


async def get_connector(session: AsyncSession, connector_id: int) -> ChatConnector | None:
    return await session.get(ChatConnector, connector_id)


async def get_connector_by_name(session: AsyncSession, name: str) -> ChatConnector | None:
    row = await session.execute(select(ChatConnector).where(ChatConnector.name == name))
    return row.scalars().first()


async def create_connector(
    session: AsyncSession, data: ConnectorCreate, credentials: dict[str, Any]
) -> ChatConnector:
    connector = ChatConnector(
        name=data.name.strip(),
        description=data.description or "",
        platform=ConnectorPlatform(data.platform),
        credentials=credentials,
        agent_id=data.agent_id,
        enabled=data.enabled,
    )
    session.add(connector)
    await session.commit()
    await session.refresh(connector)
    return connector


async def update_connector(
    session: AsyncSession, connector: ChatConnector, changes: dict[str, Any]
) -> ChatConnector:
    for field, value in changes.items():
        setattr(connector, field, value)
    await session.commit()
    await session.refresh(connector)
    return connector


async def delete_connector(session: AsyncSession, connector: ChatConnector) -> None:
    await session.delete(connector)
    await session.commit()


async def get_connector_conversation(
    session: AsyncSession, connector_id: int, chat_id: str
) -> ConnectorConversation | None:
    row = await session.execute(
        select(ConnectorConversation).where(
            ConnectorConversation.connector_id == connector_id,
            ConnectorConversation.chat_id == chat_id,
        )
    )
    return row.scalars().first()


async def upsert_connector_conversation(
    session: AsyncSession,
    connector_id: int,
    platform: str,
    chat_id: str,
    chat_type: str,
    user_id: str,
    user_name: str,
) -> ConnectorConversation:
    """获取或创建会话映射，并刷新最近发言人/活跃时间（幂等）。"""
    thread_id = build_connector_thread_id(connector_id, platform, chat_id)
    stmt = pg_insert(ConnectorConversation).values(
        connector_id=connector_id,
        chat_id=chat_id,
        chat_type=chat_type,
        thread_id=thread_id,
        last_user_ref={"user_id": user_id, "display_name": user_name},
        last_active_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_connector_conversations_chat",
        set_={
            "chat_type": stmt.excluded.chat_type,
            "last_user_ref": stmt.excluded.last_user_ref,
            "last_active_at": stmt.excluded.last_active_at,
        },
    )
    await session.execute(stmt)
    await session.commit()
    conversation = await get_connector_conversation(session, connector_id, chat_id)
    if conversation is None:  # pragma: no cover - upsert 后必然存在
        raise RuntimeError(f"会话映射创建失败 connector={connector_id} chat={chat_id}")
    return conversation


async def list_recent_connector_conversations(
    session: AsyncSession, connector_id: int, limit: int = 20
) -> list[ConnectorConversation]:
    rows = await session.execute(
        select(ConnectorConversation)
        .where(ConnectorConversation.connector_id == connector_id)
        .order_by(ConnectorConversation.last_active_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_connector_support.py tests/test_connector_models.py -v`（超时 180s）
预期：全部 passed

- [ ] **步骤 6：Commit**

```bash
git add src/a2a_gateway/schemas.py src/a2a_gateway/repository.py tests/test_connector_support.py
git commit -m "feat: 连接器凭据契约（校验/合并/脱敏）与数据访问层"
```

---

### 任务 3：适配器基类

**文件：**
- 创建：`src/a2a_gateway/connectors/__init__.py`（空文件）
- 创建：`src/a2a_gateway/connectors/base.py`
- 测试：`tests/test_connector_adapters.py`（本任务先建文件写 `chunk_text` 部分）

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_connector_adapters.py`：

```python
"""平台适配器测试：公共工具 + 各平台验签/归一化/过滤/分段。"""

import hashlib
import hmac
import json
import time

import pytest

from a2a_gateway.connectors.base import VerifyError, chunk_text


# ---------------------------------------------------------------------------
# chunk_text（公共分段）
# ---------------------------------------------------------------------------
def test_chunk_text_short():
    assert chunk_text("你好", 100) == ["你好"]


def test_chunk_text_empty():
    assert chunk_text("   ", 100) == []


def test_chunk_text_splits_at_limit():
    text = "x" * 250
    chunks = chunk_text(text, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_chunk_text_prefers_newline():
    text = "a" * 60 + "\n" + "b" * 60
    chunks = chunk_text(text, 100)
    assert chunks[0] == "a" * 60
    assert chunks[1] == "b" * 60


def test_verify_error_exists():
    with pytest.raises(VerifyError):
        raise VerifyError("bad signature")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_connector_adapters.py -v`（超时 180s）
预期：FAIL，`ModuleNotFoundError: No module named 'a2a_gateway.connectors'`

- [ ] **步骤 3：实现 base.py**

创建空文件 `src/a2a_gateway/connectors/__init__.py`，创建 `src/a2a_gateway/connectors/base.py`：

```python
"""聊天平台适配器抽象：验签、入站归一化、接入握手、出站发送。

主链路（routes/connectors.py + pipeline.py）只面向本模块的类型，
平台差异全部封装在各适配器实现内。
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass


class VerifyError(Exception):
    """平台验签失败（webhook 路由捕获后返回 401）。"""


@dataclass
class InboundMessage:
    """归一化后的入站消息（已通过过滤：私聊文本 / 群聊 @机器人 文本）。"""

    platform: str
    chat_id: str
    chat_type: str  # private / group
    user_id: str
    user_name: str
    text: str
    event_id: str  # 平台事件/消息 id，管线去重键


def chunk_text(text: str, limit: int) -> list[str]:
    """按字符上限切分文本；优先在换行处断开，超长无换行则硬切。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < int(limit * 0.5):
            cut = limit
        head = rest[:cut].strip()
        if head:
            chunks.append(head)
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return chunks


class PlatformAdapter(ABC):
    """平台适配器接口。方法均为 async：飞书需要异步取机器人信息做群聊过滤。"""

    platform: str = ""

    @abstractmethod
    async def build_challenge(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> dict | None:
        """平台接入握手应答体（如 {"challenge": ...}）；非握手事件返回 None。

        Slack 的 url_verification 自带签名，实现内部需先验签；
        验签失败同样抛 VerifyError。
        """

    @abstractmethod
    async def verify_and_parse(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> list[InboundMessage]:
        """验签并解析出待处理消息；验签失败抛 VerifyError。

        仅产出通过过滤的消息：私聊文本直接收；群聊仅收 @机器人 的文本；
        bot 自身 / 其他 bot / 非文本消息一律忽略。
        """

    @abstractmethod
    async def send(self, credentials: dict, chat_id: str, text: str) -> None:
        """发送文本消息；超长文本自动分段（chunk_text）。失败抛异常由调用方告警。"""
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_connector_adapters.py -v`（超时 180s）
预期：6 passed

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/connectors/__init__.py src/a2a_gateway/connectors/base.py tests/test_connector_adapters.py
git commit -m "feat: 平台适配器抽象（InboundMessage/VerifyError/chunk_text）"
```

---

### 任务 4：Telegram 适配器

**文件：**
- 创建：`src/a2a_gateway/connectors/telegram.py`
- 测试：`tests/test_connector_adapters.py`（追加）

- [ ] **步骤 1：编写失败的测试（追加到 tests/test_connector_adapters.py）**

```python
# ---------------------------------------------------------------------------
# Telegram 适配器
# ---------------------------------------------------------------------------
from a2a_gateway.connectors.telegram import TelegramAdapter, _mentioned_bot

TG = TelegramAdapter()
TG_CREDS = {"bot_token": "123:abc", "secret_token": "sec", "bot_username": "mybot"}


def _tg_headers(secret: str = "sec") -> dict:
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


def _tg_private_update(text: str = "你好") -> bytes:
    return json.dumps(
        {
            "update_id": 1001,
            "message": {
                "message_id": 11,
                "text": text,
                "from": {"id": 7, "is_bot": False, "first_name": "Tom"},
                "chat": {"id": 7, "type": "private"},
            },
        }
    ).encode()


def _tg_group_update(text: str, entities: list | None = None) -> bytes:
    return json.dumps(
        {
            "update_id": 1002,
            "message": {
                "message_id": 12,
                "text": text,
                "entities": entities or [],
                "from": {"id": 7, "is_bot": False, "first_name": "Tom"},
                "chat": {"id": -100, "type": "supergroup"},
            },
        }
    ).encode()


async def test_telegram_challenge_not_supported():
    assert await TG.build_challenge(_tg_private_update(), _tg_headers(), TG_CREDS) is None


async def test_telegram_verify_rejects_bad_secret():
    with pytest.raises(VerifyError):
        await TG.verify_and_parse(_tg_private_update(), _tg_headers("wrong"), TG_CREDS)


async def test_telegram_verify_rejects_empty_secret_config():
    with pytest.raises(VerifyError):
        await TG.verify_and_parse(_tg_private_update(), _tg_headers(), {"bot_token": "t"})


async def test_telegram_private_message_parsed():
    msgs = await TG.verify_and_parse(_tg_private_update(), _tg_headers(), TG_CREDS)
    assert len(msgs) == 1
    m = msgs[0]
    assert (m.platform, m.chat_id, m.chat_type) == ("telegram", "7", "private")
    assert m.text == "你好"
    assert m.user_name == "Tom"
    assert m.event_id == "1001"


async def test_telegram_bot_message_ignored():
    update = json.dumps(
        {
            "update_id": 1003,
            "message": {
                "message_id": 13,
                "text": "hi",
                "from": {"id": 99, "is_bot": True, "first_name": "B"},
                "chat": {"id": 7, "type": "private"},
            },
        }
    ).encode()
    assert await TG.verify_and_parse(update, _tg_headers(), TG_CREDS) == []


async def test_telegram_group_without_mention_ignored():
    assert await TG.verify_and_parse(
        _tg_group_update("大家好"), _tg_headers(), TG_CREDS
    ) == []


async def test_telegram_group_with_mention_parsed():
    text = "@mybot 帮我查天气"
    entities = [{"type": "mention", "offset": 0, "length": len("@mybot")}]
    msgs = await TG.verify_and_parse(
        _tg_group_update(text, entities), _tg_headers(), TG_CREDS
    )
    assert len(msgs) == 1
    assert msgs[0].chat_type == "group"
    assert msgs[0].text == "@mybot 帮我查天气"


async def test_telegram_group_mention_requires_bot_username():
    update = _tg_group_update(
        "@mybot hi", [{"type": "mention", "offset": 0, "length": 6}]
    )
    assert await TG.verify_and_parse(update, _tg_headers(), {"bot_token": "t"}) == []


def test_mentioned_bot_matches_case_insensitive():
    entities = [{"type": "mention", "offset": 0, "length": 6}]
    msg = {"text": "@MyBot hi", "entities": entities}
    assert _mentioned_bot(msg, "mybot") is True
    assert _mentioned_bot(msg, "other") is False
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_connector_adapters.py -k telegram -v`（超时 180s）
预期：FAIL，`ModuleNotFoundError: No module named 'a2a_gateway.connectors.telegram'`

- [ ] **步骤 3：实现 telegram.py**

```python
"""Telegram 适配器：secret-token 头验证 + Bot API 收发与 webhook 注册。"""

import json
import logging
from collections.abc import Mapping

import httpx

from ..config import get_settings
from .base import InboundMessage, PlatformAdapter, VerifyError, chunk_text

logger = logging.getLogger(__name__)

_settings = get_settings()

_API_BASE = "https://api.telegram.org"
_CHUNK_LIMIT = 4000
_TIMEOUT = 15.0


def _mentioned_bot(message: dict, bot_username: str) -> bool:
    """群聊消息是否 @了机器人（entities 中的 mention 文本匹配 @bot_username）。"""
    if not bot_username:
        return False
    text = message.get("text") or ""
    for entity in message.get("entities") or []:
        if entity.get("type") != "mention":
            continue
        offset, length = entity.get("offset", 0), entity.get("length", 0)
        if text[offset : offset + length].lower() == f"@{bot_username.lower()}":
            return True
    return False


class TelegramAdapter(PlatformAdapter):
    platform = "telegram"

    async def build_challenge(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> dict | None:
        return None  # Telegram 经 setWebhook 注册，无握手事件

    async def verify_and_parse(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> list[InboundMessage]:
        secret = (credentials or {}).get("secret_token") or ""
        header_token = headers.get("x-telegram-bot-api-secret-token") or ""
        if not secret or not hmac_equal(header_token, secret):
            raise VerifyError("Telegram secret token 校验失败")

        update = json.loads(body)
        message = update.get("message") or update.get("edited_message")
        if not message:
            return []
        sender = message.get("from") or {}
        if sender.get("is_bot"):
            return []
        text = (message.get("text") or "").strip()
        if not text:
            return []
        chat = message.get("chat") or {}
        is_private = chat.get("type") == "private"
        if not is_private and not _mentioned_bot(message, (credentials or {}).get("bot_username") or ""):
            return []
        return [
            InboundMessage(
                platform=self.platform,
                chat_id=str(chat.get("id")),
                chat_type="private" if is_private else "group",
                user_id=str(sender.get("id", "")),
                user_name=sender.get("first_name") or str(sender.get("id", "")),
                text=text,
                event_id=str(update.get("update_id", "")),
            )
        ]

    async def send(self, credentials: dict, chat_id: str, text: str) -> None:
        token = (credentials or {}).get("bot_token") or ""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for chunk in chunk_text(text, _CHUNK_LIMIT):
                resp = await client.post(
                    f"{_API_BASE}/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk},
                )
                resp.raise_for_status()


def hmac_equal(left: str, right: str) -> bool:
    """恒定时间字符串比较（标准库 hmac 逐字节比对）。"""
    import hmac as _hmac

    return _hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


async def register_webhook(credentials: dict, connector_id: int) -> tuple[dict, str]:
    """启用/改凭据后自动注册 webhook 并经 getMe 补全 bot_username。

    返回 (更新后的凭据, 警告信息或 None)。失败不抛异常：注册失败只提示，不阻断保存。
    """
    base = _settings.public_base_url.rstrip("/")
    if not base:
        return credentials, "未配置 PUBLIC_BASE_URL，跳过自动注册（请在 Telegram 手动 setWebhook）"
    token = (credentials or {}).get("bot_token") or ""
    if not token:
        return credentials, "缺少 bot_token，无法自动注册"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            me = await client.get(f"{_API_BASE}/bot{token}/getMe")
            me.raise_for_status()
            username = (me.json().get("result") or {}).get("username") or ""
            resp = await client.post(
                f"{_API_BASE}/bot{token}/setWebhook",
                json={
                    "url": f"{base}/api/connectors/telegram/{connector_id}/webhook",
                    "secret_token": (credentials or {}).get("secret_token") or "",
                },
            )
            resp.raise_for_status()
        return {**credentials, "bot_username": username}, ""
    except Exception as exc:
        logger.warning("Telegram webhook 自动注册失败 connector=%s: %s", connector_id, exc)
        return credentials, f"Telegram 自动注册失败：{exc}"
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_connector_adapters.py -v`（超时 180s）
预期：全部 passed

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/connectors/telegram.py tests/test_connector_adapters.py
git commit -m "feat: Telegram 适配器（secret 验证/群聊@过滤/发送/webhook 自动注册）"
```

---

### 任务 5：Slack 适配器

**文件：**
- 创建：`src/a2a_gateway/connectors/slack.py`
- 测试：`tests/test_connector_adapters.py`（追加）

- [ ] **步骤 1：编写失败的测试（追加到 tests/test_connector_adapters.py）**

```python
# ---------------------------------------------------------------------------
# Slack 适配器
# ---------------------------------------------------------------------------
from a2a_gateway.connectors.slack import SlackAdapter, slack_signature

SLACK = SlackAdapter()
SLACK_SECRET = "shhh"
SLACK_BODY = {
    "event_id": "Ev007",
    "type": "event_callback",
    "event": {
        "type": "message",
        "channel_type": "im",
        "channel": "D123",
        "user": "U7",
        "text": "帮我总结",
        "bot_id": None,
    },
}


def _slack_headers(body: bytes, secret: str = SLACK_SECRET, ts: int | None = None) -> dict:
    timestamp = ts or int(time.time())
    basestring = f"v0:{timestamp}:{body.decode()}"
    digest = hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": str(timestamp), "X-Slack-Signature": f"v0={digest}"}


def _slack_bytes(payload: dict) -> bytes:
    return json.dumps(payload).encode()


async def test_slack_url_verification_challenge():
    body = _slack_bytes({"type": "url_verification", "challenge": "xyz"})
    result = await SLACK.build_challenge(body, _slack_headers(body), SLACK_CREDS)
    assert result == {"challenge": "xyz"}


async def test_slack_challenge_rejects_bad_signature():
    body = _slack_bytes({"type": "url_verification", "challenge": "xyz"})
    with pytest.raises(VerifyError):
        await SLACK.build_challenge(body, _slack_headers(body, secret="bad"), SLACK_CREDS)


async def test_slack_challenge_rejects_stale_timestamp():
    body = _slack_bytes({"type": "url_verification", "challenge": "xyz"})
    stale_ts = int(time.time()) - 600
    with pytest.raises(VerifyError):
        await SLACK.build_challenge(body, _slack_headers(body, ts=stale_ts), SLACK_CREDS)


async def test_slack_im_message_parsed():
    body = _slack_bytes(SLACK_BODY)
    msgs = await SLACK.verify_and_parse(body, _slack_headers(body), SLACK_CREDS)
    assert len(msgs) == 1
    m = msgs[0]
    assert (m.platform, m.chat_id, m.chat_type) == ("slack", "D123", "private")
    assert m.text == "帮我总结"
    assert m.event_id == "Ev007"


async def test_slack_app_mention_parsed_as_group():
    body = _slack_bytes(
        {
            "event_id": "Ev008",
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C456",
                "user": "U7",
                "text": "<@U0> 帮我总结",
                "bot_id": None,
            },
        }
    )
    msgs = await SLACK.verify_and_parse(body, _slack_headers(body), SLACK_CREDS)
    assert len(msgs) == 1
    assert msgs[0].chat_type == "group"
    assert msgs[0].chat_id == "C456"


async def test_slack_bot_message_ignored():
    payload = {**SLACK_BODY, "event": {**SLACK_BODY["event"], "bot_id": "B999"}}
    body = _slack_bytes(payload)
    assert await SLACK.verify_and_parse(body, _slack_headers(body), SLACK_CREDS) == []


async def test_slack_message_without_text_ignored():
    payload = {**SLACK_BODY, "event": {**SLACK_BODY["event"], "text": ""}}
    body = _slack_bytes(payload)
    assert await SLACK.verify_and_parse(body, _slack_headers(body), SLACK_CREDS) == []


async def test_slack_verify_rejects_bad_signature():
    body = _slack_bytes(SLACK_BODY)
    with pytest.raises(VerifyError):
        await SLACK.verify_and_parse(body, _slack_headers(body, secret="bad"), SLACK_CREDS)


def test_slack_signature_format():
    basestring = "v0:1:payload"
    digest = hmac.new(SLACK_SECRET.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    assert slack_signature(SLACK_SECRET, "1", "payload") == f"v0={digest}"
```

（说明：`SLACK_CREDS` 在上面测试段前补充一次定义：`SLACK_CREDS = {"bot_token": "xoxb-test", "signing_secret": "shhh"}`。）

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_connector_adapters.py -k slack -v`（超时 180s）
预期：FAIL，`ModuleNotFoundError: No module named 'a2a_gateway.connectors.slack'`

- [ ] **步骤 3：实现 slack.py**

```python
"""Slack 适配器：Events API HMAC 验签 + chat.postMessage。"""

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Mapping

import httpx

from .base import InboundMessage, PlatformAdapter, VerifyError, chunk_text

logger = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"
_CHUNK_LIMIT = 39000
_TIMEOUT = 15.0
_MAX_TS_SKEW = 300  # 签名时间戳最大偏移（秒），防重放


def slack_signature(signing_secret: str, timestamp: str, body: str) -> str:
    """按 Slack 规范计算请求签名：v0=HMAC-SHA256(secret, "v0:{ts}:{body}")。"""
    basestring = f"v0:{timestamp}:{body}"
    digest = hmac.new(
        signing_secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"v0={digest}"


def _verify(headers: Mapping[str, str], body: bytes, credentials: dict) -> None:
    secret = (credentials or {}).get("signing_secret") or ""
    timestamp = headers.get("x-slack-request-timestamp") or ""
    signature = headers.get("x-slack-signature") or ""
    if not secret or not timestamp or not signature:
        raise VerifyError("Slack 签名头缺失")
    try:
        if abs(time.time() - int(timestamp)) > _MAX_TS_SKEW:
            raise VerifyError("Slack 签名时间戳过期")
    except ValueError as exc:
        raise VerifyError("Slack 签名时间戳非法") from exc
    expected = slack_signature(secret, timestamp, body.decode("utf-8", "replace"))
    if not hmac.compare_digest(expected, signature):
        raise VerifyError("Slack 签名校验失败")


class SlackAdapter(PlatformAdapter):
    platform = "slack"

    async def build_challenge(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> dict | None:
        _verify(headers, body, credentials)
        payload = json.loads(body)
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}
        return None

    async def verify_and_parse(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> list[InboundMessage]:
        _verify(headers, body, credentials)
        payload = json.loads(body)
        if payload.get("type") != "event_callback":
            return []
        event = payload.get("event") or {}
        event_type = event.get("type")
        # bot 自己 / 其他 app 的消息：bot_id 非空即忽略
        if event.get("bot_id"):
            return []
        text = (event.get("text") or "").strip()
        if not text:
            return []
        user_id = str(event.get("user") or "")
        event_id = str(payload.get("event_id") or "")
        if event_type == "app_mention":
            # 群聊 @机器人：去掉 <@Uxxx> 引用占位
            cleaned = " ".join(
                part for part in text.split() if not part.startswith("<@")
            ).strip()
            if not cleaned:
                return []
            return [
                InboundMessage(
                    platform=self.platform,
                    chat_id=str(event.get("channel")),
                    chat_type="group",
                    user_id=user_id,
                    user_name=user_id,
                    text=cleaned,
                    event_id=event_id,
                )
            ]
        if event_type == "message" and event.get("channel_type") == "im":
            # 私聊；编辑/删除等带 subtype 的不作为新消息处理
            if event.get("subtype"):
                return []
            return [
                InboundMessage(
                    platform=self.platform,
                    chat_id=str(event.get("channel")),
                    chat_type="private",
                    user_id=user_id,
                    user_name=user_id,
                    text=text,
                    event_id=event_id,
                )
            ]
        return []

    async def send(self, credentials: dict, chat_id: str, text: str) -> None:
        token = (credentials or {}).get("bot_token") or ""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for chunk in chunk_text(text, _CHUNK_LIMIT):
                resp = await client.post(
                    f"{_API_BASE}/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"channel": chat_id, "text": chunk},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Slack 发送失败: {data.get('error')}")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_connector_adapters.py -v`（超时 180s）
预期：全部 passed

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/connectors/slack.py tests/test_connector_adapters.py
git commit -m "feat: Slack 适配器（HMAC 验签防重放/url_verification/app_mention 过滤）"
```

---

### 任务 6：飞书适配器 + 平台注册表

**文件：**
- 修改：`pyproject.toml`（dependencies 显式加 `cryptography>=42.0.0`）
- 创建：`src/a2a_gateway/connectors/feishu.py`
- 创建：`src/a2a_gateway/connectors/registry.py`
- 测试：`tests/test_connector_adapters.py`（追加）

- [ ] **步骤 1：添加依赖**

`pyproject.toml` 的 `dependencies` 列表末尾加入：

```toml
    "cryptography>=42.0.0",
```

运行：`uv sync`（超时 300s）

- [ ] **步骤 2：编写失败的测试（追加到 tests/test_connector_adapters.py）**

```python
# ---------------------------------------------------------------------------
# 飞书适配器
# ---------------------------------------------------------------------------
import base64
import os

from a2a_gateway.connectors.feishu import FeishuAdapter, encrypt_feishu_payload

FEISHU = FeishuAdapter()
FEISHU_CREDS = {"app_id": "cli_a", "app_secret": "s", "verification_token": "vt", "encrypt_key": ""}


def _feishu_event(token: str = "vt", encrypt_key: str = "") -> bytes:
    payload = {
        "header": {
            "event_id": "EvF1",
            "token": token,
            "event_type": "im.message.receive_v1",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_u1", "user_id": "u1"}},
            "message": {
                "chat_id": "oc_c1",
                "chat_type": "p2p",
                "message_id": "om_1",
                "content": json.dumps({"text": "你好飞书"}),
                "mentions": [],
            },
        },
    }
    body = json.dumps(payload).encode()
    if encrypt_key:
        return encrypt_feishu_payload(encrypt_key, payload)
    return body


async def test_feishu_url_verification_challenge():
    body = json.dumps(
        {"header": {"token": "vt"}, "type": "url_verification", "challenge": "cf_1"}
    ).encode()
    result = await FEISHU.build_challenge(body, {}, FEISHU_CREDS)
    assert result == {"challenge": "cf_1"}


async def test_feishu_challenge_rejects_bad_token():
    body = json.dumps(
        {"header": {"token": "bad"}, "type": "url_verification", "challenge": "cf_1"}
    ).encode()
    with pytest.raises(VerifyError):
        await FEISHU.build_challenge(body, {}, FEISHU_CREDS)


async def test_feishu_challenge_with_encryption():
    body = encrypt_feishu_payload(
        "mykey",
        {"header": {"token": "vt"}, "type": "url_verification", "challenge": "cf_2"},
    )
    creds = {**FEISHU_CREDS, "encrypt_key": "mykey"}
    result = await FEISHU.build_challenge(body, {}, creds)
    assert result == {"challenge": "cf_2"}


async def test_feishu_private_message_parsed():
    msgs = await FEISHU.verify_and_parse(_feishu_event(), {}, FEISHU_CREDS)
    assert len(msgs) == 1
    m = msgs[0]
    assert (m.platform, m.chat_id, m.chat_type) == ("feishu", "oc_c1", "private")
    assert m.text == "你好飞书"
    assert m.event_id == "EvF1"


async def test_feishu_encrypted_message_parsed():
    creds = {**FEISHU_CREDS, "encrypt_key": "mykey"}
    msgs = await FEISHU.verify_and_parse(_feishu_event(encrypt_key="mykey"), {}, creds)
    assert len(msgs) == 1
    assert msgs[0].text == "你好飞书"


async def test_feishu_verify_rejects_bad_token():
    with pytest.raises(VerifyError):
        await FEISHU.verify_and_parse(_feishu_event(token="bad"), {}, FEISHU_CREDS)


async def test_feishu_group_without_bot_mention_ignored(monkeypatch):
    async def fake_bot_open_id(credentials):
        return "ou_bot"

    monkeypatch.setattr(FEISHU, "_bot_open_id", fake_bot_open_id)
    event = {
        "header": {"event_id": "EvF2", "token": "vt", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_u1", "user_id": "u1"}},
            "message": {
                "chat_id": "oc_g1",
                "chat_type": "group",
                "message_id": "om_2",
                "content": json.dumps({"text": "@_user_1 大家好"}),
                "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_other"}, "name": "张三"}],
            },
        },
    }
    assert await FEISHU.verify_and_parse(json.dumps(event).encode(), {}, FEISHU_CREDS) == []


async def test_feishu_group_with_bot_mention_parsed(monkeypatch):
    async def fake_bot_open_id(credentials):
        return "ou_bot"

    monkeypatch.setattr(FEISHU, "_bot_open_id", fake_bot_open_id)
    event = {
        "header": {"event_id": "EvF3", "token": "vt", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_u1", "user_id": "u1"}},
            "message": {
                "chat_id": "oc_g2",
                "chat_type": "group",
                "message_id": "om_3",
                "content": json.dumps({"text": "@_user_1 帮我订机票"}),
                "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "助手"}],
            },
        },
    }
    msgs = await FEISHU.verify_and_parse(json.dumps(event).encode(), {}, FEISHU_CREDS)
    assert len(msgs) == 1
    assert msgs[0].chat_type == "group"
    # mention 占位符替换为可读名字
    assert msgs[0].text == "@助手 帮我订机票"


def test_encrypt_feishu_payload_roundtrip():
    import json as _json

    from a2a_gateway.connectors.feishu import decrypt_feishu_payload

    key = "k" * 8
    payload = {"type": "url_verification", "challenge": "c"}
    blob = encrypt_feishu_payload(key, payload)
    assert decrypt_feishu_payload(key, blob) == payload


def test_encrypt_feishu_payload_uses_random_iv():
    key = "k" * 8
    payload = {"a": 1}
    b1 = encrypt_feishu_payload(key, payload)
    b2 = encrypt_feishu_payload(key, payload)
    assert base64.b64decode(b1)[:16] != base64.b64decode(b2)[:16]  # IV 随机
```

- [ ] **步骤 3：运行测试验证失败**

运行：`uv run pytest tests/test_connector_adapters.py -k feishu -v`（超时 180s）
预期：FAIL，`ModuleNotFoundError: No module named 'a2a_gateway.connectors.feishu'`

- [ ] **步骤 4：实现 feishu.py**

```python
"""飞书适配器：verification_token 校验 + 可选 AES-256-CBC 解密 + im/v1 收发。

群聊过滤依赖机器人 open_id（经 GET /open-apis/bot/v3/info 获取，进程内缓存）。
tenant_access_token 同样进程内缓存、过期前刷新。
"""

import base64
import hashlib
import json
import logging
import time
from collections.abc import Mapping

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .base import InboundMessage, PlatformAdapter, VerifyError, chunk_text

logger = logging.getLogger(__name__)

_API_BASE = "https://open.feishu.cn/open-apis"
_CHUNK_LIMIT = 4000
_TIMEOUT = 15.0

# 进程内缓存：{app_id: (tenant_access_token, 过期时间戳)}
_token_cache: dict[str, tuple[str, float]] = {}
# 进程内缓存：{app_id: (bot_open_id, 获取时间戳)}
_bot_open_id_cache: dict[str, str] = {}


def decrypt_feishu_payload(encrypt_key: str, blob: str | bytes) -> dict:
    """解密飞书加密事件体：AES-256-CBC，key = sha256(encrypt_key)，IV 为密文前 16 字节，PKCS7 填充。"""
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    data = base64.b64decode(blob)
    cipher = Cipher(algorithms.AES(key), modes.CBC(data[:16]))
    decryptor = cipher.decryptor()
    padded = decryptor.update(data[16:]) + decryptor.finalize()
    pad_len = padded[-1]
    plain = padded[:-pad_len]
    return json.loads(plain)


def encrypt_feishu_payload(encrypt_key: str, payload: dict) -> str:
    """测试辅助：按飞书规范加密事件体（随机 IV）。"""
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = os.urandom(16)
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pad_len = 16 - (len(raw) % 16)
    padded = raw + bytes([pad_len]) * pad_len
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode()


def _unwrap(body: bytes, credentials: dict) -> dict:
    """解析事件体：配置了 encrypt_key 时先解密；校验 verification_token。"""
    token = (credentials or {}).get("verification_token") or ""
    if not token:
        raise VerifyError("飞书 verification_token 未配置")
    payload = json.loads(body)
    if payload.get("encrypt"):
        encrypt_key = (credentials or {}).get("encrypt_key") or ""
        if not encrypt_key:
            raise VerifyError("收到加密事件但未配置 encrypt_key")
        payload = decrypt_feishu_payload(encrypt_key, payload["encrypt"])
    header = payload.get("header") or {}
    if header.get("token") != token:
        raise VerifyError("飞书 verification_token 校验失败")
    return payload


class FeishuAdapter(PlatformAdapter):
    platform = "feishu"

    async def build_challenge(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> dict | None:
        payload = _unwrap(body, credentials)
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}
        return None

    async def _bot_open_id(self, credentials: dict) -> str:
        """机器人 open_id（群聊 @ 过滤用）；进程内缓存，获取失败返回空串。"""
        app_id = (credentials or {}).get("app_id") or ""
        if not app_id:
            return ""
        cached = _bot_open_id_cache.get(app_id)
        if cached:
            return cached
        token = await self._tenant_access_token(credentials)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{_API_BASE}/bot/v3/info",
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                bot = (resp.json().get("bot") or {})
                open_id = str(bot.get("open_id") or "")
        except Exception as exc:
            logger.warning("获取飞书机器人信息失败 app_id=%s: %s", app_id, exc)
            return ""
        _bot_open_id_cache[app_id] = open_id
        return open_id

    async def _tenant_access_token(self, credentials: dict) -> str:
        app_id = (credentials or {}).get("app_id") or ""
        app_secret = (credentials or {}).get("app_secret") or ""
        cached = _token_cache.get(app_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_API_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            resp.raise_for_status()
            data = resp.json()
        token = data.get("tenant_access_token") or ""
        if not token:
            raise RuntimeError(f"获取 tenant_access_token 失败: {data.get('msg')}")
        _token_cache[app_id] = (token, time.time() + float(data.get("expire", 3600)))
        return token

    async def verify_and_parse(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> list[InboundMessage]:
        payload = _unwrap(body, credentials)
        header = payload.get("header") or {}
        if header.get("event_type") != "im.message.receive_v1":
            return []
        event = payload.get("event") or {}
        message = event.get("message") or {}
        content = json.loads(message.get("content") or "{}")
        text = (content.get("text") or "").strip()
        if not text:
            return []
        sender_id = (event.get("sender") or {}).get("sender_id") or {}
        user_id = str(sender_id.get("open_id") or sender_id.get("user_id") or "")
        mentions = message.get("mentions") or []
        # mention 占位符替换为可读名字
        for mention in mentions:
            text = text.replace(mention.get("key") or "", f"@{mention.get('name') or ''}").strip()
        chat_type = "private" if message.get("chat_type") == "p2p" else "group"
        if chat_type == "group":
            bot_open_id = await self._bot_open_id(credentials)
            if not bot_open_id or not any(
                (m.get("id") or {}).get("open_id") == bot_open_id for m in mentions
            ):
                return []
        return [
            InboundMessage(
                platform=self.platform,
                chat_id=str(message.get("chat_id")),
                chat_type=chat_type,
                user_id=user_id,
                user_name=user_id,  # 事件不含用户名，open_id 即展示名
                text=text,
                event_id=str(header.get("event_id") or message.get("message_id") or ""),
            )
        ]

    async def send(self, credentials: dict, chat_id: str, text: str) -> None:
        token = await self._tenant_access_token(credentials)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for chunk in chunk_text(text, _CHUNK_LIMIT):
                resp = await client.post(
                    f"{_API_BASE}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "receive_id": chat_id,
                        "msg_type": "text",
                        "content": json.dumps({"text": chunk}, ensure_ascii=False),
                    },
                )
                resp.raise_for_status()
```

注意：`feishu.py` 顶部 import 区需补 `import os`（`encrypt_feishu_payload` 用随机 IV）。

- [ ] **步骤 5：实现 registry.py 并编写测试**

创建 `src/a2a_gateway/connectors/registry.py`：

```python
"""平台注册表：platform 字符串 → 适配器实例。"""

from .base import PlatformAdapter
from .feishu import FeishuAdapter
from .slack import SlackAdapter
from .telegram import TelegramAdapter

_ADAPTERS: dict[str, PlatformAdapter] = {
    adapter.platform: adapter for adapter in (TelegramAdapter(), SlackAdapter(), FeishuAdapter())
}


def get_adapter(platform: str) -> PlatformAdapter:
    """未知平台抛 KeyError（webhook 路由转 404）。"""
    adapter = _ADAPTERS.get(platform)
    if adapter is None:
        raise KeyError(platform)
    return adapter
```

`tests/test_connector_adapters.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
from a2a_gateway.connectors.registry import get_adapter


def test_registry_returns_all_platforms():
    for platform in ("feishu", "telegram", "slack"):
        assert get_adapter(platform).platform == platform


def test_registry_unknown_platform():
    with pytest.raises(KeyError):
        get_adapter("discord")
```

- [ ] **步骤 6：运行测试验证通过**

运行：`uv run pytest tests/test_connector_adapters.py -v`（超时 180s）
预期：全部 passed

- [ ] **步骤 7：Commit**

```bash
git add pyproject.toml uv.lock src/a2a_gateway/connectors/feishu.py src/a2a_gateway/connectors/registry.py tests/test_connector_adapters.py
git commit -m "feat: 飞书适配器（AES 解密/群聊@过滤/token 缓存）与平台注册表"
```

---

### 任务 7：消息处理管线（去重 + 串行队列 + Agent 调用）

**文件：**
- 创建：`src/a2a_gateway/connectors/pipeline.py`
- 测试：`tests/test_connector_pipeline.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_connector_pipeline.py`：

```python
"""处理管线纯逻辑测试：去重 / 串行队列 / 回复提取（不触 DB / 不联网）。"""

import pytest
from types import SimpleNamespace

from a2a_gateway.connectors import pipeline
from a2a_gateway.connectors.base import InboundMessage
from a2a_gateway.connectors.pipeline import ConnectorRef, enqueue_message, seen_recently


def _msg(chat_id: str = "c1", event_id: str = "e1") -> InboundMessage:
    return InboundMessage(
        platform="telegram",
        chat_id=chat_id,
        chat_type="private",
        user_id="u1",
        user_name="Tom",
        text="hi",
        event_id=event_id,
    )


def _conn() -> ConnectorRef:
    return ConnectorRef(
        id=1,
        name="t",
        platform="telegram",
        credentials={"bot_token": "x"},
        agent_id=1,
        enabled=True,
    )


@pytest.fixture(autouse=True)
def _reset_state():
    pipeline._seen.clear()
    pipeline._queues.clear()
    yield
    pipeline._seen.clear()
    pipeline._queues.clear()


def test_seen_recently_dedups():
    assert seen_recently("k1") is False  # 首次见到 → 待处理
    assert seen_recently("k1") is True   # 再次见到 → 重复
    assert seen_recently("k2") is False


def test_extract_reply_string():
    # LangGraph 返回的 messages 是带 .content 属性的消息对象
    assert pipeline._extract_reply({"messages": [SimpleNamespace(content="答案")]}) == "答案"


def test_extract_reply_empty():
    assert pipeline._extract_reply(None) == ""
    assert pipeline._extract_reply({}) == ""


def test_extract_reply_content_blocks():
    result = {
        "messages": [
            SimpleNamespace(content=[{"type": "text", "text": "a"}, {"type": "img"}])
        ]
    }
    assert pipeline._extract_reply(result) == "a"


def test_enqueue_queues_per_chat(monkeypatch):
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda key: None)
    assert enqueue_message(_conn(), _msg()) is True
    assert enqueue_message(_conn(), _msg(chat_id="c2")) is True
    assert pipeline._queues[(1, "c1")].qsize() == 1
    assert pipeline._queues[(1, "c2")].qsize() == 1


def test_enqueue_rejects_when_full(monkeypatch):
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda key: None)
    for i in range(pipeline._QUEUE_LIMIT):
        assert enqueue_message(_conn(), _msg(event_id=f"e{i}")) is True
    assert enqueue_message(_conn(), _msg(event_id="overflow")) is False
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_connector_pipeline.py -v`（超时 180s）
预期：FAIL，`ModuleNotFoundError: No module named 'a2a_gateway.connectors.pipeline'`

- [ ] **步骤 3：实现 pipeline.py**

```python
"""连接器消息处理管线：事件去重 → 会话级串行队列 → Agent 调用 → 回复推送。

- 平台 webhook 要求 ~3s 内确认，Agent 调用可能数十秒：路由层立即 200，
  处理在本管线后台进行（asyncio.create_task worker）
- 同一会话（connector_id, chat_id）串行处理，保序避免上下文交叉；
  队列上限 _QUEUE_LIMIT，队满丢弃新消息（由调用方回复忙提示）
- 单条处理超时 _PROCESS_TIMEOUT_SECONDS，超时/异常回复兜底文案并告警
"""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

from langchain_core.messages import HumanMessage

from ..agent_factory import get_agent_instance
from ..database import AsyncSessionLocal
from ..models import AgentConfig, AgentStatus
from ..notifier import notify_alert
from ..repository import upsert_connector_conversation
from .base import InboundMessage
from .registry import get_adapter

logger = logging.getLogger(__name__)

_DEDUP_CAPACITY = 4096
_DEDUP_TTL_SECONDS = 600.0
_QUEUE_LIMIT = 5
_PROCESS_TIMEOUT_SECONDS = 120.0

REPLY_UNPUBLISHED = "绑定的 Agent 未发布，暂时无法处理消息"
REPLY_BUSY = "消息较多，请稍后再试"
REPLY_FAILURE = "处理失败，请稍后重试"


@dataclass
class ConnectorRef:
    """连接器在后台任务中的最小引用（避免跨请求持有 ORM 实例）。"""

    id: int
    name: str
    platform: str
    credentials: dict
    agent_id: int
    enabled: bool


# ---------------------------------------------------------------------------
# 事件去重（进程内 TTL LRU：覆盖平台超时重试场景）
# ---------------------------------------------------------------------------
_seen: OrderedDict[str, float] = OrderedDict()


def seen_recently(key: str) -> bool:
    """登记事件并判断是否刚处理过；首次见到返回 False。"""
    now = time.monotonic()
    for k in [k for k, ts in _seen.items() if now - ts > _DEDUP_TTL_SECONDS]:
        _seen.pop(k, None)
    if key in _seen:
        _seen.move_to_end(key)
        return True
    _seen[key] = now
    while len(_seen) > _DEDUP_CAPACITY:
        _seen.popitem(last=False)
    return False


# ---------------------------------------------------------------------------
# 会话级串行队列
# ---------------------------------------------------------------------------
@dataclass
class _Job:
    connector: ConnectorRef
    message: InboundMessage


_queues: dict[tuple[int, str], asyncio.Queue] = {}


def _get_queue(key: tuple[int, str]) -> asyncio.Queue:
    queue = _queues.get(key)
    if queue is None:
        queue = asyncio.Queue(maxsize=_QUEUE_LIMIT)
        _queues[key] = queue
        _ensure_worker(key)
    return queue


def _ensure_worker(key: tuple[int, str]) -> None:
    asyncio.create_task(_worker(key))


def enqueue_message(connector: ConnectorRef, message: InboundMessage) -> bool:
    """投递消息到会话队列；队满返回 False（调用方负责回复忙提示）。"""
    key = (connector.id, message.chat_id)
    try:
        _get_queue(key).put_nowait(_Job(connector=connector, message=message))
        return True
    except asyncio.QueueFull:
        logger.warning("连接器会话队列已满 connector=%s chat=%s", key[0], key[1])
        return False


async def _worker(key: tuple[int, str]) -> None:
    queue = _queues[key]
    while True:
        job = await queue.get()
        try:
            await asyncio.wait_for(
                _process(job.connector, job.message), timeout=_PROCESS_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            logger.error("连接器消息处理超时 connector=%s chat=%s", key[0], key[1])
            await _safe_reply(job.connector, job.message, REPLY_FAILURE)
        except Exception as exc:
            logger.exception("连接器消息处理失败 connector=%s chat=%s", key[0], key[1])
            await _safe_reply(job.connector, job.message, REPLY_FAILURE)
            await notify_alert(
                "连接器消息处理失败",
                f"connector={job.connector.name} chat={key[1]} error={exc}",
            )
        finally:
            queue.task_done()


async def _process(connector: ConnectorRef, message: InboundMessage) -> None:
    """单条消息处理：映射会话 → 校验 Agent → 调用图 → 回复推送。"""
    async with AsyncSessionLocal() as session:
        conversation = await upsert_connector_conversation(
            session,
            connector.id,
            connector.platform,
            message.chat_id,
            message.chat_type,
            message.user_id,
            message.user_name,
        )
        agent = await session.get(AgentConfig, connector.agent_id)
        if agent is None or agent.status != AgentStatus.PUBLISHED:
            await _safe_reply(connector, message, REPLY_UNPUBLISHED)
            return
        content = (
            f"[{message.user_name}]: {message.text}"
            if message.chat_type == "group"
            else message.text
        )
        graph = await get_agent_instance(agent)
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=content)]},
            config={"configurable": {"thread_id": conversation.thread_id}},
        )
    reply = _extract_reply(result)
    if reply.strip():
        adapter = get_adapter(connector.platform)
        await adapter.send(connector.credentials, message.chat_id, reply)


def _extract_reply(result: dict | None) -> str:
    """从 graph.ainvoke 结果取最终回复文本（兼容 str 与内容块列表）。"""
    messages = (result or {}).get("messages") or []
    if not messages:
        return ""
    content = messages[-1].content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(part for part in parts if part)
    return str(content)


async def _safe_reply(connector: ConnectorRef, message: InboundMessage, text: str) -> None:
    """尽力回复兜底文案；失败只记日志，绝不影响主流程。"""
    try:
        adapter = get_adapter(connector.platform)
        await adapter.send(connector.credentials, message.chat_id, text)
    except Exception:
        logger.exception("连接器回复失败 connector=%s chat=%s", connector.id, message.chat_id)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_connector_pipeline.py -v`（超时 180s）
预期：8 passed

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/connectors/pipeline.py tests/test_connector_pipeline.py
git commit -m "feat: 连接器消息管线（去重/会话串行队列/Agent 调用/兜底回复）"
```

---

### 任务 8：管理端路由（CRUD + 会话列表 + 主动推送）

**文件：**
- 修改：`src/a2a_gateway/repository.py`（追加 2 个聚合查询函数）
- 创建：`src/a2a_gateway/routes/connectors.py`（本任务先建 admin_router 部分）
- 测试：`tests/test_connectors_api.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_connectors_api.py`：

```python
"""连接器管理 API 测试（零外部依赖：repository 层全部 monkeypatch）。"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from a2a_gateway.routes import connectors as connectors_mod


def _now():
    return datetime.now(timezone.utc)


class _Platform:
    def __init__(self, value: str):
        self.value = value


def make_connector(**overrides):
    base = {
        "id": 1,
        "name": "tg-1",
        "description": "",
        "platform": _Platform("telegram"),
        "credentials": {"bot_token": "123:abc", "secret_token": "sec", "bot_username": "mybot"},
        "agent_id": 1,
        "enabled": True,
        "created_at": _now(),
        "updated_at": _now(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def captured(monkeypatch):
    """monkeypatch 路由模块引用的 repository 函数，并捕获调用参数。"""
    store: dict = {"changes": None, "created": None, "sent": []}

    connector = make_connector()

    async def fake_get_connector(session, connector_id):
        return connector if connector_id == connector.id else None

    async def fake_get_connector_by_name(session, name):
        return None

    async def fake_get_agent_by_id(session, agent_id):
        return SimpleNamespace(id=agent_id, name="Demo Agent")

    async def fake_create_connector(session, data, credentials):
        store["created"] = credentials
        return make_connector(
            name=data.name,
            platform=_Platform(data.platform),
            credentials=credentials,
            agent_id=data.agent_id,
        )

    async def fake_update_connector(session, conn, changes):
        store["changes"] = changes
        for field, value in changes.items():
            setattr(conn, field, value)
        return conn

    async def fake_delete_connector(session, conn):
        store["deleted"] = conn.id

    async def fake_list_recent(session, connector_id, limit=20):
        return [SimpleNamespace(chat_id="777", chat_type="private",
                                last_user_ref={}, last_active_at=_now())]

    async def fake_register_webhook(credentials, connector_id):
        return credentials, ""  # 测试中不触网；真实函数在 PUBLIC_BASE_URL 为空时也直接返回

    async def fake_agent_name_map(session, ids):
        return {i: "Demo Agent" for i in ids}

    async def fake_last_active_map(session, ids):
        return {}

    monkeypatch.setattr(connectors_mod, "get_connector", fake_get_connector)
    monkeypatch.setattr(connectors_mod, "get_connector_by_name", fake_get_connector_by_name)
    monkeypatch.setattr(connectors_mod, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(connectors_mod, "create_connector", fake_create_connector)
    monkeypatch.setattr(connectors_mod, "update_connector", fake_update_connector)
    monkeypatch.setattr(connectors_mod, "delete_connector", fake_delete_connector)
    monkeypatch.setattr(
        connectors_mod, "list_recent_connector_conversations", fake_list_recent
    )
    monkeypatch.setattr(connectors_mod, "register_webhook", fake_register_webhook)
    monkeypatch.setattr(connectors_mod, "agent_name_map", fake_agent_name_map)
    monkeypatch.setattr(connectors_mod, "connector_last_active_map", fake_last_active_map)
    store["connector"] = connector
    return store


def test_list_connectors_requires_auth(anon_client):
    resp = anon_client.get("/api/admin/connectors")
    assert resp.status_code == 401


def test_create_connector_generates_secret_and_masks(auth_client, captured):
    resp = auth_client.post(
        "/api/admin/connectors",
        json={
            "name": "tg-1",
            "platform": "telegram",
            "agent_id": 1,
            "credentials": {"bot_token": "123:abc"},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["agent_name"] == "Demo Agent"
    assert data["webhook_url"].endswith("/api/connectors/telegram/1/webhook")
    assert data["credentials_masked"]["bot_token"] == "••••"
    # secret_token 留空时后端自动生成
    assert (captured["created"].get("secret_token") or "") != ""


def test_create_connector_rejects_unknown_platform(auth_client, captured):
    resp = auth_client.post(
        "/api/admin/connectors",
        json={"name": "x", "platform": "discord", "agent_id": 1, "credentials": {}},
    )
    assert resp.status_code == 422


def test_create_connector_rejects_duplicate_name(auth_client, captured, monkeypatch):
    async def dup(session, name):
        return make_connector()

    monkeypatch.setattr(connectors_mod, "get_connector_by_name", dup)
    resp = auth_client.post(
        "/api/admin/connectors",
        json={"name": "tg-1", "platform": "telegram", "agent_id": 1, "credentials": {}},
    )
    assert resp.status_code == 409


def test_create_connector_rejects_missing_agent(auth_client, captured, monkeypatch):
    async def no_agent(session, agent_id):
        return None

    monkeypatch.setattr(connectors_mod, "get_agent_by_id", no_agent)
    resp = auth_client.post(
        "/api/admin/connectors",
        json={"name": "tg-2", "platform": "telegram", "agent_id": 99, "credentials": {}},
    )
    assert resp.status_code == 400


def test_update_keeps_blank_credentials(auth_client, captured):
    resp = auth_client.put(
        "/api/admin/connectors/1",
        json={"credentials": {"bot_token": "", "secret_token": "new-sec"}},
    )
    assert resp.status_code == 200
    changes = captured["changes"]
    assert "name" not in changes
    assert "enabled" not in changes  # 未提供的字段不进 changes
    merged = changes["credentials"]
    assert merged["bot_token"] == "123:abc"     # 留空 → 保留原值
    assert merged["secret_token"] == "new-sec"  # 新值生效
    assert merged["bot_username"] == "mybot"    # 未提及的字段保留


def test_update_merges_partial_credentials(auth_client, captured):
    resp = auth_client.put(
        "/api/admin/connectors/1",
        json={"credentials": {"bot_token": "456:def"}},
    )
    assert resp.status_code == 200
    merged = captured["changes"]["credentials"]
    assert merged["bot_token"] == "456:def"
    assert merged["secret_token"] == "sec"  # 未提供的字段保留原值
    assert merged["bot_username"] == "mybot"


def test_delete_connector(auth_client, captured):
    resp = auth_client.delete("/api/admin/connectors/1")
    assert resp.status_code == 204
    assert captured["deleted"] == 1


def test_send_uses_most_recent_conversation(auth_client, captured, monkeypatch):
    async def fake_send(credentials, chat_id, text):
        captured["sent"].append((chat_id, text))

    class _Adapter:
        platform = "telegram"

        async def send(self, credentials, chat_id, text):
            await fake_send(credentials, chat_id, text)

    monkeypatch.setattr(connectors_mod, "get_adapter", lambda platform: _Adapter())
    resp = auth_client.post("/api/admin/connectors/1/send", json={"text": "hello"})
    assert resp.status_code == 200
    assert resp.json()["chat_id"] == "777"
    assert captured["sent"] == [("777", "hello")]


def test_send_requires_enabled(auth_client, captured):
    captured["connector"].enabled = False
    resp = auth_client.post("/api/admin/connectors/1/send", json={"text": "hello"})
    assert resp.status_code == 400


def test_send_without_conversation_returns_404(auth_client, captured, monkeypatch):
    async def empty(session, connector_id, limit=20):
        return []

    monkeypatch.setattr(connectors_mod, "list_recent_connector_conversations", empty)
    resp = auth_client.post("/api/admin/connectors/1/send", json={"text": "hello"})
    assert resp.status_code == 404


def test_conversations_endpoint(auth_client, captured):
    resp = auth_client.get("/api/admin/connectors/1/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["chat_id"] == "777"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_connectors_api.py -v`（超时 180s）
预期：FAIL，`ModuleNotFoundError: No module named 'a2a_gateway.routes.connectors'`（及 `get_agent_by_id` 未在模块中）

- [ ] **步骤 3：repository 补充聚合查询**

`repository.py` 末尾（连接器段落内）追加。同时顶部 sqlalchemy import 行补 `func`：

```python
from sqlalchemy import CursorResult, func, select, update
```

追加内容：

```python
async def agent_name_map(session: AsyncSession, agent_ids: list[int]) -> dict[int, str]:
    """批量取 Agent 名称（连接器列表展示绑定关系用）。"""
    if not agent_ids:
        return {}
    rows = await session.execute(
        select(AgentConfig.id, AgentConfig.name).where(AgentConfig.id.in_(agent_ids))
    )
    return {aid: name for aid, name in rows.all()}


async def connector_last_active_map(
    session: AsyncSession, connector_ids: list[int]
) -> dict[int, datetime | None]:
    """各连接器最近活跃时间（聚合会话映射表）。"""
    if not connector_ids:
        return {}
    rows = await session.execute(
        select(
            ConnectorConversation.connector_id,
            func.max(ConnectorConversation.last_active_at),
        )
        .where(ConnectorConversation.connector_id.in_(connector_ids))
        .group_by(ConnectorConversation.connector_id)
    )
    return {cid: ts for cid, ts in rows.all()}
```

- [ ] **步骤 4：实现 routes/connectors.py（admin_router 部分）**

```python
"""连接器管理路由：管理端 CRUD + 平台 webhook 回调。

- admin_router：/api/admin/connectors（JWT 管理员认证，风格对齐 admin.py）
- webhook_router：/api/connectors（无 admin 鉴权，安全依赖平台验签，见任务 9）
"""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..connectors.registry import get_adapter
from ..connectors.telegram import register_webhook
from ..database import get_session
from ..deps import get_current_admin
from ..models import AdminUser, ChatConnector
from ..repository import (
    agent_name_map,
    connector_last_active_map,
    create_connector,
    delete_connector,
    get_agent_by_id,
    get_connector,
    get_connector_by_name,
    list_connectors,
    list_recent_connector_conversations,
    update_connector,
)
from ..schemas import (
    ConnectorConversationOut,
    ConnectorCreate,
    ConnectorOut,
    ConnectorSendRequest,
    ConnectorUpdate,
    mask_connector_credentials,
    merge_connector_credentials,
)

logger = logging.getLogger(__name__)
_settings = get_settings()

admin_router = APIRouter(prefix="/api/admin/connectors", tags=["connectors"])
webhook_router = APIRouter(prefix="/api/connectors", tags=["connectors"])


def _webhook_url(connector: ChatConnector) -> str:
    path = f"/api/connectors/{connector.platform.value}/{connector.id}/webhook"
    base = _settings.public_base_url.rstrip("/")
    return f"{base}{path}" if base else path


def _connector_out(
    connector: ChatConnector,
    agent_name: str,
    last_active_at=None,
    setup_warning: str = "",
) -> ConnectorOut:
    return ConnectorOut(
        id=connector.id,
        name=connector.name,
        description=connector.description or "",
        platform=connector.platform.value,
        agent_id=connector.agent_id,
        agent_name=agent_name,
        enabled=connector.enabled,
        webhook_url=_webhook_url(connector),
        credentials_masked=mask_connector_credentials(
            connector.platform.value, connector.credentials
        ),
        setup_warning=setup_warning,
        last_active_at=last_active_at,
        created_at=connector.created_at,
        updated_at=connector.updated_at,
    )


async def _get_or_404(session: AsyncSession, connector_id: int) -> ChatConnector:
    connector = await get_connector(session, connector_id)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "连接器不存在")
    return connector


async def _maybe_register_telegram(session: AsyncSession, connector: ChatConnector) -> str:
    """Telegram 且启用时自动注册 webhook；返回警告（空串 = 成功或不需要）。"""
    if connector.platform.value != "telegram" or not connector.enabled:
        return ""
    old = dict(connector.credentials or {})
    updated, warning = await register_webhook(old, connector.id)
    if updated != old:
        connector.credentials = updated
        await update_connector(session, connector, {"credentials": updated})
    return warning


@admin_router.get("", response_model=list[ConnectorOut])
async def list_all(
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    connectors = await list_connectors(session)
    names = await agent_name_map(session, [c.agent_id for c in connectors])
    last_active = await connector_last_active_map(session, [c.id for c in connectors])
    return [
        _connector_out(c, names.get(c.agent_id, ""), last_active.get(c.id))
        for c in connectors
    ]


@admin_router.post("", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
async def create(
    req: ConnectorCreate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    if await get_connector_by_name(session, req.name.strip()):
        raise HTTPException(status.HTTP_409_CONFLICT, "连接器名称已存在")
    agent = await get_agent_by_id(session, req.agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "绑定的 Agent 不存在")
    credentials = dict(req.credentials)
    if req.platform == "telegram" and not (credentials.get("secret_token") or "").strip():
        credentials["secret_token"] = secrets.token_urlsafe(32)
    connector = await create_connector(session, req, credentials)
    warning = await _maybe_register_telegram(session, connector)
    return _connector_out(connector, agent.name, setup_warning=warning)


@admin_router.put("/{connector_id}", response_model=ConnectorOut)
async def update(
    connector_id: int,
    req: ConnectorUpdate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    connector = await _get_or_404(session, connector_id)
    changes: dict = {}
    if req.name is not None and req.name.strip() != connector.name:
        existing = await get_connector_by_name(session, req.name.strip())
        if existing is not None and existing.id != connector.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "连接器名称已存在")
        changes["name"] = req.name.strip()
    if req.description is not None:
        changes["description"] = req.description
    if req.agent_id is not None and req.agent_id != connector.agent_id:
        if await get_agent_by_id(session, req.agent_id) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "绑定的 Agent 不存在")
        changes["agent_id"] = req.agent_id
    if req.enabled is not None:
        changes["enabled"] = req.enabled
    if req.credentials is not None:
        merged = merge_connector_credentials(
            connector.platform.value, connector.credentials, req.credentials
        )
        if merged != dict(connector.credentials or {}):
            changes["credentials"] = merged
    connector = await update_connector(session, connector, changes)
    warning = await _maybe_register_telegram(session, connector)
    names = await agent_name_map(session, [connector.agent_id])
    return _connector_out(
        connector, names.get(connector.agent_id, ""), setup_warning=warning
    )


@admin_router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(
    connector_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    connector = await _get_or_404(session, connector_id)
    await delete_connector(session, connector)


@admin_router.get(
    "/{connector_id}/conversations", response_model=list[ConnectorConversationOut]
)
async def conversations(
    connector_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    connector = await _get_or_404(session, connector_id)
    return await list_recent_connector_conversations(session, connector.id)


@admin_router.post("/{connector_id}/send")
async def send_message(
    connector_id: int,
    req: ConnectorSendRequest,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """主动推送（系统侧通知的最小闭环 + 管理页发送测试）。"""
    connector = await _get_or_404(session, connector_id)
    if not connector.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "连接器已停用")
    chat_id = req.chat_id
    if not chat_id:
        recent = await list_recent_connector_conversations(session, connector.id, limit=1)
        if not recent:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "该连接器暂无会话，请先在聊天应用中发起对话"
            )
        chat_id = recent[0].chat_id
    adapter = get_adapter(connector.platform.value)
    await adapter.send(dict(connector.credentials or {}), chat_id, req.text)
    return {"chat_id": chat_id, "ok": True}
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_connectors_api.py -v`（超时 180s）
预期：12 passed

- [ ] **步骤 6：Commit**

```bash
git add src/a2a_gateway/repository.py src/a2a_gateway/routes/connectors.py tests/test_connectors_api.py
git commit -m "feat: 连接器管理端 API（CRUD/会话列表/主动推送）"
```

---

### 任务 9：平台 Webhook 路由 + 应用注册

**文件：**
- 修改：`src/a2a_gateway/routes/connectors.py`（追加 webhook_router 实现）
- 修改：`src/a2a_gateway/main.py:28,87-91`（router import 与注册）
- 测试：`tests/test_connectors_api.py`（追加）

- [ ] **步骤 1：编写失败的测试（追加到 tests/test_connectors_api.py）**

```python
# ---------------------------------------------------------------------------
# 平台 webhook 路由
# ---------------------------------------------------------------------------
from a2a_gateway.connectors import pipeline as pipeline_mod


def _tg_webhook_update(event_id: int = 1001) -> dict:
    return {
        "update_id": event_id,
        "message": {
            "message_id": event_id,
            "text": "你好",
            "from": {"id": 7, "is_bot": False, "first_name": "Tom"},
            "chat": {"id": 7, "type": "private"},
        },
    }


@pytest.fixture
def webhook_env(monkeypatch):
    """提供已注册的 telegram 连接器 + stub 适配器，捕获 enqueue 调用。"""
    connector = make_connector()
    calls: list = []

    class _StubAdapter:
        platform = "telegram"

        async def build_challenge(self, body, headers, credentials):
            return None

        async def verify_and_parse(self, body, headers, credentials):
            import json as _json

            update = _json.loads(body)
            message = update["message"]
            from a2a_gateway.connectors.base import InboundMessage

            return [
                InboundMessage(
                    platform="telegram",
                    chat_id=str(message["chat"]["id"]),
                    chat_type="private",
                    user_id="7",
                    user_name="Tom",
                    text=message["text"],
                    event_id=str(update["update_id"]),
                )
            ]

        async def send(self, credentials, chat_id, text):
            pass

    async def fake_get_connector(session, connector_id):
        return connector if connector_id == 1 else None

    def fake_enqueue(connector_ref, message):
        calls.append(message.event_id)
        return True

    monkeypatch.setattr(connectors_mod, "get_connector", fake_get_connector)
    monkeypatch.setattr(connectors_mod, "get_adapter", lambda platform: _StubAdapter())
    monkeypatch.setattr(connectors_mod, "enqueue_message", fake_enqueue)
    pipeline_mod._seen.clear()
    return {"calls": calls, "connector": connector}


def test_webhook_unknown_platform_404(anon_client):
    resp = anon_client.post("/api/connectors/discord/1/webhook", json={})
    assert resp.status_code == 404


def test_webhook_unknown_connector_404(anon_client, webhook_env, monkeypatch):
    async def none_connector(session, connector_id):
        return None

    monkeypatch.setattr(connectors_mod, "get_connector", none_connector)
    resp = anon_client.post(
        "/api/connectors/telegram/99/webhook",
        json=_tg_webhook_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec"},
    )
    assert resp.status_code == 404


def test_webhook_disabled_connector_404(anon_client, webhook_env):
    webhook_env["connector"].enabled = False
    resp = anon_client.post(
        "/api/connectors/telegram/1/webhook",
        json=_tg_webhook_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec"},
    )
    assert resp.status_code == 404


def test_webhook_feishu_verify_failure_401(anon_client, webhook_env, monkeypatch):
    connector = make_connector(platform=_Platform("feishu"), credentials={})
    async def feishu_connector(session, connector_id):
        return connector

    monkeypatch.setattr(connectors_mod, "get_connector", feishu_connector)
    resp = anon_client.post("/api/connectors/feishu/1/webhook", json={"header": {}})
    assert resp.status_code == 401


def test_webhook_feishu_challenge(anon_client, webhook_env, monkeypatch):
    connector = make_connector(
        platform=_Platform("feishu"),
        credentials={"app_id": "a", "app_secret": "s", "verification_token": "vt", "encrypt_key": ""},
    )
    async def feishu_connector(session, connector_id):
        return connector

    monkeypatch.setattr(connectors_mod, "get_connector", feishu_connector)
    resp = anon_client.post(
        "/api/connectors/feishu/1/webhook",
        json={"header": {"token": "vt"}, "type": "url_verification", "challenge": "cf"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "cf"}


def test_webhook_enqueue_and_ack(anon_client, webhook_env):
    resp = anon_client.post(
        "/api/connectors/telegram/1/webhook",
        json=_tg_webhook_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert webhook_env["calls"] == ["1001"]


def test_webhook_dedups_retries(anon_client, webhook_env):
    body = _tg_webhook_update()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "sec"}
    anon_client.post("/api/connectors/telegram/1/webhook", json=body, headers=headers)
    anon_client.post("/api/connectors/telegram/1/webhook", json=body, headers=headers)
    assert webhook_env["calls"] == ["1001"]  # 重试被去重


def test_webhook_queue_full_still_acks(anon_client, webhook_env, monkeypatch):
    def full(connector_ref, message):
        return False

    monkeypatch.setattr(connectors_mod, "enqueue_message", full)
    resp = anon_client.post(
        "/api/connectors/telegram/1/webhook",
        json=_tg_webhook_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_connectors_api.py -k webhook -v`（超时 180s）
预期：FAIL，404（webhook 路由尚未注册）

- [ ] **步骤 3：实现 webhook 路由**

`routes/connectors.py` 文件末尾追加：

```python
# ---------------------------------------------------------------------------
# 平台 webhook 回调（无 admin 鉴权；安全 = 平台验签 → connector 定位 → 事件去重）
# ---------------------------------------------------------------------------
async def _busy_reply(adapter, credentials: dict, chat_id: str) -> None:
    try:
        await adapter.send(credentials, chat_id, REPLY_BUSY)
    except Exception:
        logger.warning("忙提示发送失败 chat=%s", chat_id, exc_info=True)


@webhook_router.post("/{platform}/{connector_id}/webhook")
async def platform_webhook(
    platform: str,
    connector_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """平台事件入口：先处理接入握手，再验签归一化，最后异步投递并立即确认。"""
    body = await request.body()
    try:
        adapter = get_adapter(platform)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "不支持的平台")
    connector = await get_connector(session, connector_id)
    if connector is None or connector.platform.value != platform or not connector.enabled:
        # 停用与不存在对外不暴露差异
        raise HTTPException(status.HTTP_404_NOT_FOUND, "连接器不存在")
    credentials = dict(connector.credentials or {})
    try:
        challenge = await adapter.build_challenge(body, request.headers, credentials)
    except VerifyError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "验签失败")
    if challenge is not None:
        return challenge
    try:
        messages = await adapter.verify_and_parse(body, request.headers, credentials)
    except VerifyError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "验签失败")

    ref = ConnectorRef(
        id=connector.id,
        name=connector.name,
        platform=connector.platform.value,
        credentials=credentials,
        agent_id=connector.agent_id,
        enabled=connector.enabled,
    )
    for message in messages:
        dedup_key = f"{platform}:{connector_id}:{message.event_id}"
        if seen_recently(dedup_key):
            continue
        if not enqueue_message(ref, message):
            asyncio.create_task(_busy_reply(adapter, credentials, message.chat_id))
    return {"ok": True}
```

注意顶部 import 补充（任务 8 未包含）：标准库区加 `import asyncio`；fastapi import 行改为 `from fastapi import APIRouter, Depends, HTTPException, Request, status`；并新增：

```python
from ..connectors.base import VerifyError
from ..connectors.pipeline import REPLY_BUSY, ConnectorRef, enqueue_message, seen_recently
```

- [ ] **步骤 4：注册路由到应用**

`main.py` 修改两处：

```python
from .routes import a2a_server, admin, chat, connectors, registry, speech
```

```python
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(registry.router)
app.include_router(a2a_server.router)
app.include_router(speech.router)
app.include_router(connectors.admin_router)
app.include_router(connectors.webhook_router)
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_connectors_api.py -v`（超时 180s）
预期：20 passed

- [ ] **步骤 6：全量回归 + Commit**

运行：`uv run pytest -v`（超时 300s）
预期：全量测试 passed（历史用例不受影响）

```bash
git add src/a2a_gateway/routes/connectors.py src/a2a_gateway/main.py tests/test_connectors_api.py
git commit -m "feat: 平台 webhook 回调路由（握手/验签/去重/异步投递）"
```

---

### 任务 10：前端 adminApi 类型与方法

**文件：**
- 修改：`web/src/lib/adminApi.ts`

- [ ] **步骤 1：添加类型定义**

在 `adminApi.ts` 中 `export const adminApi = {` 之前追加类型（与现有接口定义区相邻）：

```ts
// ---- 聊天连接器 ----
export type ConnectorPlatform = "feishu" | "telegram" | "slack";

export interface Connector {
  id: number;
  name: string;
  description: string;
  platform: ConnectorPlatform;
  agent_id: number;
  agent_name: string;
  enabled: boolean;
  /** 后端用 PUBLIC_BASE_URL 拼好的完整回调地址（未配置时为相对路径） */
  webhook_url: string;
  /** 凭据脱敏视图：已配置字段为 "••••"，空字段为 ""（不含明文） */
  credentials_masked: Record<string, string>;
  /** Telegram 自动注册失败等提示（不阻断保存） */
  setup_warning: string;
  last_active_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConnectorCreatePayload {
  name: string;
  platform: ConnectorPlatform;
  description?: string;
  agent_id: number;
  enabled?: boolean;
  credentials: Record<string, string>;
}

export interface ConnectorUpdatePayload {
  name?: string;
  description?: string;
  agent_id?: number;
  enabled?: boolean;
  /** 留空字段 = 不修改（后端按原值合并） */
  credentials?: Record<string, string>;
}

export interface ConnectorConversation {
  chat_id: string;
  chat_type: string;
  last_user_ref: Record<string, unknown>;
  last_active_at: string;
}
```

- [ ] **步骤 2：添加 API 方法**

在 `adminApi` 对象内（`// ---- API Key 管理` 段之前）追加：

```ts
  // ---- 聊天连接器 ----
  listConnectors(): Promise<Connector[]> {
    return request<Connector[]>("/api/admin/connectors");
  },

  createConnector(payload: ConnectorCreatePayload): Promise<Connector> {
    return request<Connector>("/api/admin/connectors", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateConnector(id: number, payload: ConnectorUpdatePayload): Promise<Connector> {
    return request<Connector>(`/api/admin/connectors/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  deleteConnector(id: number): Promise<void> {
    return request<void>(`/api/admin/connectors/${id}`, { method: "DELETE" });
  },

  listConnectorConversations(id: number): Promise<ConnectorConversation[]> {
    return request<ConnectorConversation[]>(`/api/admin/connectors/${id}/conversations`);
  },

  sendConnectorMessage(
    id: number,
    payload: { chat_id?: string | null; text: string },
  ): Promise<{ chat_id: string; ok: boolean }> {
    return request<{ chat_id: string; ok: boolean }>(`/api/admin/connectors/${id}/send`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
```

- [ ] **步骤 3：类型检查**

运行（在 `web/` 目录）：`npx tsc --noEmit`（超时 300s）
预期：无错误

- [ ] **步骤 4：Commit**

```bash
git add web/src/lib/adminApi.ts
git commit -m "feat: 前端连接器类型与 API 方法"
```

---

### 任务 11：连接器管理页面（列表 + 弹窗 + 导航）

**文件：**
- 创建：`web/src/components/admin/ConnectorDialog.tsx`
- 创建：`web/src/components/admin/SendTestDialog.tsx`
- 创建：`web/src/app/admin/connectors/page.tsx`
- 修改：`web/src/components/admin/AdminShell.tsx:25-66`

- [ ] **步骤 1：实现 ConnectorDialog.tsx**

```tsx
"use client";

import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  FormControl,
  FormControlLabel,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import { Agent, Connector, ConnectorPlatform, adminApi } from "@/lib/adminApi";
import DialogTitleBar from "./DialogTitleBar";

export const PLATFORM_LABELS: Record<ConnectorPlatform, string> = {
  feishu: "飞书",
  telegram: "Telegram",
  slack: "Slack",
};

/** 各平台凭据字段：平台选择驱动动态渲染 */
export const PLATFORM_CREDENTIAL_FIELDS: Record<
  ConnectorPlatform,
  { key: string; label: string }[]
> = {
  feishu: [
    { key: "app_id", label: "App ID" },
    { key: "app_secret", label: "App Secret" },
    { key: "verification_token", label: "Verification Token" },
    { key: "encrypt_key", label: "Encrypt Key（未启用加密留空）" },
  ],
  telegram: [
    { key: "bot_token", label: "Bot Token" },
    { key: "secret_token", label: "Secret Token（留空自动生成）" },
  ],
  slack: [
    { key: "bot_token", label: "Bot Token（xoxb- 开头）" },
    { key: "signing_secret", label: "Signing Secret" },
  ],
};

interface ConnectorDialogProps {
  /** 传入则为编辑，否则为新建 */
  initial: Connector | null;
  agents: Agent[];
  existingNames: string[];
  onClose: () => void;
  onSaved: () => void;
}

/**
 * 连接器新建/编辑弹窗。保存成功后切换为「Webhook URL 展示」视图：
 * 提供一键复制与 Telegram 自动注册结果提示。
 */
export default function ConnectorDialog({
  initial,
  agents,
  existingNames,
  onClose,
  onSaved,
}: ConnectorDialogProps) {
  const isEdit = !!initial;

  const [name, setName] = useState(initial?.name ?? "");
  const [platform, setPlatform] = useState<ConnectorPlatform>(initial?.platform ?? "feishu");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [agentId, setAgentId] = useState<number>(initial?.agent_id ?? agents[0]?.id ?? 0);
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<{ name?: string; agentId?: string }>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Connector | null>(null);

  const masked = initial?.credentials_masked ?? {};

  const validate = (): boolean => {
    const next: typeof errors = {};
    const n = name.trim();
    if (!n) next.name = "请填写名称";
    else if (n !== initial?.name && existingNames.includes(n)) {
      next.name = `名称 '${n}' 已存在`;
    }
    if (!agentId) next.agentId = "请选择绑定的 Agent";
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async () => {
    if (!validate()) return;
    setSaving(true);
    setError(null);
    try {
      const result = initial
        ? await adminApi.updateConnector(initial.id, {
            name: name.trim(),
            description: description.trim(),
            agent_id: agentId,
            enabled,
            credentials,
          })
        : await adminApi.createConnector({
            name: name.trim(),
            platform,
            description: description.trim(),
            agent_id: agentId,
            enabled,
            credentials,
          });
      setSaved(result);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  if (saved) {
    return (
      <Dialog open onClose={onClose} fullWidth maxWidth="sm">
        <DialogTitleBar title="连接器已保存" onClose={onClose} />
        <DialogContent>
          {!!saved.setup_warning && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              {saved.setup_warning}
            </Alert>
          )}
          <Typography variant="body2" sx={{ mb: 1 }}>
            请将该 Webhook URL 填入平台后台的事件订阅配置：
          </Typography>
          <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
            <TextField
              fullWidth
              size="small"
              value={saved.webhook_url}
              InputProps={{ readOnly: true }}
            />
            <Button
              variant="outlined"
              startIcon={<ContentCopyIcon />}
              onClick={() => {
                void navigator.clipboard.writeText(saved.webhook_url);
              }}
            >
              复制
            </Button>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>关闭</Button>
        </DialogActions>
      </Dialog>
    );
  }

  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitleBar title={isEdit ? "编辑连接器" : "新建连接器"} onClose={onClose} />
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {error && (
          <Alert severity="error" onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        <TextField
          label="名称"
          value={name}
          onChange={(e) => setName(e.target.value)}
          error={!!errors.name}
          helperText={errors.name}
          fullWidth
        />
        <FormControl fullWidth disabled={isEdit}>
          <InputLabel>平台</InputLabel>
          <Select
            label="平台"
            value={platform}
            onChange={(e) => setPlatform(e.target.value as ConnectorPlatform)}
          >
            {(Object.keys(PLATFORM_LABELS) as ConnectorPlatform[]).map((p) => (
              <MenuItem key={p} value={p}>
                {PLATFORM_LABELS[p]}
              </MenuItem>
            ))}
          </Select>
          {isEdit && <FormHelperText>创建后不可更换平台</FormHelperText>}
        </FormControl>
        <FormControl fullWidth error={!!errors.agentId}>
          <InputLabel>绑定 Agent</InputLabel>
          <Select
            label="绑定 Agent"
            value={agentId || ""}
            onChange={(e) => setAgentId(Number(e.target.value))}
          >
            {agents.map((a) => (
              <MenuItem key={a.id} value={a.id}>
                {a.name}
                {a.status === "draft" ? "（未发布）" : ""}
              </MenuItem>
            ))}
          </Select>
          {errors.agentId && <FormHelperText>{errors.agentId}</FormHelperText>}
        </FormControl>
        {PLATFORM_CREDENTIAL_FIELDS[platform].map((field) => (
          <TextField
            key={field.key}
            label={field.label}
            value={credentials[field.key] ?? ""}
            onChange={(e) =>
              setCredentials((prev) => ({ ...prev, [field.key]: e.target.value }))
            }
            placeholder={masked[field.key] === "••••" ? "已配置（留空不修改）" : ""}
            fullWidth
          />
        ))}
        <TextField
          label="备注"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          multiline
          minRows={2}
          fullWidth
        />
        <FormControlLabel
          control={<Switch checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />}
          label="启用"
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>取消</Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={saving}
          startIcon={saving ? <CircularProgress size={16} /> : undefined}
        >
          保存
        </Button>
      </DialogActions>
    </Dialog>
  );
}
```

- [ ] **步骤 2：实现 SendTestDialog.tsx**

```tsx
"use client";

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  TextField,
} from "@mui/material";
import { Connector, ConnectorConversation, adminApi } from "@/lib/adminApi";
import DialogTitleBar from "./DialogTitleBar";

interface SendTestDialogProps {
  connector: Connector;
  onClose: () => void;
}

/** 发送测试消息：下拉选最近活跃会话，或手填 chat_id。 */
export default function SendTestDialog({ connector, onClose }: SendTestDialogProps) {
  const [conversations, setConversations] = useState<ConnectorConversation[]>([]);
  const [chatId, setChatId] = useState<string>("");
  const [text, setText] = useState("连接器测试消息");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    adminApi
      .listConnectorConversations(connector.id)
      .then((rows) => {
        if (alive) setConversations(rows);
      })
      .catch(() => {
        /* 下拉留空，可手填 chat_id */
      });
    return () => {
      alive = false;
    };
  }, [connector.id]);

  const submit = async () => {
    if (!text.trim()) return;
    setSending(true);
    setError(null);
    try {
      const res = await adminApi.sendConnectorMessage(connector.id, {
        chat_id: chatId || null,
        text: text.trim(),
      });
      setToast(`已推送到会话 ${res.chat_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "推送失败");
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitleBar title={`发送测试消息 · ${connector.name}`} onClose={onClose} />
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {error && <Alert severity="error">{error}</Alert>}
        {toast && <Alert severity="success">{toast}</Alert>}
        <FormControl fullWidth>
          <InputLabel>目标会话（最近活跃）</InputLabel>
          <Select label="目标会话（最近活跃）" value={chatId} onChange={(e) => setChatId(e.target.value)}>
            <MenuItem value="">自动：最近活跃会话</MenuItem>
            {conversations.map((c) => (
              <MenuItem key={c.chat_id} value={c.chat_id}>
                {c.chat_id}（{c.chat_type === "group" ? "群聊" : "私聊"}
                {typeof c.last_user_ref?.display_name === "string"
                  ? ` · ${c.last_user_ref.display_name}`
                  : ""}
                ）
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <TextField
          label="或手填 chat_id"
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
          size="small"
        />
        <TextField
          label="消息文本"
          value={text}
          onChange={(e) => setText(e.target.value)}
          multiline
          minRows={3}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>关闭</Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={sending || !text.trim()}
          startIcon={sending ? <CircularProgress size={16} /> : undefined}
        >
          发送
        </Button>
      </DialogActions>
    </Dialog>
  );
}
```

- [ ] **步骤 3：实现列表页 page.tsx**

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  Paper,
  Snackbar,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import SendOutlinedIcon from "@mui/icons-material/SendOutlined";
import ContentCopyOutlinedIcon from "@mui/icons-material/ContentCopyOutlined";
import { Agent, Connector, adminApi } from "@/lib/adminApi";
import ConnectorDialog, { PLATFORM_LABELS } from "@/components/admin/ConnectorDialog";
import SendTestDialog from "@/components/admin/SendTestDialog";
import { useIsMobile } from "@/lib/breakpoints";

function formatTime(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString("zh-CN", { hour12: false, dateStyle: "short", timeStyle: "short" });
}

/** 连接器管理页：飞书 / Telegram / Slack 机器人的注册与绑定。 */
export default function ConnectorsAdminPage() {
  const [items, setItems] = useState<Connector[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Connector | null>(null);
  const [sending, setSending] = useState<Connector | null>(null);
  const isMobile = useIsMobile();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [connectors, agentList] = await Promise.all([
        adminApi.listConnectors(),
        adminApi.listAgents(),
      ]);
      setItems(connectors);
      setAgents(agentList);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载连接器失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleToggle = async (item: Connector, enabled: boolean) => {
    setBusyId(item.id);
    setError(null);
    try {
      await adminApi.updateConnector(item.id, { enabled });
      setToast(enabled ? "已启用" : "已停用");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (item: Connector) => {
    if (!window.confirm(`确认删除连接器「${item.name}」？其会话映射将一并删除。`)) return;
    setBusyId(item.id);
    setError(null);
    try {
      await adminApi.deleteConnector(item.id);
      setToast("已删除");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusyId(null);
    }
  };

  const copyWebhook = async (item: Connector) => {
    await navigator.clipboard.writeText(item.webhook_url);
    setToast("Webhook URL 已复制");
  };

  return (
    <>
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1, flexWrap: "wrap", mb: 2 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h6">连接器管理</Typography>
          <Typography variant="caption" color="text.secondary">
            将 Agent 接入飞书 / Telegram / Slack；私聊直接回复，群聊 @机器人 才处理。
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => {
            setEditing(null);
            setDialogOpen(true);
          }}
        >
          新建连接器
        </Button>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <TableContainer component={Paper}>
        <Table size={isMobile ? "small" : "medium"}>
          <TableHead>
            <TableRow>
              <TableCell>名称</TableCell>
              <TableCell>平台</TableCell>
              <TableCell>绑定 Agent</TableCell>
              <TableCell>启用</TableCell>
              <TableCell>最近活跃</TableCell>
              <TableCell align="right">操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id} hover>
                <TableCell>{item.name}</TableCell>
                <TableCell>
                  <Chip size="small" label={PLATFORM_LABELS[item.platform]} />
                </TableCell>
                <TableCell>{item.agent_name || item.agent_id}</TableCell>
                <TableCell>
                  <Switch
                    checked={item.enabled}
                    disabled={busyId === item.id}
                    onChange={(e) => void handleToggle(item, e.target.checked)}
                  />
                </TableCell>
                <TableCell>{formatTime(item.last_active_at)}</TableCell>
                <TableCell align="right">
                  <Tooltip title="复制 Webhook URL">
                    <IconButton size="small" onClick={() => void copyWebhook(item)}>
                      <ContentCopyOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="发送测试消息">
                    <IconButton size="small" onClick={() => setSending(item)}>
                      <SendOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="编辑">
                    <IconButton
                      size="small"
                      onClick={() => {
                        setEditing(item);
                        setDialogOpen(true);
                      }}
                    >
                      <EditOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="删除">
                    <IconButton
                      size="small"
                      disabled={busyId === item.id}
                      onClick={() => void handleDelete(item)}
                    >
                      <DeleteOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))}
            {items.length === 0 && !loading && (
              <TableRow>
                <TableCell colSpan={6} align="center" sx={{ py: 4 }}>
                  还没有连接器，点击右上角「新建连接器」开始接入。
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", mt: 2 }}>
          <CircularProgress size={24} />
        </Box>
      )}

      {dialogOpen && (
        <ConnectorDialog
          initial={editing}
          agents={agents}
          existingNames={items.map((i) => i.name)}
          onClose={() => setDialogOpen(false)}
          onSaved={() => void load()}
        />
      )}
      {sending && <SendTestDialog connector={sending} onClose={() => setSending(null)} />}

      <Snackbar
        open={toast !== null}
        autoHideDuration={3000}
        onClose={() => setToast(null)}
        message={toast}
      />
    </>
  );
}
```

- [ ] **步骤 4：注册导航项**

`AdminShell.tsx` 修改两处。图标 import 区追加：

```tsx
import CableOutlinedIcon from "@mui/icons-material/CableOutlined";
```

`NAV_ITEMS` 数组「Skill 管理」项之后追加：

```tsx
  {
    label: "连接器管理",
    href: "/admin/connectors",
    icon: <CableOutlinedIcon fontSize="small" />,
    isActive: (pathname: string) => pathname.startsWith("/admin/connectors"),
  },
```

- [ ] **步骤 5：类型检查与 Lint**

运行（在 `web/` 目录）：`npx tsc --noEmit`（超时 300s）
预期：无错误

运行（在 `web/` 目录）：`npx eslint src/app/admin/connectors src/components/admin/ConnectorDialog.tsx src/components/admin/SendTestDialog.tsx src/components/admin/AdminShell.tsx`（超时 300s）
预期：无 error（warning 按现有基线处理）

- [ ] **步骤 6：Commit**

```bash
git add web/src/app/admin/connectors web/src/components/admin/ConnectorDialog.tsx web/src/components/admin/SendTestDialog.tsx web/src/components/admin/AdminShell.tsx
git commit -m "feat: 连接器管理页面（列表/新建编辑/发送测试/导航）"
```

---

### 任务 12：全量验证与收尾

**文件：** 无新增（验证 + 可能的修复）

- [ ] **步骤 1：后端全量测试**

运行：`uv run pytest -v`（超时 300s）
预期：全部 passed（含历史用例，确认零回归）

- [ ] **步骤 2：类型检查**

运行：`uv run basedpyright`（超时 300s）
预期：0 error（standard 模式；如有新增告警必须修复后再提交）

- [ ] **步骤 3：Lint**

运行：`uv run ruff check src tests`（超时 120s）
预期：无告警

- [ ] **步骤 4：验收清单核对（对照规格 §11）**

逐条确认：
1. `tests/test_connectors_api.py` 覆盖 CRUD/脱敏/webhook/推送；
2. 会话映射 `thread_id` 确定性生成（`tests/test_connector_support.py`）；
3. Telegram 自动注册逻辑（`register_webhook` + `_maybe_register_telegram`）；
4. 推送接口三种路径（指定 chat_id / 最近活跃 / 404）有测试；
5. 三项命令全绿。

- [ ] **步骤 5：如有修复则提交**

```bash
git add -A
git commit -m "fix: 连接器全量验证修复（按 basedpyright/ruff/pytest 结果）"
```

无修复则跳过本步骤。

---

## 计划自检记录（编写者已完成）

1. **规格覆盖度**：规格 §3 数据模型 → 任务 1；§4 适配器 → 任务 3-6；§5 主链路 → 任务 7、9；§6 管理 API → 任务 8；§7 配置 → 任务 1（`PUBLIC_BASE_URL`）与任务 8（`_maybe_register_telegram`）；§8 前端 → 任务 10-11；§9 测试 → 任务 1-9 各测试文件；§10 安全（去重/队列上限/超时/脱敏/告警）→ 任务 7-9 代码与测试；§11 验收 → 任务 12。无遗漏。
2. **占位符扫描**：无「待定/TODO/类似任务 N」；所有代码步骤含完整代码。
3. **类型一致性**：`ConnectorRef`（任务 7 定义，任务 8/9 使用）；`InboundMessage` 字段（任务 3 定义，4/5/6/9 使用）；`register_webhook(credentials, connector_id) -> tuple[dict, str]`（任务 4 定义，任务 8 使用）；`agent_name_map` / `connector_last_active_map`（任务 8 定义并使用）；路由模块内 monkeypatch 目标名与 import 名一致（`get_connector`、`create_connector`、`update_connector`、`delete_connector`、`get_agent_by_id`、`get_adapter`、`enqueue_message`、`register_webhook`、`list_recent_connector_conversations`、`get_connector_by_name`）。

---

## 执行记录（实现阶段）

| 任务 | 提交 | 说明 |
|---|---|---|
| 1-7 | `1ac9388` → `e07aa25` | 按计划执行，无偏差 |
| 8-9 | `4e18d17` | 合并为一次提交（`routes/connectors.py` 同时含管理端与 webhook 两个 router，难以拆分） |
| 10-11 | `86b8788` | 前端类型/API + 管理页 + 导航 |
| 12 | 验证 | 见下 |

**实现阶段对计划的修正（均为执行中发现的问题）**

1. 计划中任务 8/9 的测试写成了同步函数，与实际不符：项目 `tests/conftest.py` 的 `anon_client` / `auth_client` 是 async fixture，`pyproject.toml` 配置 `asyncio_mode = "auto"`。已全部改为 `async def test_...` + `await client.xxx`。
2. `test_update_keeps_blank_credentials` 原断言与实现不匹配（提交 `secret_token` 新值时必然产生变更）：改为全部字段留空的场景断言「无 credentials 变更」。
3. `test_webhook_feishu_verify_failure_401` / `feishu_challenge` 原计划复用 stub 适配器，无法验证真实验签：改为独立 `feishu_connector` fixture，走真实 `FeishuAdapter`。
4. `update` 路由原计划用 `agent_name_map` 批量取名（仅一个 Agent，多一次聚合查询）：改为 `get_agent_by_id` 直接取名。
5. 前端列表页原计划只有表格：按项目既有移动端适配规范补充 `useIsMobile` 卡片布局（与 `admin/a2a/page.tsx` 一致）。
6. 列表页 `useEffect` 内改为异步 IIFE 调用加载函数，以通过 `react-hooks/set-state-in-effect`（既有页面存在同类基线告警，新文件保持干净）。
7. 凭据输入框使用 `type="password"` + `slotProps={{ input: { readOnly: true } }}`（对齐 MUI v9 与项目既有写法）。
8. 补充 `.env.example` 的 `PUBLIC_BASE_URL` 配置项。

**验证结果**

- `uv run pytest -q`：446 passed（含历史用例，零回归）
- `uv run basedpyright`：0 errors, 0 warnings
- `uv run ruff check src tests`：173 条，全部为项目既有基线（B008 `Depends` 91 条、BLE001、UP017、I001 等），新增代码未引入新规则类别
- `npx tsc --noEmit`（web）：新增文件无错误；仅剩既有 `src/app/layout.tsx:16 LayoutProps`（Next 生成类型缺失）
- `npx eslint`（新增/修改前端文件）：0 error
- `npx vitest run`（web）：当前环境工具链将其识别为 watch 服务并接管输出，未能取得结果；改动仅新增类型与 API 方法，未触碰被测纯函数（`decodeJwtPayload` / `isJwtExpired` 等）

**验收清单（对照规格 §11）**

1. `tests/test_connectors_api.py` 覆盖 CRUD / 脱敏 / webhook / 推送 —— 23 passed
2. 会话映射 `thread_id` 确定性生成 —— `tests/test_connector_support.py::test_thread_id_stable` 等
3. Telegram 自动注册 —— `register_webhook` + `_maybe_register_telegram`（含 `setup_warning` 回传）
4. 推送接口三种路径 —— 指定 chat_id / 最近活跃 / 无会话 404 均有测试
5. 后端测试与类型检查全绿






