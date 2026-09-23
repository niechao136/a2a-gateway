---
type: deployment-guide
title: 部署拓扑与流量入口
description: 说明 docker-compose 六服务拓扑、nginx 统一入口的路由/SSE/WS 规则、唯一对外端口策略、健康检查与后端 lifespan 启动序列。
tags: [deployment, docker, nginx, compose, healthcheck, operations]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-bb1ebe868e35e9e500714501
    resource: repo://Dockerfile
  - id: openwiki-source-21cd86d1835d8fa9e2e76fca
    resource: repo://nginx/default.conf
  - id: openwiki-source-5587127d632cfcdc010b44e9
    resource: repo://src/a2a_gateway/main.py
  - id: openwiki-source-bfae268cbf1ce121dc066697
    resource: repo://web/Dockerfile
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 部署拓扑与流量入口

## 职责与归属

部署由 `docker-compose.yml`（六服务）、`nginx/default.conf`（统一反向代理）、后端 `Dockerfile`、前端 `web/Dockerfile` 与沙箱 `sandbox/Dockerfile` 共同定义。对外只暴露 nginx 的 `${GATEWAY_PORT:-10099}`；端口防火墙策略见 `TODO.md:173-181`。

## 服务拓扑

| 服务 | 镜像/构建 | 监听 | 对外 | 关键约束 |
|---|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 容器内 5432 | ❌ 无 `ports` | bind mount `./postgres_data`；`pg_isready` 健康检查（`docker-compose.yml:13-32`） |
| `backend` | 根 `Dockerfile` | 8000 | 仅 `expose` | 依赖 postgres + sandbox-gate 健康；注入全部业务环境变量；`extra_hosts` 映射 `host.docker.internal`（`docker-compose.yml:34-80`） |
| `sandbox-gate` | `sandbox/Dockerfile` | 8100 | 仅 `expose` | 有网络的前门；Bearer `SANDBOX_TOKEN`；共享卷 `sandbox_ipc`；`/healthz` 健康检查；`no-new-privileges`（`docker-compose.yml:84-103`） |
| `sandbox-runner` | 同镜像，`python -m sandbox.runner` | unix socket | ❌ | **`network_mode: none`**、`read_only`、`cap_drop: ALL`、`mem_limit 256m`、`pids_limit 64`、tmpfs `/tmp`、`no-new-privileges`（`docker-compose.yml:105-122`） |
| `frontend` | `web/Dockerfile`（Next standalone） | 3000 | 仅 `expose` | 非 root `nextjs` 用户；`NEXT_PUBLIC_API_BASE_URL` 显式置空以走同源相对路径（`web/Dockerfile:13-43`） |
| `nginx` | `nginx:1.27-alpine` | 80 | **`${GATEWAY_PORT:-10099}:80`（唯一对外端口）** | 挂载只读 `default.conf`；依赖 backend/frontend 健康（`docker-compose.yml:141-154`） |

依赖链：`nginx → backend/frontend（healthy） → postgres/sandbox-gate（healthy）`；backend 镜像内置 `/health` 健康检查（`Dockerfile:31-33`），start-period 30s 覆盖启动迁移窗口。

## nginx 统一入口规则

配置在 `nginx/default.conf`，核心设计：

1. **变量 + resolver 而非 upstream 块**：`resolver 127.0.0.11 valid=10s`；上游地址写成 `set $upstream_backend http://backend:8000` 变量。原因：upstream 块只在启动时解析一次并长期缓存 IP，compose 重建容器后会全站 502；变量 + resolver 按 TTL 在请求时重新解析，容器重建后自动恢复（`nginx/default.conf:7-15`、`46-48`）。
2. **X-Forwarded 自适应**：外层代理已声明 `X-Forwarded-Proto/Host` 则透传，缺失时按本层补全；Host 用 `$http_host` 原样保留端口（`$host` 会剥掉 `:10099`），支撑 Agent Card 回连地址推导（`nginx/default.conf:17-27`、`36-44`）。
3. **路由表**：

   | 路径 | 上游 | 备注 |
   |---|---|---|
   | `/api/` | backend:8000 | SSE 关键设置：`proxy_buffering off`、读/写超时 3600s（`default.conf:52-61`） |
   | `= /a2a` 与 `/a2a/` | backend | 精确匹配 + 前缀匹配都要转发，否则落入前端 location 造成 301 循环（`default.conf:63-85`） |
   | `/ws/` | backend | WebSocket `Upgrade/Connection` 透传（ASR）（`default.conf:88-96`） |
   | `/docs`、`/redoc`、`= /openapi.json`、`= /health` | backend | 文档与健康检查（`default.conf:99-113`） |
   | `/`（其余全部） | frontend:3000 | 含 WS/HMR 升级头（`default.conf:116-122`） |

4. `client_max_body_size 20m`（`default.conf:34`）。

`$request_uri` 拼接在 `proxy_pass` 变量后以原样透传完整 URI（含查询串）（`default.conf:51-53`）。

## 端口暴露策略

`TODO.md` 的端口清单（`TODO.md:175-181`）：

| 端口 | 用途 | 策略 |
|---|---|---|
| `10099` | nginx 统一入口 | 对外放行 |
| `22` | SSH | 仅限来源 IP |
| 上游 Hermes（如 `9900`） | A2A 目标 | 仅对后端主机/内网放行 |
| `5432` | PostgreSQL | 禁止对外（compose 不发布） |
| `8000` / `3000` / `8100` | backend / frontend / gate | 仅 compose 内网（`expose` 而非 `ports`） |

## 镜像构建要点

- **后端**（`Dockerfile`）：`python:3.11-slim` + uv 系统安装项目；拷贝 `alembic.ini` 与 `alembic/` 使容器内可直接执行迁移命令；编译工具装完即清除；CMD 为 `uvicorn a2a_gateway.main:app --host 0.0.0.0 --port 8000`（`Dockerfile:18-36`）。
- **前端**（`web/Dockerfile`）：三阶段（deps → build → standalone runner）；构建时 `NEXT_PUBLIC_API_BASE_URL` 置空，避免把 `localhost:8000` 打进产物；运行阶段仅拷 standalone 产物并降权为 `nextjs` 用户（`web/Dockerfile:1-43`）。

## 启动顺序（后端 lifespan）

`src/a2a_gateway/main.py:39-58`：

1. **迁移**：`asyncio.to_thread(run_migrations)` —— 旧库先 `stamp 0001_initial` 接管，再 `upgrade head`；失败则回退 `Base.metadata.create_all`（仅建表，不做版本管理）并记录异常日志。
2. **种子数据**：`ensure_default_agent`（slug `/`，绑定 `HERMES_A2A_URL`）→ `ensure_default_admin`（`ADMIN_USERNAME/PASSWORD`）→ `ensure_all_agent_api_keys`（为每个尚无 Key 的 Agent 补默认 Key）。
3. `yield` 服务请求；关停时 `close_all()` 关闭 A2A wrapper 缓存与 checkpointer。

健康就绪由 Docker HEALTHCHECK 探测 `/health`（应用侧路由见 `main.py:96-98`）；compose 用 `condition: service_healthy` 串起依赖顺序。

## 部署使用方式

```bash
cp .env.example .env   # 按需修改
docker compose up -d --build
# 访问 http://<host>:10099
```

compose 会读取 `.env` 做 `${VAR}` 替换，再经 `environment:` 注入容器（`.env.example:6-14`）。容器访问宿主机上游用 `host.docker.internal`（Linux 需 `extra_hosts` 映射，backend 已配置）。

## 失败与边界

- nginx 在 backend/frontend 不健康时不启动（`depends_on` healthy），避免半套拓扑。
- 迁移失败不阻止启动（回退 create_all），但该库将脱离版本管理。
- runner 无网络是硬隔离：gate→runner 仅经 `sandbox_ipc` 卷上的 unix socket，业务数据不落该卷（`docker-compose.yml:156-158`）。
- SSE/WS 超时统一放宽到 3600s，长时间流式对话不会被 nginx 掐断。

## 代表性验证

- 端口与健康检查：`docker-compose.yml`、`Dockerfile`、`web/Dockerfile`。
- 流量规则回归：`tests/test_a2a_server.py` 中公网 URL 派生用例（依赖 X-Forwarded 语义）。
- 相关页：[配置体系与环境变量](/openwiki/architecture/configuration.md)、[沙箱执行](/openwiki/operations/sandbox.md)、[认证与安全面](/openwiki/concepts/security.md)、[故障处理与可观测性](/openwiki/operations/failure-and-observability.md)。
