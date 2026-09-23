---
type: concept-guide
title: 技能（Skills）系统
description: 说明 SKILL.md 格式与限额常量、四种导入来源的安全校验、审核状态机、绑定门控、四层提示注入与沙箱脚本执行开关。
tags: [skills, skill-md, prompt-injection, review, import, sandbox-scripts]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-4febd71d669a7c84b1d2f5c0
    resource: repo://src/a2a_gateway/graph.py
  - id: openwiki-source-c02a6d45a645df8106612f51
    resource: repo://src/a2a_gateway/models.py
  - id: openwiki-source-c728168ff3e83d7ca738448b
    resource: repo://src/a2a_gateway/routes/registry.py
  - id: openwiki-source-c906c556d0d86b9ccfe8ed8b
    resource: repo://src/a2a_gateway/schemas.py
  - id: openwiki-source-1157ad22d08962e1ffa75962
    resource: repo://src/a2a_gateway/skills.py
  - id: openwiki-source-4df82bf1a4678a53b6438ca1
    resource: repo://src/a2a_gateway/tools.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 技能（Skills）系统

## 职责与归属

- **解析与限额**：`src/a2a_gateway/skills.py`（纯函数、无 IO，全部上限常量集中于此）。
- **导入安全**：`src/a2a_gateway/skill_import.py`（zip/目录/URL）。
- **注册表与审核路由**：`routes/registry.py`（CRUD、import preview/commit、review、scripts/run）。
- **绑定门禁与快照**：`repository.validate_skill_bindings` / `resolve_skills` / `skill_snapshot`（见 [绑定模型](/openwiki/concepts/agents-and-bindings.md)）。
- **注入与工具**：`graph.build_skills_prompt` + `pre_model_hook` 记账重注入；`tools.make_skill_tools` / `make_script_exec_tools`。
- **模型**：`Skill` 表含正文/附件全量 JSONB、审核状态、`allow_scripts`（`models.py:153-189`）。

## SKILL.md 格式与限额

格式：YAML frontmatter（`name`/`description` 必填）+ 正文；`name` 仅 ASCII（会进 prompt 清单并作 `load_skill` 入参），中文名放 `description`（`skills.py:1-5`）。解析 `parse_skill_md` 要求 `---` 开头且闭合、frontmatter 为映射，`yaml.safe_load`（`skills.py:75-105`）。

常量（`skills.py:13-28`）：

| 常量 | 值 | 作用 |
|---|---|---|
| `MAX_CONTENT_BYTES` | 32KB | 单 SKILL.md 正文 |
| `MAX_DESCRIPTION_LEN` | 1024 | description 字符数（模型据其决定是否加载） |
| `MAX_FILE_BYTES` / `MAX_SKILL_BYTES` / `MAX_FILES` | 1MB / 4MB / 100 | 单附件 / 单 skill 总量 / 附件数 |
| `MAX_BINDING_CONTENT_BYTES` | 128KB | 单 Agent 绑定正文总量（不含附件）门禁 |
| `MAX_INJECT_CHARS` | 32KB | 单轮 hook 重注入字符预算 |
| `URL_FETCH_TIMEOUT` / `URL_MAX_REDIRECTS` / `MAX_IMPORT_BYTES` | 10s / 3 / 4MB | URL 导入与上传入口 |
| `SCRIPT_SUFFIXES` / `MAX_SCRIPT_BYTES` / 超时 | `.py/.sh/.js` / 256KB / 30–120s | 脚本白名单与沙箱口径 |

`name` 模式 `^[a-zA-Z0-9][a-zA-Z0-9._-]*…$`、长度 ≤128（`skills.py:30`、`51-57`）。

## 四种导入来源与安全

导入来源：`manual|text|url|zip|dir`（`models.py:173`）。安全要求见 [认证与安全面](/openwiki/concepts/security.md)：zip slip/符号链接拒绝、解压炸弹多级上限、路径规范化、SSRF 逐 IP 校验、4MB 响应体、后端零文件系统访问（`skill_import.py:7-12`）。粘贴（text）由路由直接 `parse_skill_md`。

### preview / commit 工作流

1. `POST /api/admin/skills/import/preview`（`registry.py:509`）：解析并返回逐条预览（含错误与重名冲突标记），**不写库**。
2. `POST /api/admin/skills/import/commit`（`registry.py:520-582`）：**重新解析**（无服务端预览态，URL 只抓一次的同批解析保证幂等）→ 按 `names` 与 `overwrite` 决定 create/update/skip：
   - 重名且不覆盖 → `skipped`（不写库，且**不**刷新快照/不失效图缓存，避免白白打掉引用方已编译的图，`registry.py:562-565`）；
   - 覆盖会重置审核为 `pending` → 对 touched 技能 `refresh_agents_for_skills` + 对引用 Agent `invalidate_agent`（`registry.py:567-575`）。
3. 返回 `{created, updated, skipped, failed, items}` 对账。

## 审核状态机

`SkillReviewStatus`：`pending → approved | rejected`（可反复变更；`models.py:147-150`）。新建/导入默认 `pending`。

- `POST /skills/{id}/review`（`registry.py:585-602`）：变更状态 + note；**必须**触发快照刷新 + 图缓存失效，否则已发布 Agent 继续用旧技能（`registry.py:592` 注释）。
- 只有 `approved` 可被 Agent 勾选/保存/发布（绑定门禁）；已绑定技能被撤回为 `rejected` 后，`resolve_skills` 过滤 + 快照刷新使其在对话中静默跳过（`TODO.md:13`）。
- `enabled=false` 不参与门禁：保留勾选、运行时跳过、启用即恢复（宽松语义）。

## 快照与绑定

`skill_snapshot` 全量含 `content`/`files`/`allow_scripts`/`review_status`（纵深防御兜底字段）（`repository.py:475-489`）；「快照即全部」使注入与工具闭包运行时零 DB 依赖（`models.py:156-157`）。绑定门禁见 [agents-and-bindings](/openwiki/concepts/agents-and-bindings.md)。删除有引用时默认拒绝，`?force=true` 解绑后删（`registry.py:466-478`）。

## 四层提示注入

1. **层 1 — 静态清单**（构图时）：`build_skills_prompt` 追加在 `system_prompt` 之后（`graph.py:98-120`）：`always` 技能**正文全文常驻**；`on_demand` 只进「name：description」清单并提示可 `load_skill`。开头声明技能不得覆盖系统约束、冲突以系统约束为准。
2. **层 2 — 工具按需加载**（`tools.py:420-505`）：`load_skill` 对 `always` 只回「已常驻」说明 + 附件清单（不重复回正文）；对 `on_demand` 回正文 + 附件清单；未命中返回可用清单而非抛错（且回显不回显请求名，防幻觉锚点，`tools.py:442-446`）。`read_skill_file` 白名单路径命中才读，避免整包附件塞进上下文。
3. **层 3 — 附件读取契约**：`make_skill_tools` 传入**全部**绑定技能（不按 load_mode 过滤），否则 `always` 技能附件不可达（`tools.py:423-425`）。
4. **层 4 — hook 记账重注入**（`graph.py:154-188`）：`AgentChatState.active_skills` 记录每个技能最近一次 `load_skill` 的消息下标；每轮合并新调用 → **stale 过滤**（不在当前绑定中的名字逐出，解绑/撤回不会随历史「复活」）→ 对滑出 `KEEP_RECENT=20` 窗口的技能按绑定顺序重注入正文，预算 `MAX_INJECT_CHARS=32768`，超预算截断并提示重新 `load_skill`。注入顺序：挂起提示 → 技能正文 → 摘要 → 最近窗口（`graph.py:231-234`）。记账失败仅记日志、本轮不注入，绝不打断对话（`graph.py:223-226`）。

## 工具挂载（构图时）

`build_graph`（`graph.py:318-327`）：

- `make_skill_tools(all bound skills)` → `load_skill` + `read_skill_file`；
- `make_script_exec_tools(skills)` → 仅 `allow_scripts` 的技能参与，且需沙箱已配置（`tools.py:522-533`、`graph.py:325-326`）；
- prompt = `system_prompt 或默认 + build_skills_prompt(...)`。

## 脚本执行（沙箱开关）

- 列 `allow_scripts`：审核时决定，变更**不重置**审核（`models.py:175-176`、`repository.py:611-612`）。
- `run_skill_script` 工具经 `sandbox_client.run_script` → gate → runner；`SANDBOX_URL` 为空则工具根本不挂载。
- 管理端试跑：`POST /skills/{id}/scripts/run`（`registry.py:606+`）——辅助审核，**不校验** `review_status`/`allow_scripts`（`schemas.py:210` 注释）。
- 白名单后缀 `.py/.sh/.js`、256KB、输出截断 32KB、超时 30–120s（`skills.py:24-28`）；隔离细节见 [沙箱执行](/openwiki/operations/sandbox.md)。

## 生命周期总览

```
导入(text/url/zip/dir) ─► preview ─► commit(upsert, 默认 pending)
        ─► review(approved/rejected) ─► Agent 勾选(门禁) ─► 写时快照
        ─► 构图注入(层1) + 工具(层2/3) + hook 重注入(层4)
        ─► 变更/撤回 ─► refresh_agents_for_skills + invalidate_agent
```

## 不变量

- 上限常量唯一来源是 `skills.py`，导入/门禁/注入共用，不新增环境变量。
- 仅 `approved+enabled` 进快照；门禁与解析双闸。
- 快照含正文全文；构图与轮内注入零 DB。
- 覆盖导入必重置审核并刷新引用方；无覆盖跳过不碰缓存。

## 代表性测试

- `tests/test_skills_parse.py`、`test_skills_schemas.py`：解析与字段校验。
- `tests/test_skill_import.py`：zip/URL 安全（离线）。
- `tests/test_skills_api.py`：CRUD/导入/审核/试跑 API。
- `tests/test_skills_binding.py`：门禁、快照刷新、缓存失效。
- `tests/test_graph_skill.py`：注入、记账、stale 过滤、预算截断。
- `tests/test_tools_skill.py`、`test_tools_script_exec.py`：工具契约与脚本执行。

相关页：[Agent 图构建与编排](/openwiki/workflows/agent-graph.md)、[Agent 配置与绑定模型](/openwiki/concepts/agents-and-bindings.md)、[沙箱执行](/openwiki/operations/sandbox.md)、[数据模型与迁移](/openwiki/architecture/data-model.md)。
