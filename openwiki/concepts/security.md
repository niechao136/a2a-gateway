---
type: security-reference
title: 认证与安全面
description: 梳理管理员 JWT、聊天身份 cookie、按 Agent API Key 三套凭据，出站认证方案、连接器验签、技能导入安全与沙箱隔离等信任边界。
tags: [security, authentication, jwt, api-key, verification, ssrf]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-24T01:27:47.842Z
sources:
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-d7601e423bff43604e9a8505
    resource: repo://sandbox/src/sandbox/protocol.py
  - id: openwiki-source-cf26b275f836c4fa92ad8b8d
    resource: repo://src/a2a_gateway/auth_scheme.py
  - id: openwiki-source-d546e8ed9b6ce8ef8c63afb6
    resource: repo://src/a2a_gateway/auth.py
  - id: openwiki-source-d63887ef5316c9268a327d9e
    resource: repo://src/a2a_gateway/connectors/feishu.py
  - id: openwiki-source-4aadfd8b6e41a836711f114e
    resource: repo://src/a2a_gateway/connectors/slack.py
  - id: openwiki-source-0340879b564de49693aa0bc7
    resource: repo://src/a2a_gateway/connectors/telegram.py
  - id: openwiki-source-4710921338b49a1a39cebe5c
    resource: repo://src/a2a_gateway/deps.py
  - id: openwiki-source-2f0a5e6928c1db642bc6b674
    resource: repo://src/a2a_gateway/llm_probe.py
  - id: openwiki-source-5587127d632cfcdc010b44e9
    resource: repo://src/a2a_gateway/main.py
  - id: openwiki-source-c02a6d45a645df8106612f51
    resource: repo://src/a2a_gateway/models.py
  - id: openwiki-source-d3e47f45c8a3dad144965b78
    resource: repo://src/a2a_gateway/repository.py
  - id: openwiki-source-d38fe19aaef9124307badd98
    resource: repo://src/a2a_gateway/routes/a2a_server.py
  - id: openwiki-source-96e2981cfaad30985f414a47
    resource: repo://src/a2a_gateway/routes/chat.py
  - id: openwiki-source-a9ded1863fe33ba2a953bab4
    resource: repo://src/a2a_gateway/routes/models.py
  - id: openwiki-source-47ce9773f0b2e90b200f8634
    resource: repo://src/a2a_gateway/sandbox_client.py
  - id: openwiki-source-c906c556d0d86b9ccfe8ed8b
    resource: repo://src/a2a_gateway/schemas.py
  - id: openwiki-source-de2718e1ecc52ece9bb9330b
    resource: repo://src/a2a_gateway/skill_import.py
generated: { by: "opencode", at: "2026-09-24T01:27:47.842Z" }
---

# 认证与安全面

## 凭据体系总览

| 凭据 | 载体 | 保护对象 | 实现 |
|---|---|---|---|
| 管理员 JWT | `Authorization: Bearer`（前端登录后内存/存储持有） | `/api/admin/*` | `auth.py` + `deps.get_current_admin` |
| 聊天身份 cookie | httpOnly `a2a_identity` | 会话目录归属（非管理权限） | `identity.py` |
| 按 Agent API Key | `X-Api-Key` 或 Bearer | 对外 `/a2a/{slug}` JSON-RPC | `a2a_server.validate_api_key` + `api_keys` 表 |
| 出站认证方案 | 目标注册表的 `token`/`auth_*` | 访问上游 A2A / MCP | `auth_scheme.py`（A2A 与 MCP 共用） |
| 连接器 webhook 凭据 | 平台签名/密钥（`credentials` JSONB，接口脱敏） | 入站平台事件 | `connectors/*` 适配器 |
| LLM 模型 API Key | `llm_models.api_key` 明文入库 | 上游 LLM 调用与连通性探针 | `routes/models.py`（出参仅 `api_key_masked`） |
| 沙箱共享令牌 | `SANDBOX_TOKEN` Bearer | backend → gate | `sandbox_client.py` / `gate.py` |

## 管理员 JWT（bcrypt + HS256）

- **密码哈希**：直接用 bcrypt（刻意不用已停维护的 passlib）；算法只用前 72 字节，超长密码显式截断，否则 bcrypt 5.x 报错（`auth.py:16-25`）。`verify_password` 捕获 `ValueError/TypeError` 返回 False（`auth.py:28-32`）。
- **令牌**：`create_access_token` 签发 `{sub, exp}`，密钥/算法/时长来自 `JWT_SECRET`/`JWT_ALGORITHM`/`JWT_EXPIRE_MINUTES`（默认 1440 分钟）（`auth.py:35-46`、`config.py:73-75`）。
- **依赖** `get_current_admin`（`deps.py:14-39`）：要求 Bearer 前缀 → 解码 → **再查库并检查 `disabled`**（禁用账号立即失效）→ 401 均带 `WWW-Authenticate: Bearer`。
- **登录播种**：默认管理员来自 `ADMIN_USERNAME/ADMIN_PASSWORD`，仅在不存在时创建（`repository.ensure_default_admin`，由 lifespan 调用）。
- 登录成功同时执行会话归并与身份 cookie 换发（见 [身份与会话](/openwiki/concepts/identity-and-conversations.md)）；退出签发全新访客 cookie（`admin.py:117-127`）。

## 聊天身份 cookie

httpOnly + 后端 JWT 签名，`samesite=lax`，`secure` 由 `COOKIE_SECURE` 控制（`identity.py:86-96`）。**它只证明「会话归谁」，不授予任何管理权限**；管理接口从不读它。防伪造依赖服务端签名而非前端可见性（`identity.py:9-11`）。

## 对外 A2A API Key

`validate_api_key`（`a2a_server.py:111-132`）：

1. 取 `X-Api-Key`，缺省则回退 `Authorization: Bearer`；
2. 查库：Key 存在、`enabled=true`、**`agent_id` 等于被调用 Agent** —— 任一不满足 → 401 + `WWW-Authenticate: Bearer`。

管理约束：`(agent_id, name)` 唯一；Key 格式 `a2a-<token_urlsafe(24)>`；`is_default=true` 的默认 Key 不可删除（`models.py:202-213`、`admin.py:306-307`）；启动时为每个缺 Key 的 Agent 补默认 Key（`main.py:52-53`）。**信任边界观察**：Key 以明文存储于 `api_keys.key` 并在管理 API `ApiKeyOut` 中返回给已认证管理员（`repository.get_api_key_by_key`、`schemas.ApiKeyOut`）——依赖管理面 JWT 与数据库访问控制，而非哈希验证。

## LLM 模型注册表密钥

- **明文入库**：`llm_models.api_key` 直接存库（对齐连接器 `credentials` 惯例，`models.py:183-184`）；管理 API 出参经 `mask_secret` 只回 `api_key_masked`（前 3 后 4，≤8 打码 `***`，`schemas.py:165-171`），`LLMModelOut` 不含明文字段。
- **更新语义**：`api_key` 留空或不传 = 保持原值（前端编辑不回显明文，`repository.py:474-476`）。
- **管理面**：`/api/admin/models*` 全部 `Depends(get_current_admin)`（JWT，`routes/models.py:48-154`）。
- **探针端点**：`POST /api/admin/models/{id}/test` 与 `POST /api/admin/models/test` 调用 `probe_llm` 发一条 `max_tokens=1` 的极短只读消息验证三元组（`llm_probe.py:52-106`），不写库、不执行工具；失败信息全中文区分 key 无效 / 模型不存在 / 地址不可达 / 超时。
- 构图侧空 key 在进底层客户端前抛中文错误，不会把 key 泄漏进英文栈（`llm.py:163-166`）。详见 [LLM 模型管理](/openwiki/concepts/llm-model-management.md)。

## 出站认证方案（A2A 与 MCP 共用）

`auth_scheme.py` 定义五种：`none` / `bearer`（默认）/ `header` / `query` / `basic`；`token` 统一承载密钥，`auth_name` 按类型解释为头名、参数名或 Basic 用户名（`auth_scheme.py:1-14`、`32-57`）。stdio MCP 无法带 HTTP 头，改为注入 `MCP_AUTH_TOKEN/TYPE/NAME` 环境变量给子进程（`auth_scheme.py:26-29`、`60-73`）。密钥只在出站请求构造时注入，不经浏览器回传。

## 连接器 webhook 验签

入站不走管理 JWT，由各平台算法验真（`VerifyError` → 401）：

| 平台 | 机制 | 关键点 |
|---|---|---|
| Telegram | `X-Telegram-Bot-Api-Secret-Token` 与凭据 `secret_token` 常量时间比较 | `hmac.compare_digest`（`telegram.py:44-52`）；secret 创建时自动生成 |
| Slack | Events API HMAC-SHA256：`v0:{ts}:{body}` | 时间戳偏移 >300s 拒绝（防重放），`hmac.compare_digest` 比较（`slack.py:19-45`）；challenge 与 parse 均先验签 |
| 飞书 | `verification_token` 校验 + 可选 AES-256-CBC 解密 | `key=sha256(encrypt_key)`、IV=密文前 16 字节、PKCS7（`feishu.py:32-69`）；收到加密体但未配 `encrypt_key` 直接拒绝 |

凭据存放于 `chat_connectors.credentials` JSONB，管理接口返回时经 `mask_connector_credentials` 脱敏；更新时空值表示「保持不变」（`schemas.py` 合并/掩码逻辑）。disabled/不存在的连接器返回不可区分的 404（`routes/connectors.py`）。

## 技能导入安全

`skill_import.py` 模块头明确安全要求（`skill_import.py:7-12`）：

- **zip slip** 路径拒绝、**符号链接**拒绝；
- **压缩炸弹**限制：`MAX_IMPORT_BYTES` 入口硬上限、`MAX_SKILL_BYTES` 解压总量、`MAX_FILE_BYTES` 单条目、`MAX_FILES` 条目数；
- 目录路径规范化（拒绝绝对路径/`..`/反斜杠）；
- **SSRF**：URL 导入 DNS 解析后**逐 IP 校验**，重定向 ≤ `URL_MAX_REDIRECTS`、超时 `URL_MAX_REDIRECTS`/`URL_FETCH_TIMEOUT`、响应体 4MB 上限；
- 后端**零文件系统访问**（全内存处理）。

上限常量集中在 `skills.py`，未新增环境变量（`TODO.md:13`）。导入后仍须人工审核 `approved` 才可绑定（[技能页](/openwiki/concepts/skills.md)）。

## 沙箱隔离

- **gate**：Bearer `SANDBOX_TOKEN`、请求体 8MB 上限、并发信号量（`sandbox/gate.py`）。
- **runner**：解释器白名单 `.py/.sh/.js`、相对路径校验（拒绝绝对/`..`）、子进程环境白名单（不继承宿主密钥）、rlimit（AS 256MB / NPROC 64 / FSIZE 16MB）（`sandbox/protocol.py`、`runner.py`）。
- **容器**：runner `network_mode: none` + `read_only` + `cap_drop ALL` + 256m + 64 pids + `no-new-privileges`（`docker-compose.yml:105-122`）。
- backend 侧 `SANDBOX_URL` 为空则根本不挂载 `run_skill_script` 工具（`sandbox_client.py:37-39`）。

## CORS 与浏览器边界

CORS 单一来源 `FRONTEND_ORIGIN`，`allow_credentials=True`、全方法全头（`main.py:68-74`）。这只约束浏览器；**聊天 `/api/chat/*` 路由本身除 cookie 所有权外无额外认证**——非浏览器客户端不受 CORS 限制，防护依赖 `thread_id` 未知性 + 所有权校验 + cookie 签名（`chat.py:398-406`）。

## 信任边界观察（如实记录）

1. **`X-Forwarded-*` 信任**：`public_base_url` 在无外层代理声明时按本层 `Host` 补全；若服务直接暴露而非经 nginx，客户端可影响 Agent Card 回连地址（`public_url.py:29-47`、`nginx/default.conf:17-27`）。部署契约是 nginx 为唯一入口。
2. **API Key 明文存储**（`api_keys.key` 与 `llm_models.api_key` 均明文；后者出参脱敏、更新留空保持原值，上文已述）。
3. **聊天路由仅凭 cookie 所有权**（上文已述）。
4. **`JWT_SECRET`/`ADMIN_PASSWORD` 默认值**适合开发，生产必须覆盖（`.env.example`、`config.py:73-77`）。
5. 告警 webhook、ONNX Hub、沙箱等密钥均服务端注入，前端只接触代理后的相对接口（`speech.py` 密钥注入）。
6. **模型探针**：仅管理员可达，只发极短只读请求；`llm_models.api_key` 明文与 `api_keys` 同一信任模型（管理面 JWT + DB 访问控制）。

## 相关失败语义

- 所有认证失败统一 401（带 `WWW-Authenticate`）；slug 未发布 404（不区分不存在/未发布，`a2a_server.py:138-145`）。
- 连接器 `VerifyError` → 401；禁用/不存在 → 404（不可区分）。
- 全局异常兜底返回通用 500，不泄栈（`main.py:77-84`）。

## 代表性测试

- `tests/test_admin_api.py`：登录/401/CRUD。
- `tests/test_auth_scheme.py`：五种出站方案与 stdio 环境注入。
- `tests/test_conversations.py`：cookie 篡改/过期拒绝。
- `tests/test_a2a_server.py`：API Key 归属与 401 路径。
- `tests/test_llm_models_api.py`：模型列表脱敏、探针端点、删除 409/force。
- `tests/test_connector_adapters.py`：Slack 签名/飞书加解密/Telegram 过滤。
- `tests/test_skill_import.py`：离线安全（zip-slip、SSRF、炸弹）。
- `tests/test_sandbox_client.py`：三类错误映射。

相关页：[身份、会话与所有权](/openwiki/concepts/identity-and-conversations.md)、[配置体系](/openwiki/architecture/configuration.md)、[LLM 模型管理](/openwiki/concepts/llm-model-management.md)、[部署拓扑](/openwiki/architecture/deployment.md)、[沙箱执行](/openwiki/operations/sandbox.md)。
