---
type: operations-guide
title: 测试策略与验证
description: 说明零外部依赖的测试哲学（dependency_overrides + ASGITransport + monkeypatch）、各测试簇守护的行为、e2e smoke 与沙箱独立测试套件、前端 vitest 覆盖。
tags: [testing, pytest, vitest, e2e, fixtures, quality-gates]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-0a2421646c2a0feced2c5db0
    resource: repo://docs/superpowers/specs/2026-09-20-skill-binding-design.md
  - id: openwiki-source-b96aa6210b211403523506d9
    resource: repo://docs/superpowers/specs/2026-09-21-skill-preview-edit-script-execution-design.md
  - id: openwiki-source-58f8f681eccaf8cd37b2b965
    resource: repo://sandbox/tests/test_runner.py
  - id: openwiki-source-f0a6e7dc03522b2682f88655
    resource: repo://tests/conftest.py
  - id: openwiki-source-4c3c4d2d2f4eb718b3a6a072
    resource: repo://tests/e2e_smoke.py
  - id: openwiki-source-03574e9f9a51ca0c4b19ed5b
    resource: repo://tests/test_a2a_server.py
  - id: openwiki-source-ab3221bd368b09c365e6536b
    resource: repo://tests/test_chat_api.py
  - id: openwiki-source-29e8451f8ef027e820ad9114
    resource: repo://tests/test_sandbox_client.py
  - id: openwiki-source-dd522498ad39ed2f45ebe0c9
    resource: repo://tests/test_skill_import.py
  - id: openwiki-source-db948e9533f927a84c2c8c46
    resource: repo://tests/test_skills_binding.py
  - id: openwiki-source-14e56945b7c632a3b335dcec
    resource: repo://web/package.json
  - id: openwiki-source-fc050f101d888fde458f328d
    resource: repo://web/src/lib/adminApi.test.ts
  - id: openwiki-source-fa33445db596da6b57d8fbdb
    resource: repo://web/src/lib/api.test.ts
  - id: openwiki-source-e7527710b04e6e21a422dbe8
    resource: repo://web/src/lib/skillForm.test.ts
  - id: openwiki-source-f1b5da2944c2db4a3d8c477a
    resource: repo://web/src/lib/skillUtils.test.ts
  - id: openwiki-source-3b6241dfcd4faf1a09f1cced
    resource: repo://web/src/lib/speech.test.ts
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 测试策略与验证

## 零外部依赖哲学

`tests/conftest.py` 头注释即契约（`conftest.py:1-7`）：

- **不连接数据库**：`app.dependency_overrides[get_session] = _fake_session`（假会话仅 `yield None`，路由层已被 monkeypatch 不会真正用它），管理员认证经 `get_current_admin` 覆盖（`conftest.py:80-107`）；
- **不触发 FastAPI lifespan**：`httpx.ASGITransport(app=app)` 不执行 startup，因此**不会跑迁移、不初始化默认 Agent/管理员**；
- **不发起真实网络请求**：httpx / A2A / LLM / MCP / 沙箱全部 mock 或 monkeypatch 替身。

公共夹具：`anon_client`（仅覆盖 DB 会话）、`auth_client`（再覆盖管理员依赖）、`make_agent` / `make_admin` / `make_api_key`（`SimpleNamespace` 替身，满足序列化字段）。夹具在退出时 `dependency_overrides.clear()` 防泄漏。

三件套验证（各设计规格验收条款一致）：`uv run pytest` 全绿 + `uv run basedpyright` 无新增/0 error + `uv run ruff check` 无新增告警；前端 `cd web && npm test` 全绿（`docs/superpowers/specs/*`、`AGENTS.md`）。

## 运行方式

| 套件 | 入口 | 说明 |
|---|---|---|
| 后端单测 | **`uv run pytest -q`**（仓库根） | 收集 `tests/test_*.py`；零外部依赖 |
| 沙箱 | `uv run pytest -q`（`sandbox/` 目录）或按 `sandbox/tests` 运行 | **真实子进程冒烟**（唯一允许起真进程的套件） |
| 前端 | `cd web && npm test`（= `vitest run`，`web/package.json:10`） | 纯函数单测，5 个文件 |
| E2E 冒烟 | `python tests/e2e_smoke.py`（推荐 backend 容器内） | 需真实部署实例；**文件名不以 `test_` 开头故不被 pytest 收集** |

质量门禁常用组合（设计规格验收）：`uv run pytest`、`uv run basedpyright`、`uv run ruff check`、`cd web && npm test`。

## 按行为簇组织的代表性测试

避免逐文件罗列，按守护的行为分四簇：

### 1. API 契约簇（HTTP 层）

经 `anon_client`/`auth_client` + `ASGITransport` 打真实路由，断言状态码、响应模型、错误文案：

| 文件 | 守护行为 |
|---|---|
| `test_admin_api.py` | JWT 登录/登出、Agent CRUD、发布/下线、保留 slug 400、API Key 管理、test-connection |
| `test_registry_api.py` | A2A 端点 / MCP 服务 CRUD、探测测试、工具列表 |
| `test_skills_api.py` | 技能 CRUD、重名 409、导入预览→落库、覆盖重置 pending、审核、删除 409/force |
| `test_chat_api.py` | SSE 事件序列、retry、**`test_chat_stream_error_event_is_friendly`（错误文案不泄漏）**、history |
| `test_a2a_server.py` | API Key 三态 401、卡片公开/404、`public_base_url` 六则、流式 INPUT_REQUIRED、恢复、TaskNotFound、task/context 同 id |
| `test_connectors_api.py` | 连接器 CRUD、webhook 验签 401、404 不区分停用、立即 200、掩码合并、主动 send |

### 2. 域逻辑簇（纯函数 / 无 IO）

| 文件 | 守护行为 |
|---|---|
| `test_skills_parse.py` / `test_skills_schemas.py` | SKILL.md 解析、name/description/frontmatter 校验、限额 |
| `test_skill_import.py` | zip slip、符号链接、解压炸弹、SSRF（私网/环回/元数据/重定向/体积极限）——全离线 |
| `test_auth_scheme.py` | 五种出站鉴权 header/query/stdio 行为与 `validate_auth` |
| `test_config.py` | 默认 DB URL、组件 URL 构建、显式 URL 优先 |
| `test_bindings.py` | 绑定合并：`manual=None` 保留、列表替换去重、MCP 按 name 键 |
| `test_pending_store.py` | upsert/get/delete、TTL 过期删除、Fake store |
| `test_notifier.py` | 未配置退化日志、配置后 payload、**推送失败被吞** |
| `test_connector_models.py` / `test_connector_support.py` | 连接器模型校验、工具函数 |
| `test_skills_schemas` 类 | 凭据掩码/合并等 schema 层语义 |

### 3. 集成簇（组件协作，仍离线）

| 文件 | 守护行为 |
|---|---|
| `test_a2a_client.py` | 三分类、重试四则（含已产出不重试）、URL 改写、origin 回退、事件抽取、GetTask 补拉、整数还原 |
| `test_tools_a2a.py` | `a2a_call` 中断写 store、`a2a_resume` 命中/未命中/目标变更/失败删挂起 |
| `test_tools_mcp.py` | schema→model、退化 arguments、工厂闭包晚绑定、`mcp_call` 回退 |
| `test_tools_skill.py` | `load_skill` 命中/未命中/stale、`read_skill_file` 白名单 |
| `test_tools_script_exec.py` | 沙箱门禁（未配置不挂载、allow_scripts 过滤）、错误文本化、monkeypatch |
| `test_mcp_client.py` | 三传输、8/60s 超时、`_format_error` 展开 ExceptionGroup |
| `test_sandbox_client.py` | Timeout/Rejected/Unavailable 三类映射、空 URL 禁用 |
| `test_graph.py` | pre_model_hook 挂起注入、注入顺序、store 异常降级、摘要叠加 |
| `test_graph_skill.py` | 清单顺序、always/on_demand 常驻、记账、超窗重注入、stale 过滤、预算截断、**图级回归（注入真的到达模型输入）** |
| `test_skills_binding.py` | 绑定门禁 pending→400、总量超限、快照只含 approved、enabled=false 跳过、refresh+invalidate |
| `test_agent_factory.py` | 图缓存复用与失效、MCP 探测注入 |
| `test_connector_adapters.py` | Feishu 解密/token、Slack 签名与 300s 偏移、Telegram secret 头与 mention |
| `test_connector_pipeline.py` | 去重 TTL/容量、队满、120s 超时、未发布、回复提取 |
| `test_chat_input_required.py` | 恢复分支走 graph、轮末 interrupt、删会话清挂起 |
| `test_conversations.py` | 会话目录登记/读取 |
| `test_llm_thought_signature.py` | 流式入站兼容补丁 |

### 4. E2E 簇

`tests/e2e_smoke.py`（171 行）：**对已部署实例**验证「管理中心 → 自定义 Agent → A2A 全链路」——登录 → 复用默认 Agent 的 A2A 目标 → 创建/发布测试 Agent → `/api/chat/{slug}` 强制 `a2a_call` 并校验 SSE 事件 → 清理（`--keep` 可保留）。需要真实 LLM + 真实 A2A 目标；管理员凭据从容器环境变量（`ADMIN_USERNAME`/`ADMIN_PASSWORD`）读；`E2E_BASE_URL` 或 `--base-url` 指定实例；**退出码 0/1 可用于发版后校验/CI**（`e2e_smoke.py:1-18`、`63-73`）。SSE 解析兼容 sse-starlette 的 CRLF 分隔（`e2e_smoke.py:33-54`）。

## 沙箱独立测试套件

`sandbox/tests/`（`test_protocol.py`、`test_gate.py`、`test_runner.py`、`conftest.py`）——**唯一允许真实子进程的套件**（与后端零依赖哲学分离）：

- runner 冒烟：python stdout 捕获、`sleep 5` 超时 kill（断言 `timeout=True` 且无半截输出）、>32KB 输出截断；
- **env 白名单断言**：子进程环境不可见 backend 注入的 `LLM_API_KEY` 类密钥；
- 协议：路径穿越拒绝、解释器白名单、文件数/总量上限、timeout clamp；
- gate：Bearer 401、8MB 413、信号量、转发错误映射（可 mock socket）。

设计意图（规格 §9 测试）：与 `tests/` 分开是因为沙箱需要 POSIX 子进程/rlimit 行为，Windows dev 宿主上不保证——runner 恒为 Linux 容器。

## 前端 vitest 覆盖

`web/src/lib/*.test.ts` 共 5 个文件（`package.json`：`"test": "vitest run"`，vitest ^3.2.7）：

| 文件 | 覆盖 |
|---|---|
| `api.test.ts` | SSE/interrupt 事件解析、请求封装 |
| `adminApi.test.ts` | 管理端 API 客户端方法与 URL 拼装 |
| `skillUtils.test.ts` | token/字符预算估算、字节格式化、后缀判断 |
| `skillForm.test.ts` | 技能导入/编辑表单校验纯函数 |
| `speech.test.ts` | 长文本切句、TTS 播放流水线纯逻辑 |

约定（移动适配设计 §）：**不为薄封装引入 React 测试工具依赖**——组件层不强制单测，纯逻辑抽到 `src/lib` 后测；新增纯逻辑单元应补测。

## 测试金字塔与守护边界

```
              e2e_smoke（真实实例，手动/CI 触发）
           ─── sandbox/tests（真实子进程，POSIX）───
        ─── 集成簇（组件协作，mock 网络/DB/LLM）───
     ─── API 契约 + 域逻辑（ASGITransport / 纯函数，全离线）───
```

- **后端单测永不触网/触库**——这是回归速度与可重复性的根基；需要真实 IO 的验证显式分流到 `e2e_smoke.py` 与 `sandbox/tests`。
- 安全回归重点：`test_skill_import`（SSRF/zip slip）、`test_a2a_server`（API Key 归属）、`test_skills_binding`（审核门禁）、`test_chat_api`（错误不泄漏）、`test_sandbox_client`（三类错误）。
- fail-soft 回归重点：`test_notifier`、`test_mcp_client`、`test_graph*`（hook 降级）、`test_connector_pipeline`（超时兜底）——见 [故障处理与可观测性](/openwiki/operations/failure-and-observability.md)。

## 不变量

- `tests/` 全套零外部依赖（假会话 + ASGITransport + monkeypatch，不跑 lifespan/迁移）。
- E2E 与沙箱套件**不**被默认 pytest 收集（命名/目录隔离），需显式入口。
- 三件套 + 前端 `npm test` 是各规格的统一验收口径。
- 源码与测试是权威（`AGENTS.md`）：brief/设计中的未知项是验证缺口，不自动当需求。

相关页：[架构总览](/openwiki/architecture/overview.md)、[故障处理与可观测性](/openwiki/operations/failure-and-observability.md)、[沙箱执行](/openwiki/operations/sandbox.md)、[聊天生命周期](/openwiki/workflows/chat-lifecycle.md)。
