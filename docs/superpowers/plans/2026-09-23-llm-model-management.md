# 模型管理功能实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增模型注册表（CRUD + 连通性测试），每个 Agent 可单选绑定模型并覆盖推理参数，未绑定时回落全局环境变量。

**架构：** 完全对齐现有"A2A/MCP/Skill 注册表"范式——`llm_models` 表 + Agent 侧快照绑定 + repository 三件套（resolve/refresh/detach）+ `/api/admin/models` 路由 + 前端管理页三件套（列表页/Dialog/NAV_ITEMS）。`build_llm` 参数化按 provider 分支（openai 兼容 / anthropic 原生）。

**技术栈：** FastAPI + SQLAlchemy 2.0 async + Alembic（幂等 raw SQL）+ pytest（无 DB 模式）；Next.js 16 App Router + MUI v9 + vitest。

**规格：** `docs/superpowers/specs/2026-09-23-llm-model-management-design.md`（计划的论证依据来自规格，执行者两份都读）

## 全局约束

- 所有新管理端点必须 `Depends(get_current_admin)`；错误用 `HTTPException` 中文 detail；测试端点契约 `{"ok": bool, "message": str}`
- 模型记录变更后必须 `refresh_agents_for_model` + 逐个 `invalidate_agent`（对齐 registry.py L229-233 的 MCP 模式）
- PG 枚举列必须 `values_callable=lambda enum_cls: [m.value for m in enum_cls]`（models.py L87-91 注释点名的坑）；迁移用 DO 块幂等创建枚举
- api_key 明文入库（String(512)），`ModelOut` 出参只给 `api_key_masked`（`mask_secret`），更新时 api_key 留空 = 保持原值
- 类型检查命令是 `uv run basedpyright`（standard 模式，要求 0 error）；ruff line-length 100（全仓库存在既有告警，门禁按「本次改动文件 0 新增」执行）；后端测试不连真实 DB / 不发真实网络请求（conftest 模式）
- Python 命令统一用 `uv run`；前端命令在 `web/` 目录下执行
- 每个任务结束必须 commit；测试先行（TDD）

## 文件结构

**创建：**

| 文件 | 职责 |
|------|------|
| `alembic/versions/0013_llm_models.py` | llm_models 表 + agent_configs 绑定列（幂等 raw SQL） |
| `src/a2a_gateway/llm_probe.py` | LLM 连通性探针（httpx 直调，provider 分支） |
| `src/a2a_gateway/routes/models.py` | `/api/admin/models` CRUD + 两种测试端点 |
| `tests/test_models_schema.py` | ORM 列定义断言 |
| `tests/test_llm_build.py` | build_llm_from_snapshot / resolve_llm 单测 |
| `tests/test_llm_probe.py` | 探针单测（httpx.MockTransport） |
| `tests/test_llm_models_api.py` | 模型 API 测试（monkeypatch repository） |
| `web/src/app/admin/models/page.tsx` | 模型管理列表页 |
| `web/src/components/admin/ModelDialog.tsx` | 模型新增/编辑弹窗（含测试连接） |

**修改：**

| 文件 | 改动 |
|------|------|
| `src/a2a_gateway/models.py` | `LLMProvider`/`LLMModel` + `AgentConfig.model_id`/`model_snapshot` |
| `src/a2a_gateway/llm.py` | `build_llm_from_snapshot` + `resolve_llm` |
| `src/a2a_gateway/graph.py` | `build_graph` 用 `resolve_llm(agent.model_snapshot)` |
| `src/a2a_gateway/schemas.py` | LLMModel 系列 schema + `AgentModelBinding` + Out 调整 |
| `src/a2a_gateway/repository.py` | 快照解析 + refresh/detach/agents_using 三件套 |
| `src/a2a_gateway/main.py` | 挂载 models router |
| `src/a2a_gateway/routes/admin.py` | （仅受 schema 驱动，无逻辑改动；如需模型摘要展示则微调） |
| `pyproject.toml` | 新增 `langchain-anthropic` |
| `web/src/lib/adminApi.ts` | LLMModel 接口 + 5 个方法 + Agent payload 加 `model` |
| `web/src/components/admin/AdminShell.tsx` | NAV_ITEMS 注册"模型管理" |
| `web/src/components/admin/AgentForm.tsx` | 模型下拉 + 参数覆盖输入 |

---

### 任务 1：数据模型与迁移

**文件：**
- 修改：`src/a2a_gateway/models.py`（在 `McpServer` 类之后、`SkillReviewStatus` 之前插入新代码；`AgentConfig` 在 L82 `skills` 字段后追加两个字段）
- 创建：`alembic/versions/0013_llm_models.py`
- 测试：`tests/test_models_schema.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_models_schema.py
"""模型管理：ORM schema 定义测试（无需 DB，纯元数据断言）。"""
from a2a_gateway.models import AgentConfig, LLMModel, LLMProvider


def test_provider_values():
    assert [m.value for m in LLMProvider] == ["openai", "anthropic"]


def test_llm_model_columns():
    cols = {c.name for c in LLMModel.__table__.columns}
    assert {
        "id", "name", "provider", "base_url", "api_key",
        "model", "description", "created_at", "updated_at",
    } <= cols


def test_agent_config_model_binding_columns():
    cols = {c.name for c in AgentConfig.__table__.columns}
    assert {"model_id", "model_snapshot"} <= cols
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_models_schema.py -v`
预期：FAIL，`ImportError: cannot import name 'LLMModel'`

- [ ] **步骤 3：实现 ORM 定义**

在 `models.py` 的 `McpServer` 类（L144 `enabled` 行）之后插入：

```python
class LLMProvider(str, PyEnum):
    # openai = OpenAI 兼容协议族（OpenAI/DeepSeek/Qwen/vLLM/Ollama 等），
    # 具体供应商由 base_url + model 区分；anthropic = Anthropic 原生协议
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class LLMModel(Base, BaseMixin):
    """模型注册表（「模型管理」维护，Agent 单选绑定）。

    与 A2A/MCP 注册表同范式：这里是可复用的模型定义，
    Agent 侧保存 model_id + 运行时快照（agent_configs.model_snapshot）。
    """

    __tablename__ = "llm_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider: Mapped[LLMProvider] = mapped_column(
        Enum(
            LLMProvider,
            name="llmprovider",
            # 与 AgentStatus 同坑：必须按「成员值」建 PG 枚举
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        default=LLMProvider.OPENAI,
        server_default=LLMProvider.OPENAI.value,
    )
    # openai 兼容端点必填（通常以 /v1 结尾）；anthropic 留空用官方默认
    base_url: Mapped[str] = mapped_column(String(512), default="")
    # 明文入库（对齐 credentials 惯例）；API 出参只给脱敏形式
    api_key: Mapped[str] = mapped_column(String(512), default="")
    # 模型标识，如 deepseek-chat / claude-sonnet-4-5
    model: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
```

在 `AgentConfig` 的 `skills` 字段（L82）之后、`status` 之前插入：

```python
    # 绑定的模型注册表条目（单选；NULL = 回落全局 LLM_* 环境变量）
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_models.id"), nullable=True
    )
    # 由 model_id 解析而来的运行时快照（含 temperature/max_tokens 覆盖）
    model_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_models_schema.py -v`
预期：PASS（3 个用例）

- [ ] **步骤 5：编写迁移 0013**

创建 `alembic/versions/0013_llm_models.py`（照抄 0012 的幂等风格）：

```python
"""llm models: 模型注册表 + agent 模型绑定

Revision ID: 0013_llm_models
Revises: 0012_chat_connectors
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_llm_models"
down_revision: str | None = "0012_chat_connectors"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # 1) 幂等创建枚举类型（与 ORM 的 name="llmprovider" 一致）
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n "
            "ON n.oid = t.typnamespace "
            "WHERE t.typname = 'llmprovider' AND n.nspname = 'public') "
            "THEN CREATE TYPE llmprovider AS ENUM ('openai', 'anthropic'); "
            "END IF; END $$;"
        )
    )
    # 2) 模型注册表
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS llm_models (
                id SERIAL PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                provider llmprovider NOT NULL DEFAULT 'openai',
                base_url VARCHAR(512) NOT NULL DEFAULT '',
                api_key VARCHAR(512) NOT NULL DEFAULT '',
                model VARCHAR(128) NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                CONSTRAINT uq_llm_models_name UNIQUE (name)
            );
            """
        )
    )
    op.execute(
        sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_llm_models_name ON llm_models (name);")
    )
    # 3) Agent 侧绑定列（单选；NULL = 回落全局环境变量）
    op.execute(
        sa.text(
            "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS model_id INTEGER;"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS model_snapshot JSONB;"
        )
    )
    # 4) FK（幂等：仅当约束不存在时创建）；不设级联——删除走应用层解绑流程
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_agent_configs_model_id') "
            "THEN ALTER TABLE agent_configs ADD CONSTRAINT fk_agent_configs_model_id "
            "FOREIGN KEY (model_id) REFERENCES llm_models(id); "
            "END IF; END $$;"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE agent_configs DROP CONSTRAINT IF EXISTS fk_agent_configs_model_id;")
    )
    op.execute(sa.text("ALTER TABLE agent_configs DROP COLUMN IF EXISTS model_snapshot;"))
    op.execute(sa.text("ALTER TABLE agent_configs DROP COLUMN IF EXISTS model_id;"))
    op.execute(sa.text("DROP TABLE IF EXISTS llm_models;"))
    sa.Enum(name="llmprovider").drop(op.get_bind(), checkfirst=True)
```

- [ ] **步骤 6：验证迁移链完整**

运行：`uv run alembic heads`（只解析脚本，不连 DB）
预期：`0013_llm_models (head)`

- [ ] **步骤 7：质量门禁 + Commit**

运行：`uv run ruff check <本次改动文件> && uv run basedpyright`
预期：0 error

```bash
git add src/a2a_gateway/models.py alembic/versions/0013_llm_models.py tests/test_models_schema.py
git commit -m "feat: 模型注册表 ORM 与迁移 0013"
```

---

### 任务 2：`build_llm_from_snapshot` 与依赖

**文件：**
- 修改：`pyproject.toml`（dependencies 数组）
- 修改：`src/a2a_gateway/llm.py`（文件末尾追加）
- 测试：`tests/test_llm_build.py`

- [ ] **步骤 1：新增依赖**

`pyproject.toml` 的 `dependencies` 中 `langchain-openai` 行之后加：

```toml
    "langchain-anthropic>=0.3",
```

运行：`uv sync`

- [ ] **步骤 2：编写失败的测试**

```python
# tests/test_llm_build.py
"""build_llm_from_snapshot / resolve_llm：provider 分支与参数覆盖。"""
import pytest

from a2a_gateway.llm import (
    ThoughtSignatureChatOpenAI,
    build_llm_from_snapshot,
    resolve_llm,
)


def _snapshot(**overrides):
    base = {
        "provider": "openai",
        "name": "DeepSeek V3",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-test",
        "model": "deepseek-chat",
        "temperature": None,
        "max_tokens": None,
    }
    base.update(overrides)
    return base


def test_openai_branch_returns_patched_client():
    llm = build_llm_from_snapshot(_snapshot())
    assert isinstance(llm, ThoughtSignatureChatOpenAI)
    assert llm.model_name == "deepseek-chat"
    assert llm.streaming is True


def test_openai_param_overrides():
    llm = build_llm_from_snapshot(_snapshot(temperature=0.3, max_tokens=2048))
    assert llm.temperature == 0.3
    assert llm.max_tokens == 2048


def test_openai_without_overrides_keeps_defaults():
    llm = build_llm_from_snapshot(_snapshot())
    assert llm.temperature is None


def test_anthropic_branch():
    llm = build_llm_from_snapshot(
        _snapshot(provider="anthropic", base_url="", model="claude-sonnet-4-5", api_key="sk-ant")
    )
    assert type(llm).__name__ == "ChatAnthropic"
    assert llm.model_name == "claude-sonnet-4-5"


def test_unknown_provider_raises_chinese():
    with pytest.raises(ValueError, match="不支持的模型供应商"):
        build_llm_from_snapshot(_snapshot(provider="gemini"))


def test_resolve_llm_none_falls_back_to_global():
    llm = resolve_llm(None)
    assert isinstance(llm, ThoughtSignatureChatOpenAI)


def test_resolve_llm_with_snapshot():
    llm = resolve_llm(_snapshot(model="custom-model"))
    assert llm.model_name == "custom-model"
```

- [ ] **步骤 3：运行测试验证失败**

运行：`uv run pytest tests/test_llm_build.py -v`
预期：FAIL，`ImportError: cannot import name 'build_llm_from_snapshot'`

- [ ] **步骤 4：实现**

在 `llm.py` 顶部 import 区补充：

```python
from langchain_anthropic import ChatAnthropic
```

在文件末尾追加：

```python
def build_llm_from_snapshot(snapshot: dict[str, Any]):
    """按模型快照构造 LLM（provider 分支）；temperature/max_tokens 非空才覆盖。

    快照结构见 repository.llm_model_snapshot：provider/name/base_url/api_key/
    model/temperature/max_tokens。
    """
    provider = (snapshot.get("provider") or "").strip().lower()
    api_key = SecretStr(snapshot.get("api_key") or "")
    model = snapshot.get("model") or ""
    temperature = snapshot.get("temperature")
    max_tokens = snapshot.get("max_tokens")

    if provider == "openai":
        install_thought_signature_patch()
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "base_url": snapshot.get("base_url") or _settings.llm_base_url,
            "streaming": True,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return ThoughtSignatureChatOpenAI(**kwargs)

    if provider == "anthropic":
        kwargs = {"model_name": model, "api_key": api_key}
        if snapshot.get("base_url"):
            kwargs["base_url"] = snapshot["base_url"]
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return ChatAnthropic(**kwargs)

    raise ValueError(f"不支持的模型供应商：{provider}")


def resolve_llm(model_snapshot: dict[str, Any] | None):
    """有模型快照按快照构建，否则回落全局 LLM_* 环境变量（零配置兜底）。"""
    if model_snapshot:
        return build_llm_from_snapshot(model_snapshot)
    return build_llm()
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_llm_build.py -v`
预期：PASS（7 个用例）

- [ ] **步骤 6：质量门禁 + Commit**

运行：`uv run ruff check <本次改动文件> && uv run basedpyright`

```bash
git add pyproject.toml uv.lock src/a2a_gateway/llm.py tests/test_llm_build.py
git commit -m "feat: 按快照构建 LLM（openai/anthropic 分支）与全局回落"
```

---

### 任务 3：`build_graph` 接入模型快照

**文件：**
- 修改：`src/a2a_gateway/graph.py`（import 区 + L318）
- 测试：`tests/test_llm_build.py`（追加）

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_llm_build.py` 末尾追加：

```python
def test_resolve_llm_bad_provider_from_snapshot_raises():
    # 快照存在但 provider 非法时应显式报错，而不是静默回落全局配置
    with pytest.raises(ValueError, match="不支持的模型供应商"):
        resolve_llm(_snapshot(provider="palm"))
```

- [ ] **步骤 2：运行测试验证通过（该行为已在任务 2 实现，此处锁定快照优先语义）**

运行：`uv run pytest tests/test_llm_build.py -v`
预期：PASS。若失败说明快照被静默忽略，必须先修复。

- [ ] **步骤 3：修改 `graph.py`**

找到 import 区的 `from .llm import build_llm`（若为 `from .llm import build_llm` 单行，替换为）：

```python
from .llm import resolve_llm
```

将 `build_graph` 中（L318）：

```python
    llm = build_llm()
```

替换为：

```python
    # Agent 绑定了模型快照则按快照构建（provider 分支 + 参数覆盖），否则回落全局配置
    llm = resolve_llm(agent.model_snapshot)
```

同时用 `grep -n "build_llm" src/a2a_gateway/graph.py` 确认没有其他调用点残留旧引用。

- [ ] **步骤 4：运行全量测试确认无回归**

运行：`uv run pytest -x -q`
预期：全绿（`resolve_llm(None)` 行为与原 `build_llm()` 一致，现有用例不受影响）

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/graph.py tests/test_llm_build.py
git commit -m "feat: build_graph 按 Agent 模型快照构建 LLM"
```

---

### 任务 4：LLM 连通性探针

**文件：**
- 创建：`src/a2a_gateway/llm_probe.py`
- 测试：`tests/test_llm_probe.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_llm_probe.py
"""LLM 连通性探针：用 httpx.MockTransport 模拟 OpenAI 兼容 / Anthropic 端点。"""
import json

import httpx
import pytest

from a2a_gateway.llm_probe import test_llm


def _openai_transport(handler):
    return httpx.MockTransport(handler)


async def test_openai_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-chat"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "pong"}}]}
        )

    ok, message = await test_llm(
        "openai", "https://api.deepseek.com/v1", "sk-test", "deepseek-chat",
        transport=_openai_transport(handler),
    )
    assert ok is True
    assert "pong" in message


async def test_anthropic_success_uses_messages_api():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/messages")
        assert request.headers["x-api-key"] == "sk-ant"
        body = json.loads(request.content)
        assert body["max_tokens"] == 1
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": "pong"}]}
        )

    ok, message = await test_llm(
        "anthropic", "", "sk-ant", "claude-sonnet-4-5",
        transport=_openai_transport(handler),
    )
    assert ok is True
    assert "pong" in message


async def test_unauthorized_maps_to_key_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    ok, message = await test_llm(
        "openai", "https://x/v1", "bad", "m", transport=_openai_transport(handler)
    )
    assert ok is False
    assert "API Key" in message


async def test_not_found_maps_to_url_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    ok, message = await test_llm(
        "openai", "https://x/wrong", "k", "m", transport=_openai_transport(handler)
    )
    assert ok is False
    assert "base_url" in message


async def test_unknown_model_maps_to_model_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "model not found: nope"}}
        )

    ok, message = await test_llm(
        "openai", "https://x/v1", "k", "nope", transport=_openai_transport(handler)
    )
    assert ok is False
    assert "模型" in message


async def test_connect_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    ok, message = await test_llm(
        "openai", "http://127.0.0.1:1/v1", "k", "m", transport=_openai_transport(handler)
    )
    assert ok is False
    assert "无法连接" in message


async def test_unknown_provider_rejected():
    ok, message = await test_llm("palm", "", "k", "m")
    assert ok is False
    assert "不支持" in message
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_llm_probe.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'a2a_gateway.llm_probe'`

- [ ] **步骤 3：实现探针**

```python
# src/a2a_gateway/llm_probe.py
"""LLM 连通性探针：向模型发一条极短消息，同时验证 base_url / api_key / model。

设计要点：
- httpx 直调 REST（不依赖 langchain 客户端），provider 分支构造请求
- transport 参数供测试注入 httpx.MockTransport
- 失败信息必须能区分：key 无效 / 模型不存在 / 地址不可达 / 超时
"""

import httpx

ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
PING_MESSAGE = "ping"


def _summarize_body(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _error_message(status_code: int, body: str) -> str:
    if status_code in (401, 403):
        return f"认证失败（HTTP {status_code}）：API Key 无效或无权限；{_summarize_body(body)}"
    if status_code == 404:
        return (
            f"接口不存在（HTTP 404）：请检查 base_url 是否正确"
            f"（OpenAI 兼容地址通常以 /v1 结尾）；{_summarize_body(body)}"
        )
    if status_code == 400 and ("model" in body.lower()):
        return f"模型不存在或不可用（HTTP 400）：请检查模型标识；{_summarize_body(body)}"
    return f"服务返回错误（HTTP {status_code}）：{_summarize_body(body)}"


async def test_llm(
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    *,
    timeout: float = 10.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bool, str]:
    """发送极短测试消息，返回 (ok, message)。"""
    provider = (provider or "").strip().lower()
    if provider not in ("openai", "anthropic"):
        return False, f"不支持的模型供应商：{provider}"

    if provider == "openai":
        url = f"{(base_url or '').rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": PING_MESSAGE}],
            "max_tokens": 1,
        }
        resp_field = lambda data: (  # noqa: E731
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
        )
    else:
        root = (base_url or "").rstrip("/") or ANTHROPIC_DEFAULT_BASE
        url = f"{root}/v1/messages"
        headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
        payload = {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": PING_MESSAGE}],
        }
        resp_field = lambda data: (  # noqa: E731
            ((data.get("content") or [{}])[0]).get("text")
        )

    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException:
        return False, f"连接超时（{timeout:g}s）：请检查地址可达性"
    except httpx.ConnectError as exc:
        return False, f"无法连接到服务地址：{type(exc).__name__}: {exc}"

    if resp.status_code >= 400:
        return False, _error_message(resp.status_code, resp.text)

    try:
        reply = resp_field(resp.json())
    except Exception:
        reply = None
    return True, f"连接成功，模型已回复：{_summarize_body(str(reply or ''), 60)}"
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_llm_probe.py -v`
预期：PASS（7 个用例）

- [ ] **步骤 5：质量门禁 + Commit**

运行：`uv run ruff check <本次改动文件> && uv run basedpyright`

```bash
git add src/a2a_gateway/llm_probe.py tests/test_llm_probe.py
git commit -m "feat: LLM 连通性探针（openai/anthropic，错误分型中文提示）"
```

---

### 任务 5：模型 schemas 与 repository

**文件：**
- 修改：`src/a2a_gateway/schemas.py`（MCP 段与 Skill 段之间追加模型段；`AgentUpdate`/`AgentOut` 的绑定改动在任务 8）
- 修改：`src/a2a_gateway/repository.py`
- 测试：`tests/test_repository_model.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_repository_model.py
"""模型绑定纯函数测试：快照构造、脱敏、校验（无 DB）。"""
from types import SimpleNamespace

from a2a_gateway.models import LLMProvider
from a2a_gateway.repository import llm_model_snapshot
from a2a_gateway.schemas import mask_secret, validate_llm_model

import pytest


def _model(**kw):
    base = {
        "name": "DeepSeek V3",
        "provider": LLMProvider.OPENAI,
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-12345678",
        "model": "deepseek-chat",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_llm_model_snapshot_fields():
    snap = llm_model_snapshot(_model(), temperature=0.5, max_tokens=1024)
    assert snap == {
        "provider": "openai",
        "name": "DeepSeek V3",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-12345678",
        "model": "deepseek-chat",
        "temperature": 0.5,
        "max_tokens": 1024,
    }


def test_llm_model_snapshot_accepts_enum_and_string_provider():
    assert llm_model_snapshot(_model(provider=LLMProvider.ANTHROPIC))["provider"] == "anthropic"
    assert llm_model_snapshot(_model(provider="openai"))["provider"] == "openai"


def test_mask_secret():
    assert mask_secret("") == ""
    assert mask_secret("short") == "***"
    masked = mask_secret("sk-abcdefghij")
    assert masked.startswith("sk-") and masked.endswith("hij")
    assert "abcdef" not in masked


def test_validate_llm_model():
    validate_llm_model("openai", "https://x/v1", "m")          # 不抛
    validate_llm_model("anthropic", "", "m")                   # anthropic 允许空 base_url
    with pytest.raises(ValueError, match="base_url"):
        validate_llm_model("openai", "", "m")
    with pytest.raises(ValueError, match="模型标识"):
        validate_llm_model("openai", "https://x/v1", " ")
    with pytest.raises(ValueError, match="不支持的模型供应商"):
        validate_llm_model("palm", "https://x", "m")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_repository_model.py -v`
预期：FAIL，`ImportError: cannot import name 'llm_model_snapshot'`

- [ ] **步骤 3：实现 schemas**

在 `schemas.py` 的 MCP 段（`McpServerOut` 之后）追加：

```python
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
```

- [ ] **步骤 4：实现 repository**

`repository.py` 顶部 import 区：

- `models` import 列表加 `LLMModel`（按字母序插入）
- `schemas` import 列表加 `LLMModelCreate, LLMModelUpdate`

在 `resolve_mcp_snapshot` 之后追加：

```python
def llm_model_snapshot(
    m: Any, temperature: float | None = None, max_tokens: int | None = None
) -> dict[str, Any]:
    """模型注册表 → 运行时快照（build_llm_from_snapshot 的唯一输入）。

    兼容 ORM 对象（provider 为枚举）与 SimpleNamespace 替身（provider 为字符串）。
    """
    provider = m.provider.value if hasattr(m.provider, "value") else str(m.provider)
    return {
        "provider": provider,
        "name": m.name,
        "base_url": m.base_url or "",
        "api_key": m.api_key or "",
        "model": m.model or "",
        "temperature": temperature,
        "max_tokens": max_tokens,
    }


async def resolve_model_binding(
    session: AsyncSession, data: Any
) -> tuple[int | None, dict[str, Any] | None]:
    """解析 Agent 的模型绑定载荷 → (model_id, model_snapshot)。

    data 为 None 或 model_id 为空 → (None, None)（回落全局 LLM_* 环境变量）。
    所选模型不存在时抛 ValueError（由路由层转 400）。
    """
    if data is None or getattr(data, "model_id", None) is None:
        return None, None
    m = await session.get(LLMModel, data.model_id)
    if m is None:
        raise ValueError(f"所选模型不存在（id={data.model_id}）")
    return m.id, llm_model_snapshot(m, data.temperature, data.max_tokens)
```

在 MCP 注册表 CRUD 函数区（`update_mcp_server` 之后的区域）追加模型注册表 CRUD：

```python
# ---------------------------------------------------------------------------
# 模型注册表 CRUD
# ---------------------------------------------------------------------------
async def list_llm_models(session: AsyncSession) -> list[LLMModel]:
    result = await session.execute(select(LLMModel).order_by(LLMModel.id))
    return list(result.scalars().all())


async def get_llm_model(session: AsyncSession, model_id: int) -> LLMModel | None:
    return await session.get(LLMModel, model_id)


async def get_llm_model_by_name(session: AsyncSession, name: str) -> LLMModel | None:
    result = await session.execute(select(LLMModel).where(LLMModel.name == name))
    return result.scalar_one_or_none()


async def create_llm_model(session: AsyncSession, data: LLMModelCreate) -> LLMModel:
    m = LLMModel(**data.model_dump())
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return m


async def update_llm_model(
    session: AsyncSession, m: LLMModel, data: LLMModelUpdate
) -> LLMModel:
    for field in ("name", "provider", "base_url", "model", "description"):
        value = getattr(data, field)
        if value is not None:
            setattr(m, field, value)
    # api_key 特殊语义：None 或空串都表示"保持原值"（前端编辑时不回显明文）
    if data.api_key:
        m.api_key = data.api_key
    await session.commit()
    await session.refresh(m)
    return m


async def delete_llm_model(session: AsyncSession, m: LLMModel) -> None:
    await session.delete(m)
    await session.commit()


async def agents_using_model(session: AsyncSession, model_id: int) -> list[AgentConfig]:
    result = await session.execute(
        select(AgentConfig).where(AgentConfig.model_id == model_id).order_by(AgentConfig.id)
    )
    return list(result.scalars().all())


async def refresh_agents_for_model(session: AsyncSession, model_id: int) -> list[AgentConfig]:
    """模型记录变更后刷新引用方快照（保留各自的 temperature/max_tokens 覆盖）。"""
    m = await session.get(LLMModel, model_id)
    if m is None:
        return []
    agents = await agents_using_model(session, model_id)
    for agent in agents:
        snap = agent.model_snapshot or {}
        agent.model_snapshot = llm_model_snapshot(
            m, snap.get("temperature"), snap.get("max_tokens")
        )
    await session.commit()
    return agents


async def detach_model_from_agents(session: AsyncSession, model_id: int) -> int:
    """删除前置空所有引用（model_id/model_snapshot 置 NULL），返回解除数量。"""
    agents = await agents_using_model(session, model_id)
    for agent in agents:
        agent.model_id = None
        agent.model_snapshot = None
    await session.commit()
    return len(agents)
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_repository_model.py -v`
预期：PASS（5 个用例）

- [ ] **步骤 6：质量门禁 + Commit**

运行：`uv run ruff check <本次改动文件> && uv run basedpyright`

```bash
git add src/a2a_gateway/schemas.py src/a2a_gateway/repository.py tests/test_repository_model.py
git commit -m "feat: 模型注册表 schemas 与 repository（快照/三件套/CRUD）"
```

---

### 任务 6：模型 CRUD API 与路由挂载

**文件：**
- 创建：`src/a2a_gateway/routes/models.py`
- 修改：`src/a2a_gateway/main.py`（include 区）
- 修改：`tests/conftest.py`（`make_agent` 替身补 `model_id`/`model_snapshot` 字段）
- 测试：`tests/test_llm_models_api.py`

- [ ] **步骤 1：conftest 替身补字段**

`tests/conftest.py` 的 `make_agent` 中 `"skill_ids": [],` 行后追加：

```python
            "model_id": None,
            "model_snapshot": None,
```

- [ ] **步骤 2：编写失败的测试**

```python
# tests/test_llm_models_api.py
"""模型注册表 API 测试（monkeypatch repository，无 DB、无真实网络）。"""
from datetime import datetime, timezone
from types import SimpleNamespace

from a2a_gateway.routes import models as models_mod


def _model(**overrides):
    base = {
        "id": 1,
        "name": "DeepSeek V3",
        "provider": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-abcdef123456",
        "model": "deepseek-chat",
        "description": "",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _async(value):
    async def _call(*args, **kwargs):
        return value

    return _call


async def test_list_masks_api_key(auth_client, monkeypatch):
    monkeypatch.setattr(models_mod.repo, "list_llm_models", _async([_model()]))
    resp = await auth_client.get("/api/admin/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["api_key_masked"].startswith("sk-")
    assert "api_key" not in body[0]
    assert "sk-abcdef123456" not in resp.text


async def test_create_requires_base_url_for_openai(auth_client):
    resp = await auth_client.post(
        "/api/admin/models",
        json={"name": "X", "provider": "openai", "base_url": "", "model": "m"},
    )
    assert resp.status_code == 400
    assert "base_url" in resp.json()["detail"]


async def test_create_duplicate_name_409(auth_client, monkeypatch):
    monkeypatch.setattr(
        models_mod.repo, "get_llm_model_by_name", _async(_model(name="重复"))
    )
    resp = await auth_client.post(
        "/api/admin/models",
        json={"name": "重复", "provider": "openai", "base_url": "https://x/v1", "model": "m"},
    )
    assert resp.status_code == 409


async def test_create_ok(auth_client, monkeypatch):
    monkeypatch.setattr(models_mod.repo, "get_llm_model_by_name", _async(None))
    monkeypatch.setattr(
        models_mod.repo, "create_llm_model", _async(_model(name="New", model="m1"))
    )
    resp = await auth_client.post(
        "/api/admin/models",
        json={"name": "New", "provider": "openai", "base_url": "https://x/v1", "model": "m1"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "New"


async def test_update_refreshes_snapshots_and_invalidates(auth_client, monkeypatch):
    refreshed: list[int] = []
    invalidated: list[int] = []

    async def fake_get(session, mid):
        return _model(id=mid)

    async def fake_update(session, m, data):
        return m

    async def fake_refresh(session, model_id):
        refreshed.append(model_id)
        return [SimpleNamespace(id=7, name="A")]

    async def fake_invalidate(agent_id):
        invalidated.append(agent_id)

    monkeypatch.setattr(models_mod.repo, "get_llm_model", fake_get)
    monkeypatch.setattr(models_mod.repo, "update_llm_model", fake_update)
    monkeypatch.setattr(models_mod.repo, "refresh_agents_for_model", fake_refresh)
    monkeypatch.setattr(models_mod, "invalidate_agent", fake_invalidate)

    resp = await auth_client.put(
        "/api/admin/models/1",
        json={"model": "deepseek-chat-v2"},
    )
    assert resp.status_code == 200
    assert refreshed == [1]
    assert invalidated == [7]


async def test_delete_referenced_409(auth_client, monkeypatch):
    monkeypatch.setattr(models_mod.repo, "get_llm_model", _async(_model()))
    monkeypatch.setattr(
        models_mod.repo,
        "agents_using_model",
        _async([SimpleNamespace(id=1, name="A")]),
    )
    resp = await auth_client.delete("/api/admin/models/1")
    assert resp.status_code == 409
    assert "force" in resp.json()["detail"]


async def test_delete_force_detaches(auth_client, monkeypatch):
    detached: list[int] = []
    monkeypatch.setattr(models_mod.repo, "get_llm_model", _async(_model()))
    monkeypatch.setattr(
        models_mod.repo,
        "agents_using_model",
        _async([SimpleNamespace(id=1, name="A")]),
    )
    monkeypatch.setattr(models_mod, "invalidate_agent", _async(None))

    async def fake_detach(session, model_id):
        detached.append(model_id)

    monkeypatch.setattr(models_mod.repo, "detach_model_from_agents", fake_detach)
    monkeypatch.setattr(models_mod.repo, "delete_llm_model", _async(None))

    resp = await auth_client.delete("/api/admin/models/1?force=true")
    assert resp.status_code == 204
    assert detached == [1]
```

- [ ] **步骤 3：运行测试验证失败**

运行：`uv run pytest tests/test_llm_models_api.py -v`
预期：FAIL，`ModuleNotFoundError` / 404（路由不存在）

- [ ] **步骤 4：实现路由**

创建 `src/a2a_gateway/routes/models.py`：

```python
"""模型注册表路由：CRUD + 连通性测试。

所有接口均位于 /api/admin 下，需要 JWT 管理员认证。
删除保护语义与 A2A/MCP/Skill 注册表一致：被 Agent 引用时默认拒绝（409），
`?force=true` 强制删除并自动解绑。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import repository as repo
from ..database import get_session
from ..deps import get_current_admin
from ..models import AdminUser, LLMModel
from ..agent_factory import invalidate_agent
from ..schemas import LLMModelCreate, LLMModelOut, LLMModelUpdate, mask_secret, validate_llm_model

router = APIRouter(prefix="/api/admin", tags=["admin-registry"])


def _used_by_message(agents) -> str:
    names = "、".join(a.name for a in agents)
    return f"仍被 {len(agents)} 个 Agent 引用（{names}）；可先取消绑定，或用 ?force=true 强制删除并自动解绑"


def _out(m: LLMModel) -> dict:
    provider = m.provider.value if hasattr(m.provider, "value") else str(m.provider)
    return {
        "id": m.id,
        "name": m.name,
        "provider": provider,
        "base_url": m.base_url,
        "model": m.model,
        "description": m.description,
        "api_key_masked": mask_secret(m.api_key or ""),
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }


@router.get("/models", response_model=list[LLMModelOut])
async def list_models(
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    return [_out(m) for m in await repo.list_llm_models(session)]


@router.post("/models", response_model=LLMModelOut, status_code=status.HTTP_201_CREATED)
async def create_model(
    data: LLMModelCreate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    if not data.name.strip():
        raise HTTPException(400, "名称不能为空")
    try:
        validate_llm_model(data.provider, data.base_url, data.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if await repo.get_llm_model_by_name(session, data.name.strip()) is not None:
        raise HTTPException(409, f"名称 '{data.name}' 已存在")
    return _out(await repo.create_llm_model(session, data))


@router.put("/models/{model_id}", response_model=LLMModelOut)
async def update_model(
    model_id: int,
    data: LLMModelUpdate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    m = await repo.get_llm_model(session, model_id)
    if m is None:
        raise HTTPException(404, "模型不存在")

    # 合并后按 provider 校验必填项（与 create 一致）
    provider = data.provider or (
        m.provider.value if hasattr(m.provider, "value") else str(m.provider)
    )
    base_url = data.base_url if data.base_url is not None else m.base_url
    model_name = data.model if data.model is not None else m.model
    try:
        validate_llm_model(provider, base_url or "", model_name or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    new_name = data.name.strip() if data.name else m.name
    if new_name != m.name:
        dup = await repo.get_llm_model_by_name(session, new_name)
        if dup is not None:
            raise HTTPException(409, f"名称 '{new_name}' 已存在")

    updated = await repo.update_llm_model(session, m, data)
    # 配置变更 → 刷新引用方快照并失效图缓存（Agent 无需重启即生效）
    for agent in await repo.refresh_agents_for_model(session, model_id):
        await invalidate_agent(agent.id)
    return _out(updated)


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(
    model_id: int,
    force: bool = Query(False, description="为 true 时自动从所有 Agent 解绑后删除"),
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    m = await repo.get_llm_model(session, model_id)
    if m is None:
        raise HTTPException(404, "模型不存在")
    used = await repo.agents_using_model(session, model_id)
    if used and not force:
        raise HTTPException(409, _used_by_message(used))
    if used:
        for agent in used:
            await invalidate_agent(agent.id)
        await repo.detach_model_from_agents(session, model_id)
    await repo.delete_llm_model(session, m)
    return None
```

`main.py` 的 include 区（`app.include_router(registry.router)` 之后）追加：

```python
from .routes import models as model_routes
app.include_router(model_routes.router)
```

（import 放到文件顶部既有 routes import 区，与现有写法保持一致；别名 `model_routes` 避免与 ORM `models` 模块混淆。）

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_llm_models_api.py tests/test_models_schema.py -v`
预期：PASS

- [ ] **步骤 6：质量门禁 + Commit**

运行：`uv run ruff check <本次改动文件> && uv run basedpyright`

```bash
git add src/a2a_gateway/routes/models.py src/a2a_gateway/main.py tests/conftest.py tests/test_llm_models_api.py
git commit -m "feat: /api/admin/models CRUD 与删除保护"
```

---

### 任务 7：模型连通性测试端点

**文件：**
- 修改：`src/a2a_gateway/routes/models.py`（追加两个端点）
- 测试：`tests/test_llm_models_api.py`（追加）

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_llm_models_api.py` 末尾追加：

```python
async def test_saved_model_test_endpoint(auth_client, monkeypatch):
    calls: list[tuple] = []

    async def fake_probe(provider, base_url, api_key, model, **kw):
        calls.append((provider, base_url, api_key, model))
        return True, "连接成功"

    monkeypatch.setattr(models_mod.repo, "get_llm_model", _async(_model()))
    monkeypatch.setattr(models_mod, "test_llm", fake_probe)
    resp = await auth_client.post("/api/admin/models/1/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "message": "连接成功"}
    assert calls == [("openai", "https://api.deepseek.com/v1", "sk-abcdef123456", "deepseek-chat")]


async def test_saved_model_test_not_found(auth_client):
    resp = await auth_client.post("/api/admin/models/999/test")
    assert resp.status_code == 404


async def test_form_model_test_endpoint(auth_client, monkeypatch):
    received: dict = {}

    async def fake_probe(provider, base_url, api_key, model, **kw):
        received.update(provider=provider, model=model)
        return False, "认证失败：API Key 无效"

    monkeypatch.setattr(models_mod, "test_llm", fake_probe)
    resp = await auth_client.post(
        "/api/admin/models/test",
        json={"name": "临时", "provider": "anthropic", "base_url": "", "api_key": "k", "model": "m"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert received == {"provider": "anthropic", "model": "m"}


async def test_form_model_test_rejects_invalid(auth_client):
    resp = await auth_client.post(
        "/api/admin/models/test",
        json={"name": "X", "provider": "openai", "base_url": "", "model": "m"},
    )
    assert resp.status_code == 400
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_llm_models_api.py -k test -v`
预期：新增 4 个用例 FAIL（404 / 405，端点不存在）

- [ ] **步骤 3：实现端点**

在 `routes/models.py` 顶部 import 区补：

```python
from ..llm_probe import test_llm
```

文件末尾追加（**必须放在 `/models/{model_id}` 动态路由之后无妨，路径段数不同不会遮蔽**）：

```python
@router.post("/models/{model_id}/test")
async def test_model_by_id(
    model_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """测试已保存模型的连通性（发一条极短消息验证三元组）。"""
    m = await repo.get_llm_model(session, model_id)
    if m is None:
        raise HTTPException(404, "模型不存在")
    provider = m.provider.value if hasattr(m.provider, "value") else str(m.provider)
    ok, message = await test_llm(provider, m.base_url, m.api_key, m.model)
    return {"ok": ok, "message": message}


@router.post("/models/test")
async def test_model_form(
    data: LLMModelCreate,
    _: AdminUser = Depends(get_current_admin),
):
    """未保存表单直测：请求体复用 LLMModelCreate（api_key 为表单明文）。"""
    try:
        validate_llm_model(data.provider, data.base_url, data.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    ok, message = await test_llm(data.provider, data.base_url, data.api_key, data.model)
    return {"ok": ok, "message": message}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_llm_models_api.py -v`
预期：PASS（全部用例）

- [ ] **步骤 5：全量回归 + Commit**

运行：`uv run pytest -x -q`

```bash
git add src/a2a_gateway/routes/models.py tests/test_llm_models_api.py
git commit -m "feat: 模型连通性测试端点（已保存/表单直测）"
```

---

### 任务 8：Agent 模型绑定扩展

**文件：**
- 修改：`src/a2a_gateway/schemas.py`（Agent 段）
- 修改：`src/a2a_gateway/repository.py`（`create_agent` / `update_agent`）
- 修改：`src/a2a_gateway/routes/admin.py`（ValueError → 400）
- 测试：`tests/test_admin_api.py`（追加）

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_admin_api.py` 末尾追加（沿用该文件既有的 import，若 `admin_mod` 名称不同则以文件内现状为准）：

```python
# ---- Agent 模型绑定 ----
async def test_create_agent_passes_model_binding(auth_client, monkeypatch, make_agent):
    captured: dict = {}

    async def fake_create(session, data):
        captured["data"] = data
        return make_agent(id=2, slug="with-model")

    monkeypatch.setattr(admin_mod, "create_agent", fake_create)
    resp = await auth_client.post("/api/admin/agents", json={
        "slug": "with-model", "name": "绑定模型", "description": "",
        "a2a_target_ids": [], "mcp_server_ids": [], "skill_ids": [],
        "model": {"model_id": 3, "temperature": 0.2, "max_tokens": 1024},
    })
    assert resp.status_code == 201
    assert captured["data"].model.model_id == 3
    assert captured["data"].model.temperature == 0.2
    assert captured["data"].model.max_tokens == 1024


async def test_update_agent_model_object_replaces(auth_client, monkeypatch, make_agent):
    captured: dict = {}

    async def fake_update(session, agent, data):
        captured["data"] = data
        return agent

    monkeypatch.setattr(admin_mod, "update_agent", fake_update)
    resp = await auth_client.put("/api/admin/agents/1", json={
        "model": {"model_id": None},
    })
    assert resp.status_code == 200
    # 显式对象 + model_id null = 清除绑定；对象缺失 = 不修改（两者必须可区分）
    assert captured["data"].model is not None
    assert captured["data"].model.model_id is None


async def test_create_agent_unknown_model_400(auth_client, monkeypatch, make_agent):
    async def fake_create(session, data):
        raise ValueError("所选模型不存在（id=99）")

    monkeypatch.setattr(admin_mod, "create_agent", fake_create)
    resp = await auth_client.post("/api/admin/agents", json={
        "slug": "bad-model", "name": "X", "description": "",
        "a2a_target_ids": [], "mcp_server_ids": [], "skill_ids": [],
        "model": {"model_id": 99},
    })
    assert resp.status_code == 400
    assert "所选模型不存在" in resp.json()["detail"]


async def test_agent_out_includes_model_fields(auth_client, monkeypatch, make_agent):
    agent = make_agent(
        model_id=1, model_snapshot={"provider": "openai", "model": "m"}
    )

    async def fake_list(session):
        return [agent]

    monkeypatch.setattr(admin_mod, "list_agents", fake_list)
    resp = await auth_client.get("/api/admin/agents")
    body = resp.json()
    assert body[0]["model_id"] == 1
    assert body[0]["model_snapshot"]["provider"] == "openai"
```

注意：若 `AgentBase` 存在其他必填字段导致 422，按提示在 json 中补齐即可（替身路径不变）。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_admin_api.py -k model -v`
预期：新用例 FAIL（`model` 字段被 pydantic 丢弃或 422）

- [ ] **步骤 3：实现 schemas**

`schemas.py` 的 `AgentCreate` 定义之前追加：

```python
class AgentModelBinding(BaseModel):
    """Agent 的模型绑定。

    更新时整体替换语义：字段对象为 null = 不修改绑定；
    提供对象且 model_id 为 null = 清除绑定（回落全局配置）。
    """

    model_id: int | None = Field(default=None, description="模型注册表 id；null = 清除绑定")
    temperature: float | None = Field(default=None, description="留空用运行时默认")
    max_tokens: int | None = Field(default=None, description="留空用运行时默认")
```

- `AgentCreate` 内追加字段：`model: AgentModelBinding | None = None`
- `AgentUpdate` 内追加字段：`model: AgentModelBinding | None = None`
- `AgentOut` 内追加字段：

```python
    model_id: int | None = None
    model_snapshot: dict[str, Any] | None = None
```

（确认 `schemas.py` 顶部已有 `from typing import Any`，没有则补。）

- [ ] **步骤 4：实现 repository 接线**

`repository.py`：

1. schemas import 列表加 `AgentModelBinding`
2. `create_agent` 中 `skills_snapshot = await resolve_skills(...)` 行后追加，并在 `AgentConfig(...)` 构造参数 `skills=skills_snapshot,` 后加两行：

```python
    model_id, model_snapshot = await resolve_model_binding(session, data.model)
```

```python
        model_id=model_id,
        model_snapshot=model_snapshot,
```

3. `update_agent` 的 Skill 绑定块（`agent.skills = await resolve_skills(...)` 所在 if 块）之后追加：

```python
    # 模型绑定：整体替换语义（data.model 为 None = 不修改；model_id null = 清除回落全局）
    if data.model is not None:
        agent.model_id, agent.model_snapshot = await resolve_model_binding(
            session, data.model
        )
```

- [ ] **步骤 5：admin.py 校验失败转 400**

`routes/admin.py`：

`create_new_agent` 中 `return await create_agent(session, data)` 改为：

```python
    try:
        return await create_agent(session, data)
    except ValueError as exc:
        # 模型绑定校验失败（如所选模型不存在）
        raise HTTPException(400, str(exc))
```

`update_existing_agent` 中 `updated = await update_agent(session, agent, data)` 改为：

```python
    try:
        updated = await update_agent(session, agent, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
```

- [ ] **步骤 6：运行测试验证通过**

运行：`uv run pytest tests/test_admin_api.py -v`
预期：PASS（含既有用例）

- [ ] **步骤 7：全量回归 + Commit**

运行：`uv run pytest -x -q && uv run ruff check src tests && uv run pyright`

```bash
git add src/a2a_gateway/schemas.py src/a2a_gateway/repository.py src/a2a_gateway/routes/admin.py tests/test_admin_api.py
git commit -m "feat: Agent 可绑定模型（整体替换语义 + 参数覆盖）"
```

---

### 任务 9：前端模型管理页

**文件：**
- 修改：`web/src/lib/adminApi.ts`
- 修改：`web/src/components/admin/AdminShell.tsx`
- 创建：`web/src/components/admin/ModelDialog.tsx`
- 创建：`web/src/app/admin/models/page.tsx`

- [ ] **步骤 1：adminApi.ts 增加类型与方法**

类型区（`McpServer` 接口附近）追加：

```ts
export type LLMProvider = "openai" | "anthropic";

export interface LLMModel {
  id: number;
  name: string;
  provider: LLMProvider;
  base_url: string;
  model: string;
  description: string;
  api_key_masked: string;
  created_at: string;
  updated_at: string;
}

export interface LLMModelCreatePayload {
  name: string;
  provider: LLMProvider;
  base_url?: string;
  api_key?: string;
  model: string;
  description?: string;
}

export interface LLMModelUpdatePayload {
  name?: string;
  provider?: LLMProvider;
  base_url?: string;
  /** 留空/不传 = 保持原值（后端不回显明文 key） */
  api_key?: string;
  model?: string;
  description?: string;
}
```

`adminApi` 对象内（MCP 段之后）追加：

```ts
  // ---- 模型注册表 ----
  listModels(): Promise<LLMModel[]> {
    return request<LLMModel[]>("/api/admin/models");
  },

  createModel(payload: LLMModelCreatePayload): Promise<LLMModel> {
    return request<LLMModel>("/api/admin/models", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateModel(id: number, payload: LLMModelUpdatePayload): Promise<LLMModel> {
    return request<LLMModel>(`/api/admin/models/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  /** 删除模型；被 Agent 引用时后端返回 409，可用 force=true 自动解绑。 */
  deleteModel(id: number, force = false): Promise<void> {
    return request<void>(`/api/admin/models/${id}${force ? "?force=true" : ""}`, {
      method: "DELETE",
    });
  },

  testModel(id: number): Promise<{ ok: boolean; message: string }> {
    return request<{ ok: boolean; message: string }>(`/api/admin/models/${id}/test`, {
      method: "POST",
    });
  },

  /** 未保存表单直测（复用创建 payload；api_key 用表单明文）。 */
  testModelForm(payload: LLMModelCreatePayload): Promise<{ ok: boolean; message: string }> {
    return request<{ ok: boolean; message: string }>("/api/admin/models/test", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
```

同时给 `AgentCreatePayload` 与 `AgentUpdatePayload` 接口追加可选字段（供任务 10 使用）：

```ts
  model?: {
    model_id: number | null;
    temperature?: number | null;
    max_tokens?: number | null;
  } | null;
```

- [ ] **步骤 2：AdminShell 注册菜单**

`AdminShell.tsx`：

import 区追加：`import MemoryOutlinedIcon from "@mui/icons-material/MemoryOutlined";`

`NAV_ITEMS` 数组在"Agent 管理"项之后插入：

```tsx
  {
    label: "模型管理",
    href: "/admin/models",
    icon: <MemoryOutlinedIcon fontSize="small" />,
    isActive: (pathname: string) => pathname.startsWith("/admin/models"),
  },
```

（"Agent 管理"的 `isActive` 只匹配 `/admin` 与 `/admin/agents`，不会与 `/admin/models` 冲突。）

- [ ] **步骤 3：ModelDialog 组件**

创建 `web/src/components/admin/ModelDialog.tsx`（布局细节对齐 `McpServerDialog.tsx`；核心逻辑必须完整）：

```tsx
"use client";

import { useEffect, useState } from "react";
import {
  Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent,
  DialogTitle, Divider, FormControl, FormControlLabel, FormLabel, MenuItem,
  Radio, RadioGroup, Stack, TextField,
} from "@mui/material";
import { adminApi, LLMModel, LLMModelCreatePayload, LLMProvider } from "@/lib/adminApi";

interface ModelDialogProps {
  open: boolean;
  initial?: LLMModel | null;
  onClose: () => void;
  onSaved: () => void;
}

const BASE_URL_HINT: Record<LLMProvider, string> = {
  openai: "如 https://api.deepseek.com/v1（通常以 /v1 结尾）",
  anthropic: "留空使用官方默认 https://api.anthropic.com",
};

export default function ModelDialog({ open, initial, onClose, onSaved }: ModelDialogProps) {
  const isEdit = !!initial;
  const [name, setName] = useState("");
  const [provider, setProvider] = useState<LLMProvider>("openai");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState(""); // 编辑时留空 = 不修改
  const [model, setModel] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testMessage, setTestMessage] = useState<{ ok: boolean; text: string } | null>(null);

  // 打开时按 initial 重置（编辑不回填明文 key）
  useEffect(() => {
    if (open) {
      setName(initial?.name ?? "");
      setProvider(initial?.provider ?? "openai");
      setBaseUrl(initial?.base_url ?? "");
      setApiKey("");
      setModel(initial?.model ?? "");
      setDescription(initial?.description ?? "");
      setError("");
      setTestMessage(null);
    }
  }, [open, initial]);

  const buildPayload = (): LLMModelCreatePayload => ({
    name: name.trim(),
    provider,
    base_url: baseUrl.trim(),
    model: model.trim(),
    description,
    ...(apiKey ? { api_key: apiKey } : {}),
  });

  const handleTest = async () => {
    setTesting(true);
    setTestMessage(null);
    try {
      const r = await adminApi.testModelForm(buildPayload());
      setTestMessage({ ok: r.ok, text: r.message });
    } catch (e) {
      setTestMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setTesting(false);
    }
  };

  const handleSubmit = async () => {
    setError("");
    setSaving(true);
    try {
      if (isEdit && initial) {
        await adminApi.updateModel(initial.id, buildPayload());
      } else {
        await adminApi.createModel(buildPayload());
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEdit ? "编辑模型" : "新增模型"}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField label="名称" value={name} onChange={(e) => setName(e.target.value)}
            placeholder="如 DeepSeek V3" required fullWidth />
          <FormControl>
            <FormLabel>供应商协议</FormLabel>
            <RadioGroup row value={provider}
              onChange={(e) => setProvider(e.target.value as LLMProvider)}>
              <FormControlLabel value="openai" control={<Radio />} label="OpenAI 兼容" />
              <FormControlLabel value="anthropic" control={<Radio />} label="Anthropic" />
            </RadioGroup>
          </FormControl>
          <TextField label="Base URL" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
            helperText={BASE_URL_HINT[provider]} fullWidth />
          <TextField label="API Key" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            placeholder={isEdit ? `留空保持不变（当前 ${initial?.api_key_masked || "未设置"}）` : "必填"}
            fullWidth />
          <TextField label="模型标识" value={model} onChange={(e) => setModel(e.target.value)}
            placeholder="如 deepseek-chat / claude-sonnet-4-5" required fullWidth />
          <TextField label="备注" value={description} onChange={(e) => setDescription(e.target.value)}
            multiline minRows={2} fullWidth />
          <Divider />
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Button onClick={handleTest} disabled={testing || !model.trim()}
              startIcon={testing ? <CircularProgress size={16} /> : undefined}>
              测试连接
            </Button>
            {testMessage && (
              <Alert severity={testMessage.ok ? "success" : "error"} sx={{ flex: 1 }}>
                {testMessage.text}
              </Alert>
            )}
          </Box>
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>取消</Button>
        <Button onClick={handleSubmit} disabled={saving || !name.trim() || !model.trim()} variant="contained">
          保存
        </Button>
      </DialogActions>
    </Dialog>
  );
}
```

- [ ] **步骤 4：列表页**

创建 `web/src/app/admin/models/page.tsx`（表格与删除交互对齐 `admin/mcp/page.tsx` 的既有模式）：

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Button, IconButton, Paper, Snackbar, Stack, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, Tooltip, Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import NetworkCheckOutlinedIcon from "@mui/icons-material/NetworkCheckOutlined";
import ModelDialog from "@/components/admin/ModelDialog";
import ConfirmForceDialog from "@/components/admin/ConfirmForceDialog";
import { adminApi, LLMModel } from "@/lib/adminApi";

const PROVIDER_LABEL: Record<string, string> = {
  openai: "OpenAI 兼容",
  anthropic: "Anthropic",
};

export default function ModelsPage() {
  const [models, setModels] = useState<LLMModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<LLMModel | null>(null);
  const [snack, setSnack] = useState("");
  // 删除被引用的模型时（409）弹出的强制删除确认
  const [forceTarget, setForceTarget] = useState<LLMModel | null>(null);
  const [forceReason, setForceReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setModels(await adminApi.listModels());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleDelete = async (m: LLMModel, force = false) => {
    try {
      await adminApi.deleteModel(m.id, force);
      setForceTarget(null);
      setSnack(`已删除「${m.name}」${force ? "（已自动解绑引用的 Agent）" : ""}`);
      await load();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (force) {
        setError(message); // force 也失败（非 409 原因），直接展示
        return;
      }
      // 409：仍被 Agent 引用 → 弹强制删除确认
      setForceReason(message);
      setForceTarget(m);
    }
  };

  const handleTest = async (m: LLMModel) => {
    try {
      const r = await adminApi.testModel(m.id);
      setSnack(r.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h6">模型管理</Typography>
        <Button variant="contained" startIcon={<AddIcon />}
          onClick={() => { setEditing(null); setDialogOpen(true); }}>
          新增模型
        </Button>
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>名称</TableCell>
              <TableCell>供应商</TableCell>
              <TableCell>模型</TableCell>
              <TableCell>Base URL</TableCell>
              <TableCell>API Key</TableCell>
              <TableCell align="right">操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {models.map((m) => (
              <TableRow key={m.id}>
                <TableCell>{m.name}</TableCell>
                <TableCell>{PROVIDER_LABEL[m.provider] ?? m.provider}</TableCell>
                <TableCell>{m.model}</TableCell>
                <TableCell>{m.base_url || "（官方默认）"}</TableCell>
                <TableCell>{m.api_key_masked || "—"}</TableCell>
                <TableCell align="right">
                  <Tooltip title="测试连接">
                    <IconButton size="small" onClick={() => handleTest(m)}>
                      <NetworkCheckOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="编辑">
                    <IconButton size="small"
                      onClick={() => { setEditing(m); setDialogOpen(true); }}>
                      <EditOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="删除">
                    <IconButton size="small" onClick={() => handleDelete(m)}>
                      <DeleteOutlineIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))}
            {models.length === 0 && !loading && (
              <TableRow>
                <TableCell colSpan={6} align="center">暂无模型，点击右上角新增</TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <ModelDialog open={dialogOpen} initial={editing} onClose={() => setDialogOpen(false)}
        onSaved={() => { setSnack("已保存"); void load(); }} />

      <ConfirmForceDialog
        open={forceTarget !== null}
        title={`删除「${forceTarget?.name ?? ""}」`}
        message={forceReason}
        confirmLabel="解绑并删除"
        onCancel={() => setForceTarget(null)}
        onConfirm={() => forceTarget && handleDelete(forceTarget, true)}
      />

      <Snackbar open={snack !== ""} autoHideDuration={4000} onClose={() => setSnack("")}
        message={snack} />
    </Box>
  );
}
```

注意：`ConfirmForceDialog` 若项目中不存在（MCP/A2A 页面用的是内联 Dialog），则按 `admin/mcp/page.tsx` 中 409 → force 确认的实现方式就地实现一个同等的确认 Dialog（Dialog + 两个 Button），不要新建全局组件。执行者先 grep `force` 于 `web/src/app/admin/mcp/page.tsx` 确认现状后择一。

- [ ] **步骤 5：验证**

运行（在 `web/` 下）：`npm test`（现有用例不回归）与 `npm run build`
预期：测试全绿；build 成功无类型错误

- [ ] **步骤 6：Commit**

```bash
git add web/src/lib/adminApi.ts web/src/components/admin/AdminShell.tsx web/src/components/admin/ModelDialog.tsx web/src/app/admin/models/page.tsx
git commit -m "feat: 模型管理页（列表/新增/编辑/测试/删除保护）"
```

---

### 任务 10：Agent 表单选择模型

**文件：**
- 修改：`web/src/components/admin/AgentForm.tsx`
- 修改：`web/src/app/admin/agents/new/page.tsx` 与 `web/src/app/admin/agents/[id]/edit/page.tsx`（加载模型列表传入）

- [ ] **步骤 1：AgentForm 增加模型选择**

props 增加一项（`skills` 之后）：

```ts
  /** 「模型管理」中的模型，供单选绑定 */
  llmModels?: LLMModel[];
```

参数解构增加 `llmModels = [],`。state 区追加：

```ts
  // 模型绑定：单选；null = 回落全局 LLM_* 环境变量
  const [modelId, setModelId] = useState<number | null>(initial?.model_id ?? null);
  const [modelTemperature, setModelTemperature] = useState<string>(
    initial?.model_snapshot?.temperature != null
      ? String(initial.model_snapshot.temperature)
      : "",
  );
  const [modelMaxTokens, setModelMaxTokens] = useState<string>(
    initial?.model_snapshot?.max_tokens != null
      ? String(initial.model_snapshot.max_tokens)
      : "",
  );
```

提交 payload（现有 onSubmit 组装处）追加 `model` 字段：

```ts
    model: {
      model_id: modelId,
      temperature: modelTemperature === "" ? null : Number(modelTemperature),
      max_tokens: modelMaxTokens === "" ? null : Number(modelMaxTokens),
    },
```

表单 UI（技能选择区之后）追加：

```tsx
        <FormControl fullWidth>
          <InputLabel id="agent-model-label">模型（不选则使用全局配置）</InputLabel>
          <Select
            labelId="agent-model-label"
            value={modelId ?? ""}
            label="模型（不选则使用全局配置）"
            onChange={(e) => setModelId(e.target.value === "" ? null : Number(e.target.value))}
          >
            <MenuItem value="">不指定（使用全局配置）</MenuItem>
            {llmModels.map((m) => (
              <MenuItem key={m.id} value={m.id}>
                {m.name}（{m.provider} / {m.model}）
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <Stack direction="row" spacing={2}>
          <TextField
            label="Temperature 覆盖（可选）"
            type="number"
            inputProps={{ step: "0.1", min: 0, max: 2 }}
            value={modelTemperature}
            onChange={(e) => setModelTemperature(e.target.value)}
            helperText="留空使用运行时默认"
          />
          <TextField
            label="Max Tokens 覆盖（可选）"
            type="number"
            inputProps={{ min: 1 }}
            value={modelMaxTokens}
            onChange={(e) => setModelMaxTokens(e.target.value)}
            helperText="留空使用运行时默认"
          />
        </Stack>
```

（`Select/MenuItem/InputLabel/FormControl` 若未 import 则补；对齐文件既有 import 风格。）

- [ ] **步骤 2：两个页面传入模型列表**

`agents/new/page.tsx` 与 `agents/[id]/edit/page.tsx`：按文件内已有的 `listMcpServers`/`listSkills` 并行加载方式，追加 `adminApi.listModels()`（`Promise.all` 中加一项），结果传入 `<AgentForm llmModels={models} ... />`。以现有代码为准照葫芦画瓢，不改变其余加载逻辑。

- [ ] **步骤 3：验证**

运行（在 `web/` 下）：`npm test && npm run build`
预期：全绿

- [ ] **步骤 4：Commit**

```bash
git add web/src/components/admin/AgentForm.tsx web/src/app/admin/agents/new/page.tsx "web/src/app/admin/agents/[id]/edit/page.tsx"
git commit -m "feat: Agent 表单支持选择模型与参数覆盖"
```

---

### 任务 11：终验与冒烟

**文件：** 无新改动（发现问题则回改对应任务并重跑）

- [ ] **步骤 1：后端全量**

```bash
uv run pytest -q
uv run basedpyright
uv run ruff check <本次改动文件>
```
预期：全绿 / 0 error

- [ ] **步骤 2：前端全量**

```bash
cd web; npm test; npm run build
```
预期：全绿 / build 成功

- [ ] **步骤 3：DB 迁移冒烟（需要 Docker 环境）**

```bash
docker compose up -d --build
docker compose logs backend | Select-String "migration|alembic"
```
预期：启动日志显示迁移执行成功（应用启动自动 `upgrade head` 到 `0013_llm_models`）

- [ ] **步骤 4：对照规格 §9 手动验收**

1. 管理中心 → 模型管理 → 新增一个 OpenAI 兼容模型 → 测试连接成功；故意填错 Key 测试提示"认证失败"；填错 model 名提示"模型不存在"
2. Agent 编辑页选择该模型并设 temperature → 发布后对话生效
3. 编辑该模型记录（如改 model 名）→ 不重启，重新对话即生效
4. 删除被引用模型 → 409 提示；force 删除 → Agent 回落全局配置仍可对话
5. 新建一个不选模型的 Agent → 行为与改动前一致
6. 步骤 1-2 的后端/前端全量命令全绿

- [ ] **步骤 5：收尾 Commit（如有回改）**

```bash
git add -A
git commit -m "chore: 模型管理功能终验修正"
```
（无回改则跳过）
