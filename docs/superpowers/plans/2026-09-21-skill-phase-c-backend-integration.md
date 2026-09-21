# Skill Phase C 实现计划：backend 集成（sandbox_client + Agent 工具 + 门禁 + 管理端试跑）

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** backend 打通沙箱：`sandbox_client`（HTTP 封装 + 错误三分类）、`run_skill_script` Agent 工具（门禁：allow_scripts + approved + enabled + 沙箱已配置）、管理端 `POST /skills/{id}/scripts/run` 手动试跑（任意审核状态）。

**架构：** 文件组装在 backend 完成（技能快照的 files 原样透传：script=base64 / text=utf-8，工作目录视图 = 技能包运行时投影）；工具挂载在图构建时闭包判定（快照零 DB 依赖，与 load_skill 同模式）；`SANDBOX_URL` 未配置时工具整体不挂载、管理端试跑返回明确错误。

**技术栈：** httpx（`MockTransport` 注入测试，与 `skill_import.fetch_url` 同模式）；LangChain `StructuredTool.from_function`（与 `make_skill_tools` 同模式）。

**规格：** `docs/superpowers/specs/2026-09-21-skill-preview-edit-script-execution-design.md` §3.2/§7/§8/§11/§12

**前置：** Phase A（files 含 entry_type/encoding、快照含 allow_scripts）、Phase B（gate /v1/run 协议）已落地。

## 全局约束

- 不新增环境变量语义：`SANDBOX_URL`（空 = 功能关闭）、`SANDBOX_TOKEN`；数值口径复用 `skills.py` 常量（`SCRIPT_TIMEOUT_DEFAULT_S=30` / `SCRIPT_TIMEOUT_MAX_S=120`），不重复定义
- 门禁矩阵（Agent 运行时）：`allow_scripts && review_status==approved && enabled`（后三者由 `resolve_skills` 保证，快照兜底校验）+ 沙箱已配置；管理端试跑只要求管理员身份，不校验 allow_scripts / review_status
- 工具报错绝不抛异常打断对话：全部收敛为工具输出文本（与 `load_skill` 未命中同模式）
- 测试零外部依赖：不连库、不发真网（`MockTransport`）、`get_settings` 用 `cache_clear()` + monkeypatch env
- basedpyright standard 0 error；测试/实现文案中文

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/a2a_gateway/config.py` | 修改 | Settings 增加 `sandbox_url` / `sandbox_token` |
| `src/a2a_gateway/sandbox_client.py` | 创建 | run_script 封装：payload 组装、超时、错误三分类 |
| `src/a2a_gateway/tools.py` | 修改 | `RunSkillScriptArgs` + `make_script_exec_tools`（挂载门禁 + 文件组装 + 输出格式化） |
| `src/a2a_gateway/graph.py` | 修改 | `build_graph` 挂载脚本执行工具 |
| `src/a2a_gateway/schemas.py` | 修改 | `SkillScriptRunRequest` / `SkillScriptRunOut` |
| `src/a2a_gateway/routes/registry.py` | 修改 | `POST /skills/{id}/scripts/run` 端点 |
| `tests/test_sandbox_client.py` | 创建 | client 错误分类 / 未配置 / payload 组装 |
| `tests/test_tools_script_exec.py` | 创建 | 工具挂载门禁 / 路径校验 / 输出格式化（mock client） |
| `tests/test_skills_api.py` | 修改 | 试跑端点：401/404/400/200 |

---

### 任务 1：Settings 扩展 + `sandbox_client.py`

**文件：**
- 修改：`src/a2a_gateway/config.py`（`alert_webhook_token` 之后）
- 创建：`src/a2a_gateway/sandbox_client.py`
- 测试：`tests/test_sandbox_client.py`

- [ ] **步骤 1：编写失败的测试**

`tests/test_sandbox_client.py`：

```python
"""sandbox_client 测试：MockTransport 注入，零真实网络；env + cache_clear 控制 Settings。"""

import pytest
import httpx

from a2a_gateway import sandbox_client
from a2a_gateway.config import get_settings
from a2a_gateway.sandbox_client import (
    SandboxRejected,
    SandboxTimeout,
    SandboxUnavailable,
    build_run_files,
    run_script,
)


@pytest.fixture
def sandbox_env(monkeypatch):
    monkeypatch.setenv("SANDBOX_URL", "http://sandbox-gate:8100")
    monkeypatch.setenv("SANDBOX_TOKEN", "tok-1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_disabled_without_sandbox_url(monkeypatch):
    monkeypatch.delenv("SANDBOX_URL", raising=False)
    get_settings.cache_clear()
    with pytest.raises(SandboxUnavailable):
        await run_script(files=[], entry="a.py")
    get_settings.cache_clear()


async def test_success_and_payload_shape(sandbox_env):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.read()
        return httpx.Response(200, json={"exit_code": 0, "stdout": "2\n", "stderr": "", "truncated": False, "timeout": False, "duration_ms": 5})

    result = await run_script(
        files=[{"path": "main.py", "content": "print(1+1)", "encoding": "utf-8", "entry_type": "text"}],
        entry="main.py",
        argv=["--x", "1"],
        stdin="hi",
        timeout_s=45,
        transport=_client(handler),
    )
    assert result["stdout"] == "2\n"
    assert captured["auth"] == "Bearer tok-1"
    body = captured["body"].decode()
    assert '"entry":"main.py"' in body and '"argv":["--x","1"]' in body
    assert '"timeout_s":45' in body  # clamp 之外的原样透传（合法范围内）
    assert '"stdin_b64":"' in body


async def test_error_classification(sandbox_env):
    def _status(code: int):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(code, json={"detail": "x"})
        return handler

    with pytest.raises(SandboxTimeout):
        await run_script(files=[], entry="a.py", transport=_client(504))
    with pytest.raises(SandboxRejected):
        await run_script(files=[], entry="a.py", transport=_client(401))
    with pytest.raises(SandboxRejected):
        await run_script(files=[], entry="a.py", transport=_client(500))

    def _network(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(SandboxUnavailable):
        await run_script(files=[], entry="a.py", transport=_client(_network))


def test_build_run_files_defaults():
    out = build_run_files([{"path": "a.md", "content": "文本"}, {"path": "s.py", "content": "QQ==", "encoding": "base64"}])
    assert out[0] == {"path": "a.md", "encoding": "utf-8", "content": "文本"}
    assert out[1] == {"path": "s.py", "encoding": "base64", "content": "QQ=="}
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_sandbox_client.py -q`
预期：FAIL，`ModuleNotFoundError: a2a_gateway.sandbox_client`

- [ ] **步骤 3：实现**

`config.py` 的 `Settings` 中 `alert_webhook_token` 之后追加：

```python
    # 沙箱执行器（Skill 捆绑脚本）：为空 = 功能关闭（不挂载 run_skill_script 工具）
    sandbox_url: str = Field(default="", alias="SANDBOX_URL")
    sandbox_token: str = Field(default="", alias="SANDBOX_TOKEN")
```

`src/a2a_gateway/sandbox_client.py`（完整文件）：

```python
"""沙箱执行客户端：backend → sandbox-gate 的 HTTP 封装。

错误三分类（规格 §3.2）：
- SandboxUnavailable：未配置（SANDBOX_URL 空 = 功能关闭）或 gate 不可达
- SandboxTimeout：gate 504（runner 执行超时）
- SandboxRejected：鉴权失败 / 其它非 200

payload 组装遵循沙箱协议（Phase B protocol.py）：files 为「技能包运行时视图」，
script 条目 base64 原样透传，text 条目 utf-8。
"""

import base64
from typing import Any

import httpx

from .config import get_settings
from .skills import SCRIPT_TIMEOUT_DEFAULT_S, SCRIPT_TIMEOUT_MAX_S


class SandboxError(RuntimeError):
    """沙箱执行失败基类（message 面向最终用户/模型）。"""


class SandboxUnavailable(SandboxError):
    """沙箱未配置或不可达。"""


class SandboxTimeout(SandboxError):
    """脚本执行超时。"""


class SandboxRejected(SandboxError):
    """沙箱拒绝执行（鉴权 / 协议 / 5xx）。"""


def sandbox_enabled() -> bool:
    """SANDBOX_URL 是否已配置（未配置时 Agent 工具整体不挂载）。"""
    return bool(get_settings().sandbox_url)


def build_run_files(files: list[dict[str, Any]]) -> list[dict[str, str]]:
    """技能附件 → 沙箱请求 files；encoding 缺省按 utf-8（兼容存量数据）。"""
    return [
        {
            "path": str(f.get("path") or ""),
            "encoding": str(f.get("encoding") or "utf-8"),
            "content": str(f.get("content") or ""),
        }
        for f in files
    ]


async def run_script(
    *,
    files: list[dict[str, Any]],
    entry: str,
    argv: list[str] | None = None,
    stdin: str = "",
    timeout_s: int = SCRIPT_TIMEOUT_DEFAULT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """提交一次脚本执行；@returns gate 的 RunResult dict；失败抛 SandboxError 子类。"""
    settings = get_settings()
    if not settings.sandbox_url:
        raise SandboxUnavailable("沙箱执行未启用（未配置 SANDBOX_URL）")
    timeout_s = max(1, min(int(timeout_s), SCRIPT_TIMEOUT_MAX_S))
    payload = {
        "files": build_run_files(files),
        "entry": entry,
        "argv": list(argv or []),
        "stdin_b64": base64.b64encode(stdin.encode("utf-8")).decode("ascii") if stdin else None,
        "timeout_s": timeout_s,
    }
    async with httpx.AsyncClient(timeout=timeout_s + 10, transport=transport) as client:
        try:
            resp = await client.post(
                f"{settings.sandbox_url.rstrip('/')}/v1/run",
                json=payload,
                headers={"Authorization": f"Bearer {settings.sandbox_token}"},
            )
        except httpx.HTTPError as exc:
            raise SandboxUnavailable(f"沙箱服务不可达：{type(exc).__name__}") from exc
    if resp.status_code == 504:
        raise SandboxTimeout("脚本执行超时")
    if resp.status_code == 401:
        raise SandboxRejected("沙箱鉴权失败")
    if resp.status_code != 200:
        raise SandboxRejected(f"沙箱拒绝执行（HTTP {resp.status_code}）")
    return dict(resp.json())
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_sandbox_client.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/config.py src/a2a_gateway/sandbox_client.py tests/test_sandbox_client.py
git commit -m "feat(skills): 沙箱执行客户端（错误三分类 + 未配置降级）"
```

---

### 任务 2：`run_skill_script` 工具 + 图挂载

**文件：**
- 修改：`src/a2a_gateway/tools.py`（`make_skill_tools` 之前加 args 模型，`make_skill_tools` 之后加构造函数；import 区补 `sandbox_client`）
- 修改：`src/a2a_gateway/graph.py`（`build_graph` 内 `tools.extend(make_skill_tools(...))` 之后一行）
- 测试：`tests/test_tools_script_exec.py`

- [ ] **步骤 1：编写失败的测试**

`tests/test_tools_script_exec.py`：

```python
"""run_skill_script 工具测试：挂载门禁 + 执行链路（client monkeypatch，零网络）。"""

import pytest

from a2a_gateway import sandbox_client
from a2a_gateway.config import get_settings
from a2a_gateway.sandbox_client import SandboxTimeout, SandboxUnavailable
from a2a_gateway.tools import make_script_exec_tools

SKILL = {
    "name": "gen-skill",
    "description": "d",
    "content": "c",
    "load_mode": "on_demand",
    "allow_scripts": True,
    "files": [
        {"path": "scripts/gen.py", "size": 11, "content": "cHJpbnQoMSk=", "entry_type": "script", "encoding": "base64"},
        {"path": "refs/a.md", "size": 4, "content": "文本", "entry_type": "text", "encoding": "utf-8"},
    ],
}

SKILL_NO_SCRIPTS = {"name": "plain", "description": "d", "content": "c", "load_mode": "on_demand", "allow_scripts": False, "files": []}


@pytest.fixture
def sandbox_on(monkeypatch):
    monkeypatch.setenv("SANDBOX_URL", "http://sandbox-gate:8100")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_not_mounted_without_sandbox_url(monkeypatch):
    monkeypatch.delenv("SANDBOX_URL", raising=False)
    get_settings.cache_clear()
    assert make_script_exec_tools([SKILL]) == []
    get_settings.cache_clear()


def test_not_mounted_without_allow_scripts(sandbox_on):
    assert make_script_exec_tools([SKILL_NO_SCRIPTS]) == []


def test_mounted_with_allow_scripts(sandbox_on):
    tools = make_script_exec_tools([SKILL, SKILL_NO_SCRIPTS])
    assert len(tools) == 1
    assert tools[0].name == "run_skill_script"
    assert "scripts/gen.py" in tools[0].description  # 工具描述静态列出可执行脚本


async def test_run_success_and_files_passthrough(sandbox_on, monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"exit_code": 0, "stdout": "1\n", "stderr": "", "truncated": False, "timeout": False, "duration_ms": 3}

    monkeypatch.setattr(sandbox_client, "run_script", fake_run)
    tool = make_script_exec_tools([SKILL])[0]
    out = await tool.coroutine("gen-skill", "scripts/gen.py", ["--n", "1"], "")
    assert "exit_code: 0" in out and "1\n" in out
    # 文件组装：技能包全部附件原样透传（script=b64 / text=utf-8）
    assert captured["entry"] == "scripts/gen.py"
    assert {f["path"]: f["encoding"] for f in captured["files"]} == {
        "scripts/gen.py": "base64",
        "refs/a.md": "utf-8",
    }


async def test_run_wrong_path_lists_available(sandbox_on, monkeypatch):
    async def fake_run(**kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("不应发起执行")

    monkeypatch.setattr(sandbox_client, "run_script", fake_run)
    tool = make_script_exec_tools([SKILL])[0]
    out = await tool.coroutine("gen-skill", "scripts/missing.py", [], "")
    assert "脚本不存在或不可执行" in out and "scripts/gen.py" in out


async def test_run_unknown_skill_lists_catalog(sandbox_on):
    tool = make_script_exec_tools([SKILL])[0]
    out = await tool.coroutine("no-such", "scripts/gen.py", [], "")
    assert "未找到该技能" in out and "gen-skill" in out


async def test_run_sandbox_errors_converge_to_text(sandbox_on, monkeypatch):
    async def fake_timeout(**kwargs):
        raise SandboxTimeout("脚本执行超时")

    async def fake_unavailable(**kwargs):
        raise SandboxUnavailable("沙箱服务不可达：ConnectError")

    monkeypatch.setattr(sandbox_client, "run_script", fake_timeout)
    tool = make_script_exec_tools([SKILL])[0]
    assert "超时" in await tool.coroutine("gen-skill", "scripts/gen.py", [], "")

    monkeypatch.setattr(sandbox_client, "run_script", fake_unavailable)
    assert "不可达" in await tool.coroutine("gen-skill", "scripts/gen.py", [], "")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_tools_script_exec.py -q`
预期：FAIL，`ImportError: cannot import name 'make_script_exec_tools'`

- [ ] **步骤 3：实现工具（`tools.py`）**

import 区补：

```python
from .sandbox_client import SandboxError, run_script, sandbox_enabled
```

`make_skill_tools` 之前（与 `LoadSkillArgs`/`ReadSkillFileArgs` 同区）加：

```python
class RunSkillScriptArgs(BaseModel):
    """run_skill_script 参数。"""

    skill_name: str = Field(description="技能名（取自可用技能清单）")
    script_path: str = Field(description="脚本相对路径（取自该技能附件清单中的脚本条目）")
    argv: list[str] = Field(default_factory=list, description="传给脚本的命令行参数")
    stdin: str = Field(default="", description="可选：通过标准输入传给脚本的文本")
```

（若该文件未导入 `Field`，从 `pydantic` 补——与既有 args 模型写法对齐。）

`make_skill_tools` 之后追加：

```python
def _script_catalog(skills: list[dict[str, Any]]) -> str:
    """可执行脚本清单（name → 脚本路径），供工具描述与错误提示共用。"""
    lines: list[str] = []
    for s in skills:
        scripts = [
            str(f.get("path") or "")
            for f in (s.get("files") or [])
            if str(f.get("entry_type") or "") == "script"
        ]
        if scripts:
            lines.append(f"- {s.get('name')}: " + ", ".join(scripts))
    return "\n".join(lines) or "（无可执行脚本）"


def make_script_exec_tools(skills: list[dict[str, Any]]) -> list[StructuredTool]:
    """构造 `run_skill_script` 工具（规格 §8.1）。

    挂载门禁（图构建时闭包判定，运行时零 DB 依赖）：
    - 沙箱已配置（SANDBOX_URL 非空），否则返回 []（工具整体不出现）
    - 仅 allow_scripts 技能参与；approved+enabled 由 resolve_skills 保证，快照兜底
    - 工具描述静态列出可执行脚本清单，避免模型捏造路径
    执行错误一律收敛为工具文本输出，绝不打断对话。
    """
    if not sandbox_enabled():
        return []
    runnable = [s for s in skills if s.get("allow_scripts")]
    if not runnable:
        return []
    by_name = {str(s.get("name") or ""): s for s in runnable}
    catalog = _script_catalog(runnable)

    async def _run(skill_name: str, script_path: str, argv: list[str] | None = None, stdin: str = "") -> str:
        skill = by_name.get(skill_name)
        if skill is None:
            return (
                "未找到该技能或该技能未开放脚本执行。可执行脚本的技能清单：\n"
                f"{catalog}"
            )
        entry = next(
            (
                str(f.get("path") or "")
                for f in (skill.get("files") or [])
                if str(f.get("path") or "") == script_path
                and str(f.get("entry_type") or "") == "script"
            ),
            None,
        )
        if entry is None:
            return (
                f"脚本不存在或不可执行：{script_path}\n"
                f"该技能可用脚本：\n{_script_catalog([skill])}"
            )
        try:
            result = await run_script(
                files=list(skill.get("files") or []),
                entry=entry,
                argv=list(argv or []),
                stdin=stdin,
            )
        except SandboxError as exc:
            return f"脚本执行失败：{exc}"
        parts = [
            f"exit_code: {result.get('exit_code')}",
            f"duration_ms: {result.get('duration_ms')}",
        ]
        if result.get("timeout"):
            parts.append("（执行超时，进程已被强制终止）")
        if result.get("truncated"):
            parts.append("（输出已截断）")
        if result.get("error"):
            parts.append(f"执行器错误：{result['error']}")
        parts += ["--- stdout ---", str(result.get("stdout") or "") or "（空）"]
        if result.get("stderr"):
            parts += ["--- stderr ---", str(result["stderr"])]
        return "\n".join(parts)

    def _run_sync(*args: Any) -> str:
        raise RuntimeError("run_skill_script 仅支持异步调用")

    return [
        StructuredTool.from_function(
            coroutine=_run,
            func=_run_sync,
            name="run_skill_script",
            description=(
                "在隔离沙箱中执行已绑定技能捆绑的脚本（无网络、无凭据，仅标准库可用）。"
                "skill_name 与 script_path 必须取自下述清单：\n"
                f"{catalog}\n"
                "argv 为命令行参数；stdin 为可选标准输入；输出含 exit_code 与 stdout/stderr。"
            ),
            args_schema=RunSkillScriptArgs,
        )
    ]
```

- [ ] **步骤 4：图挂载（`graph.py`）**

import 区改：

```python
from .tools import make_mcp_call_tool, make_mcp_tools, make_script_exec_tools, make_skill_tools
```

`build_graph` 中 `tools.extend(make_skill_tools(bound_skills))` 之后追加一行：

```python
    # 脚本执行工具：仅 allow_scripts 技能 + 沙箱已配置时挂载（规格 §8）
    tools.extend(make_script_exec_tools(bound_skills))
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_tools_script_exec.py tests/test_graph_skill.py tests/test_graph.py -q`
预期：PASS（既有图用例在未配置 SANDBOX_URL 时工具数量不变——未配置即不挂载，天然向后兼容）

- [ ] **步骤 6：Commit**

```bash
git add src/a2a_gateway/tools.py src/a2a_gateway/graph.py tests/test_tools_script_exec.py
git commit -m "feat(skills): run_skill_script Agent 工具与图挂载门禁"
```

---

### 任务 3：管理端试跑端点

**文件：**
- 修改：`src/a2a_gateway/schemas.py`（`SkillReviewRequest` 之后）
- 修改：`src/a2a_gateway/routes/registry.py`（`review_skill` 端点之后）
- 测试：`tests/test_skills_api.py`

- [ ] **步骤 1：编写失败的测试**

`tests/test_skills_api.py` 追加（import 区补 `from a2a_gateway import sandbox_client as sandbox_client_mod` 与 `from a2a_gateway.sandbox_client import SandboxTimeout`）：

```python
SCRIPTED_SKILL_FILES = [
    {"path": "scripts/gen.py", "size": 11, "content": "cHJpbnQoMSk=", "entry_type": "script", "encoding": "base64"},
    {"path": "refs/a.md", "size": 4, "content": "文本", "entry_type": "text", "encoding": "utf-8"},
]


async def test_script_run_requires_auth(anon_client):
    assert (await anon_client.post("/api/admin/skills/1/scripts/run", json={"path": "scripts/gen.py"})).status_code == 401


async def test_script_run_404(auth_client, monkeypatch):
    async def fake_get(session, skill_id):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    resp = await auth_client.post("/api/admin/skills/9/scripts/run", json={"path": "scripts/gen.py"})
    assert resp.status_code == 404


async def test_script_run_rejects_non_script_path(auth_client, monkeypatch):
    async def fake_get(session, skill_id):
        return _skill(files=[{"path": "refs/a.md", "size": 4, "content": "文本"}])

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    resp = await auth_client.post("/api/admin/skills/1/scripts/run", json={"path": "refs/a.md"})
    assert resp.status_code == 400


async def test_script_run_success_passthrough(auth_client, monkeypatch):
    async def fake_get(session, skill_id):
        return _skill(files=SCRIPTED_SKILL_FILES)

    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"exit_code": 0, "stdout": "1\n", "stderr": "", "truncated": False, "timeout": False, "duration_ms": 4}

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    monkeypatch.setattr(registry_mod, "run_skill_script", fake_run)
    resp = await auth_client.post(
        "/api/admin/skills/1/scripts/run",
        json={"path": "scripts/gen.py", "argv": ["--n", "1"], "stdin": "x", "timeout_s": 60},
    )
    assert resp.status_code == 200
    assert resp.json()["stdout"] == "1\n"
    assert captured["entry"] == "scripts/gen.py"
    assert captured["timeout_s"] == 60
    assert {f["path"] for f in captured["files"]} == {"scripts/gen.py", "refs/a.md"}


async def test_script_run_sandbox_error_502(auth_client, monkeypatch):
    async def fake_get(session, skill_id):
        return _skill(files=SCRIPTED_SKILL_FILES)

    async def fake_run(**kwargs):
        raise SandboxTimeout("脚本执行超时")

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    monkeypatch.setattr(registry_mod, "run_skill_script", fake_run)
    resp = await auth_client.post("/api/admin/skills/1/scripts/run", json={"path": "scripts/gen.py"})
    assert resp.status_code == 502
    assert "超时" in resp.json()["detail"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_skills_api.py -k script_run -q`
预期：FAIL，404 路由不存在

- [ ] **步骤 3：实现**

`schemas.py`（`SkillReviewRequest` 之后）：

```python
class SkillScriptRunRequest(BaseModel):
    """管理端脚本试跑请求（审核辅助；不校验 review_status / allow_scripts）。"""

    path: str = Field(description="脚本相对路径（必须是该技能的脚本附件）")
    argv: list[str] = Field(default_factory=list)
    stdin: str = ""
    timeout_s: int = Field(default=30, ge=1, le=120)


class SkillScriptRunOut(BaseModel):
    """沙箱 RunResult 透传（字段与 Phase B 协议一致）。"""

    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timeout: bool = False
    duration_ms: int = 0
    error: str | None = None
```

`registry.py`：import 区补 `from ..sandbox_client import SandboxError, run_skill_script as _sandbox_run`——注意命名冲突：路由函数也想叫 `run_skill_script`。采用导入别名 `from ..sandbox_client import SandboxError` + `from .. import sandbox_client`，端点内调 `sandbox_client.run_script(...)`（测试里 monkeypatch `registry_mod.sandbox_client.run_script` 时改为 patch `registry_mod` 可见名字——为让测试的 `monkeypatch.setattr(registry_mod, "run_skill_script", fake_run)` 生效，统一用 `from ..sandbox_client import run_skill_script` 并让端点直接调用该名字）。

最终 import 与端点：

```python
from ..sandbox_client import SandboxError, run_skill_script
```

```python
@router.post("/skills/{skill_id}/scripts/run", response_model=SkillScriptRunOut)
async def run_skill_script_endpoint(
    skill_id: int,
    data: SkillScriptRunRequest,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """管理端手动试跑（规格 §7）：pending 也允许，辅助「先跑一下再审核」。"""
    skill = await repo.get_skill(session, skill_id)
    if skill is None:
        raise HTTPException(404, "Skill 不存在")
    entry = next(
        (
            str(f.get("path") or "")
            for f in (skill.files or [])
            if str(f.get("path") or "") == data.path
            and str(f.get("entry_type") or "") == "script"
        ),
        None,
    )
    if entry is None:
        raise HTTPException(400, "脚本不存在：path 必须指向该技能的脚本附件")
    try:
        return await run_skill_script(
            files=list(skill.files or []),
            entry=entry,
            argv=data.argv,
            stdin=data.stdin,
            timeout_s=data.timeout_s,
        )
    except SandboxError as exc:
        # 沙箱不可用/超时/拒绝：管理端如实透出（区别于 Agent 工具的降级文案）
        raise HTTPException(502, str(exc))
```

（测试相应 monkeypatch `registry_mod.run_skill_script`——与上面测试代码一致；`SkillScriptRunOut`/`SkillScriptRunRequest` 加入既有 schemas import。）

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_skills_api.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/schemas.py src/a2a_gateway/routes/registry.py tests/test_skills_api.py
git commit -m "feat(skills): 管理端脚本试跑端点（审核辅助）"
```

---

### 任务 4：全量回归

- [ ] **步骤 1：后端全量 + 类型检查**

运行：`uv run pytest -q` 然后 `uv run basedpyright`
预期：全绿 + 0 error

- [ ] **步骤 2：环境变量清单核对**

`.env.example` / `docker-compose.yml` 已在 Phase B 含 `SANDBOX_TOKEN`；确认 backend `environment` 含 `SANDBOX_URL`（Phase B 已加）。无其它环境变量新增（规格 §11 口径）。

- [ ] **步骤 3：Commit（如有遗漏）**

```bash
git add -A
git commit -m "chore(skills): Phase C 收尾"
```

---

## 自检记录

- 规格覆盖：§3.2 错误三分类 → 任务 1；§8.1 工具签名/挂载门禁/描述清单/错误收敛 → 任务 2；§8.2 文件组装（script=b64/text=utf-8 原样透传）→ 任务 2 测试断言；§8.3 chat 流零改动 → 任务 2 无 chat 改动（既有 SSE 事件天然覆盖）；§7 试跑端点（任意审核状态 + 404/400/502）→ 任务 3；§11 环境变量 → 任务 4 核对
- 类型一致性：`run_script` 关键字参数（files/entry/argv/stdin/timeout_s，任务 1 定义）↔ 任务 2/3 消费；`SandboxError` 子类（任务 1）↔ 任务 2/3 捕获；`RunResult` 字段 ↔ `SkillScriptRunOut`（任务 3）↔ Phase D 前端类型
- 命名冲突已显式处理：`registry.py` 内 `from ..sandbox_client import run_skill_script` 与端点函数名 `run_skill_script_endpoint` 错开
- 无占位符；全部测试不连库、不发真网
