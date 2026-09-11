# 多 Agent 平台开发 TODO

> ## 当前进度（2026-09-11）
> - ✅ **Phase 0**（技术选型/脚手架/DB/Checkpointer/A2A SDK）
> - ✅ **Phase 1**（Agent 数据模型/CRUD/默认 Agent 初始化/A2A Client 封装/LangGraph 图+工厂缓存/缓存失效）—— input-required 按结论二期再做
> - ✅ **Phase 2**（动态路由+SSE 流式/历史 API/管理中心 API/JWT 认证/统一错误处理）
> - ✅ **Phase 3**（前端对话界面：动态路由/MUI 对话组件/SSE 客户端/404+错误态/匿名 session/响应式/**对话历史侧边栏+多会话管理**）
> - ✅ **Phase 4**（前端管理中心：登录/Agent 列表/创建编辑表单/A2A 连通性测试/发布下线/工具勾选/测试对话/slug 校验）
> - 🔄 **Phase 5**（A2A Client 封装 + 管理中心连通性测试已打通；Task 轮询/重试、Hermes 全链路联调待做）
> - 🔄 **Phase 6**（前后端容器化 + nginx 统一入口 + 环境变量清单已完成；Alembic/监控/防火墙待做）
> - ⏳ **下一步**：Phase 5（Hermes 全链路联调 + 重试/轮询）→ Phase 6（Alembic 迁移）→ Phase 7（测试）
> - 后端启动：`docker compose up -d` → `uv run python -m a2a_gateway.main`
> - 前端启动：`cd web && npm run dev` → `http://localhost:3000`
> - 管理中心：`http://localhost:3000/admin`（账号见 `ADMIN_USERNAME` / `ADMIN_PASSWORD`）
> - API 文档：`http://localhost:8000/docs`
> - 生产部署（devops-43）：nginx 统一入口 `http://43.156.187.79:10099`

## 项目概述

一个基于 LangGraph 的多 Agent 平台：
- **默认 Agent**：绑定服务器上的 Hermes（通过 A2A），路由 `/`，前端提供对话界面
- **自定义 Agent**：结构与默认 Agent 一致（同一套 LangGraph 图），仅绑定的 A2A 目标不同，可自定义路由发布
- **管理中心**：创建、配置、发布自定义 Agent

**技术栈**：LangGraph + FastAPI（后端）；React + Next.js + Material UI（前端）

---

## 已确认的架构决策

| 决策项 | 选定方案 |
|---|---|
| 自定义 Agent 部署方式 | 所有 Agent 共享同一个后端进程，按路由动态加载配置（非每个 Agent 独立进程） |
| LangGraph 与 Hermes 的关系 | 增强模式：LangGraph Agent 自带记忆/工具，A2A 调用 Hermes 是其工具之一，而非纯转发网关 |
| 权限模型 | 单用户/管理员模式，暂不做多租户 |

### 待讨论 8 问题的最终结论（已与用户确认 ✅）

| 编号 | 问题 | 最终方案 |
|---|---|---|
| 1 | Agent 配置持久化存储 | PostgreSQL（Docker Compose 搭建） |
| 2 | LangGraph Checkpointer | Postgres Checkpointer（与配置库共用同一 PostgreSQL） |
| 3 | 自定义 Agent 是否对外暴露 A2A Server | 仅作 Client，不对外暴露（简化 MVP） |
| 4 | 管理员认证方式 | JWT 完整账号体系 |
| 5 | 前后端会话/访客身份 | Cookie/localStorage 匿名 session（无需登录即可对话） |
| 6 | 工具集是否可配置 | 每 Agent 可单独勾选启用工具 |
| 7 | 流式响应协议 | SSE |
| 8 | input-required 人工确认 | 二期再做，MVP 不支持 |

---

## ⚠️ 待讨论的问题清单（开发前 / 开发中需要确认）

这些问题目前按合理默认值处理，写入了下面的 TODO，但**在对应任务开始前应与你二次确认**，避免返工：

1. **Agent 配置的持久化存储**：默认用 PostgreSQL，是否已有现成的数据库实例？还是需要 TODO 里包含数据库选型/部署任务？
2. **LangGraph Checkpointer（记忆持久化）选型**：Postgres checkpointer（推荐，与配置库共用一个数据库）还是 Redis？你之前在语音表单项目里评估过 `langgraph-checkpoint-redis`，这里是否沿用同样的技术选型？
3. **自定义 Agent 是否也需要作为 A2A Server 被外部发现/调用**，还是只作为 A2A Client 去调用 Hermes（当前默认：仅作为 Client，不对外暴露 A2A Server 能力，简化 MVP 范围）？
4. **管理员认证方式**：简单的密码/Token 登录即可，还是需要更完整的账号体系（为将来多用户做准备）？
5. **前后端会话/访客身份**：匿名访客对话历史是否需要持久化？用 Cookie/localStorage 生成的匿名 session id 是否足够，还是需要登录后才能对话？
6. **自定义 Agent 的工具集是否可配置**：管理中心是否允许为每个自定义 Agent 单独勾选启用哪些工具（网页搜索、代码执行等），还是所有 Agent 共享同一套固定工具集，仅 A2A 绑定目标不同？
7. **流式响应协议**：SSE（更简单，单向）还是 WebSocket（双向，支持未来的人工确认/中断场景）？当前默认 SSE。
8. **A2A 的 `input-required`（人工确认）场景是否要在 MVP 阶段支持**，还是先只做无需人工介入的直接问答，人工确认流程放到二期？

---

## Phase 0：项目初始化与技术选型确认

- [x] 与用户确认上述 8 个待讨论问题的最终方案 ✅（结论见上表）
- [x] 初始化 monorepo 或前后端分离仓库结构（讨论：monorepo 还是分仓库）→ 采用 monorepo：根目录后端（Python/uv）+ `web/` 前端（Next.js）
- [x] 后端项目脚手架：FastAPI + LangGraph + uv/poetry 依赖管理 ✅（`pyproject.toml` + `uv sync`，88 包已安装，editable 模式安装）
- [ ] 前端项目脚手架：Next.js（App Router）+ TypeScript + Material UI（已有 Next.js 16 脚手架，待接入 MUI —— 见 Phase 3）
- [x] 确定数据库（PostgreSQL）并搭建本地开发环境（Docker Compose）✅（`docker-compose.yml` + `.env`/`.env.example`）
- [x] 确定 LangGraph Checkpointer 方案并接入 ✅（`AsyncPostgresSaver`，`agent_factory.get_checkpointer`）
- [x] 确定 A2A Python SDK（`a2a-sdk`）版本并加入依赖 ✅（`a2a-sdk`，已封装 `A2AClientWrapper`）

---

## Phase 1：后端核心 —— LangGraph Agent 工厂

- [x] 设计 Agent 配置数据模型（数据库表）：
  - `id`, `slug`（路由标识，`/` 为默认 Agent 保留）, `name`, `description`
  - `a2a_targets`（绑定的 A2A 目标列表：URL、认证 token）
  - `system_prompt`（可选覆盖）
  - `enabled_tools`（每 Agent 可勾选，见问题 6 结论）
  - `status`（draft / published）
  - `created_at`, `updated_at`
  → `src/a2a_gateway/models.py`（`AgentConfig` + `AdminUser`，`Base` 声明式映射）
- [x] 实现 Agent 配置的 CRUD 数据访问层（Repository）✅ `src/a2a_gateway/repository.py`
- [x] 设计"默认 Agent"的初始化逻辑：应用启动时若不存在 `slug='/'` 的记录，自动创建，绑定服务器 Hermes 的 A2A 地址（读取环境变量 `HERMES_A2A_URL`、`HERMES_A2A_TOKEN`）✅ `ensure_default_agent()`
- [x] 实现 LangGraph 图定义（所有 Agent 共用同一套图结构）：
  - 节点设计：对话节点、工具调用节点（（可选）记忆检索节点由 Checkpointer 承担）→ 用 `create_react_agent`
  - 工具层：封装 A2A 调用为标准 LangChain/LangGraph Tool → `src/a2a_gateway/tools.py`（`a2a_call` + 可选工具注册表）
  - 工具需处理：发现（Card 解析）、发送消息、流式接收、超时重试 → `src/a2a_gateway/a2a_client.py`（错误分类 network/timeout/target_error）
- [x] 实现"Agent 实例工厂"：根据 Agent 配置（含缓存机制，避免每次请求都重新构建图）动态生成绑定了对应 A2A 目标 / system_prompt / 工具集的 LangGraph 图实例 ✅ `src/a2a_gateway/agent_factory.py`（按 `(id, updated_at)` 缓存）
- [x] 实现配置变更后的缓存失效机制（管理中心修改配置后，无需重启进程即可生效）✅ `updated_at` 自动失效 + `invalidate_agent()` 显式失效
- [ ] 实现 A2A 调用中 `input-required` 状态的处理策略（按问题 8 结论：**二期再做**，MVP 不透传）

---

## Phase 2：后端核心 —— FastAPI 路由与 API

- [x] 动态路由设计：
  - `POST /api/chat/{slug}`（`slug` 为空或 `/` 时命中默认 Agent）：接收用户消息，返回流式响应 → `POST /api/chat`（默认）+ `POST /api/chat/{slug}`（自定义）
  - 路由处理逻辑：根据 `slug` 查询 Agent 配置 → 未找到或未发布则返回 404 → 找到则调用对应 Agent 实例 ✅ `_resolve_agent` + `get_agent_instance`
- [x] 实现流式响应端点（按问题 7 结论：SSE）✅ `routes/chat.py`（`EventSourceResponse`，事件 `token/tool_start/tool_end/done/error`）
- [x] 实现会话历史 API：`GET /api/chat/{slug}/history`（按问题 5 结论：匿名 session，无需登录）✅ `GET /api/chat/history` + `GET /api/chat/{slug}/history`（从 Checkpointer 提取）
- [x] 管理中心 API（`/api/admin/...`，需管理员认证）✅ `routes/admin.py`：
  - `GET /api/admin/agents`：列出所有自定义 Agent
  - `POST /api/admin/agents`：创建 Agent（校验 `slug` 唯一性，禁止使用 `/` 或已占用路径）
  - `PUT /api/admin/agents/{id}`：更新 Agent 配置
  - `DELETE /api/admin/agents/{id}`：删除 Agent
  - `POST /api/admin/agents/{id}/publish` / `unpublish`：发布/下线
  - `POST /api/admin/agents/{id}/test`：管理中心内直接测试对话（不经过公开路由，draft 也可测）
  - 额外：`POST /api/admin/agents/test-connection`（连通性测试）、`POST /api/admin/login`（JWT 颁发）
- [x] 管理员认证中间件（按问题 4 结论：JWT 完整账号体系）✅ `deps.get_current_admin`（Bearer token 校验），除 `/login` 外全部受保护；`auth.py`（JWT + bcrypt）
- [x] 统一错误处理与日志（A2A 调用失败、超时、目标不可达的分类定位）✅ `main.py` 全局异常兜底；`A2ATargetError` 三分类（network/timeout/target_error）；流式错误友好提示不暴露底层

---

## Phase 3：前端 —— 公开对话界面

- [x] Next.js 动态路由：`app/[[...slug]]/page.tsx`，根据路径匹配默认 Agent 或自定义 Agent ✅（可选 catch-all 同时匹配 `/` 与 `/{slug}`；删除旧 `app/page.tsx` 避免路由冲突）
- [x] 对话界面组件（Material UI）：消息气泡、输入框、发送按钮、流式打字机效果 ✅ `components/ChatPage.tsx` + `MessageBubble.tsx` + `ChatInput.tsx`
- [x] 对接后端流式接口（SSE 客户端封装）✅ `lib/api.ts`（`streamChat` 基于 fetch + ReadableStream 解析 SSE 事件：token/tool_start/tool_end/done/error）
- [x] 加载态、错误态处理（Agent 不存在 / 未发布 → 友好的 404 页面；A2A 目标不可达 → 提示用户稍后重试，而不是暴露底层错误）✅ `page.tsx` 404 页 + ChatPage error Alert
- [x] （按问题 5 结论）匿名访客 session 管理 / 历史记录展示 ✅ `lib/session.ts`（localStorage 按 slug 存 thread_id）+ 页面加载时从后端拉取历史
- [x] 响应式布局，适配移动端 ✅ MUI sx 响应式断点（`px: { xs: 1, sm: 3 }`）+ 输入框多行自适应
- [x] 对话历史侧边栏：多会话列表（按 slug 存 localStorage）、新建/切换/删除会话、按首条消息自动命名、旧 session 数据自动迁移 ✅ `lib/conversations.ts` + `components/ConversationList.tsx`（桌面常驻 + 移动端抽屉）

---

## Phase 4：前端 —— 管理中心

- [x] 管理员登录页面 ✅ `app/admin/login/page.tsx`（JWT 存 localStorage）
- [x] Agent 列表页：展示所有自定义 Agent（名称、路由、状态、绑定的 A2A 目标）✅ `app/admin/page.tsx`
- [x] Agent 创建/编辑表单 ✅ `components/admin/AgentForm.tsx` + `app/admin/agents/new`、`app/admin/agents/[id]/edit`
  - [x] 基本信息（名称、描述、自定义路由 slug）
  - [x] A2A 目标配置（URL、认证方式、可测试连通性）✅ 每个目标独立「测试连接」按钮
  - [x] System Prompt 编辑
  - [x] （按待讨论问题 6）工具集勾选 ✅ `a2a_call` 默认启用，可选工具来自后端 `OPTIONAL_TOOLS`
- [x] 发布/下线操作与状态提示 ✅ 列表页与编辑页均支持，带 Snackbar 反馈
- [x] 管理中心内的即时测试对话窗口（复用公开对话组件）✅ `components/admin/TestChatDialog.tsx`（draft 状态也可测）
- [x] 路由 slug 冲突校验的前端提示（禁止占用 `/` 或已存在的 slug）✅ 前端正则 + 保留字 + 已存在校验，后端 409 兜底

---

## Phase 5：A2A 集成细节

- [ ] 实现/验证 A2A Client 封装（基于 `a2a-sdk`），支持：🔄 核心已完成，长任务场景待补
  - [x] 目标 Agent Card 获取与缓存 ✅ `A2AClientWrapper`（客户端实例按 Agent 配置缓存，Card 随连接建立解析）
  - [x] 消息发送与流式结果接收 ✅ `stream_message`
  - [ ] Task 状态轮询（针对长任务）
  - [ ] 错误重试与超时控制 —— 超时（httpx 60s）与错误三分类已完成，**自动重试待补**
- [ ] 默认 Agent 与服务器 Hermes 的 A2A 连接联调 —— 已在 devops-43 配置 `HERMES_A2A_URL=http://43.156.187.79:9900` + `HERMES_A2A_TOKEN` 并验证端点可达（HTTP 200），**完整对话链路待联调验证**
- [x] 自定义 Agent 绑定任意 A2A 目标的连通性测试功能（管理中心"测试连接"按钮）
- [ ] （若待讨论问题 8 确定支持）`input-required` 状态在前端的呈现与用户确认交互

---

## Phase 6：部署与运维

- [x] 后端容器化（Dockerfile）✅ 多阶段构建 + 镜像内置健康检查（`Dockerfile`）
- [x] 前端容器化 / 静态部署方案确定 ✅ 自托管 Next.js standalone（`web/Dockerfile`）+ nginx 统一反向代理
- [ ] 数据库迁移脚本（Alembic）
- [x] 环境变量清单整理 ✅ `.env.example` + `docker-compose.yml`（组件式 `POSTGRES_*`、`LLM_*`、`HERMES_A2A_*`、`JWT_*`/`ADMIN_*`、`GATEWAY_PORT`）
- [ ] 日志与监控接入（至少保证 A2A 调用失败、Agent 加载失败有告警）—— 已有分级日志与错误三分类，告警接入待做
- [ ] 云服务器安全组/防火墙规则梳理 —— 当前对外仅暴露 nginx `10099`，Postgres 不对外

---

## Phase 7：测试

- [ ] 后端单元测试：Agent 配置 CRUD、路由匹配逻辑、A2A 工具封装的 mock 测试
- [ ] 后端集成测试：针对一个本地起的测试 A2A Agent（可复用之前提到的 `a2a-samples` Helloworld Agent）验证完整链路
- [ ] 前端组件测试：对话流式渲染、管理中心表单校验
- [ ] 端到端测试：创建自定义 Agent → 发布 → 通过自定义路由对话 → 验证消息确实经由 A2A 到达绑定的目标

---

## 建议的开发顺序

1. Phase 0 + Phase 1（先把"能跑通默认 Agent 对默接 Hermes"这条最小链路走通，不做管理中心）
2. Phase 2 + Phase 3（补上路由动态化和公开对话界面，此时默认 Agent 应该可以通过网页完整对话）
3. Phase 5 中与自定义目标的连通性测试提前做，验证"绑定不同 A2A 目标"这个核心差异点没有架构问题
4. Phase 4（管理中心，此时后端 API 已经稳定，前端管理界面是相对独立的开发工作）
5. Phase 6 + Phase 7 贯穿全程，不必等到最后

这样安排的原因：**核心风险点（LangGraph Agent 如何正确调用 A2A、动态路由加载配置是否可行）在最前面就验证掉**，管理中心作为"配置的可视化外壳"放在后面，即使它的 UI 细节需要反复调整，也不会拖慢核心链路的验证进度。