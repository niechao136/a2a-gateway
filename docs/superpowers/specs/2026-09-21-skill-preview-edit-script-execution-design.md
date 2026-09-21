# Skill 预览、编辑与脚本沙箱执行 — 设计规格

- 日期：2026-09-21
- 状态：已与用户逐项确认（决策记录见 §1），待实现
- 范围：a2a-gateway 单仓库（后端 `src/a2a_gateway/`、前端 `web/`、新增沙箱服务 `sandbox/`、部署编排 `docker-compose.yml`）

---

## 0. 背景与目标

Skill 管理当前只支持「导入 → 审核」两条链路。本设计补齐三块能力：

1. **预览**：管理端查看技能正文（Markdown 渲染）与附件内容；
2. **编辑**：管理端修改 description / content / load_mode / enabled / allow_scripts 与附件（后端 `PUT /skills/{id}` 已存在，扩展 files）；
3. **脚本执行（Claude Code 风格）**：技能附件中的脚本（.py/.sh/.js）可在隔离沙箱中真正执行——Agent 运行时由 LLM 按需调用 + 管理端手动试跑辅助审核。

## 1. 已确认决策（与用户逐项确认）

| 决策项 | 结论 |
|---|---|
| 执行含义 | C：执行技能捆绑脚本（沙箱），非试运行对话、非 Skill-as-Task |
| 沙箱形态 | 方案二：独立沙箱执行器服务（gate + runner 双容器，unix socket IPC） |
| 脚本运行时 | Python + Shell + Node（.py / .sh / .js） |
| 触发通路 | 双通路：Agent 运行时工具 + 管理端手动试跑 |
| 编辑范围 | description / content / load_mode + 附件（name 不可改） |
| 审核语义 | 正文或附件变更 → 重置 pending；allow_scripts 属策略，变更不重置 |
| 导入来源新增 | 支持上传单个 SKILL.md 文件（填入粘贴 tab，复用 source=text，后端零改动） |

## 2. 现状基线（本设计所依赖的既有事实）

- `Skill` 模型（`models.py`）：`files` JSONB 为纯文本附件 `[{"path","size","content"}]`；`review_status`（pending/approved/rejected）；`enabled`。
- 审核门禁：`repository.resolve_skills` 只放行 `enabled && approved` 进 Agent 快照；快照即全部、运行时零 DB 依赖。
- 编辑路由 `PUT /skills/{id}`（`routes/registry.py`）：已有 description/content/load_mode/enabled，内容变更自动重置 pending 并 `refresh_agents_for_skills` + 图缓存失效。
- 导入管线（`skill_import.py`）：zip/目录/URL/text 四来源；二进制条目按内容判定后**一律跳过**并标 `skipped_binary`；zip slip / SSRF / 压缩炸弹防护已完备。
- 运行时注入（`graph.py` / `tools.py`）：`build_skills_prompt`（always 常驻 / on_demand 清单）、`load_skill` / `read_skill_file`（白名单读附件）、`pre_model_hook` 记账重注入。
- 部署：docker-compose（postgres / backend / frontend / nginx），backend 容器 env 含全部敏感凭据；无 docker.sock 挂载。
- 前端：`/admin/skills` 列表只有导入/审核/删除；无 Markdown 渲染依赖。

## 3. 总体架构（沙箱子系统）

### 3.1 拓扑

`network_mode: none` 的容器无法被访问，故沙箱拆为**同一镜像的两个角色**，经共享 volume 上的 unix domain socket 通信：

```
backend ──HTTP(内网)──▶ sandbox-gate ──unix socket(/ipc/sandbox.sock)──▶ sandbox-runner
(compose 网络)          (有网络:鉴权/限流/协议校验)                     (network_mode: none
                                                                       read_only rootfs
                                                                       cap_drop: [ALL]
                                                                       非 root、pids/mem limit
                                                                       tmpfs /tmp)
```

- `Dockerfile.sandbox`：一个镜像（python3-slim + nodejs + bash），compose 两个 service 用不同 command 启动 gate / runner。
- 共享 volume `sandbox_ipc`（named volume，仅放 socket 文件）。

### 3.2 职责边界

- **gate**（领域无关的执行前门）：
  - `POST /v1/run`：请求体 `{runtime, files, argv, stdin_b64?, timeout_s}`；校验内部 token（`Authorization: Bearer ${SANDBOX_TOKEN}`）、runtime 白名单、files 路径安全与总量、timeout clamp。
  - 并发信号量（默认 3）；转发 runner（等待 timeout_s + 5s）；`GET /healthz` 探测 runner socket 可达。
- **runner**（领域无关的执行器）：
  - 每请求建独立工作目录 `/tmp/exec-<uuid>/`，按相对路径铺开 `files`，`chdir` 到包根；
  - 按入口脚本后缀路由解释器：`.py → python3`、`.sh → bash`、`.js → node`，`cwd=包根`，脚本以相对路径调用（可 `open` 读兄弟附件）；
  - 干净 env（仅 `PATH`、`HOME=/tmp`、`TMPDIR=/tmp`、`LANG=C.UTF-8`、`PYTHONDONTWRITEBYTECODE=1`）；
  - `resource.setrlimit`：CPU 秒、AS 内存、NPROC、FSIZE；
  - `asyncio.wait_for` 超时 → kill 进程组；stdout/stderr 各截断 32KB；
  - 响应 `{exit_code, stdout, stderr, truncated, timeout, duration_ms}`。
- **backend `sandbox_client.py`**：httpx 封装；组装请求（从技能快照取入口脚本 + 全部文本附件）；错误三分类（unavailable / timeout / denied）；`SANDBOX_URL` 未配置 → `run_skill_script` 工具整体不挂载（详见 §8.1），管理端试跑返回明确错误，均不影响主对话。

## 4. 数据模型变更

### 4.1 `skills.files` 条目结构演进（JSONB 内，应用层兼容）

```json
{"path": "scripts/gen.py",     "size": 1234, "content": "<base64>", "entry_type": "script", "encoding": "base64"}
{"path": "references/a.md",    "size": 456,  "content": "<文本>",    "entry_type": "text",   "encoding": "utf-8"}
```

- 存量数据缺 `entry_type`/`encoding` → 读取处兜底视为 `text`/`utf-8`；
- `size` 恒为 base64 解码后的原始字节；`size_bytes` 口径同步（脚本按原始字节计）。

### 4.2 新列

- `Skill.allow_scripts: bool`，default `False`，`server_default=sa.false()`；
- Alembic 迁移 `0011_skill_scripts`（仅加列；files 结构演进不迁移）。

### 4.3 快照透传

`skill_snapshot` 增加 `allow_scripts`；`files` 原样透传（含 entry_type/encoding）。`resolve_skills` 门禁不变。

### 4.4 常量集中（`skills.py`）

```python
SCRIPT_SUFFIXES = {".py", ".sh", ".js"}
MAX_SCRIPT_BYTES = 262144        # 单脚本 256KB（原始字节）
MAX_SCRIPT_OUTPUT_BYTES = 32768  # stdout/stderr 截断（与沙箱服务约定一致）
SCRIPT_TIMEOUT_DEFAULT_S = 30
SCRIPT_TIMEOUT_MAX_S = 120
```

## 5. 导入管线变更（`skill_import.py`）

- `_split_binary`：二进制条目不再一律跳过——后缀 ∈ `SCRIPT_SUFFIXES` 且 ≤ `MAX_SCRIPT_BYTES` → 以 `entry_type="script"`、base64 编码入库；其余仍跳过并标注 `skipped_binary`。
- preview item / commit 结果携带 entry_type（前端预览可区分「N 个脚本，M 个文本」）。
- 安全复用：路径校验 `_assert_safe_rel_path`、`MAX_FILE_BYTES`/`MAX_SKILL_BYTES` 上限口径不变。

### 5.1 新增导入来源：上传单个 SKILL.md 文件（用户追加需求）

- **前端改动（零后端改动）**：导入弹窗「粘贴」tab 增加「从本地选择 SKILL.md」按钮（`accept=".md,.markdown"`，单选）；`file.text()` 读入后**填入既有文本域**，管理员可继续手改，随后走既有 `source=text` 预览/落库。
- 不新增第 5 个 tab 的理由：粘贴 tab 已具备完整流程，文件选择只是「填内容」的另一种方式，且填入后可改再导，优于直接提交。

## 6. 编辑功能

### 6.1 `SkillUpdate` 扩展

```python
class SkillFileIn(BaseModel):
    path: str
    content: str                  # text 原文 或 base64
    entry_type: Literal["text", "script"] = "text"
    encoding: Literal["utf-8", "base64"] = "utf-8"

class SkillUpdate(BaseModel):
    description: str | None = None
    content: str | None = None
    load_mode: Literal["always", "on_demand"] | None = None
    enabled: bool | None = None
    allow_scripts: bool | None = None
    files: list[SkillFileIn] | None = None   # None=不改；列表=全量替换
```

- `files` 全量替换语义（前端整体提交当前附件列表），比增量 patch 简单可靠；
- 后端校验：`_assert_safe_rel_path` 逐条、entry_type 与后缀一致性（script 必须命中白名单后缀）、单脚本 ≤ `MAX_SCRIPT_BYTES`、总量 ≤ `MAX_SKILL_BYTES`、条目数 ≤ `MAX_FILES`；重算 `size_bytes` / `file_count`；
- `name` 不可改（已确认范围）。

### 6.2 审核联动

- `repository.update_skill`：files 变更与 description/content 变更同等对待 → 重置 pending、清空 reviewed_at；
- `allow_scripts` / `load_mode` / `enabled` 变更不重置；
- 编辑后既有 `refresh_agents_for_skills` + `invalidate_agent` 链路自动生效，无需新增。

## 7. 管理端 API（`routes/registry.py`）

| 端点 | 说明 |
|---|---|
| `POST /api/admin/skills/{id}/scripts/run` | 手动试跑。body `{path, argv?: list[str], stdin?: str, timeout_s?: int}`。校验：技能存在、`path ∈ files && entry_type == "script"`。**不校验 review_status / allow_scripts**（审核辅助：pending 也可试跑）。经 `sandbox_client` 执行，返回 `{exit_code, stdout, stderr, truncated, timeout, duration_ms, error?}`。 |
| `GET /skills` / `PUT /skills/{id}` | 既有端点，SkillOut 增加 `allow_scripts`；PUT 支持 files。 |

不新增 `GET /skills/{id}` 单查端点：列表已全量下发，详情弹窗直接复用列表数据（YAGNI）。

## 8. Agent 运行时集成（`tools.py` / `agent_factory.py`）

### 8.1 新工具 `run_skill_script`

- 签名：`run_skill_script(skill_name: str, script_path: str, argv: list[str] = [], stdin: str = "")`；
- **挂载条件（图构建时闭包判定）**：技能 `allow_scripts == True`（`approved + enabled` 已由 `resolve_skills` 保证）；
- 工具描述静态列出该技能可执行脚本清单（path 列表），避免模型捏造路径；
- `read_skill_file` 行为不变（脚本源码同样可读——LLM 需要理解脚本才能正确传参）；
- 输出格式化：`exit_code` / `stdout` / `stderr` / `truncated` / `timeout` / `duration_ms`，截断时注入「输出已截断」提示；
- `SANDBOX_URL` 未配置 → 不挂载工具（快照含 allow_scripts 但构建工具时按环境变量短路），并在技能附件清单处标注「脚本执行未启用」。

### 8.2 请求组装（backend 侧）

入口脚本 + 该技能**全部文本附件** → `files` 数组（相对路径保持技能包内路径；script 条目按 `encoding=base64` 原样透传快照内容，text 条目 utf-8）。工作目录视图 = 技能包文本内容的运行时投影。

### 8.3 chat 流

工具调用天然产生 `tool_start` / `tool_end` SSE 事件，**chat 路由零改动**。

## 9. 沙箱服务实现（新目录 `sandbox/`）

```
sandbox/
  Dockerfile.sandbox     # 一个镜像，双角色
  pyproject.toml         # 独立 uv 项目（fastapi + uvicorn，无 DB 依赖）
  src/
    protocol.py          # 请求/响应模型 + 共享校验（路径安全、白名单、上限）
    gate.py              # FastAPI 前门：token、信号量、unix socket 转发
    runner.py            # unix socket server：铺文件、rlimit 子进程、超时、截断
  tests/                 # runner 真实子进程冒烟（python/bash/node）
```

- runner 进程内并发处理 socket 请求（asyncio）；单请求隔离靠独立子目录 + rlimit + 进程组 kill；
- gate 与 runner 的 socket 文件：`/ipc/sandbox.sock`；gate 启动时等待 socket 出现（重试 N 秒）；
- 健康检查：gate `/healthz` 探测 socket 连通；compose `healthcheck` 挂到 backend `depends_on`。

### 9.1 compose 变更

```yaml
  sandbox-gate:
    build: { context: ., dockerfile: sandbox/Dockerfile.sandbox }
    command: ["python", "-m", "sandbox.gate"]
    environment: { SANDBOX_TOKEN: ${SANDBOX_TOKEN:-dev-only-sandbox-token} }
    volumes: [ sandbox_ipc:/ipc ]
    expose: ["8100"]

  sandbox-runner:
    build: { context: ., dockerfile: sandbox/Dockerfile.sandbox }   # 与 gate 同一构建
    command: ["python", "-m", "sandbox.runner"]
    network_mode: none
    read_only: true
    cap_drop: [ALL]
    pids_limit: 64
    mem_limit: 256m
    tmpfs: [ /tmp:size=64m ]
    volumes: [ sandbox_ipc:/ipc ]

  backend:
    environment:
      SANDBOX_URL: ${SANDBOX_URL:-http://sandbox-gate:8100}
      SANDBOX_TOKEN: ${SANDBOX_TOKEN:-dev-only-sandbox-token}
    depends_on:
      sandbox-gate: { condition: service_healthy }
```

- `SANDBOX_URL` 缺省即功能关闭（向后兼容：现有部署不配置就完全不挂载脚本工具）。

## 10. 前端设计（`web/`）

### 10.1 新依赖

`react-markdown` + `remark-gfm`（详情弹窗正文渲染；本设计不改动公开对话页）。

### 10.2 组件

| 组件 | 内容 |
|---|---|
| `SkillDetailDialog`（新） | frontmatter 摘要（name/description/load_mode/enabled/allow_scripts/审核状态/来源/大小）、正文 Markdown 渲染、附件表（path、类型徽标 text/script、大小；文本附件点击抽屉预览；脚本附件带「试跑」入口） |
| `SkillEditDialog`（新） | description / load_mode / enabled / allow_scripts 表单 + content 多行文本域 + 附件管理（上传文本/脚本文件、删除既有附件；保存全量提交 files）；保存前提示「内容或附件变更将重置审核为 pending」 |
| 列表页 | 行操作加「详情」「编辑」 |
| `SkillImportDialog` | 粘贴 tab 加「从本地选择 SKILL.md」按钮（见 §5.1） |
| `ScriptRunPanel`（新，嵌详情弹窗） | 脚本选择、argv 输入、stdin 文本域、超时输入（默认 30）；结果区展示 stdout / stderr / exit_code / 耗时 / 截断与超时标记 |

- `adminApi` 增加 `runSkillScript(id, payload)`；`Skill` 类型增加 `allow_scripts`。

## 11. 环境变量清单

| 变量 | 作用域 | 默认 | 说明 |
|---|---|---|---|
| `SANDBOX_URL` | backend | 空（=功能关闭） | 沙箱 gate 地址 |
| `SANDBOX_TOKEN` | backend + sandbox-gate | `dev-only-sandbox-token` | 内部执行 token |

不新增其它环境变量；超时/上限常量集中在代码（与现有上限常量策略一致）。

## 12. 测试策略

- **后端 pytest**（无外部依赖口径不变）：
  - 导入管线：脚本入库（b64 往返）、白名单外二进制仍跳过、超限脚本跳过、存量 text 条目兼容；
  - files 编辑校验矩阵：路径穿越 / script 后缀不一致 / 超 256KB / 总量超限 / 全量替换重算计数；
  - 审核联动：files 变更重置 pending、allow_scripts 变更不重置；
  - 工具门禁：allow_scripts false / 沙箱未配置 → 不挂载或降级文案；mock sandbox_client 的成功/超时/不可用路径；
  - admin 试跑 API：鉴权、404、非脚本 path 400。
- **沙箱服务 pytest**（`sandbox/tests`）：真实子进程冒烟——`python -c` 式脚本 stdout 捕获、`sleep 5` 超时 kill、>32KB 输出截断、env 干净（断言不可见 LLM_API_KEY 类注入）、路径穿越文件拒绝、runtime 白名单。
- **e2e**（compose 全套）：导入含脚本 zip → 管理端试跑出结果 → 开 allow_scripts + approved + 绑定 Agent → 对话中触发 `run_skill_script` → `tool_end` 事件带回输出。
- **前端 vitest**：导入弹窗文件填充纯函数、编辑表单校验纯函数（抽出为 `skillFormValidation.ts`）。

## 13. 安全分析与残留风险（如实声明）

| 风险 | 缓解 | 残留 |
|---|---|---|
| 脚本探测内网 / 窃取凭据 | `network_mode: none`（内网彻底不可达）+ 干净 env（无任何凭据注入） | 无 |
| 脚本耗尽资源 | rlimit（CPU/内存/进程数/文件大小）+ pids/mem_limit + 超时 kill + 并发信号量 | 理论逃逸出 rlimit 的内核漏洞 |
| 容器逃逸 | runner 无网络、只读 rootfs、cap_drop ALL、非 root、默认 seccomp | 子进程隔离 ≠ VM 级；内核 0day 风险接受 |
| prompt injection 诱导执行 | 门禁三重（allow_scripts + approved + 绑定）；输出/输入均受限于沙箱爆炸半径 | 恶意脚本若通过审核且被开启，可消耗沙箱 CPU/内存 |
| 脚本源码被 LLM 读取后伪造路径 | 工具描述静态列真实脚本清单；未命中 path 返回清单 | 低 |
| gate 被内网横向调用 | Bearer token + 仅 compose 内网 + 不对宿主机发布端口 | token 泄漏面 = backend 容器 |

## 14. Non-goals（v1 明确不做）

- 脚本网络访问（未来 per-skill egress 白名单）
- 依赖安装（pip/npm）；脚本只能用镜像预置运行时 + 标准库
- 跨技能脚本依赖；执行历史持久化（仅日志留痕）
- 非 Linux 执行路径（runner 恒为 Linux 容器；Windows 仅作 dev 宿主）
- 公开对话页 Markdown 渲染；`name` 修改
- Skill-as-Task / 试运行对话（本次确认排除）

## 15. 里程碑与验收标准

| Phase | 内容 | 可独立合入 |
|---|---|---|
| A | 数据模型 + 迁移 + 导入管线脚本入库 + files 编辑（后端）+ SKILL.md 文件上传（前端） | ✅ |
| B | sandbox 服务（镜像/gate/runner/compose/自测） | ✅（A 不依赖 B） |
| C | backend 集成：sandbox_client + run_skill_script + 门禁 + admin 试跑 API | 依赖 A、B |
| D | 前端：详情弹窗 / 编辑弹窗 / 试跑面板 / 导入弹窗增强 | 依赖 A（试跑依赖 C） |

**真机验收清单**：

1. 导入含 `scripts/*.py` 的 zip → 详情弹窗可见脚本 → 试跑返回 stdout；
2. 编辑正文/附件 → 状态自动回到 pending，引用它的 Agent 快照刷新；
3. `allow_scripts=true + approved` 且绑定 Agent → 对话中模型调用 `run_skill_script` 成功；关闭后工具不挂载；
4. 不配置 `SANDBOX_URL` → 现有功能零回归，脚本工具不出现；
5. `alembic upgrade head` 在真实 PostgreSQL 通过（`0011_skill_scripts`，含 server_default 写法核对——沿用 `0002/0004` 的 `sa.text()` 口径备忘）。
