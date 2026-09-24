---
type: data-model-reference
title: 数据模型与迁移
description: 说明 a2a-gateway 的 11 张应用表、快照与 id 列表并存的绑定存储、模型注册表与单选绑定列、会话目录与 checkpointer 的分工，以及 Alembic 0001-0013 迁移与旧库接管机制。
tags: [data-model, alembic, postgres, jsonb, persistence, migrations]
sources:
  - id: openwiki-source-d94c5bb3e89b0c7c5b92e516
    resource: repo://alembic/versions/0002_registries.py
  - id: openwiki-source-72904fa0fee686c7ad665e4a
    resource: repo://alembic/versions/0010_skills.py
  - id: openwiki-source-0bdafe851e73f2f2667b0444
    resource: repo://alembic/versions/0013_llm_models.py
  - id: openwiki-source-43725894c7c3e0df0d15d0b5
    resource: repo://src/a2a_gateway/config.py
  - id: openwiki-source-4632c5faac1ef57d5c2875ea
    resource: repo://src/a2a_gateway/database.py
  - id: openwiki-source-5587127d632cfcdc010b44e9
    resource: repo://src/a2a_gateway/main.py
  - id: openwiki-source-0316833341c8160aca348df0
    resource: repo://src/a2a_gateway/migrations.py
  - id: openwiki-source-c02a6d45a645df8106612f51
    resource: repo://src/a2a_gateway/models.py
  - id: openwiki-source-d3e47f45c8a3dad144965b78
    resource: repo://src/a2a_gateway/repository.py
  - id: openwiki-source-e74227ee06b16f894c3e3826
    resource: repo://TODO.md
generated: { by: "opencode", at: "2026-09-24T01:27:47.842Z" }
verified:
  - by: openwiki/0.5.2
    at: 2026-09-24T01:27:47.842Z
---

# 数据模型与迁移

## 职责与归属

ORM 模型集中在 `src/a2a_gateway/models.py`（11 张应用自有表，另有 Alembic 的 `alembic_version` 与 LangGraph checkpointer 系列表）；CRUD 与绑定解析集中在 `src/a2a_gateway/repository.py`；引擎/会话在 `src/a2a_gateway/database.py`；启动迁移在 `src/a2a_gateway/migrations.py`，迁移脚本在 `alembic/versions/`（0001–0013）。消息本体不进应用表，由 LangGraph `AsyncPostgresSaver` 按 `thread_id` 存放于同库的 checkpoints 系列表。

## 表清单与职责

| 表 | 模型 | 职责 |
|---|---|---|
| `agent_configs` | `AgentConfig` | Agent 配置：slug 路由标识（`/` 保留为默认）、绑定 id 列表 + JSONB 运行时快照、`model_id`/`model_snapshot` 单选模型绑定、`system_prompt`、发布状态 |
| `llm_models` | `LLMModel` | 模型注册表（「模型管理」）：provider（openai/anthropic）、base_url/api_key/model；Agent 侧保存引用 + 快照（`models.py:160-187`） |
| `a2a_endpoints` | `A2AEndpoint` | A2A 上游注册表：url/token/鉴权方式，可复用的服务定义 |
| `mcp_servers` | `McpServer` | MCP 注册表：三种 transport（stdio/sse/streamable_http）+ 凭据 |
| `skills` | `Skill` | 技能注册表：SKILL.md 正文与附件全量入库、审核状态机、`allow_scripts` |
| `api_keys` | `ApiKey` | 对外 A2A 调用凭据：按 Agent 独立管理，`(agent_id, name)` 唯一 |
| `admin_users` | `AdminUser` | 管理中心登录账号（bcrypt 哈希） |
| `conversations` | `Conversation` | 会话目录：`thread_id` → 身份归属（visitor/user），只存标题/时间，不存消息 |
| `pending_a2a_tasks` | `PendingA2ATask` | input-required 挂起映射：网关会话/对外 task_id → 下游任务 |
| `chat_connectors` | `ChatConnector` | 聊天平台连接器：平台凭据 JSONB、1:1 绑定 Agent |
| `chat_connector_conversations` | `ConnectorConversation` | 连接器会话映射：`(connector_id, chat_id)` ↔ `thread_id` |

公共时间戳由 `BaseMixin` 提供：`created_at`/`updated_at` 均 `server_default=now()`，`updated_at` 带 `onupdate`（`src/a2a_gateway/models.py:49-57`）。

## 快照与 id 列表并存的绑定存储

`agent_configs` 对 A2A/MCP/技能各存两列（`src/a2a_gateway/models.py:68-82`）：

- **id 列表**（`a2a_target_ids` / `mcp_server_ids` / `skill_ids`）：管理界面勾选的注册表主键，是「选择意图」。
- **JSONB 快照**（`a2a_targets` / `mcp_servers` / `skills`）：写入时由 id 解析出的运行时载荷，是「实际绑定」；`a2a_client`、MCP 工具构造与提示注入只读快照，构图路径零 DB 依赖。

模型绑定遵循同一范式但为**单选**：`model_id`（FK → `llm_models.id`，可空，NULL 表示回落全局 `LLM_*`）+ `model_snapshot`（JSONB，含 temperature/max_tokens 覆盖，`models.py:83-88`）。运行时 `resolve_llm` 只读快照；与 A2A/MCP 不同，模型**没有** id 勾选列表字段，管理端提交单个 `model_id` 即可。

写时解析规则：

- 按勾选顺序解析；已删除或 `enabled=false` 的条目静默跳过（`src/a2a_gateway/repository.py:90-115`）。
- 技能额外要求 `review_status == approved` 才进入快照（`src/a2a_gateway/repository.py:652-668`）。
- 注册表解析结果与手动条目合并：注册表结果始终保留，手动条目按去重键（A2A 用 `url`、MCP 用 `name`）过滤冲突（`src/a2a_gateway/repository.py:173-199`）。
- 模型解析为整体替换语义：`resolve_model_binding` 将 `model_id=None` 解析为 `(None, None)`；所选模型不存在时抛 `ValueError`（`repository.py:157-170`）。
- 注册表变更后由 `refresh_agents_for_*` 把新快照刷回引用它的 Agent（A2A/MCP：`repository.py:502-533`；技能：`819-832`；模型：`572-584`，保留各自 temperature/max_tokens）。

技能快照刻意存正文全文（「快照即全部」），使 `agent_factory` / `graph.py` 的注入与 `load_skill` 闭包无需每轮查库（`src/a2a_gateway/models.py:196-212`）。

## 枚举细节：按成员 value 建 PG 类型

SQLAlchemy 默认用**成员名**（`DRAFT`/`PUBLISHED`）建 PostgreSQL 枚举，而业务代码与 `server_default` 使用**成员值**（`draft`/`published`）。`AgentStatus`、`SkillReviewStatus`、`ConnectorPlatform`、`LLMProvider` 四处均通过 `values_callable` 强制按成员 value 建类型，避免 `invalid input value for enum`（`src/a2a_gateway/models.py:89-100`、`171-180`、`220-229`、`331-338`）。`server_default` 直接写 `.value` 字符串，依赖同一约定。

## 会话目录与 checkpointer 的分工

`conversations` 只维护「谁有哪些会话 + 标题/时间」这一层目录；消息本体由 Checkpointer 按 `thread_id` 存放（`src/a2a_gateway/models.py:269-294`）。因此匿名 → 登录归并只需改 `owner_kind`/`owner_id` 两列，消息零搬迁、历史可原地续聊。`owner_kind` 取值：`visitor`（后端签发 uuid）或 `user`（`admin_users.username`）。`connector_conversations` 同样只存映射与最近发言人，消息仍在 checkpointer（`src/a2a_gateway/models.py:346-368`）。

引擎侧是同一 PostgreSQL 的两条连接：ORM 用 `database_url`（asyncpg 异步引擎，`pool_pre_ping`），checkpointer 用 `checkpoint_db_url`（psycopg）——见 `src/a2a_gateway/database.py:18-28` 与 [配置体系](/openwiki/architecture/configuration.md)。

## 挂起任务表

`pending_a2a_tasks` 以 `thread_id` 单列唯一作为双链共用键（`src/a2a_gateway/models.py:297-315`）：

- **链路 A（对话界面）**：键为会话 `thread_id`。
- **链路 B（对外 A2A Server）**：键为对外 `task_id`（新建任务时 `task_id == context_id`）。

行内保存下游 `target_url`/`task_id`/`context_id`/`question`；`agent_id` 外键级联删除。下游完成即删行，读取时按 `PENDING_A2A_TTL_SECONDS`（默认 86400s）过期清理。

## Alembic 迁移主题（0001–0013）

| 修订 | 主题 |
|---|---|
| `0001_initial` | `agent_configs`、`admin_users` 基线 |
| `0002_registries` | `a2a_endpoints`、`mcp_servers` + Agent 侧 id 列/快照列 |
| `0003_auth_and_description` | 鉴权/描述列 |
| `0004_drop_enabled_tools` | 删除历史 `enabled_tools` 列 |
| `0005`–`0007_api_keys*` | `api_keys` 表 → 补 `agent_id` → `(agent_id,name)` 唯一 |
| `0008_conversations` | 会话目录 |
| `0009_pending_a2a_tasks` | input-required 挂起映射 |
| `0010_skills` | `skills` 表 + 审核枚举（原生 DO 块幂等建类型，`alembic/versions/0010_skills.py:20-40`） |
| `0011_skill_scripts` | `allow_scripts` 列 |
| `0012_chat_connectors` | 连接器两张表 |
| `0013_llm_models` | `llm_models` 注册表 + `llmprovider` 枚举 + `agent_configs.model_id`/`model_snapshot`（`alembic/versions/0013_llm_models.py`） |

### `model_id` 外键：无级联，删除走应用层解绑

`0013` 创建的 `fk_agent_configs_model_id` **不带 `ON DELETE`**（无级联）；删除模型前必须由应用层先 `detach_model_from_agents` 将引用方的 `model_id`/`model_snapshot` 置 NULL（`repository.py:587-594`），否则受外键约束拒绝。这与 `api_keys`/`pending_a2a_tasks`/`chat_connectors` 的 `ondelete=CASCADE` 形成对比——模型是共享注册资源，不允许级联清掉 Agent 的绑定记录而不经应用逻辑。

### 已知风险：JSONB `server_default` 裸字面量

`TODO.md:16` 将 `skills.frontmatter`/`skills.files` 与 `agent_configs.skill_ids`/`skills` 四列的 JSONB `server_default` 写法标记为待真库验收项：若真库上报类型不匹配，需改为 `sa.text("'[]'::jsonb")`（`0002`/`0004` 已有该写法；`0010` 建表 SQL 已使用 `'[]'::jsonb`/`'{}'::jsonb` 显式转换）。

## 启动迁移与旧库接管

`run_migrations()`（`src/a2a_gateway/migrations.py:73-77`）顺序：

1. **`adopt_legacy_database()`**：若库中存在应用表（`agent_configs`/`admin_users`）但无 `alembic_version`，先 `stamp 0001_initial` 接管为基线库（`migrations.py:56-70`）。
2. **`upgrade head`**：执行全部迁移。

lifespan 中在线程里调用；迁移抛异常则回退 `Base.metadata.create_all`（仅建表、不做版本管理），随后仍继续播种默认 Agent/管理员/API Key（`src/a2a_gateway/main.py:41-54`）。`alembic/env.py` 注入 `migration_db_url` 并以 `Base.metadata` 为 autogenerate 目标（`alembic/env.py:28-31`）。

## 失败与边界

- 级联删除：`api_keys`、`pending_a2a_tasks`、`chat_connectors` 的 `agent_id` 均 `ondelete=CASCADE`；`connector_conversations` 对连接器同理；**`llm_models` 的 `model_id` FK 无级联**，删除须先应用层解绑。
- 注册表行被停用（`enabled=false`）不物理删除：Agent 勾选保留，解析时跳过，重新启用即恢复（`models.py:122-123`、`150`）。
- 迁移回退 `create_all` 后库不纳入版本管理，后续需人工对齐 `alembic_version`。

## 代表性测试

- `tests/test_bindings.py`：快照合并语义。
- `tests/test_skills_binding.py`、`tests/test_conversations.py`、`tests/test_pending_store.py`：绑定刷新、目录归属、挂起 TTL。
- `tests/test_connector_models.py`：模型注册与迁移文件存在性。
- `tests/test_models_schema.py`：`llm_models` 列集合、`model_id`/`model_snapshot` 绑定列、`LLMProvider` 成员值。
- `tests/test_repository_model.py`：`llm_model_snapshot` 字段、脱敏、`validate_llm_model` 校验。
- 相关页：[Agent 配置与绑定模型](/openwiki/concepts/agents-and-bindings.md)、[LLM 模型管理](/openwiki/concepts/llm-model-management.md)、[身份、会话与所有权](/openwiki/concepts/identity-and-conversations.md)、[input-required 与恢复流程](/openwiki/workflows/input-required-resume.md)。
