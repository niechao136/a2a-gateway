---
type: integration-guide
title: MCP 集成
description: 说明 MCP 服务端注册表到工具的映射：三种传输（stdio/sse/streamable_http）会话打开、工具探测与调用、绑定工具与回退 mcp_call、提示格式化与超时。
tags: [mcp, stdio, sse, streamable-http, tool-binding, probe]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-472b0deb13ce18764b8bb6bc
    resource: repo://src/a2a_gateway/agent_factory.py
  - id: openwiki-source-cf26b275f836c4fa92ad8b8d
    resource: repo://src/a2a_gateway/auth_scheme.py
  - id: openwiki-source-4febd71d669a7c84b1d2f5c0
    resource: repo://src/a2a_gateway/graph.py
  - id: openwiki-source-452ba3d6ee67065515a01609
    resource: repo://src/a2a_gateway/mcp_client.py
  - id: openwiki-source-c02a6d45a645df8106612f51
    resource: repo://src/a2a_gateway/models.py
  - id: openwiki-source-d3e47f45c8a3dad144965b78
    resource: repo://src/a2a_gateway/repository.py
  - id: openwiki-source-4df82bf1a4678a53b6438ca1
    resource: repo://src/a2a_gateway/tools.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# MCP 集成

`src/a2a_gateway/mcp_client.py` 封装 MCP 客户端（连通性测试 / 工具列表 / 工具调用）；`tools.py` 把探测到的工具绑定为 LangChain `StructuredTool`；`agent_factory` / `graph` 在构图时做目录探测并按快照挂载。设计核心：**按需一次性会话**——每次调用「连接 → initialize → 执行 → 关闭」，不在 Agent 图生命周期内维护长连接，无需保活与断线重建（代价是每次多一次握手）（`mcp_client.py:3-13`）。所有入口带超时并收敛异常：**MCP 失败不影响主对话流程**。

## 三种传输与会话打开

`_open_session`（`mcp_client.py:73-106`）按 `McpConnection.transport` 分派，鉴权落到不同位置：

| 传输 | 实现 | 鉴权落点 |
|---|---|---|
| `stdio` | `stdio_client(StdioServerParameters(command, args, env))` → `ClientSession` | **注入子进程环境变量** `build_stdio_env`（`MCP_AUTH_TOKEN/TYPE/NAME`）——HTTP 头无法穿越 stdio（`auth_scheme.py:60-73`、`mcp_client.py:85-90`） |
| `sse` | `sse_client(url, headers=...)` | `headers` 参数直传 |
| `streamable_http`（默认） | `streamable_http_client(url, http_client=...)`——该传输不直接接受 headers | 自建 `httpx.AsyncClient(headers=..., timeout=60.0)` 预置鉴权头，`finally` 中 `aclose`（`mcp_client.py:95-106`） |

公共前置：`build_headers` + `apply_query_auth`（与 A2A 出站共享同一套 bearer/header/query/basic/none 方案，见 [认证与安全面](/openwiki/concepts/security.md)）。mcp 2.x 内置 httpx2 分支，`http_client` 参数按可用模块导入（`mcp_client.py:21-25`）；streamable_http 返回形态兼容 mcp 2.x 的 2 元组与早期 3 元组（按下标取 `read, write`，`mcp_client.py:100-102`）。

`connection_from_snapshot`（`mcp_client.py:58-70`）从 Agent 上的 MCP 快照（`repository.mcp_server_snapshot`）构造 `McpConnection`，快照含 `name/transport/url/command/args/env/token/auth_type/auth_name`——**连接参数随快照走，运行时零 DB 查询**（见 [数据模型](/openwiki/architecture/data-model.md)）。

## 超时与错误文本化降级

两个常量（`mcp_client.py:36-39`）：

- **`PROBE_TIMEOUT = 8.0s`**：连通性测试与 `list_tools`（握手类）——短超时避免拖慢图实例构建；
- **`CALL_TIMEOUT = 60.0s`**：单次 `call_tool`——工具本身可能较慢。

超时经 `asyncio.timeout` 包裹；`asyncio.CancelledError` 必须透传（不吞取消）。错误一律**文本化降级**而非抛出：

- `_format_error`（`mcp_client.py:109-120`）：anyio 会把真实异常包进 `ExceptionGroup`，直接 `str()` 只能看到 `"unhandled errors in a TaskGroup"`——必须递归展开 `error.exceptions` 树形渲染，才能定位真实原因；
- `test_connection` 失败 → `(False, "连接失败：...")`；`list_tools` 失败 → `(False, [], "获取工具列表失败：...")`；`call_tool` 失败 → 返回 `"MCP 调用失败（server·tool）：..."` **作为工具结果文本**（模型可读、对话不中断）；
- `CancelledError` 一律 re-raise（`mcp_client.py:155-156` 等三处）。

## 构图时的目录探测与绑定快照的关系

两层数据，职责不同：

1. **连接快照 `agent.mcp_servers`**（写时解析，`models.py:76-78`、`repository.py:142`）：由 `mcp_server_ids` 在保存/刷新时解析成 `[{name, transport, url, ...}]` JSONB，**运行时构造工具的连接参数**——改注册表后经 `refresh_agents_for_mcp_servers` 刷新引用方快照并失效图缓存（`repository.py:427-436`）。删除时可 `detach_mcp_server_from_agents` 解绑（`repository.py:458-465`）。
2. **工具目录 `mcp_tool_index`**（构图时探测，`agent_factory.py:49-89`）：`_probe_mcp_tools` 对每个快照**并发**调 `list_tools`（8s 内置超时，总耗时约等于单个超时），产出 `{server_name: [{name, description, inputSchema}]}`，注入 `build_graph`——**只影响工具说明与绑定，不写库**；探测失败该项为空。

绑定决策在 `build_tools`（`graph.py:287-297`）：

- 有 `bound_mcp`（任一服务探测成功）→ 每个工具经 `make_mcp_tools(server, index[name])` 绑定；
- 快照有服务但**全部探测失败**（服务不可达）→ 退化为**单个通用 `mcp_call`**，至少保留可用能力（`graph.py:295-297`、`tools.py:351-387`）。

## 绑定工具与回退 mcp_call

### 绑定：`make_mcp_tools`（`tools.py:311-340`）

- 工具名 `mcp_<服务名>__<工具名>`（截 64 字符，规整后重名加序号），避免与 A2A 工具及其它服务冲突；
- `json_schema_to_model`（`tools.py:238-270`）把 `inputSchema` 转 pydantic 模型：`properties`→字段、`required`→必填、其余 `| None` 默认 `None`；**无法解析（无 properties / 非 object）时退化为单个 `arguments` 字段**，保证工具仍可调用（入参按 JSON 对象传入），`_unwrap_arguments` 识别退化模式取 `arguments` 本身、正常模式丢弃未填的 `None`；
- `_build_mcp_tool`（`tools.py:281-308`）**必须经工厂函数固化 `tool_name`/`args_model`**：循环内直接定义闭包会因 Python 晚绑定让所有工具指向最后一个 `tool_name`（调用 A 实际执行 B）——代码注释明确此坑（`tools.py:289-293`）；
- 描述 = 原描述 + `（来自 MCP 服务 <name>）`。

### 回退：`make_mcp_call_tool`（`tools.py:351-387`）

通用 `mcp_call(server, tool, arguments)`，**仅在无法探测到任何工具清单时挂载**。`_description` 动态拼接：可用服务清单 + `format_tools_for_prompt` 渲染每个服务的工具目录（`mcp_client.py:210-218`：`- server：` + `· tool —— desc` 行；空列表提示「暂无可用工具（服务可能未启动或工具列表获取失败）」）。`server` 未命中时返回可用列表文本而非抛错。

## 调用链

```
构图: agent.mcp_servers 快照 ─┬─ _probe_mcp_tools(8s) → mcp_tool_index
                              └─ build_tools → make_mcp_tools 绑定 / 或 mcp_call 回退
运行: StructuredTool._acall → mcp_invoke(connection_from_snapshot(server), tool, args)
      → _open_session(按传输建一次性会话) → initialize → call_tool(60s) → _render_content
```

`_render_content`（`mcp_client.py:123-140`）：拼接 content 的 `text` 项（非文本项 `model_dump_json`），空结果回「（MCP 工具未返回内容）」，`isError` 结果前缀 `MCP 工具返回错误：`。

## 管理端

`routes/registry.py`：MCP 服务 CRUD（`GET/POST/PUT/DELETE /mcp-servers`）、`POST /mcp-servers/{id}/test`（调 `test_connection(connection_from_snapshot(...))`，`registry.py:260-270`）、`GET /mcp-servers/{id}/tools`（列工具，`registry.py:274`）。传输/URL/command 校验见 `validate_mcp_transport`（`schemas.py:87`）。Agent 绑定方式（勾选 `mcp_server_ids` + 手工 `mcp_servers` 合并 `_merge_bindings`）见 [绑定模型](/openwiki/concepts/agents-and-bindings.md)。

## 不变量

- 握手类 8s、调用类 60s；`CancelledError` 永不吞。
- 任何 MCP 失败都文本化降级：测试返回 `(False, 说明)`、调用返回错误字符串——绝不让 MCP 异常打断主对话或图构建。
- 连接参数只读自快照（运行时零 DB）；工具目录构图时探测（不落库、不影响快照）。
- 快照有服务但零工具 → 挂 `mcp_call` 兜底；有绑定工具 → 不挂 `mcp_call`。
- stdio 认证走环境变量，HTTP 传输走头或 query，与 A2A 出站同一 auth_scheme。

## 代表性测试

`tests/test_mcp_client.py`：三传输会话打开、PROBE/CALL 超时、`_format_error` 展开 ExceptionGroup、`test_connection`/`list_tools`/`call_tool` 失败文本化、`format_tools_for_prompt`；`tests/test_tools_mcp.py`（或并入）：schema→model 转换、退化 arguments 模式、工厂闭包晚绑定、`mcp_call` 回退条件；`tests/test_agent_factory.py`：探测注入与图缓存失效。

相关页：[Agent 图构建与编排](/openwiki/workflows/agent-graph.md)、[数据模型与迁移](/openwiki/architecture/data-model.md)、[Agent 配置与绑定模型](/openwiki/concepts/agents-and-bindings.md)、[A2A 上游客户端](/openwiki/integrations/a2a-client.md)。
