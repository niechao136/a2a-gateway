---
type: configuration-guide
title: 配置体系与环境变量
description: 说明 a2a-gateway 的 pydantic-settings 配置加载优先级、数据库连接串合成与迁移驱动改写，并按组件分类列出关键环境变量及默认值。
tags: [configuration, environment, dotenv, settings, database-url, alembic]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-267851376b2890357860d983
    resource: repo://alembic/env.py
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-43725894c7c3e0df0d15d0b5
    resource: repo://src/a2a_gateway/config.py
  - id: openwiki-source-47ce9773f0b2e90b200f8634
    resource: repo://src/a2a_gateway/sandbox_client.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 配置体系与环境变量

## 职责与归属

配置层集中在 `src/a2a_gateway/config.py`：定义 `Settings`（pydantic-settings）、在模块导入时加载可选的 dotenv 文件、合成数据库连接串，并通过 `get_settings()` 的 `lru_cache` 单例向全应用分发。样例配置与优先级说明见 `.env.example`。

## 加载机制与优先级

`config.py` 在模块导入时立即执行 `_load_env_files()`（`src/a2a_gateway/config.py:42-43`），规则为：

1. **真实环境变量优先**：`load_dotenv(override=False)` 只填补缺失项，不会覆盖已存在的进程环境变量（shell export、docker compose `environment:`、K8s env 等），见 `src/a2a_gateway/config.py:25-39`。
2. **未设置 `DOTENV_PATH` 时**：自动向上逐级查找 `.env`，找不到静默跳过。
3. **设置 `DOTENV_PATH` 时**：按 `os.pathsep` 分隔加载一个或多个文件（Windows 为 `;`，POSIX 为 `:`）。

文件载入后，`Settings` 统一从环境变量读取（`extra="ignore"`，`src/a2a_gateway/config.py:46-50`）。因此 compose 部署中 `.env` 有双重角色：compose 用它做 `${VAR}` 替换，容器内则由 `environment:` 注入的变量生效（`.env.example:6-14`）。

`get_settings()` 是 `@lru_cache` 单例（`src/a2a_gateway/config.py:152-154`）：同一进程内首次调用后固定一份 `Settings`。多个模块在导入时绑定 `_settings = get_settings()`（如 `main.py`、`database.py`、`auth.py`、`agent_factory.py`），另有模块按请求/调用时取值（如 `speech.py`、`sandbox_client.py`、`pending_store.py`）——改变环境变量只影响新进程，不影响已运行进程。

## 数据库连接串合成

两种配置方式，**完整连接串优先级更高**（`src/a2a_gateway/config.py:117-132`）：

| 方式 | 变量 | 结果 |
|---|---|---|
| 组件式 | `POSTGRES_USER/PASSWORD/DB/HOST/PORT` | 自动拼接 `database_url`（`postgresql+asyncpg`）与 `checkpoint_db_url`（`postgresql`）；密码经 `quote_plus` 编码 |
| 完整串 | `DATABASE_URL` / `CHECKPOINT_DB_URL` | 显式设置后覆盖对应合成结果；可只设其中一个 |

`model_validator(mode="after")` 在构造时补齐缺失的连接串（`src/a2a_gateway/config.py:117-124`）。同一 PostgreSQL 实例因此存在两条 URL：ORM 引擎用 asyncpg 方言，LangGraph checkpointer 用裸 `postgresql://` 供 psycopg v3 直连（`config.py:9-12` 注释）。

### migration_db_url 的驱动改写

Alembic/SQLAlchemy 需要显式的 `postgresql+psycopg://` 才会选用 psycopg v3，而 `checkpoint_db_url` 默认是裸 `postgresql://`。`migration_db_url` 属性在前者以此开头时做前缀替换，否则原样返回（`src/a2a_gateway/config.py:139-149`）。`alembic/env.py` 用它注入 `sqlalchemy.url`（`alembic/env.py:28-29`）。

## 环境变量目录（按组件）

### 数据库（PostgreSQL）
| 变量 | 默认值 | 说明 |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `a2a` / `a2a_secret` / `a2a_gateway` | 组件式连接配置（`config.py:53-55`） |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `localhost` / `5432` | 本机开发用 localhost；compose 覆盖为服务名 `postgres`（`docker-compose.yml:51-52`） |
| `DATABASE_URL` / `CHECKPOINT_DB_URL` | 空 | 完整连接串，设置后优先（`config.py:60-61`） |

### 默认 Agent 与 LLM
| 变量 | 默认值 | 说明 |
|---|---|---|
| `HERMES_A2A_URL` / `HERMES_A2A_TOKEN` | `http://localhost:8080/` / 空 | 种子默认 Agent 的上游目标；compose 默认 `host.docker.internal`（`config.py:64-65`、`docker-compose.yml:54-55`） |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容端点 / 空 / `gpt-4o-mini` | LangGraph 使用的 OpenAI 兼容 LLM（`config.py:68-70`） |

### 管理员认证（JWT）
| 变量 | 默认值 | 说明 |
|---|---|---|
| `JWT_SECRET` / `JWT_ALGORITHM` / `JWT_EXPIRE_MINUTES` | `dev-only-change-this` / `HS256` / `1440` | 管理中心 Bearer JWT（`config.py:73-75`） |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `change-me` | 首次启动播种默认管理员（`config.py:76-77`） |

### 身份 cookie 与应用
| 变量 | 默认值 | 说明 |
|---|---|---|
| `IDENTITY_EXPIRE_DAYS` / `VISITOR_EXPIRE_DAYS` | `30` / `180` | 登录用户与匿名访客身份 cookie 有效期；登录态刻意长于 JWT 以免会话列表「无故消失」（`config.py:79-83`） |
| `COOKIE_SECURE` | `false` | HTTPS 部署置 true（`config.py:84-85`） |
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8000` | 开发用 uvicorn 监听（`config.py:88-89`） |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | CORS 单一允许来源；compose 默认 `http://localhost:10099`（`config.py:90`、`docker-compose.yml:67`） |
| `PUBLIC_BASE_URL` | 空 | 连接器 webhook 完整 URL 展示与 Telegram 自动注册；留空只显示相对路径（`config.py:92-93`） |
| `PENDING_A2A_TTL_SECONDS` | `86400` | input-required 挂起任务读时过期秒数（`config.py:95-96`） |

### 语音（onnx-hub）
| 变量 | 默认值 | 说明 |
|---|---|---|
| `ONNX_HUB_BASE_URL` | `http://43.156.187.79:10100` | ASR/TTS 上游（`config.py:100-102`） |
| `ONNX_HUB_API_KEY` | 空 | 由网关注入，前端不可见（`config.py:103`） |
| `ONNX_HUB_ASR_MODEL` / `ONNX_HUB_TTS_MODEL` | `zipformer-streaming-bilingual-zh-en` / `vits-zh-aishell3` | 默认识别/合成模型（`config.py:104-107`） |

### 告警与沙箱
| 变量 | 默认值 | 说明 |
|---|---|---|
| `ALERT_WEBHOOK_URL` / `ALERT_WEBHOOK_TOKEN` | 空 | 配置后 A2A/Agent/流式失败 POST 到 Webhook；未配置仅写 ERROR 日志（`config.py:109-111`） |
| `SANDBOX_URL` / `SANDBOX_TOKEN` | 空 / 空 | `SANDBOX_URL` 为空即功能关闭、不挂载 `run_skill_script` 工具（`config.py:113-115`；挂载门控在 `src/a2a_gateway/sandbox_client.py:37-39`）；compose 默认指向 `http://sandbox-gate:8100`（`docker-compose.yml:74-75`） |

### 部署专用（仅 compose/样例层，不进 `Settings`）
- `GATEWAY_PORT`（默认 `10099`）：nginx 唯一对外端口（`docker-compose.yml:150-152`、`.env.example:78-80`）。
- `DOTENV_PATH`：仅在 `_load_env_files` 中通过 `os.getenv` 读取（`config.py:32`）。

## 本地开发与 compose 的差异

- **数据库主机**：本地 `POSTGRES_HOST=localhost`；compose 强制为服务名 `postgres` 且不向宿主机暴露端口（`docker-compose.yml:21-24,51-52`、`.env.example:81-83`）。
- **CORS 来源**：本地前端 `http://localhost:3000`；经 nginx 统一入口时默认 `http://localhost:${GATEWAY_PORT}`（`.env.example:86`）。
- **访问宿主机上游**：容器内需 `host.docker.internal`（compose 已做 `extra_hosts` 映射，`docker-compose.yml:76-78`）。
- **沙箱**：compose 默认开启并指向 gate 服务；本地留空 `SANDBOX_URL` 即禁用。

## 失败与边界

- 配置错误（如非法布尔值）由 pydantic 在首次构造 `Settings` 时抛出，进程启动即失败。
- 迁移使用 `migration_db_url`，与 ORM 的 asyncpg 连接串相互独立；改连接配置必须同时对齐两条 URL 的来源（组件式会同时生成，完整串需分别设置）。
- 单例意味着测试不可直接改环境变量断言 `get_settings()`；`tests/test_config.py` 用 `Settings.model_validate(...)` 只吃显式入参，避免叠加开发机本地配置（`tests/test_config.py:6-12`）。

## 代表性测试

- `tests/test_config.py`：组件式拼接、完整串优先、密码 URL 编码、`migration_db_url` 驱动改写与保留显式驱动、告警默认空值（`tests/test_config.py:15-61`）。
- 相关页：[部署拓扑与流量入口](/openwiki/architecture/deployment.md)、[数据模型与迁移](/openwiki/architecture/data-model.md)、[认证与安全面](/openwiki/concepts/security.md)、[故障处理与可观测性](/openwiki/operations/failure-and-observability.md)。
