---
type: quickstart
title: 快速开始与任务路由
description: a2a-gateway 入口页：项目是什么、本地如何运行与部署，并按任务场景导航到各专题页面。
tags: [quickstart, setup, deploy, navigation, routing]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-5f5b95b3d6a215fa02ceb945
    resource: repo://.env.example
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-5587127d632cfcdc010b44e9
    resource: repo://src/a2a_gateway/main.py
  - id: openwiki-source-e74227ee06b16f894c3e3826
    resource: repo://TODO.md
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 快速开始与任务路由

**a2a-gateway** 是基于 LangGraph 的多 Agent 平台（后端 Python/uv + FastAPI，前端 `web/` Next.js + MUI）。所有 Agent 共享同一套 ReAct 图与后端进程，按 `slug` 动态加载配置；增强模式下 LangGraph Agent 自带记忆与工具，A2A 调用（默认绑定 Hermes）只是工具之一，而非纯转发网关。权限为单管理员 JWT 模式，不做多租户。

> README.md 为空；以下以 `TODO.md` 与源码为准（架构决策、Phase 进度同见 [架构总览](/openwiki/architecture/overview.md)）。

## 本地运行

### 后端

```bash
cp .env.example .env        # 按需修改 LLM_* / HERMES_A2A_* / JWT_* / ADMIN_*
docker compose up -d        # 仅起 postgres（+ 编排内其它依赖）
uv run python -m a2a_gateway.main
```

- 启动时 lifespan 自动执行 Alembic 迁移（失败回退 `create_all`）、初始化默认 Agent（`slug=/`）、管理员账号与 API Key（`main.py:39-58`）；
- 开发入口 `main()` 带 `reload=True`，监听 `APP_HOST:APP_PORT`（默认 `0.0.0.0:8000`）；
- 环境变量经 `load_dotenv()` 加载：真实环境变量 > 文件（不覆盖已有项），也可用 `DOTENV_PATH` 指定（`.env.example:1-14`）；
- 健康检查 `GET /health`，API 文档 `http://localhost:8000/docs`（`main.py:96-98`）。

### 前端

```bash
cd web && npm run dev       # http://localhost:3000
```

- 对话界面：`http://localhost:3000/`（默认 Agent）或 `/{slug}`；
- 管理中心：`http://localhost:3000/admin`（账号 `ADMIN_USERNAME` / `ADMIN_PASSWORD`）；
- 本机直连后端时 `FRONTEND_ORIGIN=http://localhost:3000`（`.env.example:51`）。

### 全容器部署（推荐）

```bash
cp .env.example .env
docker compose up -d --build
# 浏览器访问 http://<host>:10099
```

`docker-compose.yml` 要点：

| 服务 | 角色 | 对外 |
|---|---|---|
| `postgres` | 配置 + Checkpointer 共库 | 不发布端口 |
| `backend` | FastAPI（`POSTGRES_HOST=postgres` 被编排覆盖） | 仅 `expose:8000` |
| `sandbox-gate` / `sandbox-runner` | 沙箱前门（有网）+ 执行体（无网/只读/去 capability） | gate `expose:8100` |
| `frontend` | Next.js standalone | 仅 `expose:3000` |
| `nginx` | 统一反向代理 | **唯一对外端口 `${GATEWAY_PORT:-10099}`** |

容器内访问宿主机服务用 `host.docker.internal`（如 `HERMES_A2A_URL=http://host.docker.internal:8080/`）；`FRONTEND_ORIGIN` 默认 `http://localhost:${GATEWAY_PORT}`（`.env.example:81-86`）。详见 [部署](/openwiki/architecture/deployment.md)。

## 最小配置清单

复制 `.env.example` 后至少改：

1. **`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`**——OpenAI 兼容端点（可指 ollama 等）；Gemini 3 系列自动受益于内置 `thought_signature` 兼容层；
2. **`HERMES_A2A_URL` / `HERMES_A2A_TOKEN`**——默认 Agent 绑定的下游；
3. **`JWT_SECRET` / `ADMIN_PASSWORD`**——勿用默认值上生产；
4. 外部数据库时可改用完整 `DATABASE_URL` / `CHECKPOINT_DB_URL` 覆盖组件式 `POSTGRES_*`；
5. 可选：`ALERT_WEBHOOK_URL`（失败告警）、`ONNX_HUB_*`（语音）、`SANDBOX_TOKEN`（技能脚本）、`PENDING_A2A_TTL_SECONDS`（挂起任务 TTL，默认 86400）。

完整清单与优先级见 [配置](/openwiki/architecture/configuration.md)。

## 验证安装

```bash
uv sync --extra dev && uv run pytest -q    # 后端测试（pytest asyncio auto）
cd web && npm test                          # 前端 vitest
uv run basedpyright                         # 类型检查（standard，要求 0 error）
```

端到端冒烟（容器内、真实 LLM + Hermes）：

```bash
docker cp tests/e2e_smoke.py a2a-gateway-backend:/tmp/ \
  && docker compose exec -T backend python /tmp/e2e_smoke.py
```

详见 [测试](/openwiki/operations/testing.md)。

## 按任务路由

| 你想… | 去这里 |
|---|---|
| 了解整体架构与请求链路 | [架构总览](/openwiki/architecture/overview.md) |
| 查环境变量、连接串拼装、默认值 | [配置](/openwiki/architecture/configuration.md) |
| 理解表结构、快照 JSONB、挂起表 | [数据模型](/openwiki/architecture/data-model.md) |
| 上生产 / nginx / 迁移 / 端口清单 | [部署](/openwiki/architecture/deployment.md) |
| 理解 Agent 配置、slug、绑定模型 | [Agent 配置与绑定](/openwiki/concepts/agents-and-bindings.md) |
| 会话、thread_id、匿名身份 cookie | [身份与会话](/openwiki/concepts/identity-and-conversations.md) |
| 安全基线（JWT、CORS、注入面） | [安全](/openwiki/concepts/security.md) |
| 导入/绑定 Skills、审核门禁 | [技能系统](/openwiki/concepts/skills.md) |
| 对接下游 A2A Agent | [A2A 客户端](/openwiki/integrations/a2a-client.md) |
| 把网关当 A2A Server 对外服务 | [A2A 服务端](/openwiki/integrations/a2a-server.md) |
| 接入 MCP 服务与工具探测 | [MCP 集成](/openwiki/integrations/mcp.md) |
| ASR / TTS 语音 | [语音](/openwiki/integrations/speech.md) |
| 连接器与 Webhook（Telegram 等） | [连接器](/openwiki/integrations/connectors.md) |
| 跟一次聊天回合（SSE 事件契约） | [聊天生命周期](/openwiki/workflows/chat-lifecycle.md) |
| 图构建、压缩 hook、缓存失效 | [Agent 图构建](/openwiki/workflows/agent-graph.md) |
| input-required 挂起与恢复 | [恢复流程](/openwiki/workflows/input-required-resume.md) |
| 日志、告警、失败降级清单 | [失败与可观测性](/openwiki/operations/failure-and-observability.md) |
| 沙箱执行器隔离细节 | [沙箱](/openwiki/operations/sandbox.md) |
| 跑测试 / 类型检查 / 冒烟 | [测试](/openwiki/operations/testing.md) |

## 常见入口速查

| 地址 | 用途 |
|---|---|
| `POST /api/chat`、`/api/chat/{slug}` | 对话 SSE（事件：token/tool_start/tool_end/interrupt/done/error） |
| `GET /api/chat/history?thread_id=` | 会话历史 |
| `/api/admin/...` | 管理中心 API（JWT Bearer，除 login 外全保护） |
| `/registry` 等 | Agent 注册表路由（见 [架构总览](/openwiki/architecture/overview.md)） |
| A2A RPC | 网关对外的 A2A Server 端点（见 [A2A 服务端](/openwiki/integrations/a2a-server.md)） |

## 当前状态提示（来自 TODO.md）

- Phase 0–7 已完成：后端 342+ 用例、前端 vitest、basedpyright standard 0 error；
- **待真库验收**：`alembic upgrade head` 尚未在真实 PostgreSQL 跑过（重点核对 skills 相关 JSONB `server_default`）；审核撤回→对话跳过技能链路需真机复核；
- **下一步**：管理中心表单校验前端用例、云安全组核对、二期（长任务轮询）；
- Gemini 3（OpenAI 兼容端点）函数调用必须回传 `thought_signature`，已由 `llm.py` 兼容层处理，对其它端点透明。

端口期望策略（安全组）：`10099` 放行、`22` 限源、`9900`（Hermes）仅内网、`5432`/`8000`/`3000` 禁对外（`TODO.md:173-181`）。
