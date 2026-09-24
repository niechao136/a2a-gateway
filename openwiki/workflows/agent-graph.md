---
type: workflow-guide
title: Agent 图构建与编排
description: 解释共享 ReAct 图的构建：resolve_llm 按绑定快照或全局配置选择 LLM，pre_model_hook 的历史压缩、pending 提示与技能再注入，工具组装（A2A/MCP/技能），以及 LLM 适配层的 thought_signature 兼容处理。
tags: [langgraph, react-agent, pre-model-hook, checkpointer, cache, thought-signature]
sources:
  - id: openwiki-source-472b0deb13ce18764b8bb6bc
    resource: repo://src/a2a_gateway/agent_factory.py
  - id: openwiki-source-4febd71d669a7c84b1d2f5c0
    resource: repo://src/a2a_gateway/graph.py
  - id: openwiki-source-f53adcab3542f0a7184408f6
    resource: repo://src/a2a_gateway/llm.py
  - id: openwiki-source-1157ad22d08962e1ffa75962
    resource: repo://src/a2a_gateway/skills.py
  - id: openwiki-source-9f96f06bbd9cdd32677cab48
    resource: repo://tests/test_graph.py
  - id: openwiki-source-fa957d241a6fb4842d7a22c5
    resource: repo://tests/test_llm_build.py
generated: { by: "opencode", at: "2026-09-24T01:27:47.842Z" }
verified:
  - by: openwiki/0.5.2
    at: 2026-09-24T01:27:47.842Z
---

# Agent 图构建与编排

所有 Agent **共用同一套 ReAct 图结构**（`create_react_agent`），差异全部通过 `AgentConfig` 注入：A2A 目标、MCP 服务、`system_prompt`、技能快照、模型快照（`graph.py:1-8`、`318-319`）。实例由 `agent_factory` 按缓存键构建与失效。

## checkpointer 单例

`agent_factory.get_checkpointer`（`agent_factory.py:37-46`）：

- 模块级单例 `_checkpointer` / `_checkpointer_cm`，**惰性初始化**：`AsyncPostgresSaver.from_conn_string(sync_db_url)` → `__aenter__` → `await setup()` 建表 → 缓存返回；并发首调依赖已初始化后的读（单进程 asyncio 下首次 await 前无竞态写入完成路径需注意：`from_conn_string` 后才赋值上下文）；
- 所有 Agent 图共享**同一个** checkpointer 实例——线程隔离靠每对话 `thread_id`，不靠实例隔离；
- 关停时 `close_all` 调 `_checkpointer_cm.__aexit__` 并清空（`agent_factory.py:119-128`）。

Checkpointer 落 PostgreSQL，`astream_events`/`ainvoke` 的 `configurable.thread_id` 即会话线程；**压缩只改 `llm_input_messages`，checkpoint 里的完整历史保留，time travel 不受影响**（`graph.py:335-337`）。

## 实例缓存：(agent_id, updated_at)

`_cache: dict[tuple[int, str], tuple[list[A2AClientWrapper], Any]]`（`agent_factory.py:27-29`）：

- **键 = `(agent.id, updated_at.isoformat())`**——配置变更后 `updated_at` 改变，新键自动 miss，旧键成 stale；
- **值 = (A2A wrapper 列表, graph)**——一个 Agent 可绑多个 A2A 目标，缓存 wrapper 是为了 `a2a_resume`/恢复分支复用连接参数并在关闭时统一 `close()`；
- `get_agent_instance` 未命中时：`make_a2a_tools(targets, agent_id)` → `_probe_mcp_tools` → `build_graph(...)` → 写入新键后**清理同 id 旧 updated_at 条目**并关闭其 wrappers（`agent_factory.py:70-99`）；
- `get_agent_wrappers`：命中直接返回，未命中先触发构图（`agent_factory.py:102-108`）；
- `invalidate_agent(agent_id)`：管理中心修改配置后**显式**弹出该 agent 全部键并关 wrappers（`agent_factory.py:111-116`）——技能审核、MCP 删除刷新、绑定变更都调用它；
- `close_all`：应用 lifespan 关停时清缓存 + 关 checkpointer（`main.py:57`）。

缓存与快照的关系：构图参数（A2A targets、MCP 快照、技能快照、**模型快照 `model_snapshot`**）全部来自 ORM 对象上的 JSONB 字段（repository 写时解析），**构图与运行时零 DB 查询**；DB 只在取 `AgentConfig` 行本身与 checkpointer 通道发生。

## MCP 工具探测（构图时）

`_probe_mcp_tools`（`agent_factory.py:49-67`）：

- 对每个 MCP 快照 `asyncio.gather` **并发** `list_tools`（8s 内置超时，总耗时约单个超时）；
- **尽力而为**：单项失败只让该 name 不进 index（`ok and name` 过滤），`except Exception` 兜底返回 `(name, False, [])`——**探测绝不能影响图实例构建**；
- 产出 `mcp_tool_index` 注入 `build_graph`；详见 [MCP 集成](/openwiki/integrations/mcp.md)。

## LLM 选择：resolve_llm

`build_graph` 先经 `llm = resolve_llm(agent.model_snapshot)`（`graph.py:318-319`）选择模型：

- **有绑定快照** → `build_llm_from_snapshot`（`llm.py:154-197`）按 provider 分支：
  - `openai` → `ThoughtSignatureChatOpenAI`（装 thought_signature 补丁 + `streaming=True`，`base_url` 缺省回落全局 `LLM_*`）；
  - `anthropic` → `ChatAnthropic`（`base_url` 仅在快照提供时传入）；
  - `temperature`/`max_tokens` **非空才覆盖**；
  - **失败语义（中文 `ValueError`，不静默回落）**：未知 provider → `不支持的模型供应商：{p}`；`api_key` 为空 → `模型未配置 API Key（请在「模型管理」中补充后重试）`（在进底层客户端前拦截，避免英文 `OpenAIError`）。
- **无快照**（`model_id` 为 NULL）→ `build_llm()` 读 `Settings.llm_*` 全局回落（零配置兜底）。

测试：`tests/test_llm_build.py` 覆盖两分支、参数覆盖、中文错误与 `resolve_llm` 回落/非法快照不静默回落。

## 工具组装

`build_graph` 内（`graph.py:318-337`）：

1. `build_tools(a2a_tools, mcp_servers, mcp_tool_index)`（`graph.py:273-299`）：A2A 每目标一个工具 + MCP 每工具一个绑定；**有绑定工具则不挂 `mcp_call`，全失败才退化 `mcp_call`**（`graph.py:293-297`）；
2. `tools.extend(make_skill_tools(bound_skills))`——**全部**已绑定技能（不按 load_mode 过滤），否则 always 技能附件不可读（`graph.py:322-325`）；
3. `tools.extend(make_script_exec_tools(bound_skills))`——仅 `allow_scripts` + `SANDBOX_URL` 已配（`graph.py:326-327`）；
4. `prompt = (system_prompt or DEFAULT_SYSTEM_PROMPT) + build_skills_prompt(skills)`——层 1 静态技能清单（`graph.py:328`）；
5. `create_react_agent(llm, tools, prompt, checkpointer, state_schema=AgentChatState, pre_model_hook=...)`（`graph.py:329-337`）。

`DEFAULT_SYSTEM_PROMPT` 定义委托/直答/追问转述/`a2a_resume` 的行为基线（`graph.py:45-57`）。A2A 工具命名与描述见 [A2A 客户端](/openwiki/integrations/a2a-client.md)；技能四层注入见 [技能系统](/openwiki/concepts/skills.md)。

## AgentChatState

必须**继承 `AgentState` 而非 `MessagesState`**——prebuilt 要求 `state_schema` 含 `remaining_steps`，否则构图 `ValueError: Missing required key(s) {'remaining_steps'}`（`graph.py:68-74` 注释）。新增三字段（均无默认值，历史 checkpoint 无这些键，读取处 `state.get(...) or ...` 兜底）：

| 字段 | 作用 |
|---|---|
| `summary: str` | 早期对话滚动摘要（模型不可见时仍保留在状态） |
| `summarized_count: int` | 已被摘要覆盖的前缀消息条数 |
| `active_skills: dict[str, int]` | `skill_name → 最近一次 load_skill 所在消息绝对下标`（hook 记账） |

## pre_model_hook 三职责

`_make_history_hook` 返回的 `history_compression_hook(state, config)`（`graph.py:138-270`）在每轮进模型前执行，只产出 `llm_input_messages`（+ 技能记账状态），**不改 checkpoint 历史**。

### 调优常量

| 常量 | 值 | 语义 |
|---|---|---|
| **`KEEP_RECENT = 20`** | 20 条 | 模型可见的最近消息窗口；更早的滚动进摘要（`graph.py:62-63`） |
| **`SUMMARIZE_BATCH = 12`** | 12 条 | 窗口外**累计新增未压缩**消息达到该阈值才触发一次摘要（`graph.py:64-65`、`242-244`） |
| **`MAX_INJECT_CHARS = 32768`** | 32K 字符 | 单轮 hook **技能重注入**总预算（来自 `skills.py:26`，非摘要预算） |

窗口截断 `_safe_recent`：取最近 `keep` 条后**剥掉开头的 `ToolMessage`**——OpenAI 要求 `ToolMessage` 前必须有带 `tool_calls` 的 AI 消息，否则 400（`graph.py:86-95`）。

### ① 历史压缩（摘要）

- `overflow = len(messages) - KEEP_RECENT`，`pending_count = overflow - summarized_count`；
- `len(messages) <= KEEP_RECENT` 或 `pending_count < SUMMARIZE_BATCH` → **不压缩**，直接用现有 summary（`graph.py:244-245`）；
- 否则只摘要**增量段** `messages[covered:overflow]`（避免每次全量重摘），prompt 要求合并进已有摘要、保留关键事实/偏好/决定/未完成任务、输出中文正文（`graph.py:247-254`）；
- **压缩失败回退语义**（`graph.py:255-261`）：`llm.ainvoke` 异常 → `logger.warning(..., exc_info=True)` + **本轮跳过压缩**，`llm_input_messages = build_llm_input(旧 summary)`——对话绝不因摘要失败中断，也不推进 `summarized_count`（下轮重试同一增量）；
- 成功 → 写 `summary` / `summarized_count = overflow`。

### ② pending 提示

`_pending_notice(config)`（`graph.py:190-211`）：取 `configurable.thread_id` → `pending_store.get`；命中则产一条 `SystemMessage`（目标名/URL、追问原文、指示调用 `a2a_resume` 而非编造）。**查询失败 → `logger.warning` + 本轮跳过注入**（降级为模型不知道有挂起）；仅影响 `llm_input_messages`，不写历史。

### ③ 技能记账再注入（层 4）

`_skill_reinjection`（`graph.py:154-188`）：

1. 合并 `state.active_skills` 与本轮扫描的 `load_skill` 调用（`_collect_load_skill_calls`：扫 `AIMessage.tool_calls`，同名取**最大**消息下标）；
2. **stale 过滤**：不在当前绑定 `bound_names` 的名字逐出（解绑/撤回不会随历史「复活」）；
3. `cutoff = len(messages) - KEEP_RECENT`，下标 `< cutoff` 的为滑出窗口的 stale → 需重注入；
4. **按绑定顺序**累加预算 `MAX_INJECT_CHARS`：每条 body = 提示语 + 技能正文；超预算截断并附「（已截断，完整内容请调用 load_skill 重新加载）」；预算耗尽即停；
5. 产出单条 `SystemMessage`（`---` 连接）。整段包在 try 中：**记账失败 → `logger.warning` + 本轮不注入，绝不打断对话**（`graph.py:220-226`）。

### 注入顺序

`build_llm_input`（`graph.py:231-240`）固定为：

```
挂起任务提示 (notice) → 已加载技能正文 (skill_msgs) → 历史摘要 (summary SystemMessage) → 最近窗口 (recent)
```

三层组合各自失败互不影响：技能失败跳技能、摘要失败跳摘要、pending 失败跳 pending。

## LLM 适配层：thought_signature 兼容

背景（`llm.py:1-18`）：Gemini 3 + OpenAI 兼容端点时，函数调用带非标准字段 `tool_calls[i].extra_content.google.thought_signature`，**下一轮必须原样回传**否则 400 `Function call is missing a thought_signature...`。langchain-openai 在两处丢字段：流式入站 `_convert_delta_to_message_chunk` 只取 name/args/id/index；出站 `_convert_message_to_dict` 重建 tool_calls 只留 id/type/function。

受控适配层（`llm.py`）：

| 方向 | 手段 |
|---|---|
| 入站流式 | 包装模块级 `_convert_delta_to_message_chunk`（`_patched_delta_to_message_chunk`），把 `extra_content` 记入 `additional_kwargs["__google_extra_content__"]`（按 tool_call id 索引）；`install_thought_signature_patch()` **幂等**（`_PATCH_FLAG`） |
| 入站非流式 | 子类 `ThoughtSignatureChatOpenAI._create_chat_result` 从原始响应提取 |
| 出站 | 子类 `_get_request_payload` 按 tool_call id 把 signatures 回填 `tool_calls[].extra_content` |

全部捕获/合并/回填包 try——失败 `logger.debug` 静默，**对非 Gemini 端点完全透明**（无该字段时空操作）；安装失败 `logger.exception` 后仍返回 `False` 但 `build_llm` 继续（`llm.py:88-99`、`142-150`）。`build_llm()`：`ChatOpenAI(model, api_key=SecretStr, base_url, streaming=True)` + 先装补丁；openai 分支的 `build_llm_from_snapshot` 同样装补丁（`llm.py:172-184`），anthropic 分支用 `ChatAnthropic` 不装补丁（`llm.py:186-194`）。测试：`tests/test_llm_thought_signature.py`、`tests/test_llm_build.py`。

文件头对该模块放宽 pyright 私有成员/Any 检查（`llm.py:20-24`）——因需访问 langchain-openai 私有实现。

## 构图数据流总览

```
DB AgentConfig 行 ──(取行)──► ORM 对象（a2a_targets/mcp_servers/skills/model_snapshot JSONB 快照）
     │
     ▼ get_agent_instance
缓存 miss ─► make_a2a_tools ─► _probe_mcp_tools(8s 并发) ─► get_checkpointer(单例)
     ─► build_graph: resolve_llm(agent.model_snapshot) + build_tools + make_skill_tools
                     + make_script_exec_tools + prompt(system+skills) + pre_model_hook
     ─► _cache[(id, updated_at)] = (wrappers, graph)；清同 id 旧键
```

`resolve_llm` 分支：有快照 → `build_llm_from_snapshot`（openai/anthropic，缺 key/未知 provider 抛中文 `ValueError`）；无快照 → `build_llm()` 读全局 `LLM_*`（`llm.py:200-204`）。

失效路径：`updated_at` 变化（新键）或 `invalidate_agent(id)`（显式弹出）；两者都关旧 wrappers 防泄漏。

## 不变量

- 图结构全局唯一，差异只在配置注入；`state_schema` 必须继承 `AgentState`。
- LLM 经 `resolve_llm(agent.model_snapshot)` 选择：绑定快照走 openai/anthropic 分支，缺 key/未知 provider 中文 `ValueError` 不静默回落；无快照回落全局 `LLM_*`。
- checkpointer 进程级单例；实例缓存键含 `updated_at`，失效必关 wrappers。
- hook 只产出 `llm_input_messages` + `active_skills`，不改 checkpoint 历史。
- 压缩失败/记账失败/pending 查询失败三者独立降级，任何一路失败都不打断本轮对话。
- `KEEP_RECENT=20`、`SUMMARIZE_BATCH=12`、`MAX_INJECT_CHARS=32768` 为唯一调优来源。
- thought_signature 补丁幂等安装、捕获失败静默、无字段时零行为变化。

## 代表性测试

- `tests/test_graph.py`：pending 注入进模型输入但不进 state 历史、store 异常降级、注入与摘要叠加顺序、`pre_model_hook (state, config)` 签名兼容；**`resolve_llm` 换 `_graph_llm` 替身**避免真实网络（`test_graph.py:74-76`）。
- `tests/test_graph_skill.py`：清单顺序、always 常驻、记账、超窗重注入、stale 过滤、预算截断、图级回归（注入到达模型输入）。
- `tests/test_agent_factory.py`：`test_graph_cache_reuse_and_invalidation`（缓存复用/updated_at 失效/显式 invalidate）。
- `tests/test_llm_build.py`：openai/anthropic 分支、参数覆盖、中文错误、`resolve_llm` 回落与非法快照不静默回落。
- `tests/test_llm_thought_signature.py`：补丁幂等、入站捕获、出站回填。

相关页：[技能系统](/openwiki/concepts/skills.md)、[Agent 配置与绑定模型](/openwiki/concepts/agents-and-bindings.md)、[LLM 模型管理](/openwiki/concepts/llm-model-management.md)、[MCP 集成](/openwiki/integrations/mcp.md)、[聊天生命周期](/openwiki/workflows/chat-lifecycle.md)。
