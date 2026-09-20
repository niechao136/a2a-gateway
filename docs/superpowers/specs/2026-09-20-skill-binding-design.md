# Skill 绑定设计（Agent Skills 风格）

- 日期：2026-09-20
- 状态：已实现
- 范围：`a2a-gateway` 后端（数据模型 / 导入 / 工具层 / graph）+ 管理中心前端（`web/`）
- 关联：与 `2026-09-18-a2a-resume-tool-design.md` 的注入机制同源（`pre_model_hook` 动态注入 + 状态记账）
- 定位：Skill 是继「A2A 目标」「MCP 服务」之后的**第三类绑定**，但作用机制完全不同——它不提供可调用的连接，而是注入上下文、影响**网关 Agent 的编排行为**

## 1. 背景与动机

网关已支持两类外部能力绑定：

| 绑定 | 作用机制 | 运行时产物 |
| --- | --- | --- |
| A2A 目标 | 调远端专家 Agent | `a2a_call` / `a2a_resume` 工具 |
| MCP 服务 | 调远端工具 | 每个 MCP 工具各一个 LangChain 工具 |

两者都是「模型主动调用」的能力。但**编排层面的方法知识**（该先问什么、怎么拆任务、输出什么格式、关键数据如何处理）目前只能堆在 `system_prompt` 里：不可复用、无法跨 Agent 共享、越堆越长、改一处要动整个提示词。

引入 Agent Skills（`SKILL.md` + YAML frontmatter + 可选附件）作为第三类绑定：把「方法论」做成可复用、可审核、可渐进式加载的资源。三者的分工：

> **Skill = 编排剧本，MCP = 手脚，A2A = 专家**

## 2. 目标与非目标

### 2.1 目标

- 新增「Skill 管理」注册表（DB 存储），支持 4 种导入来源：粘贴 SKILL.md、URL、zip、浏览器目录上传。
- 新增**审核标记**（pending / approved / rejected）与绑定门禁：只有 `approved` 的 skill 才能被 Agent 勾选。
- Agent 勾选绑定后，skill 以**混合模式**进入上下文：
  - `always`：正文常驻 system prompt；
  - `on_demand`：清单（name + description）常驻，正文由模型调用 `load_skill` 按需加载。
- 已加载的 skill 在长对话中**不因历史滚动压缩而失效**（状态记账 + 超窗口重注入）。
- 附件（`references/` / `assets/` 等文本文件）可通过 `read_skill_file` 按需读取。

### 2.2 非目标（明确排除）

- **不执行脚本**：skill 内的 `scripts/` 一律不做代码执行。需要执行能力的 skill，正文里写「调用哪个 MCP 工具 / 哪个 `a2a_call__*`」。
- **不修改链路的 A2A / MCP 行为**：`tools.py` 现有 `make_a2a_tools` / `make_mcp_tools` 逻辑不变。
- **不做下游透传**：skill 只影响网关 Agent 的编排行为，不写入 A2A `message.metadata` 转发给下游。
- **不做 skill 市场 / 版本 diff / 覆盖率统计**（二期按需）。
- **不做前端目录的"服务端路径读取"**：目录导入一律走浏览器上传，后端零文件系统读取。

### 2.3 关键决策记录

| 决策点 | 结论 | 理由 |
| --- | --- | --- |
| Skill 语义 | Agent Skills（`SKILL.md` + frontmatter） | 与项目内 `.codebuddy/skills/` 生态一致 |
| 生效位置 | 网关 Agent 的编排行为 | 不改下游，避免与 Hermes 自有 skill 语义混淆 |
| 存储 | PostgreSQL（快照全量存正文） | 与 `a2a_targets` / `mcp_servers` 快照模式同构，运行时零 DB 依赖 |
| 注入模式 | 混合（`load_mode`：always / on_demand） | 短规则常驻、长方法论按需，兼顾遵循度与 token |
| `load_mode` 归属 | `Skill` 表（全局），Agent 侧仅存 `skill_ids: list[int]` | 保持与 A2A/MCP 的绑定结构同构 |
| 目录导入 | 前端 `webkitdirectory` 上传 | 后端不读文件系统，零路径穿越风险 |
| 脚本执行 | v1 不做 | 不为网关新增 RCE 面 |
| 审核门禁 | **只有 `approved` 才能被勾选绑定** | 比"只拦发布"更严格，杜绝未审内容进入任何 Agent |
| 多 skill 包 | v1 支持批量（扫 `**/SKILL.md` 供勾选） | 导入体验，成本可控 |
| 二进制附件 | 跳过并在预览标注，不判定导入失败 | `read_skill_file` 仅服务文本附件，二进制无运行时用途；宽容导入减少重传 |
| 停用语义（enabled） | 宽松：保留勾选、静默跳过、启用即恢复 | 与 A2A/MCP 注册表同构；严格门禁会导致停用一个 skill 打挂所有绑定 Agent 的保存 |

## 3. 数据模型

### 3.1 `Skill` 表（`models.py`）

```python
class SkillReviewStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Skill(Base, BaseMixin):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")   # 进 prompt 清单，决定加载率
    content: Mapped[str] = mapped_column(Text, default="")       # SKILL.md 正文（已剥离 frontmatter）
    frontmatter: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # 附件：[{"path": "references/a.md", "size": 123, "content": "..."}]，仅文本
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    load_mode: Mapped[str] = mapped_column(String(16), default="on_demand")  # always | on_demand
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="manual")   # manual|text|url|zip|dir
    source_ref: Mapped[str] = mapped_column(String(512), default="")
    review_status: Mapped[SkillReviewStatus] = mapped_column(
        Enum(
            SkillReviewStatus,
            name="skillreviewstatus",
            # 与 AgentStatus 同坑：必须按「成员值」建 PG 枚举
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        default=SkillReviewStatus.PENDING,
        server_default=SkillReviewStatus.PENDING.value,
    )
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
```

### 3.2 `AgentConfig` 新增字段

```python
    skill_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)          # 勾选绑定
    skills: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)  # 运行时会用到的快照
```

### 3.3 快照结构（`repository.skill_snapshot`）

```python
{
    "id": 1,
    "name": "travel-planning",
    "description": "...",
    "content": "...",                       # 正文全文
    "load_mode": "on_demand",
    "files": [{"path": "references/x.md", "size": 12, "content": "..."}],
    "review_status": "approved",            # 运行时兜底过滤用
}
```

**为什么全量存正文**：现有两条链路都靠「快照即全部」让 `agent_factory` / `graph.py` 保持零 DB 依赖（见 `agent_factory.py:78-80` 注释）。skill 正文 2~20KB，JSONB + TOAST 可承载；`load_skill` 与 hook 重注入直接从闭包取，无需新增 store 抽象与每轮 DB 查询。

**关于 `review_status` 字段**：`resolve_skills` 已静默过滤，正常刷新后的快照恒为 `approved`。保留该字段属纵深防御——若未来出现绕过 resolve 的路径或刷新失败，运行时可据此多过滤一道（跳过 + 告警，见 §7）。

**代价与约束**：`agent_configs` 行变大，因此设绑定总量上限（见 §5）。

## 4. 导入管线

```
来源解析 → (SKILL.md 文本, 附件清单)
  → parse_skill_md()：frontmatter 解析 + 规范化 + 校验
  → 预览（dry-run，不落库；多 skill 包返回清单供勾选）
  → 用户确认 → 落库（review_status = pending）
```

### 4.1 四种来源

| 来源 | 入口 | 说明 |
| --- | --- | --- |
| 粘贴 | `skill_md` | 直接提交 SKILL.md 文本 |
| URL | `url` | 只支持 **raw 单文件** 或 **zip**；不做仓库目录浏览 |
| zip | `zip` | 上传 `*.zip`；扫 `**/SKILL.md`，支持批量 |
| 目录 | `dir` | 浏览器 `webkitdirectory` 选目录 → 上传文件数组；后端按相对路径分组，每组一个 `SKILL.md` |

### 4.2 安全要求（本次唯一新增攻击面）

| 风险 | 措施 |
| --- | --- |
| SSRF（URL 指向内网 / 云元数据） | 仅 `http(s)`；DNS 解析后逐 IP 校验，拒绝 private / loopback / link-local / reserved / multicast / unspecified；超时 10s；重定向最多 3 跳且每跳重新校验；响应体上限 4MB |
| zip slip | 拒绝绝对路径与含 `..` 的条目 |
| zip 符号链接 / 非常规条目 | 仅接受 `file_type` 为普通文件或目录的条目 |
| 压缩炸弹 | 解压后单文件 ≤ 1MB、总量 ≤ 4MB、文件数 ≤ 100 |
| 目录上传路径 | 前端相对路径统一 `posixpath.normpath` 规范化，拒绝 `..`；**后端不触碰文件系统** |
| 内容超限 | 正文 ≤ 32KB，description ≤ 1024 字符 |

### 4.3 校验与冲突

- `name` 规则：`^[a-zA-Z0-9](?:[a-zA-Z0-9._-]*[a-zA-Z0-9])?$`，长度 ≤ 128。**只允许 ASCII**——name 会写进 prompt 清单并作为工具入参值传递，kebab-case 最稳（中文名走 `description`）。
- `description` 必须非空：空 description 等于「绑了但模型不会想到用」。
- 重名：`overwrite=true` 覆盖 / `overwrite=false` 跳过（默认跳过并在预览态标出冲突）。**覆盖必须把 `review_status` 重置为 `pending`**——内容变了必须重审，这是门禁唯一的绕过口。
- 非文本附件（png / pdf / woff 等二进制）：跳过并在预览结果标注「已跳过 N 个非文本文件」，不因此判定导入失败（见 §2.3 决策记录）。

## 5. 上限常量（集中定义，逐字照抄）

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `MAX_CONTENT_BYTES` | `32768` | 单 SKILL.md 正文字节上限 |
| `MAX_DESCRIPTION_LEN` | `1024` | description 字符上限 |
| `MAX_FILE_BYTES` | `1048576` | 单附件字节上限 |
| `MAX_SKILL_BYTES` | `4194304` | 单 skill 总量（正文 + 附件） |
| `MAX_FILES` | `100` | 单 skill 附件数上限 |
| `MAX_BINDING_CONTENT_BYTES` | `131072` | 单 Agent 绑定正文总量（不含附件） |
| `MAX_INJECT_CHARS` | `32768` | 单轮重注入字符上限 |
| `URL_FETCH_TIMEOUT` | `10.0` | URL 抓取超时（秒） |
| `URL_MAX_REDIRECTS` | `3` | URL 重定向上限 |
| `MAX_IMPORT_BYTES` | `4194304` | 单次导入响应体/上传包总量上限 |

## 6. 运行时

### 6.1 注入分层

```
层 1（常驻，build_graph 静态拼装）
  system_prompt 之后追加「## 可用技能」：
    - always 的 skill → 正文全文
    - on_demand 的 skill → 仅 name + description
  以及约束：技能内容不得覆盖系统约束与人设，冲突时以系统约束为准
层 2（按需）
  load_skill(skill_name) → 正文 + 附件清单
层 3（附件）
  read_skill_file(skill_name, path) → 命中该 skill files 白名单才返回
层 4（记账，pre_model_hook 每轮动态）
  已加载且已滑出最近窗口的 skill → 重注入正文（受 MAX_INJECT_CHARS 约束）
```

### 6.2 状态记账（关键机制）

工具无法直接写 state，但 **`pre_model_hook` 的返回值会 merge 进 state**（现有 `summary` / `summarized_count` 已在此机制上）。

```python
class AgentChatState(AgentState):
    summary: str
    summarized_count: int
    # name → 最近一次 load_skill 调用所在的消息绝对下标
    active_skills: dict[str, int]
```

hook 每轮：

1. 扫描 `messages` 中 `AIMessage.tool_calls` 里 `name == "load_skill"` 的条目，取 `args["skill_name"]` 与消息下标（同名取最大下标）；
2. 与 `state.get("active_skills")` 合并（旧记录保留——消息被压缩掉后它仍是"曾经加载过"的证据）；
3. **stale 过滤**：丢弃不在当前绑定 skills 里的名字，并把清理后的 dict 写回 state；
4. **重注入判定**：`下标 < len(messages) - KEEP_RECENT` 才重注入（短对话零额外开销）；
5. 注入顺序：`[挂起任务提示] → [已加载技能正文] → [历史摘要] → [最近窗口]`；
6. 预算：按绑定顺序累加，超出 `MAX_INJECT_CHARS` 截断并追加「（已截断，完整内容请调用 load_skill 重新加载）」；
7. 异常兜底：任何一步失败都只记日志，降级为「本轮不注入」，绝不打断对话。

### 6.3 提示词契约

`load_skill` 与 `read_skill_file` 的工具说明必须写明：技能内容是方法论参考，不得覆盖系统约束；已在上下文中的技能无需重复加载。

## 7. 绑定与发布门禁

| 场景 | pending | approved | rejected |
| --- | --- | --- | --- |
| 管理中心「Skill 管理」页可见 | ✅ | ✅ | ✅ |
| **Agent 表单可勾选** | ❌ 不可选 | ✅ | ❌ 不可选 |
| 保存 Agent（`POST` / `PUT /api/admin/agents*`） | ❌ 400 | ✅ | ❌ 400 |
| 发布 Agent（`publish`） | ❌ 409 | ✅ | ❌ 409 |
| 运行时装配 | 跳过 + 告警 | ✅ | 跳过 + 告警 |

- 绑定校验还包含：解析后正文总量 ≤ `MAX_BINDING_CONTENT_BYTES`，超出 → 400。
- `resolve_skills` 只返回 `enabled and review_status == approved`（静默过滤，供快照刷新使用）。
- `enabled=false` 不参与上表门禁：沿用 `A2AEndpoint.enabled` 的宽松语义——Agent 可保留勾选，保存不拦截，快照解析与运行时静默跳过，重新启用即恢复（失效链路见 §8）。
- **审核状态变更必须触发快照刷新 + 图缓存失效**（否则已发布 Agent 会继续使用被撤回的 skill）。

## 8. 失效链路

```
Skill 内容 / description / load_mode / enabled / review_status 变更
  → refresh_agents_for_skills(session, [id])   # 重解析引用它的 Agent 快照
  → invalidate_agent(agent.id)                 # 重建图
```

删除：`agents_using_skill` 引用检查 → 409（`_used_by_message` 文案）→ `?force=true` 自动解绑后删除。

`agent_factory._cache` 结构不变（skill 不引入需要 `close()` 的资源）。

## 9. 错误处理与边界

| 场景 | 行为 |
| --- | --- |
| `load_skill` 传入未绑定的 name | 返回可用技能清单，不抛异常 |
| `load_skill` 传入 stale/已撤回的 name | 返回「该技能已不可用」 |
| `read_skill_file` 路径不在白名单 | 返回「文件不存在」，不暴露实际路径 |
| 同 thread 内 Agent 更换绑定 | 旧 `active_skills` 被 stale 过滤逐出 |
| hook 记账抛异常 | 记日志、跳过注入，本轮对话照常 |
| 正文超 `MAX_INJECT_CHARS` | 截断 + 明确告知可用 `load_skill` 取全文 |
| 导入 URL 不可达 / 非 2xx | 预览返回该条错误，其余条目照常 |
| 导入名冲突 | 默认跳过并在预览态标记；`overwrite=true` 时覆盖并重置为 pending |
| 导入含二进制附件 | 跳过该文件并在预览结果标注，其余条目照常 |
| Agent 绑定的 skill 被停用（enabled=false） | 保存不拦、快照解析静默跳过，重新启用即恢复 |

## 10. 测试策略

全部离线（沿用 `tests/conftest.py` 风格：不连库、不触发 lifespan、不发真实网络请求）。

| 模块 | 用例 |
| --- | --- |
| `test_skills_parse.py` | 正常解析；无 frontmatter；YAML 非法；name 非法（中文 / 空 / 超长）；description 缺失；正文超限；额外 frontmatter 字段保留 |
| `test_skill_import.py` | zip slip 拒绝；符号链接拒绝；超限拒绝；二进制附件跳过并在预览标注；多 skill 包分组；目录数组分组与 `..` 拒绝；SSRF：私网 IP / 环回 / 元数据地址 / 重定向超限 / 响应超限 |
| `test_skills_api.py` | CRUD、重名 409、导入预览→落库、覆盖重置为 pending、审核接口、删除引用 409 / force 解绑 |
| `test_skills_binding.py` | 绑定门禁（pending → 400）、绑定总量超限 → 400、快照解析只含 approved、enabled=false 静默跳过且保留勾选、refresh + invalidate |
| `test_tools_skill.py` | `load_skill` 命中 / 未命中 / stale；`read_skill_file` 白名单拒绝 |
| `test_graph_skill.py` | 清单拼装顺序；always 常驻 / on_demand 不常驻；hook 记账；超窗口重注入；stale 过滤；预算截断；图级回归（注入真的到达模型输入） |
| 前端 `skillUtils.test.ts` | token/字符预算估算、字节格式化 |

## 11. 验收标准

1. 从 URL 导入一个真实 skill（如 GitHub raw 的 `SKILL.md`）→ 预览显示 name/description/大小 → 落库为 `pending` → Agent 表单中不可勾选 → 审核通过后可勾选。
2. 上传一个含多个 skill 的 zip → 预览列出全部 → 勾选其中两个落库。
3. 浏览器选择本地目录导入 → 附件被正确归类到该 skill 下。
4. 绑定 a `always` + b `on_demand` → 对话中 a 的正文直接生效；问一个需要 b 的问题，模型调用 `load_skill` 后按 b 的流程作答。
5. 长对话（超过 `KEEP_RECENT` 条）后，b 的流程约束仍然生效（重注入命中）。
6. 把 b 的审核撤回为 rejected → 已发布 Agent 对话时 b 被跳过并且告警日志出现。
7. `uv run pytest` 全绿 + `basedpyright` 0 error + `cd web && npm test` 全绿。

## 12. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 恶意 skill 正文构成 prompt injection（诱导把数据发往外部 A2A/MCP） | 审核门禁（人工过目）+ 来源留痕 + 长度上限；注入段落显式声明「不得覆盖系统约束」 |
| `description` 写得差 → 模型永不加载该 skill | 预览态醒目展示 description；空 description 直接拒绝导入 |
| 重注入导致 token 成本上升 | 只在滑出窗口后重注入 + `MAX_INJECT_CHARS` 截断 + 前端展示常驻技能预算预估 |
| `AgentChatState` 新增字段与历史 checkpoint 不兼容 | 沿用 `summary` / `summarized_count` 的「无默认值 + `.get(...) or {}` 读取」写法；上线前用现有会话回归 |
| SSRF 绕过（DNS 重绑定） | 解析后按 IP 校验 + 每跳重定向重新校验 + 超时与体积限制（残留风险已在规格说明，属可接受） |
| 快照全量存正文导致 `agent_configs` 行膨胀 | 绑定正文总量上限 `MAX_BINDING_CONTENT_BYTES`，超限 400 |

## 13. 实施顺序与文件清单

1. `src/a2a_gateway/skills.py`：上限常量 + `parse_skill_md`（纯函数，先 TDD）；
2. `src/a2a_gateway/skill_import.py`：四种来源 + 安全校验；
3. `models.py` / `alembic/versions/0010_skills.py` / `schemas.py`：表与数据契约；
4. `repository.py`：Skill CRUD + 快照解析 + 引用/刷新/解绑；
5. `routes/registry.py`：skills CRUD + 导入 preview/commit + 审核；
6. `routes/admin.py` + `repository.py`：绑定门禁 + 发布门禁；
7. `tools.py` + `graph.py`：skill 工具 + prompt 装配；
8. `graph.py`：hook 记账 + 重注入 + 预算；
9. `web/`：API 客户端 + Skill 管理页 + 导入弹窗 + AgentForm 技能分区（含常驻技能预算预估，token 估算走 `skillUtils`，见 §10/§12）；
10. 更新 `TODO.md`、`.env.example` 与本文件状态。
