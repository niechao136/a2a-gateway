---
type: architecture-overview
title: 系统架构总览
description: 解释 a2a-gateway 的三重角色、核心组件关系，以及一次请求穿过 nginx、FastAPI 路由、LangGraph 与持久层的控制/数据流。
tags: [architecture, overview, fastapi, langgraph, gateway]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-472b0deb13ce18764b8bb6bc
    resource: repo://src/a2a_gateway/agent_factory.py
  - id: openwiki-source-4febd71d669a7c84b1d2f5c0
    resource: repo://src/a2a_gateway/graph.py
  - id: openwiki-source-5587127d632cfcdc010b44e9
    resource: repo://src/a2a_gateway/main.py
  - id: openwiki-source-d38fe19aaef9124307badd98
    resource: repo://src/a2a_gateway/routes/a2a_server.py
  - id: openwiki-source-96e2981cfaad30985f414a47
    resource: repo://src/a2a_gateway/routes/chat.py
  - id: openwiki-source-e74227ee06b16f894c3e3826
    resource: repo://TODO.md
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 系统架构总览

## 系统是什么

a2a-gateway 是「基于 LangGraph 的多 Agent 平台」（`pyproject.toml:4`），技术栈为 FastAPI + LangGraph（后端）、Next.js + MUI（前端）、PostgreSQL（配置 + Checkpointer 同库）、Alembic、sse-starlette、a2a-sdk、MCP（`pyproject.toml:7-30`）。README 为空，事实上的项目概述在 `TODO.md:23-30`。

## 三重角色

1. **增强聊天网关（而非纯转发代理）**：每个已发布的 Agent 共享同一套 LangGraph ReAct 图——自带记忆（checkpointer + 历史压缩）、技能注入与工具集；调用下游 A2A 目标（如 Hermes）只是其工具之一。这是明确的架构决策：「增强模式：LangGraph Agent 自带记忆/工具，A2A 调用 Hermes 是其工具之一」（`TODO.md:36-40`）。
2. **对外 A2A Server**：每个发布态 Agent 按 slug 暴露在 `/a2a/{slug}`，提供 Agent Card 与 JSON-RPC（流式 + input-required），以按 Agent 的 API Key 鉴权；默认 Agent 在 `/a2a`。入口在 `src/a2a_gateway/routes/a2a_server.py`。
3. **运维管控中心**：JWT 管理员在 `/admin` 维护 Agent 配置、A2A 端点/MCP/技能三类注册表、连接器（飞书/Telegram/Slack）、语音代理与技能导入审核。路由聚合在 `main.py:87-93`。

所有 Agent 共享同一后端进程，按路由动态加载配置，而非每 Agent 一个进程（`TODO.md:38`）。

## 核心组件与关系

```
nginx (唯一入口 :10099)
 ├─ /api/*、/a2a*、/ws/*、/docs、/health ──► FastAPI backend:8000
 │                                              ├─ routes/{chat,admin,registry,a2a_server,speech,connectors}
 │                                              ├─ agent_factory ──► graph.build_graph ──► create_react_agent
 │                                              │        │                  │
 │                                              │        │                  ├─ tools (a2a_call/resume, MCP, skills, scripts)
 │                                              │        │                  └─ pre_model_hook (压缩/挂起/技能再注入)
 │                                              │        └─ AsyncPostgresSaver (checkpointer 单例)
 │                                              ├─ repository ──► PostgreSQL (10 应用表, 快照+id 列表)
 │                                              ├─ a2a_client ──► 上游 A2A (Hermes 等)
 │                                              ├─ sandbox_client ──► sandbox-gate ──unix socket─► sandbox-runner
 │                                              └─ notifier / speech / connectors adapters
 └─ 其余 ──► Next.js frontend:3000
```

分层职责：

| 层 | 归属 | 作用 |
|---|---|---|
| 入口/会话 | `routes/chat.py`、`identity.py` | slug 解析、身份 cookie、SSE 事件契约、会话目录 |
| 编排 | `graph.py`、`agent_factory.py` | 共享 ReAct 图、实例缓存 `(id, updated_at)`、历史压缩与技能注入 |
| 工具 | `tools.py`、`mcp_client.py`、`skills.py`、`sandbox_client.py` | A2A/MCP/技能/脚本四类工具 |
| 集成 | `a2a_client.py`、`routes/a2a_server.py`、`connectors/`、`routes/speech.py` | 上游 A2A、对外 A2A、平台入站、ASR/TTS 代理 |
| 持久 | `models.py`、`repository.py`、`migrations.py` | 快照写时解析、目录表、启动迁移 |
| 横切 | `config.py`、`auth*.py`、`deps.py`、`notifier.py`、`public_url.py` | 配置、三套凭据、告警、公网 URL 派生 |

## 一次请求的控制/数据流

### 聊天（浏览器 SSE）

1. nginx `/api/` → `POST /api/chat[/{slug}]`（`routes/chat.py`）。
2. `_resolve_agent`：仅允许 `PUBLISHED` Agent（`chat.py:62-70`）。
3. 身份 cookie 确保 + 会话目录 upsert（所有权不被劫持）。
4. `get_agent_instance`：缓存命中则复用图，否则从快照构图（`agent_factory.py:70-99`）。
5. `graph.astream_events` 映射为 SSE 事件 `token|tool_start|tool_end|interrupt|done|error`（`chat.py:78-116`）。
6. 工具若调 A2A：`A2AClientWrapper.stream_message_events` 流式转发，瞬态错误有限重试；下游 `InputRequired` 写入 pending 并在回合末触发 `interrupt`。
7. 回合结束 `done`；错误经统一兜底 + 可选告警 webhook。

### 对外 A2A（机器客户端）

1. nginx `/a2a/{slug}` → `a2a_rpc`：发布态检查 + API Key 归属校验（`a2a_server.py:111-145`）。
2. JSON-RPC 2.0，1.0/0.3 方法别名归一（`METHOD_ALIASES`）。
3. 新任务 `task_id=context_id`；流式返回任务帧 WORKING → COMPLETED/INPUT_REQUIRED；非流式返回 message 或 input-required task。
4. 携带 `task_id` 的后续请求走 pending 查找恢复，经同一图或直接转发匹配的下游 wrapper。

两条链路汇聚到同一 LangGraph 路径，共享 checkpointer 与 pending 存储。

## 状态与生命周期

- **配置态**：PostgreSQL 应用表；写时解析为 JSONB 快照，构图零 DB 依赖（见 [数据模型](/openwiki/architecture/data-model.md)）。
- **会话态**：`conversations` 目录 + checkpointer 消息；线程键 `thread_id` 贯穿聊天、A2A、连接器。
- **挂起态**：`pending_a2a_tasks` 双链（聊天 thread_id / A2A task_id），TTL 读时过期。
- **进程态**：图实例缓存（键含 `updated_at`，配置变更自动+显式失效）、A2A wrapper 池、checkpointer 单例、连接器每会话串行队列；关停 `close_all()`（`agent_factory.py:119-128`、`main.py:56-58`）。
- **启动**：迁移 → 种子（默认 Agent/admin/API Key）→ 服务（`main.py:39-58`）。

## 横切关注点

- **配置**：pydantic-settings + dotenv，真实环境变量优先（[配置页](/openwiki/architecture/configuration.md)）。
- **安全**：管理员 JWT、聊天身份 cookie、按 Agent API Key 三套凭据；A2A/MCP 出站共享认证方案；连接器平台验签；技能导入 SSRF/zip 防护；沙箱双容器隔离（[安全页](/openwiki/concepts/security.md)）。
- **失败**：全局 500 兜底不泄细节；A2A 错误三分类 + 有限重试；SSE `error` 事件脱敏；告警 webhook 可选、失败降级为日志（[故障页](/openwiki/operations/failure-and-observability.md)）。
- **扩展缝**：注册表（A2A/MCP/Skill）+ Agent 勾选绑定；连接器适配器接口；技能审核后注入工具/提示；`SANDBOX_URL` 空即关脚本执行。

## 入口清单

| 入口 | 位置 |
|---|---|
| FastAPI 应用 / lifespan | `src/a2a_gateway/main.py:39-66` |
| 本地开发 | `python -m a2a_gateway.main`（reload）或 `src/main.py` |
| 生产容器 CMD | `uvicorn a2a_gateway.main:app --port 8000`（`Dockerfile:36`） |
| 迁移 CLI / 启动自动迁移 | `alembic/` + `migrations.py:73-77` |
| 前端 | `web/` Next.js（聊天 catch-all + `/admin/*`） |
| 沙箱 | `sandbox/` gate/runner 双角色 |
| E2E 冒烟 | `tests/e2e_smoke.py` |

## 不变量（阅读代码时假定成立）

- 仅 `PUBLISHED` Agent 可被聊天路由与 A2A 端点解析到。
- 运行时绑定只信快照列，不再回表解析（注册表变更靠 refresh 刷回）。
- 同一 `thread_id` 全局唯一，跨聊天/连接器/A2A 恢复共用。
- nginx 是唯一对外面；backend/frontend/gate 不直接对公网。

## 代表性测试与延伸阅读

- 契约：`tests/test_chat_api.py`、`tests/test_a2a_server.py`；域逻辑：`tests/test_graph.py`、`tests/test_agent_factory.py`、`tests/test_bindings.py`。
- 专题：[聊天生命周期](/openwiki/workflows/chat-lifecycle.md)、[Agent 图构建](/openwiki/workflows/agent-graph.md)、[部署拓扑](/openwiki/architecture/deployment.md)、[A2A 服务端](/openwiki/integrations/a2a-server.md)。
- 设计意图参考：`docs/superpowers/specs/`（源码为权威）。
