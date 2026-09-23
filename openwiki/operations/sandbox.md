---
type: operations-guide
title: 沙箱执行（gate/runner 隔离）
description: 说明技能脚本执行的双容器隔离：gate 前门（Bearer、限流、8MB 上限）经 unix socket 转发到无网络 runner（解释器白名单、路径校验、rlimits、环境白名单），及客户端三类错误。
tags: [sandbox, gate-runner, rlimit, isolation, unix-socket, timeouts]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-9cf04e39e4d8a36b3244bfc8
    resource: repo://sandbox/src/sandbox/gate.py
  - id: openwiki-source-d7601e423bff43604e9a8505
    resource: repo://sandbox/src/sandbox/protocol.py
  - id: openwiki-source-2a7d500289aa3806251d6a81
    resource: repo://sandbox/src/sandbox/runner.py
  - id: openwiki-source-47ce9773f0b2e90b200f8634
    resource: repo://src/a2a_gateway/sandbox_client.py
  - id: openwiki-source-4df82bf1a4678a53b6438ca1
    resource: repo://src/a2a_gateway/tools.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 沙箱执行（gate/runner 隔离）

技能脚本在独立沙箱服务中执行，链路：

```
backend ──HTTP(内网, Bearer SANDBOX_TOKEN)──▶ sandbox-gate ──unix socket(/ipc/sandbox.sock)──▶ sandbox-runner
```

同一镜像（`sandbox/Dockerfile`）双角色：compose 用不同 `command` 启动 gate / runner；共享 named volume `sandbox_ipc` 只放 socket 文件（`docker-compose.yml:82-122`）。**gate 是唯一有网络的沙箱组件**；runner `network none` 彻底无网络（`gate.py:1-5`）。

## gate 前门

`POST /v1/run`（`gate.py:58-70`）处理顺序：

1. **Bearer 鉴权** `_check_token`：`Authorization: Bearer ${SANDBOX_TOKEN}`，token 为空或不匹配 → **401**（`gate.py:26-29`）。backend 侧 `SANDBOX_TOKEN` 与 gate 同值（compose 内网约定，`.env.example:74-76`）。
2. **请求体上限 8MB**：`MAX_RUN_BODY_BYTES = 8 * 1024 * 1024`（4MB 附件 b64 后约 5.4MB，留余量；`protocol.py:16`），超限 → **413**（`gate.py:62-63`）。
3. **协议校验**：`RunRequest` 解析失败 → **400**（`gate.py:64-67`）；`clamp_timeout` 把 `timeout_s` 收进 `[1, 120]`。
4. **并发信号量** `asyncio.Semaphore(SANDBOX_MAX_CONCURRENCY=3)`（`gate.py:17`、`23`、`69-70`）。
5. **转发 runner** `_forward`（`gate.py:32-55`）：unix socket 连接超时 **5s**（`FORWARD_CONNECT_TIMEOUT`）→ 写请求 JSON + `write_eof` → 读响应至 EOF，等待 **`timeout_s + 5` 秒**。单连接一请求。错误映射：
   - socket 不可达（FileNotFound/ConnectionRefused/OSError/连接超时）→ **503** `runner 不可达：{类型}`；
   - 读响应超时 → **504** `runner 执行超时`；
   - OSError/ValueError → **502** `runner 通信失败：{类型}`。

`GET /healthz`：2s 内试连 socket，成功 `{"status":"ok"}`，失败 503（compose healthcheck 用它，`gate.py:73-82`、`docker-compose.yml:97-101`）。docs/redoc 关闭（`gate.py:22`）。

## runner 执行器

`runner.py` 每请求独立工作目录 `/tmp/exec-<uuid>` + rlimit 子进程 + 超时 kill 进程组。**任何内部错误都收敛进 `RunResult.error`，绝不向调用方抛异常**（`runner.py:48-49`）。

### 协议层校验（`protocol.py`）

- **解释器白名单** `SUFFIX_INTERPRETER`：`.py→python3`、`.sh→bash`、`.js→node`；白名单外 `ProtocolError("不支持的脚本类型…")`（`protocol.py:21-26`、`80-87`）。
- **路径校验** `assert_safe_rel_path`：空 / 绝对路径 / 含 `..` / 反斜杠一律拒绝，`posixpath.normpath` 后仍越界即拒绝——与 backend 导入管线同口径（`protocol.py:67-77`）。
- **数量/体量**：文件数 ≤ 100、解码后总量 ≤ 4MB（`decode_files`，`protocol.py:90-109`）。
- **环境白名单** `CHILD_ENV`：显式构造 `PATH/HOME/TMPDIR/LANG/PYTHONDONTWRITEBYTECODE`，**绝不继承容器环境**（backend env 含敏感凭据，runner 自身虽无凭据仍以白名单为底线，`protocol.py:28-36`）。
- **输出截断**：stdout/stderr 各 32KB（`MAX_OUTPUT_BYTES`，`truncate_output` 容错解码并标 `truncated`，`protocol.py:19`、`116-120`）。
- **timeout clamp**：`[1, 120]`（`clamp_timeout`，默认 30）。

### 容器级约束（compose）

runner（`docker-compose.yml:105-122`）：

| 约束 | 值 |
|---|---|
| 网络 | **`network_mode: none`** |
| 根文件系统 | **`read_only: true`** |
| capabilities | **`cap_drop: [ALL]`** |
| PID 上限 | **`pids_limit: 64`** |
| 内存 | **`mem_limit: 256m`** |
| /tmp | **`tmpfs: /tmp:size=64m,mode=1777`** |
| 提权 | **`no-new-privileges`** |
| 用户 | 非 root（镜像定义） |
| IPC | 仅挂 `sandbox_ipc:/ipc` |

gate 也有 `no-new-privileges`，`expose: 8100` 不对外（`docker-compose.yml:84-103`）。

### 进程级 rlimit 约束

`_apply_rlimits` 在子进程 `preexec_fn` 中执行（POSIX，`runner.py:38-45`）：

| rlimit | 值 | 说明 |
|---|---|---|
| `RLIMIT_CPU` | `(max(1,timeout_s), timeout_s+1)` | 与墙钟超时双保险 |
| `RLIMIT_AS` | **256MB**（`RLIMIT_AS_BYTES`） | 与容器 `mem_limit(256m)` **同水位**（`runner.py:28`） |
| `RLIMIT_NPROC` | **64**（`RLIMIT_NPROC`） | 与 `pids_limit` 对齐 |
| `RLIMIT_FSIZE` | **16MB** | 单文件写入上限 |

子进程：`start_new_session=True` 独立进程组（超时整组 kill）、`env=CHILD_ENV`、stdout/stderr PIPE、cwd=工作目录（`runner.py:65-74`）。`build_command` = `[*resolve_interpreter(entry), entry, *argv]`（`runner.py:33-35`）。

### 超时链（timeout_s+10 / +5）

三层递进，外层总是比内层宽：

1. **runner `communicate`**：`asyncio.wait_for(..., timeout=timeout_s)`（`runner.py:79-81`）——触发时 `os.killpg(SIGKILL)` 整组杀掉，等 5s 收尸；`timeout=True`、`exit_code=None`、**stdout/stderr 置空不回传半截输出**（避免误导模型，`runner.py:4-5`、`86-95`）。
2. **gate 读响应**：`timeout_s + 5`（`gate.py:46`）——覆盖 runner 收尸窗口，超时回 **504**。
3. **backend httpx**：`timeout=timeout_s + 10`（`sandbox_client.py:75`）——覆盖 gate 504 前的排队+转发，超时表现为 `httpx.TimeoutException` → `SandboxUnavailable`。

另有辅助超时：gate 连接 socket **5s**、健康检查试连 **2s**、runner 读请求 **300s**（`gate.py:20`、`77`、`runner.py:113`）。

`handle_client` 单连接一请求：读至 EOF → `execute` → 回 JSON → 关闭（`runner.py:109-126`）；任何异常回 `RunResult(error="请求处理失败：{类型}")` 而非断连。`finally` 恒 `shutil.rmtree(workdir, ignore_errors=True)`。

## 客户端三类错误与禁用开关

`sandbox_client.py` 定义 `SandboxError` 基类及三个子类，**message 面向最终用户/模型**（`sandbox_client.py:21-34`）：

| 异常 | 触发条件 |
|---|---|
| **`SandboxUnavailable`** | ① **`SANDBOX_URL` 为空 = 功能关闭**（`run_script` 开头即抛，`sandbox_client.py:65-66`）；② `httpx.HTTPError`（含超时）→ `沙箱服务不可达：{类型}`（`sandbox_client.py:82-83`）；③ gate 503 runner 不可达最终也映射到此路径之外的 5xx→Rejected——以状态码表为准见下 |
| **`SandboxTimeout`** | gate **504**（runner 执行超时）→ `脚本执行超时`（`sandbox_client.py:84-85`） |
| **`SandboxRejected`** | **401** 鉴权失败；**其它非 200**（400/413/502/503 等）→ `沙箱拒绝执行（HTTP {code}）`（`sandbox_client.py:86-89`） |

请求组装：`files` 来自技能快照（script 条目 base64 原样透传、text utf-8，encoding 缺省按 utf-8 兼容存量）、`stdin` b64、`timeout_s` clamp 到 30–120（`sandbox_client.py:42-74`）。

### 禁用开关

`sandbox_enabled()` = `bool(SANDBOX_URL)`（`sandbox_client.py:37-39`）。`make_script_exec_tools` **构图时**若未启用直接返回 `[]`——`run_skill_script` 工具整体不出现（零回归向后兼容），管理端试跑返回明确错误（`tools.py:531-532`）。compose 默认 `SANDBOX_URL=http://sandbox-gate:8100`，留空字符串可显式关闭（`docker-compose.yml:73-74`）。

## 与技能工具的边界

`make_script_exec_tools`（`tools.py:522-603`）门禁（构图时闭包判定、运行时零 DB）：

1. `sandbox_enabled()` 否 → `[]`；
2. 仅 `allow_scripts` 快照为 true 的技能参与（`tools.py:533`）；
3. 工具描述静态列出 `_script_catalog`（skill → 脚本路径），避免模型捏造路径；
4. 运行时：技能未命中/脚本 `entry_type != script` → 返回清单文本；`SandboxError` → `脚本执行失败：{message}`；成功 → 格式化 `exit_code/duration_ms` + 超时/截断标记 + stdout/stderr。**执行错误一律收敛为工具文本，绝不打断对话**（`tools.py:529`、`570-571`）。

管理端试跑 `POST /skills/{id}/scripts/run` 不校验 `review_status`/`allow_scripts`（审核辅助）。设计全貌见 [技能系统](/openwiki/concepts/skills.md)。

## 分层防御汇总

| 层 | 手段 |
|---|---|
| 传输 | 内网 compose 网络 + Bearer `SANDBOX_TOKEN` + 8MB 体上限 + 信号量 3 |
| 协议 | 解释器白名单、相对路径校验、≤100 文件、≤4MB 解码量、timeout clamp、输出 32KB 截断 |
| 进程 | 独立工作目录、`CHILD_ENV` 白名单、rlimit AS/NPROC/FSIZE/CPU、独立进程组超时 SIGKILL |
| 容器 | `network none`、`read_only`、`cap_drop ALL`、256m、64 pids、tmpfs `/tmp`、`no-new-privileges`、非 root |
| 语义 | 超时不回半截输出；runner 内部错误回传 `RunResult.error` 不断连；客户端三类错误文本化降级 |

残留风险（规格 §已接受）：子进程隔离 ≠ VM 级，内核 0day 理论逃逸；详见 [认证与安全面](/openwiki/concepts/security.md)。

## 代表性测试

- `sandbox/tests`：真实子进程冒烟——python stdout 捕获、`sleep 5` 超时 kill、>32KB 截断、env 白名单断言（不可见 backend 注入密钥）、路径穿越拒绝、解释器白名单。
- `tests/test_sandbox_client.py`：`SandboxTimeout/SandboxRejected/SandboxUnavailable` 三类映射、`SANDBOX_URL` 空禁用。
- `tests/test_tools_script_exec.py`：门禁（未配置不挂载、allow_scripts 过滤）、错误文本化、monkeypatch 模块属性调用（`tools.py:563` 注释）。

相关页：[技能系统](/openwiki/concepts/skills.md)、[认证与安全面](/openwiki/concepts/security.md)、[部署架构](/openwiki/architecture/deployment.md)、[配置参考](/openwiki/architecture/configuration.md)、[故障处理与可观测性](/openwiki/operations/failure-and-observability.md)。
