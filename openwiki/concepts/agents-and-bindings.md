---
type: concept-guide
title: Agent 配置与绑定模型
description: 解释注册表与运行时 JSONB 快照的双层绑定、写时解析与刷新/解绑、技能绑定门禁、发布门控、保留 slug 规则及工厂缓存失效协同。
tags: [agents, bindings, registry, snapshots, publish, cache-invalidation]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-472b0deb13ce18764b8bb6bc
    resource: repo://src/a2a_gateway/agent_factory.py
  - id: openwiki-source-d3e47f45c8a3dad144965b78
    resource: repo://src/a2a_gateway/repository.py
  - id: openwiki-source-7fd5ad639fdb57e093b58272
    resource: repo://src/a2a_gateway/routes/admin.py
  - id: openwiki-source-c728168ff3e83d7ca738448b
    resource: repo://src/a2a_gateway/routes/registry.py
  - id: openwiki-source-c8f81e8f018673df85b2fb8b
    resource: repo://tests/test_bindings.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# Agent 配置与绑定模型

## 职责与归属

- **注册表层**：`a2a_endpoints`、`mcp_servers`、`skills` 三张可复用定义表（`models.py:97-189`），由 [registry 路由](/openwiki/integrations/mcp.md)维护。
- **Agent 层**：`agent_configs` 保存勾选 id 列表（选择意图）+ JSONB 快照（运行时绑定），见 [数据模型](/openwiki/architecture/data-model.md)。
- **解析/刷新**：`repository.py` 的 `resolve_*`、`_merge_bindings`、`refresh_agents_for_*`、`detach_*`、`validate_skill_bindings`。
- **缓存**：`agent_factory.py` 的 `(agent_id, updated_at)` 键与 `invalidate_agent`。
- **管理面**：`routes/admin.py`（Agent CRUD/发布）、`routes/registry.py`（注册表变更后刷新）。

## 双层模型：注册表 vs 快照

```
A2AEndpoint / McpServer / Skill（可复用定义，enabled 控制停用）
        │ Agent 勾选 → a2a_target_ids / mcp_server_ids / skill_ids
        │ 写入时 resolve → a2a_targets / mcp_servers / skills（JSONB 快照）
        ▼
agent_factory 构图只读快照 → a2a_client / MCP 工具 / 技能注入
```

- 解析按勾选顺序展开；已删除或停用条目静默跳过（`repository.py:86-111`）。
- 技能额外要求 `approved` 才进快照（`repository.py:527-543`）；快照含正文全文与 `allow_scripts`（`repository.py:475-489`）。
- 运行时零回表：`get_agent_instance` 直接用 `agent.a2a_targets`/`mcp_servers`/`skills`（`agent_factory.py:76-91`）。

## 写时合并（注册表 vs 手动条目）

`_merge_bindings` 规则（`repository.py:134-179`，回归测试 `tests/test_bindings.py`）：

| 输入形态 | 行为 |
|---|---|
| 注册表解析结果 | **始终保留**在快照前部 |
| `manual is None`（请求未带手动列表，如仅改状态的老客户端） | 保留既有手动条目中不与注册表冲突的部分 |
| 显式 `manual` 列表 | 整体替换；与注册表重复（A2A 按 `url`、MCP 按 `name`）以注册表为准；手动内部去重 |

历史缺陷：旧实现只返回手动条目，导致每次保存 Agent 都清空注册表勾选的快照（`tests/test_bindings.py:3-7`）。`_resolve_bindings` 把 id 列与合并快照一并返回（`repository.py:163-179`）。

## 注册表变更 → 刷新与解绑

**更新注册表行**后，`refresh_agents_for_*` 重新解析引用它的所有 Agent 快照并提交（`repository.py:409-440`、`694-707`）；随后路由对每个受影响 Agent 调 `invalidate_agent`（`registry.py:116-118`、`231-233`、`457-459`）。

**删除注册表行**：

- 默认拒绝：若仍被引用，返回「仍被 N 个 Agent 引用…可先取消勾选，或用 `?force=true`」（`registry.py:54`、`134`、`249`、`474`）。
- `?force=true`：先 `detach_*_from_agents`（移除 id 并重解析快照，`repository.py:443-469`、`710-722`）再删除，并对受影响 Agent 失效缓存。

**停用（enabled=false）** 是宽松语义：不删行、保留 Agent 勾选，解析时静默跳过，重新启用即恢复（`models.py:116-117`）。

## 技能绑定门禁

创建/更新/发布 Agent 前走 `_validate_skill_bindings` → `validate_skill_bindings`（`admin.py:64-79`、`repository.py:497-524`）：

- 勾选了不存在的 id → 拒绝；
- `pending` / `rejected` 审核状态 → 拒绝（`approved` 才放行）；
- 绑定正文总量超过 `MAX_BINDING_CONTENT_BYTES` → 拒绝；
- `enabled` **不参与门禁**：保留勾选、运行时跳过、启用即恢复。

失败按场景转 400（创建/更新）或 409（发布）（`admin.py:149-152`、`166-169`、`200-203`）。纵深防御：`skill_snapshot` 即使被绕过解析也带 `review_status` 供注入侧再查（`repository.py:487-488`）。

## 发布门控与保留 slug

- `status` 两态：`draft` / `published`；默认与聊天路由、A2A 端点均**只接受 published**（`_resolve_agent`、`_resolve_published_agent`）。
- **发布**（`POST .../publish`）：先跑技能门禁，再失效缓存，最后置 `PUBLISHED`（`admin.py:191-205`）——门禁失败不会留下半发布状态。
- **下线**（`unpublish`）：置回 `DRAFT`；**默认 Agent（slug `/`）不可删除、不可下线**（`admin.py:184-185`、`217-218`）。
- **保留 slug**：`RESERVED_SLUGS = {"/", "", "a2a"}` —— `/` 与空串留给默认 Agent，`a2a` 避让对外地址前缀 `/a2a/{slug}`；创建时冲突返回 400/409（`admin.py:58-61`、`144-148`）。
- **测试对话**：`POST .../test` 不经公开路由，`draft` 也可测（`admin.py:223-235`）。

## 默认 Agent 播种与历史升级

`ensure_default_agent`（`repository.py:855-873`）：

- 不存在则创建 slug `/` 并登记 Hermes 端点进注册表（由 `HERMES_A2A_URL/TOKEN` 构造，`repository.py:875+`）。
- 已存在但只有内嵌 `a2a_targets`（无 id 列）的历史行：自动为每个 url 在注册表补登记并回填 `a2a_target_ids`，使管理中心能显示「已勾选」。

配合启动时的 `ensure_all_agent_api_keys`（`main.py:49-53`），构成配置层的自举。

## 工厂缓存失效：updated_at 键 + 显式 invalidate

两套机制协同（`agent_factory.py`）：

1. **自动**：缓存键含 `agent.updated_at.isoformat()`；行被 ORM 更新后键变化，`get_agent_instance` 未命中即重建，并清理同 id 旧键及其 wrapper（`agent_factory.py:70-99`）。
2. **显式**：所有管理/注册表写路径在提交后调用 `invalidate_agent(agent_id)`，立即关闭旧 wrapper 并移除缓存（`admin.py:171,186,204,219`；`registry.py` 各变更点）。

变更顺序刻意为「写库 → invalidate →（发布则置状态）」，确保下一请求一定重建。应用关停时 `close_all` 清空全部缓存与 checkpointer（`agent_factory.py:119-128`）。

## 不变量

- 运行时绑定 = 快照列；快照列与 id 列在每次 Agent 保存、注册表 refresh/detach 时同步维护。
- 仅 `approved` 技能可出现在快照中；门禁在创建/更新/发布三处重复施加。
- `/`、`""`、`a2a` 永不分配给自定义 Agent；默认 Agent 不可删/不可下线。
- 注册表行被删除前要么无引用，要么 `force` 解绑；`enabled=false` 永不自动解绑。

## 代表性测试

- `tests/test_bindings.py`：合并语义与手动 MCP 校验。
- `tests/test_admin_api.py`：保留 slug/冲突、发布下线、401。
- `tests/test_registry_api.py`、`tests/test_skills_binding.py`：注册表刷新、`?force` 解绑、技能门禁与快照刷新。
- `tests/test_agent_factory.py`：缓存复用、按 `updated_at` 失效、显式失效。

相关页：[数据模型与迁移](/openwiki/architecture/data-model.md)、[技能系统](/openwiki/concepts/skills.md)、[Agent 图构建与编排](/openwiki/workflows/agent-graph.md)、[A2A 上游客户端](/openwiki/integrations/a2a-client.md)。
