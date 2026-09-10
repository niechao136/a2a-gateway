# 多 Agent 平台开发 TODO

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

- [ ] 与用户确认上述 8 个待讨论问题的最终方案
- [ ] 初始化 monorepo 或前后端分离仓库结构（讨论：monorepo 还是分仓库）
- [ ] 后端项目脚手架：FastAPI + LangGraph + uv/poetry 依赖管理
- [ ] 前端项目脚手架：Next.js（App Router）+ TypeScript + Material UI
- [ ] 确定数据库（PostgreSQL）并搭建本地开发环境（Docker Compose）
- [ ] 确定 LangGraph Checkpointer 方案并接入
- [ ] 确定 A2A Python SDK（`a2a-sdk`）版本并加入依赖

---

## Phase 1：后端核心 —— LangGraph Agent 工厂

- [ ] 设计 Agent 配置数据模型（数据库表）：
  - `id`, `slug`（路由标识，`/` 为默认 Agent 保留）, `name`, `description`
  - `a2a_targets`（绑定的 A2A 目标列表：URL、认证 token）
  - `system_prompt`（可选覆盖）
  - `enabled_tools`（待讨论问题 6 确定后实现）
  - `status`（draft / published）
  - `created_at`, `updated_at`
- [ ] 实现 Agent 配置的 CRUD 数据访问层（Repository）
- [ ] 设计"默认 Agent"的初始化逻辑：应用启动时若不存在 `slug='/'` 的记录，自动创建，绑定服务器 Hermes 的 A2A 地址（读取环境变量 `HERMES_A2A_URL`、`HERMES_A2A_TOKEN`）
- [ ] 实现 LangGraph 图定义（所有 Agent 共用同一套图结构）：
  - 节点设计：对话节点、工具调用节点、（可选）记忆检索节点
  - 工具层：封装 A2A 调用为标准 LangChain/LangGraph Tool（`a2a_call_hermes` 或更通用的 `a2a_call(target, message)`）
  - 工具需处理：发现（可选，若目标固定可跳过）、发送消息、流式接收、超时重试
- [ ] 实现"Agent 实例工厂"：根据 Agent 配置（含缓存机制，避免每次请求都重新构建图）动态生成绑定了对应 A2A 目标 / system_prompt / 工具集的 LangGraph 图实例
- [ ] 实现配置变更后的缓存失效机制（管理中心修改配置后，无需重启进程即可生效）
- [ ] 实现 A2A 调用中 `input-required` 状态的处理策略（按待讨论问题 8 的结论实现：MVP 阶段是否透传给前端）

---

## Phase 2：后端核心 —— FastAPI 路由与 API

- [ ] 动态路由设计：
  - `POST /api/chat/{slug}`（`slug` 为空或 `/` 时命中默认 Agent）：接收用户消息，返回流式响应
  - 路由处理逻辑：根据 `slug` 查询 Agent 配置 → 未找到或未发布则返回 404 → 找到则调用对应 Agent 实例
- [ ] 实现流式响应端点（按待讨论问题 7 的结论：SSE 或 WebSocket）
- [ ] 实现会话历史 API：`GET /api/chat/{slug}/history`（按待讨论问题 5 的结论确定是否需要登录）
- [ ] 管理中心 API（`/api/admin/...`，需管理员认证）：
  - `GET /api/admin/agents`：列出所有自定义 Agent
  - `POST /api/admin/agents`：创建 Agent（校验 `slug` 唯一性，禁止使用 `/` 或已占用路径）
  - `PUT /api/admin/agents/{id}`：更新 Agent 配置
  - `DELETE /api/admin/agents/{id}`：删除 Agent
  - `POST /api/admin/agents/{id}/publish` / `unpublish`：发布/下线
  - `POST /api/admin/agents/{id}/test`：管理中心内直接测试对话（不经过公开路由）
- [ ] 管理员认证中间件（按待讨论问题 4 的结论实现）
- [ ] 统一错误处理与日志（尤其是 A2A 调用失败、超时、目标不可达的情况，参考之前排查 Hermes A2A 网络问题的经验，确保日志里能清楚定位是"配置问题"还是"网络问题"还是"目标 Agent 内部错误"）

---

## Phase 3：前端 —— 公开对话界面

- [ ] Next.js 动态路由：`app/[[...slug]]/page.tsx`，根据路径匹配默认 Agent 或自定义 Agent
- [ ] 对话界面组件（Material UI）：消息气泡、输入框、发送按钮、流式打字机效果
- [ ] 对接后端流式接口（SSE/WebSocket 客户端封装）
- [ ] 加载态、错误态处理（Agent 不存在 / 未发布 → 友好的 404 页面；A2A 目标不可达 → 提示用户稍后重试，而不是暴露底层错误）
- [ ] （按待讨论问题 5 的结论）匿名访客 session 管理 / 历史记录展示
- [ ] 响应式布局，适配移动端

---

## Phase 4：前端 —— 管理中心

- [ ] 管理员登录页面
- [ ] Agent 列表页：展示所有自定义 Agent（名称、路由、状态、绑定的 A2A 目标）
- [ ] Agent 创建/编辑表单：
  - 基本信息（名称、描述、自定义路由 slug）
  - A2A 目标配置（URL、认证方式、可测试连通性）
  - System Prompt 编辑
  - （按待讨论问题 6）工具集勾选
- [ ] 发布/下线操作与状态提示
- [ ] 管理中心内的即时测试对话窗口（复用公开对话组件）
- [ ] 路由 slug 冲突校验的前端提示（禁止占用 `/` 或已存在的 slug）

---

## Phase 5：A2A 集成细节

- [ ] 实现/验证 A2A Client 封装（基于 `a2a-sdk`），支持：
  - 目标 Agent Card 获取与缓存
  - 消息发送与流式结果接收
  - Task 状态轮询（针对长任务）
  - 错误重试与超时控制
- [ ] 默认 Agent 与服务器 Hermes 的 A2A 连接联调（复用之前已经跑通的 Hermes A2A 配置：`A2A_PEER_TOKENS`/`A2A_BEARER_TOKEN`，把本项目的后端注册为一个可信 peer）
- [ ] 自定义 Agent 绑定任意 A2A 目标的连通性测试功能（管理中心"测试连接"按钮）
- [ ] （若待讨论问题 8 确定支持）`input-required` 状态在前端的呈现与用户确认交互

---

## Phase 6：部署与运维

- [ ] 后端容器化（Dockerfile）
- [ ] 前端容器化 / 静态部署方案确定（Vercel 还是自托管，需讨论）
- [ ] 数据库迁移脚本（Alembic）
- [ ] 环境变量清单整理（`HERMES_A2A_URL`、`HERMES_A2A_TOKEN`、数据库连接串、管理员凭证等）
- [ ] 日志与监控接入（至少保证 A2A 调用失败、Agent 加载失败有告警）
- [ ] 云服务器安全组/防火墙规则梳理（复用之前配置 Hermes A2A 端口时的经验，确认新增端口的放行范围）

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