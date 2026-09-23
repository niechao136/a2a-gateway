# 模型管理功能设计规格

- 日期：2026-09-23
- 状态：设计已分节展示并经用户确认（方案 A：注册表 + 单选快照绑定）
- 范围：d:\web\a2a-gateway

## 1. 背景与目标

当前网关的 LLM 配置是全局唯一的：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 三个环境变量（`src/a2a_gateway/config.py`），`llm.py` 的 `build_llm()` 无参读取模块级缓存的 settings，所有 agent 共用同一模型，改配置需重启。

目标：

1. 新增"模型注册表"：管理页面支持新增、修改、连通性测试、删除模型配置；支持 OpenAI 兼容端点与 Anthropic 原生协议两个协议族。
2. 每个 agent 可指定模型，并可选覆盖 `temperature` / `max_tokens`；未指定时回落全局环境变量，保留零配置启动能力。

## 2. 范围

**纳入**：

- 新表 `llm_models` + alembic 迁移 `0013`
- 模型注册表 CRUD + 连通性测试 API
- `agent_configs` 模型绑定（`model_id` + 快照）、repository 三件套
- `build_llm` 参数化与 provider 分支
- 管理前端：模型管理页、模型表单弹窗、Agent 表单扩展

**明确排除（YAGNI）**：

- api_key 静态加密（沿用明文入库 + 出参脱敏惯例）
- 通过环境变量名引用 key 的间接机制
- 模型用量统计、配额、流式测试
- Gemini 等其他协议族（`llm_provider` 枚举值可扩展）

## 3. 数据模型

### 3.1 新表 `llm_models`

| 字段 | 类型 | 约束/说明 |
|------|------|-----------|
| `id` | INTEGER PK | 自增 |
| `provider` | `llm_provider` 枚举 | `openai`（涵盖一切兼容端点）/ `anthropic`；PG 枚举类型，DO 块幂等创建 |
| `name` | VARCHAR(128) | 展示名，如 "DeepSeek V3" |
| `base_url` | VARCHAR(512) | `openai` 必填；`anthropic` 可空（用 SDK 默认） |
| `api_key` | VARCHAR(512) | 明文入库，对齐现有 credentials 惯例 |
| `model` | VARCHAR(128) | 模型标识，如 `deepseek-chat`、`claude-sonnet-4-5` |
| `description` | VARCHAR(512) | 可空备注 |
| `created_at` / `updated_at` | DateTime | 对齐现有表 |

### 3.2 `agent_configs` 扩展（单选绑定）

- `model_id`：INTEGER 可空，FK → `llm_models(id)`；无级联删除——删除一律走解绑流程
- `model_snapshot`：JSONB 可空：

```json
{
  "provider": "openai",
  "name": "DeepSeek V3",
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-...",
  "model": "deepseek-chat",
  "temperature": 0.7,
  "max_tokens": 4096
}
```

`temperature` / `max_tokens` 为 Agent 表单覆盖值（可空），其余字段由注册表解析。api_key 进快照对齐现有范式（MCP snapshot 携带 credentials），运行时零额外查询。

### 3.3 迁移 `alembic/versions/0013_llm_models.py`

幂等 raw SQL 风格（对齐既有迁移）：

1. DO 块幂等创建枚举类型 `llm_provider`（`'openai'`, `'anthropic'`）
2. `CREATE TABLE IF NOT EXISTS llm_models (...)`
3. `ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS model_id INTEGER` / `model_snapshot JSONB`
4. `model_id` 外键约束（IF NOT EXISTS 守卫）
5. 启动时由 `run_migrations()` 自动执行，无需手工操作

### 3.4 Repository（`src/a2a_gateway/repository.py`）

- Agent create/update：`_resolve_bindings` 同处解析 `model_snapshot`（按 `model_id` 查 `llm_models`，合并 temperature/max_tokens 覆盖）
- `refresh_agents_for_model(model_id)`：模型记录变更后刷新所有引用方快照（对齐 `refresh_agents_for_mcp_servers` 模式）
- `detach_model_from_agents(model_id)`：删除前置空引用方 `model_id`/`model_snapshot`（对齐 `detach_mcp_server_from_agents` 模式）

## 4. 后端 API

### 4.1 路由 `src/a2a_gateway/routes/models.py`

`APIRouter(prefix="/api/admin")`，在 `main.py` 挂载；全部 `Depends(get_current_admin)`。

| 端点 | 说明 |
|------|------|
| `GET /api/admin/models` | 列表，api_key 脱敏 |
| `POST /api/admin/models` | 创建；按 provider 校验必填项（openai 必填 base_url） |
| `PUT /api/admin/models/{id}` | 修改；api_key 留空 = 保持原值 |
| `DELETE /api/admin/models/{id}?force=` | 被引用返回 409（中文 detail）；`force=true` 先解绑再删 |
| `POST /api/admin/models/{id}/test` | 已保存条目连通性测试 |
| `POST /api/admin/models/test` | 未保存表单参数直测（对齐 `agents/test-connection` 模式） |

### 4.2 请求/响应 schema（pydantic）

- `ModelCreate` / `ModelUpdate`：`provider`, `name`, `base_url`, `api_key`, `model`, `description`
- `ModelOut`：api_key 输出为脱敏形式（对齐现有 `credentials_masked` 惯例）
- Agent 请求体新增 `model` 绑定对象：`{model_id: int, temperature?: float, max_tokens?: int}` 或 null。创建时 null = 不绑定（回落全局）；更新时 null = 不修改绑定，提供则**整体替换**（`model_id` 传 null = 清除绑定回落全局，避免"不提供"与"清除"歧义）；响应附带模型摘要（id/name/provider/model）
- 错误风格：`HTTPException` 中文 detail；测试端点契约 `{"ok": bool, "message": str}`

### 4.3 连通性探针 `src/a2a_gateway/llm_probe.py`

- `async probe_llm(provider, base_url, api_key, model, timeout=10.0) -> (ok, message)`，httpx 直调 REST（函数名不用 test_ 前缀，避免被 pytest 误收集）
- `openai`：`POST {base_url}/chat/completions`，body `{"model", "messages":[{"role":"user","content":"ping"}], "max_tokens":1}`，`Authorization: Bearer`
- `anthropic`：`POST {base_url|https://api.anthropic.com}/v1/messages`，headers `x-api-key` + `anthropic-version: 2023-06-01`，body `{"model", "max_tokens":1, "messages":[...]}`
- 成功：`ok=True`，message 附模型回复确认；失败：将超时 / 401 / 403 / 404 / 连接错误映射为中文 message（能区分 key 无效、模型不存在、地址不可达）

## 5. 模型构建链路

### 5.1 `src/a2a_gateway/llm.py`

- 保留无参 `build_llm()`（回落路径，读全局 settings，行为不变）
- 新增 `build_llm_from_snapshot(snapshot: dict)`：
  - `provider == "openai"`：现有 `ThoughtSignatureChatOpenAI`（保留 Gemini thought_signature 补丁与 `streaming=True`）
  - `provider == "anthropic"`：`ChatAnthropic`（`model_name`, `api_key`, `max_tokens`）
  - `temperature` / `max_tokens` 非空时作为构造参数传入，否则不传
  - 未知 provider 抛中文 `ValueError`

### 5.2 `src/a2a_gateway/graph.py`

`build_graph(agent)` 中原 `llm = build_llm()` 改为：

```python
llm = build_llm_from_snapshot(agent.model_snapshot) if agent.model_snapshot else build_llm()
```

缓存机制不变：模型注册表变更 → `refresh_agents_for_model` 更新快照与 `updated_at` → `get_agent_instance` 缓存键失效 → 图自动重建。

### 5.3 依赖

`pyproject.toml` 新增 `langchain-anthropic`。

## 6. 前端

### 6.1 新增文件

- `web/src/app/admin/models/page.tsx`：列表页（Paper + Table：名称 / 供应商 / 模型 / base_url / 操作），含被引用提示与删除确认（409 时提示 force）
- `web/src/components/admin/ModelDialog.tsx`：新增/编辑弹窗（参考 `McpServerDialog.tsx`）：provider 下拉切换表单项、api_key 编辑留空提示"不修改"、内嵌"测试连接"按钮（走 `POST /models/test`）

### 6.2 修改文件

- `web/src/components/admin/AdminShell.tsx`：`NAV_ITEMS` 注册"模型管理"
- `web/src/lib/adminApi.ts`：新增 `listModels` / `createModel` / `updateModel` / `deleteModel` / `testModel` 方法（复用 `request<T>` 鉴权封装）
- `web/src/components/admin/AgentForm.tsx`：新增模型下拉（单选、可清空 = 回落全局）、`temperature` / `max_tokens` 可选数字输入；新建/编辑页共用表单自动生效

## 7. 测试策略

- 后端 pytest（无 DB 模式）：
  - 模型 CRUD API：conftest `dependency_overrides` + monkeypatch 路由模块内 repository 函数（参考 `tests/test_admin_api.py`）
  - 删除保护：被引用 409、force 解绑用例
  - 连通性测试端点：FakeWrapper 替换探针函数（参考现有连通性测试写法）
  - `build_llm_from_snapshot` 单测：provider 分支、参数覆盖传递、无快照回落
- 前端 vitest：`adminApi` 新方法与表单校验等纯函数用例
- 质量门禁：pyright standard 0 error、ruff line-length 100

## 8. 安全与边界

- api_key 明文入库 + 出参脱敏（沿用 credentials 惯例，不引入加密机制）
- 默认 Agent（slug="/"）同样适用：不选模型即回落全局 `LLM_*` env
- 环境变量 `LLM_*` 三项保留为兜底，`.env.example` 不删
- 全局兜底：未保存的表单测试不接受任意 URL 之外的额外能力，仅做一次只读的短消息调用

## 9. 验收标准

1. 管理页可新增模型并通过"测试连接"验证；测试结果能区分 key 无效 / 模型不存在 / 地址不可达
2. Agent 编辑页可选模型并覆盖 temperature / max_tokens；发布后对话使用所选模型与参数
3. 模型记录修改后，引用它的 agent 无需重启即生效
4. 被引用的模型删除返回 409；`force=true` 解绑并删除后，agent 自动回落全局配置
5. 未选择模型的 agent 行为与现状完全一致（回归保证）
6. pytest 全绿、pyright 0 error、ruff 通过
