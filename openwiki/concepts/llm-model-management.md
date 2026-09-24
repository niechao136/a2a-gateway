---
type: concept-guide
title: LLM 模型管理
description: 说明模型注册表 llm_models、Agent 单选绑定与 model_snapshot 快照、连通性探针、按快照构图与全局 LLM_* 回落，以及管理端 CRUD/删除保护/前端模型页。
tags: [llm, model-registry, openai, anthropic, snapshot, probe, admin-ui]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-24T01:27:47.842Z
sources:
  - id: openwiki-source-4febd71d669a7c84b1d2f5c0
    resource: repo://src/a2a_gateway/graph.py
  - id: openwiki-source-2f0a5e6928c1db642bc6b674
    resource: repo://src/a2a_gateway/llm_probe.py
  - id: openwiki-source-f53adcab3542f0a7184408f6
    resource: repo://src/a2a_gateway/llm.py
  - id: openwiki-source-c02a6d45a645df8106612f51
    resource: repo://src/a2a_gateway/models.py
  - id: openwiki-source-d3e47f45c8a3dad144965b78
    resource: repo://src/a2a_gateway/repository.py
  - id: openwiki-source-a9ded1863fe33ba2a953bab4
    resource: repo://src/a2a_gateway/routes/models.py
  - id: openwiki-source-c906c556d0d86b9ccfe8ed8b
    resource: repo://src/a2a_gateway/schemas.py
  - id: openwiki-source-fa957d241a6fb4842d7a22c5
    resource: repo://tests/test_llm_build.py
  - id: openwiki-source-494639d234919c508222a9b4
    resource: repo://tests/test_llm_models_api.py
  - id: openwiki-source-6afbff39c9cceaa4209f6269
    resource: repo://tests/test_llm_probe.py
  - id: openwiki-source-394d273dff8cb185336e3e7e
    resource: repo://web/src/app/admin/models/page.tsx
  - id: openwiki-source-1873eb270f998ac2a1fcc38f
    resource: repo://web/src/components/admin/AgentForm.tsx
  - id: openwiki-source-aa36e7f832fcbc2b59f73122
    resource: repo://web/src/components/admin/ModelDialog.tsx
generated: { by: "opencode", at: "2026-09-24T01:27:47.842Z" }
---

# LLM 模型管理

## 职责与归属

- **注册表**：`llm_models` 表（`models.py:160-187`）——可复用的 LLM 定义（provider / base_url / api_key / model），与 A2A/MCP/Skill 注册表同范式，但 **Agent 侧为单选**（`model_id` FK + `model_snapshot`，无勾选列表列）。
- **管理面**：`routes/models.py` 全部 `/api/admin/models*` 接口（JWT 管理员认证）——CRUD、删除保护、连通性测试。
- **运行时**：`repository.llm_model_snapshot` / `resolve_model_binding` 写时解析快照；`llm.resolve_llm` / `build_llm_from_snapshot` 按快照或全局 `LLM_*` 构图。
- **探针**：`llm_probe.probe_llm`——httpx 直调 REST 发一条极短消息，区分 key 无效 / 模型不存在 / 地址不可达 / 超时。
- **前端**：`/admin/models` 列表页 + `ModelDialog` 新建/编辑弹窗；Agent 表单内嵌模型单选（`AgentForm`）。

## 注册表结构

| 列 | 说明 |
| --- | --- |
| `name` | 展示名，全局唯一（创建/改名重名 → 409） |
| `provider` | `openai` / `anthropic`（PG 枚举按**成员值**建，`models.py:171-180`） |
| `base_url` | openai 兼容端点必填（通常以 `/v1` 结尾）；anthropic 留空用官方默认 |
| `api_key` | **明文入库**（对齐 credentials 惯例）；管理 API 出参只给 `api_key_masked` |
| `model` | 模型标识，如 `deepseek-chat` / `claude-sonnet-4-5` |
| `description` | 备注 |

- `agent_configs.model_id` FK → `llm_models.id` **无级联**（迁移 `0013`）；删除前必须应用层 `detach_model_from_agents` 置空引用，否则外键拒绝（见 [数据模型](/openwiki/architecture/data-model.md)）。
- 模型**没有** `enabled` 停用列——删除即移除，不存在「停用后解析时跳过」的宽松语义（与 A2A/MCP/Skill 不同）。

## 管理 API（`routes/models.py`）

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/admin/models` | 列表；`_out` 只回 `api_key_masked`（`mask_secret`：前 3 后 4，≤8 字符打码 `***`，`schemas.py:165-171`） |
| POST | `/api/admin/models` | 创建；名称空 → 400；`validate_llm_model` 不合法 → 400（中文）；重名 → 409 |
| PUT | `/api/admin/models/{id}` | 更新；合并后按 provider 再校验；改名查重 → 409；**成功后** `refresh_agents_for_model` 刷新引用方快照（保留各自 temperature/max_tokens 覆盖）并对每个受影响 Agent `invalidate_agent`（`models.py:101-105`）——无需重启进程 |
| DELETE | `/api/admin/models/{id}` | 被引用且未 `?force=true` → **409**（提示可取消绑定或 force）；`force=true` 先对引用 Agent `invalidate_agent` → `detach_model_from_agents` 置空 `model_id`/`model_snapshot` → 再删行（`models.py:118-125`） |
| POST | `/api/admin/models/{id}/test` | 已保存模型连通性探测（读库内三元组） |
| POST | `/api/admin/models/test` | **未保存表单直测**；请求体复用 `LLMModelCreate`（api_key 为表单明文），不写库 |

- 更新时 `api_key` **留空/不传 = 保持原值**（配合出参脱敏：前端编辑不回显明文 key，`repository.py:474-476`、`ModelDialog.tsx:89-90`）。
- 出参 schema `LLMModelOut` 不含明文 `api_key` 字段（`schemas.py:193-204`）。

## 连通性探针（`llm_probe.py`）

`probe_llm(provider, base_url, api_key, model, timeout=10)` → `(ok, message)`：

- **httpx 直调 REST**（不依赖 langchain 客户端）；openai → `{base_url}/chat/completions` + Bearer；anthropic → `{base_url or 默认}/v1/messages` + `x-api-key` + `anthropic-version`；均发 `max_tokens=1` 的 `"ping"`。
- `transport` 参数供测试注入 `httpx.MockTransport`（零外部依赖）。
- 函数名刻意不用 `test_` 前缀，避免被 pytest 误收集。
- 失败信息全中文、可区分：
  - 超时 → `连接超时（Ns）：请检查地址可达性`
  - 连接错误 → `无法连接到服务地址：{ExcType}: {exc}`
  - 401/403 → `认证失败（HTTP n）：API Key 无效或无权限`
  - 404 → `接口不存在（HTTP 404）：请检查 base_url 是否正确（…通常以 /v1 结尾）`
  - 400 且 body 含 `model` → `模型不存在或不可用（HTTP 400）：请检查模型标识`
  - 其他 ≥400 → `服务返回错误（HTTP n）` + body 截断 200 字
- 成功但响应非 JSON（网关错误页）也返回 `ok=True`（连接本身已通）。

## 按快照构图与全局回落

```
Agent 绑定? ──否──► resolve_llm(None) ──► build_llm() 读 Settings.llm_*（零配置兜底）
    │是
    ▼
resolve_llm(agent.model_snapshot) ──► build_llm_from_snapshot(snapshot)
    provider 分支：openai → ThoughtSignatureChatOpenAI（含 thought_signature 补丁）
                   anthropic → ChatAnthropic
```

- `resolve_llm`（`llm.py:200-204`）：快照非空走 `build_llm_from_snapshot`，否则走 `build_llm()`。
- `build_llm_from_snapshot`（`llm.py:154-197`）：
  - provider 非 `openai`/`anthropic` → 中文 `ValueError`：`不支持的模型供应商：{p}`；
  - `api_key` 为空 → 提前抛中文 `模型未配置 API Key（请在「模型管理」中补充后重试）`（避免底层抛英文 `OpenAIError`）；
  - `temperature`/`max_tokens` **非空才覆盖**（快照里为 `None` 则用运行时默认）；
  - openai 分支 `base_url` 缺省回落 `_settings.llm_base_url`；anthropic 分支仅在快照提供 `base_url` 时传入。
- **显式绑定失败不会静默回落 `LLM_*`**——provider 非法或缺 key 时构图直接抛错（见 [配置体系](/openwiki/architecture/configuration.md)）。
- 构图调用点：`graph.py:319` `llm = resolve_llm(agent.model_snapshot)`（零回表，只读快照）。

## Agent 单选绑定语义

`AgentModelBinding`（`schemas.py:360-369`）三态——与 A2A/MCP/Skill 的勾选列表不同，**整体替换**（详见 [Agent 配置与绑定](/openwiki/concepts/agents-and-bindings.md)）：

| 载荷 | 结果 |
| --- | --- |
| `model` 字段缺失或 `null` | 不修改既有绑定 |
| 提供对象且 `model_id` 有值 | 整体替换为 `(model_id, llm_model_snapshot(...))`，可带 temperature/max_tokens 覆盖 |
| 提供对象且 `model_id: null` | **清除绑定**：`model_id`/`model_snapshot` 置 NULL，回落全局 `LLM_*` |

- `resolve_model_binding`（`repository.py:157-170`）：所选模型不存在 → `ValueError` → 路由 **400**。
- 快照结构由 `llm_model_snapshot` 唯一产出：`provider/name/base_url/api_key/model/temperature/max_tokens`（`repository.py:138-154`）。
- 模型行变更协同：`refresh_agents_for_model`（`repository.py:572-584`）重刷引用方快照并保留覆盖 → `invalidate_agent` 关旧 wrapper；删除走 `detach_model_from_agents`（`repository.py:587-594`）。

## 前端

- **列表页** `web/src/app/admin/models/page.tsx`：测试连接 / 编辑 / 删除操作；删除遇 409 弹二次确认后带 `?force=true` 重试（`page.tsx:88-115`）；平板档隐藏 Base URL/API Key 列。
- **弹窗** `ModelDialog.tsx`：本地校验（名称唯一、openai 必填 base_url、模型标识必填）；「测试连接」走未保存表单直测端点；编辑时 api_key 留空不提交该字段。
- **Agent 表单** `AgentForm.tsx:545-604`：模型单选下拉（空值 = 不指定/全局配置）+ temperature/max_tokens 覆盖输入；已绑模型不在列表中（可能已删）时警告「保存后将回落全局配置」；列表为空时提示前往「模型管理」。
- **API 封装** `adminApi.ts:629-665`：`listModels` / `createModel` / `updateModel` / `deleteModel(id, force)` / `testModel` / `testModelForm`。

## 测试

| 测试文件 | 覆盖 |
| --- | --- |
| `tests/test_llm_build.py` | openai/anthropic 分支、temperature/max_tokens 覆盖、未知 provider 与空 key 中文错误、`resolve_llm` 回落 |
| `tests/test_llm_probe.py` | `httpx.MockTransport` 模拟两端点；401→key 错误等状态映射 |
| `tests/test_llm_models_api.py` | 列表脱敏、openai 必填 base_url、重名 409、更新 refresh+invalidate、删除 409/force detach、已存/表单直测端点 |
| `tests/test_models_schema.py` | `llm_models` 列集合、`model_id`/`model_snapshot` 绑定列、`LLMProvider` 成员值 |
| `tests/test_repository_model.py` | 快照构图与绑定解析 |
| `tests/test_admin_api.py` | Agent 模型绑定三态与未知模型 400 |

- 全部零外部依赖：探针用 `MockTransport`，API 测试 monkeypatch repository，不发真实网络请求（见 [测试策略](/openwiki/operations/testing.md)）。

## 安全与配置注意

- `llm_models.api_key` **明文入库**；信任边界依赖管理面 JWT 与 DB 访问控制——出参只回 `api_key_masked`，更新留空保持原值（与 [安全](/openwiki/concepts/security.md) 中 API Key 明文惯例一致）。
- 探针端点仅管理员可达，只发 `max_tokens=1` 的极短只读请求。
- 表内模型配置**不经过 `Settings`**——修改后配合 refresh + 缓存失效立即生效，无需改环境变量或重启（与 `LLM_*` 全局回落的关系见 [配置体系](/openwiki/architecture/configuration.md)）。

相关页：[Agent 配置与绑定模型](/openwiki/concepts/agents-and-bindings.md)、[数据模型与迁移](/openwiki/architecture/data-model.md)、[配置体系与环境变量](/openwiki/architecture/configuration.md)、[Agent 图构建与编排](/openwiki/workflows/agent-graph.md)、[认证与安全面](/openwiki/concepts/security.md)、[测试策略](/openwiki/operations/testing.md)。
