# Skill Phase A 实现计划：数据模型 + 脚本导入 + 附件编辑 + SKILL.md 上传

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** Skill 注册表支持脚本附件入库（zip/目录导入）、附件全量编辑（PUT /skills/{id} 扩展 files + allow_scripts）、前端粘贴 tab 支持选择本地 SKILL.md 文件。为 Phase B/C（沙箱执行）备好数据面。

**架构：** 附件条目演进为 `{path, size, content, entry_type, encoding}`（JSONB 内演进，读取处兜底兼容存量）；脚本按后缀白名单（.py/.sh/.js）+ 256KB 上限判定，base64 入库；编辑走全量替换语义；`allow_scripts` 布尔列由审核人决定，变更不重置审核。

**技术栈：** FastAPI + SQLAlchemy 2 + Alembic（原生 SQL 幂等迁移）；React 19 + MUI 9 + vitest。

**规格：** `docs/superpowers/specs/2026-09-21-skill-preview-edit-script-execution-design.md` §4/§5/§6/§10（本计划的论证依据，执行者两份都读）

## 全局约束

- 上限常量全部集中在 `src/a2a_gateway/skills.py`，不新增任何环境变量
- Alembic 迁移用原生 SQL 幂等写法（`ADD COLUMN IF NOT EXISTS`，参照 `0010_skills.py`）
- 测试零外部依赖：不连库（monkeypatch 替身）、不发网、不触发 lifespan（沿用 `tests/conftest.py` 的 `auth_client` / `anon_client`）
- `SkillParseError` / `SkillImportError` 的 message 面向最终用户，用中文
- 审核语义：description / content / files 任一变更 → `review_status` 重置 pending 且清空 `reviewed_at`；`allow_scripts` / `load_mode` / `enabled` 变更不重置
- `size_bytes` 口径保持「正文 + description 的 UTF-8 字节」，附件只进 `file_count`（与 `parse_skill_md` / `create_skill` 现状一致）
- 前端 MUI 9 + Next 16 App Router；新增 UI 文案用中文；前端纯函数放 `web/src/lib/skillUtils.ts` 并配 vitest
- 项目质量基线：`uv run pytest -q` 全绿 + `npm test` 全绿 + `npm run build` 成功 + basedpyright standard 0 error

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/a2a_gateway/skills.py` | 修改 | 新增脚本常量与 `is_script_path` 纯函数 |
| `src/a2a_gateway/skill_import.py` | 修改 | `_split_entries` 三分类（文本/脚本/跳过）、`_attach_scripts` 归组、`normalize_file_entries` + `files_differ`（编辑校验复用路径安全） |
| `src/a2a_gateway/models.py` | 修改 | `Skill.allow_scripts` 布尔列 |
| `alembic/versions/0011_skill_scripts.py` | 创建 | 幂等加列迁移 |
| `src/a2a_gateway/schemas.py` | 修改 | `SkillFileIn`、`SkillUpdate.files/allow_scripts`、`SkillOut.allow_scripts`、`SkillImportPreviewItem.scripts` |
| `src/a2a_gateway/repository.py` | 修改 | `update_skill` 支持附件替换与 pending 重置；`skill_snapshot` 透传 `allow_scripts` |
| `src/a2a_gateway/routes/registry.py` | 修改 | `update_skill` 路由调附件校验；`_preview_item` 暴露脚本清单 |
| `web/src/lib/adminApi.ts` | 修改 | `Skill` / `SkillUpdatePayload` / `SkillFilePayload` / preview 类型扩展 |
| `web/src/lib/skillUtils.ts` | 修改 | `isSkillMdFileName` 纯函数 |
| `web/src/components/admin/SkillImportDialog.tsx` | 修改 | 粘贴 tab 加「从本地选择 SKILL.md」按钮 |
| `tests/test_skills_parse.py` | 修改 | 常量与 `is_script_path` 用例 |
| `tests/test_skills_schemas.py` | 修改 | 模型列存在性用例 |
| `tests/test_skill_import.py` | 修改 | 脚本入库 / 超限跳过 / 大小写 / entry_type 用例 |
| `tests/test_skills_api.py` | 修改 | 编辑 files / allow_scripts / preview scripts 用例（含 `_skill` 助手加字段） |
| `tests/test_skills_binding.py` | 修改 | 快照透传 `allow_scripts` 用例 |
| `web/src/lib/skillUtils.test.ts` | 创建 | `isSkillMdFileName` 用例 |

---

### 任务 1：脚本常量与纯函数（`skills.py`）

**文件：**
- 修改：`src/a2a_gateway/skills.py`（常量区，`MAX_IMPORT_BYTES` 之后）
- 测试：`tests/test_skills_parse.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_skills_parse.py` 追加（import 区加 `from a2a_gateway.skills import SCRIPT_SUFFIXES, is_script_path`）：

```python
def test_is_script_path():
    assert is_script_path("scripts/gen.py")
    assert is_script_path("run.SH")  # 大小写不敏感
    assert is_script_path("a/b/tool.JS")
    assert not is_script_path("references/a.md")
    assert not is_script_path("noext")
    assert not is_script_path("")


def test_script_constants():
    assert SCRIPT_SUFFIXES == {".py", ".sh", ".js"}
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_skills_parse.py -q`
预期：FAIL，`ImportError: cannot import name 'is_script_path'`

- [ ] **步骤 3：编写最少实现**

`skills.py` 常量区（`MAX_IMPORT_BYTES` 行后）追加：

```python
SCRIPT_SUFFIXES = {".py", ".sh", ".js"}  # 脚本白名单后缀（沙箱运行时：python3/bash/node）
MAX_SCRIPT_BYTES = 262144                # 单脚本原始字节上限（256KB）
MAX_SCRIPT_OUTPUT_BYTES = 32768          # 脚本 stdout/stderr 截断（沙箱服务同口径）
SCRIPT_TIMEOUT_DEFAULT_S = 30            # 沙箱执行默认超时
SCRIPT_TIMEOUT_MAX_S = 120               # 沙箱执行超时上限
```

模块尾部（`validate_skill_fields` 之后）追加：

```python
def is_script_path(path: str) -> bool:
    """按后缀判定脚本附件（大小写不敏感；无后缀 / 空路径恒为 False）。"""
    dot = path.rfind(".")
    if dot < 0:
        return False
    return path[dot:].lower() in SCRIPT_SUFFIXES
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_skills_parse.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/skills.py tests/test_skills_parse.py
git commit -m "feat(skills): 脚本附件常量与 is_script_path 纯函数"
```

---

### 任务 2：`allow_scripts` 模型列 + 迁移 0011

**文件：**
- 修改：`src/a2a_gateway/models.py`（`Skill` 类，`enabled` 列之前）
- 创建：`alembic/versions/0011_skill_scripts.py`
- 测试：`tests/test_skills_schemas.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_skills_schemas.py` 追加（import 区加 `from a2a_gateway.models import Skill`）：

```python
def test_skill_model_has_allow_scripts_column():
    col = Skill.__table__.c["allow_scripts"]
    assert col.default.arg is False          # python 端默认
    assert col.server_default is not None    # DB 端默认（迁移侧 FALSE）
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_skills_schemas.py -q`
预期：FAIL，`KeyError: 'allow_scripts'`

- [ ] **步骤 3：实现模型列**

`models.py` 的 `Skill` 类中、`enabled` 列定义之前插入（`Boolean` 已在现有 import 中，无需新增 import）：

```python
    # 是否允许在沙箱中执行捆绑脚本（Phase B 沙箱上线后生效；审核时决定，变更不重置审核）
    allow_scripts: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
```

若 `models.py` 顶部尚未 `import sqlalchemy as sa`，则补 `import sqlalchemy as sa`。

- [ ] **步骤 4：创建迁移**

`alembic/versions/0011_skill_scripts.py`（完整文件，参照 `0010_skills.py` 的原生 SQL 幂等风格）：

```python
"""skills.allow_scripts: 是否允许沙箱执行捆绑脚本

Revision ID: 0011_skill_scripts
Revises: 0010_skills
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_skill_scripts"
down_revision: str | None = "0010_skills"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS "
            "allow_scripts BOOLEAN NOT NULL DEFAULT FALSE;"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE skills DROP COLUMN IF EXISTS allow_scripts;"))
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_skills_schemas.py -q`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add src/a2a_gateway/models.py alembic/versions/0011_skill_scripts.py tests/test_skills_schemas.py
git commit -m "feat(skills): allow_scripts 列与 0011 迁移"
```

---

### 任务 3：导入管线脚本入库（`skill_import.py`）

**文件：**
- 修改：`src/a2a_gateway/skill_import.py`
- 测试：`tests/test_skill_import.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_skill_import.py` 追加（沿用文件内既有 zip 构造助手；若现有助手只收 `str` 值，则新增二进制版助手）：

```python
def _zip_bytes_b64(entries: dict[str, bytes]) -> str:
    """二进制版 zip 助手：值为 bytes，直接写入。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, blob in entries.items():
            zf.writestr(path, blob)
    return base64.b64encode(buf.getvalue()).decode()


SKILL_MD_DEMO = "---\nname: demo-skill\ndescription: 演示\n---\n正文"


def test_parse_zip_ingests_scripts():
    groups = parse_zip_base64(
        _zip_bytes_b64(
            {
                "demo-skill/SKILL.md": SKILL_MD_DEMO.encode(),
                "demo-skill/scripts/gen.py": b"print('hi')\n",
                "demo-skill/references/a.md": "文本".encode(),
            }
        )
    )
    assert len(groups) == 1
    entries = {f["path"]: f for f in groups[0]["files"]}
    script = entries["scripts/gen.py"]
    assert script["entry_type"] == "script"
    assert script["encoding"] == "base64"
    assert base64.b64decode(script["content"]) == b"print('hi')\n"
    assert script["size"] == len(b"print('hi')\n")
    assert entries["references/a.md"]["entry_type"] == "text"
    assert entries["references/a.md"]["encoding"] == "utf-8"
    assert groups[0]["skipped_binary"] == []


def test_parse_zip_skips_oversized_script(monkeypatch):
    import a2a_gateway.skill_import as mod

    monkeypatch.setattr(mod, "MAX_SCRIPT_BYTES", 4)
    groups = parse_zip_base64(
        _zip_bytes_b64(
            {
                "demo-skill/SKILL.md": SKILL_MD_DEMO.encode(),
                "demo-skill/scripts/big.py": b"print(123456)\n",
            }
        )
    )
    # 超限脚本不降级为文本附件，直接标注跳过
    assert groups[0]["files"] == []
    assert groups[0]["skipped_binary"] == ["demo-skill/scripts/big.py"]


def test_parse_zip_non_script_binary_still_skipped():
    groups = parse_zip_base64(
        _zip_bytes_b64(
            {
                "demo-skill/SKILL.md": SKILL_MD_DEMO.encode(),
                "demo-skill/assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00",
            }
        )
    )
    assert groups[0]["files"] == []
    assert groups[0]["skipped_binary"] == ["demo-skill/assets/logo.png"]


def test_parse_zip_script_suffix_case_insensitive():
    groups = parse_zip_base64(
        _zip_bytes_b64(
            {
                "demo-skill/SKILL.md": SKILL_MD_DEMO.encode(),
                "demo-skill/run.PY": b"print(1)\n",
            }
        )
    )
    assert groups[0]["files"][0]["entry_type"] == "script"


def test_parse_dir_files_ingests_scripts():
    groups = parse_dir_files(
        [
            {"path": "demo-skill/SKILL.md", "content": SKILL_MD_DEMO},
            {"path": "demo-skill/scripts/gen.py", "content": "print('hi')\n"},
        ]
    )
    assert groups[0]["files"][0]["entry_type"] == "script"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_skill_import.py -q`
预期：FAIL（`entry_type` / `skipped_binary` 断言不满足）

- [ ] **步骤 3：实现**

`skill_import.py` 的 import 区补：

```python
import binascii

from .skills import SCRIPT_SUFFIXES, is_script_path
```

把 `_split_binary` 整体替换为三分类版本：

```python
def _split_entries(
    entries: list[tuple[str, bytes]],
) -> tuple[list[tuple[str, str]], list[tuple[str, bytes]], list[str]]:
    """拆分为 (文本条目, 脚本条目, 被跳过的文件名)。

    脚本判定先于文本判定（后缀命中即脚本，超限直接跳过、不降级为文本附件）；
    其余二进制仍跳过并标注（规格 §5）。
    """
    text_entries: list[tuple[str, str]] = []
    script_entries: list[tuple[str, bytes]] = []
    skipped: list[str] = []
    for name, blob in entries:
        if is_script_path(name):
            if len(blob) <= MAX_SCRIPT_BYTES:
                script_entries.append((name, blob))
            else:
                skipped.append(name)
        elif is_text_blob(blob):
            text_entries.append((name, blob.decode("utf-8")))
        else:
            skipped.append(name)
    return text_entries, script_entries, skipped
```

`_group_entries` 中 `files` 列表推导改为带 `entry_type` / `encoding`：

```python
        files = [
            {
                "path": _assert_safe_rel_path(name[len(prefix) :]),
                "size": len(text.encode("utf-8")),
                "content": text,
                "entry_type": "text",
                "encoding": "utf-8",
            }
            for name, text in entries
            if name.startswith(prefix) and name != f"{prefix}SKILL.md"
        ]
```

在 `_assign_skipped` 之后新增（归组口径与 `_assign_skipped` 完全一致）：

```python
def _attach_scripts(
    groups: list[dict[str, Any]], roots: list[str], scripts: list[tuple[str, bytes]]
) -> None:
    """把脚本条目按「最长前缀 skill 根目录」归到对应组（base64 入库）。

    无归属条目与 _assign_skipped 同口径：静默丢弃。
    """
    for name, blob in scripts:
        best, best_len = -1, -1
        for idx, root in enumerate(roots):
            prefix = f"{root}/" if root else ""
            if name.startswith(prefix) and len(root) > best_len:
                best, best_len = idx, len(root)
        if best < 0:
            continue
        prefix = f"{roots[best]}/" if roots[best] else ""
        groups[best]["files"].append(
            {
                "path": _assert_safe_rel_path(name[len(prefix) :]),
                "size": len(blob),
                "content": base64.b64encode(blob).decode("ascii"),
                "entry_type": "script",
                "encoding": "base64",
            }
        )
```

`_build_groups` 改签名并接脚本（`parse_zip` / `parse_dir_files` 的调用点同步改为解包三元组）：

```python
def _build_groups(
    text_entries: list[tuple[str, str]],
    script_entries: list[tuple[str, bytes]],
    skipped: list[str],
) -> list[dict[str, Any]]:
    """分组 → 解析 → 脚本归组 → 标注跳过的二进制附件。"""
    groups = _group_entries(text_entries)
    roots = _ordered_roots(text_entries)
    _attach_scripts(groups, roots, script_entries)
    _assign_skipped(groups, roots, skipped)
    return groups
```

`parse_zip` 与 `parse_dir_files` 中 `_split_binary(...)` 调用改为：

```python
    text_entries, script_entries, skipped = _split_entries(raw_entries)
    return _build_groups(text_entries, script_entries, skipped)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_skill_import.py -q`
预期：PASS（含既有用例——旧 zip 用例的 files 断言若只查 path 不受影响；若查整个 dict 形状需同步补 `entry_type`/`encoding` 键）

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/skill_import.py tests/test_skill_import.py
git commit -m "feat(skills): 导入管线支持脚本附件 base64 入库"
```

---

### 任务 4：preview 暴露脚本清单（`schemas.py` + `registry.py`）

**文件：**
- 修改：`src/a2a_gateway/schemas.py`（`SkillImportPreviewItem`，约 215 行）
- 修改：`src/a2a_gateway/routes/registry.py:293-306`（`_preview_item`）
- 测试：`tests/test_skills_api.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_skills_api.py` 追加（沿用文件内 `_zip_b64` 助手；该助手只收 str，脚本内容恰好是文本可复用）：

```python
async def test_preview_surfaces_scripts(auth_client, monkeypatch):
    resp = await auth_client.post(
        "/api/admin/skills/import/preview",
        json={
            "source": "zip",
            "zip_b64": _zip_b64(
                {
                    "demo-skill/SKILL.md": SKILL_MD,
                    "demo-skill/scripts/gen.py": "print('hi')",
                }
            ),
        },
    )
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["scripts"] == ["scripts/gen.py"]
    assert item["file_count"] == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_skills_api.py::test_preview_surfaces_scripts -q`
预期：FAIL，断言 `scripts` 键不存在

- [ ] **步骤 3：实现**

`schemas.py` 的 `SkillImportPreviewItem` 在 `skipped_binary` 字段后追加：

```python
    scripts: list[str] = Field(default_factory=list, description="脚本附件相对路径（Phase B 起可沙箱执行）")
```

`registry.py` 的 `_preview_item` 整体替换为：

```python
def _preview_item(parsed: dict[str, Any], conflict: bool) -> SkillImportPreviewItem:
    entries = list(parsed.get("files") or [])
    files = [str(f.get("path") or "") for f in entries if str(f.get("entry_type") or "text") == "text"]
    scripts = [str(f.get("path") or "") for f in entries if str(f.get("entry_type") or "") == "script"]
    content_bytes = int(parsed.get("size_bytes") or 0)
    attachments = sum(int(f.get("size") or 0) for f in entries)
    return SkillImportPreviewItem(
        name=str(parsed.get("name") or ""),
        description=str(parsed.get("description") or ""),
        content_bytes=content_bytes,
        file_count=len(files) + len(scripts),
        total_bytes=content_bytes + attachments,
        files=files,
        scripts=scripts,
        skipped_binary=[str(p) for p in (parsed.get("skipped_binary") or [])],
        conflict=conflict,
    )
```

（要点：text 来源的组没有 `files` 键，`or []` 兜底；`entry_type` 缺省按 text 处理，兼容存量。）

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_skills_api.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/schemas.py src/a2a_gateway/routes/registry.py tests/test_skills_api.py
git commit -m "feat(skills): 导入预览暴露脚本附件清单"
```

---

### 任务 5：附件编辑（校验 + repository + 路由）

**文件：**
- 修改：`src/a2a_gateway/skill_import.py`（新增编辑校验函数）
- 修改：`src/a2a_gateway/schemas.py`（`SkillFileIn` / `SkillUpdate` / `SkillOut`）
- 修改：`src/a2a_gateway/repository.py:584-600`（`update_skill`）
- 修改：`src/a2a_gateway/routes/registry.py:418-441`（`update_skill` 路由）
- 测试：`tests/test_skills_api.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_skills_api.py`：先给 `_skill` 助手的 base dict 增加 `"allow_scripts": False,`（`SkillOut` 即将新增该字段，缺了会让全部既有用例序列化失败）。然后追加：

```python
SCRIPT_B64 = base64.b64encode(b"print('hi')\n").decode()


def _patch_update_chain(monkeypatch, captured: dict):
    async def fake_get(session, skill_id):
        return _skill(review_status="approved", reviewed_at=_NOW)

    async def fake_using(session, skill_id):
        return []

    async def fake_update(session, skill, data, files=None):
        captured["data"] = data
        captured["files"] = files
        return _skill(review_status="pending")

    async def fake_refresh(session, ids):
        return 0

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    monkeypatch.setattr(registry_mod.repo, "update_skill", fake_update)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_skills", fake_refresh)


async def test_update_skill_replaces_files_and_resets_review(auth_client, monkeypatch):
    captured: dict = {}
    _patch_update_chain(monkeypatch, captured)
    resp = await auth_client.put(
        "/api/admin/skills/1",
        json={
            "files": [
                {"path": "scripts/gen.py", "content": SCRIPT_B64, "entry_type": "script", "encoding": "base64"},
                {"path": "refs/a.md", "content": "文本"},
            ],
        },
    )
    assert resp.status_code == 200
    files = captured["files"]
    assert [f["path"] for f in files] == ["scripts/gen.py", "refs/a.md"]
    assert files[0]["entry_type"] == "script" and files[0]["size"] == len(b"print('hi')\n")
    assert files[1]["entry_type"] == "text" and files[1]["encoding"] == "utf-8"


async def test_update_skill_rejects_unsafe_path(auth_client, monkeypatch):
    captured: dict = {}
    _patch_update_chain(monkeypatch, captured)
    resp = await auth_client.put(
        "/api/admin/skills/1",
        json={"files": [{"path": "../escape.md", "content": "x"}]},
    )
    assert resp.status_code == 400


async def test_update_skill_rejects_script_without_whitelist_suffix(auth_client, monkeypatch):
    captured: dict = {}
    _patch_update_chain(monkeypatch, captured)
    resp = await auth_client.put(
        "/api/admin/skills/1",
        json={"files": [{"path": "bin/run.exe", "content": SCRIPT_B64, "entry_type": "script", "encoding": "base64"}]},
    )
    assert resp.status_code == 400


async def test_update_skill_allow_scripts_only_does_not_touch_files(auth_client, monkeypatch):
    captured: dict = {}
    _patch_update_chain(monkeypatch, captured)
    resp = await auth_client.put("/api/admin/skills/1", json={"allow_scripts": True})
    assert resp.status_code == 200
    assert captured["files"] is None
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_skills_api.py -k update_skill -q`
预期：FAIL（`files` 字段未定义 → 422；`normalize_file_entries` 不存在）

- [ ] **步骤 3：实现校验函数（`skill_import.py`）**

`skill_import.py` 模块尾部追加（复用本模块的路径安全与上限口径）：

```python
def normalize_file_entries(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """编辑提交的附件全量规范化与校验（规格 §6.1）。

    - path 安全校验与导入同口径（zip slip / 目录穿越拒绝）
    - entry_type 与后缀一致性：script 必须命中白名单后缀且 encoding=base64
    - 单脚本 ≤ MAX_SCRIPT_BYTES（按解码后原始字节）；附件总量 ≤ MAX_SKILL_BYTES；条目数 ≤ MAX_FILES
    不合法抛 SkillImportError；@returns 规范化条目。
    """
    if len(files) > MAX_FILES:
        raise SkillImportError(f"附件数超过 {MAX_FILES} 上限")
    normalized: list[dict[str, Any]] = []
    total = 0
    for item in files:
        path = _assert_safe_rel_path(str(item.get("path") or ""))
        entry_type = str(item.get("entry_type") or "text")
        encoding = str(item.get("encoding") or "utf-8")
        if entry_type == "script":
            if not is_script_path(path):
                raise SkillImportError(
                    f"脚本附件后缀必须属于 {sorted(SCRIPT_SUFFIXES)}：{path}"
                )
            if encoding != "base64":
                raise SkillImportError(f"脚本附件编码必须为 base64：{path}")
            try:
                blob = base64.b64decode(str(item.get("content") or ""), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise SkillImportError(f"脚本附件 base64 解码失败：{path}") from exc
            if len(blob) > MAX_SCRIPT_BYTES:
                raise SkillImportError(f"脚本超过 {MAX_SCRIPT_BYTES} 字节上限：{path}")
            total += len(blob)
            normalized.append(
                {
                    "path": path,
                    "size": len(blob),
                    "content": str(item.get("content") or ""),
                    "entry_type": "script",
                    "encoding": "base64",
                }
            )
        elif entry_type == "text":
            if encoding != "utf-8":
                raise SkillImportError(f"文本附件编码必须为 utf-8：{path}")
            text = str(item.get("content") or "")
            size = len(text.encode("utf-8"))
            if size > MAX_FILE_BYTES:
                raise SkillImportError(f"单文件超过 {MAX_FILE_BYTES} 字节上限：{path}")
            total += size
            normalized.append(
                {"path": path, "size": size, "content": text, "entry_type": "text", "encoding": "utf-8"}
            )
        else:
            raise SkillImportError(f"未知附件类型：{entry_type}")
    if total > MAX_SKILL_BYTES:
        raise SkillImportError(f"附件总量超过 {MAX_SKILL_BYTES} 字节上限")
    return normalized


def files_differ(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> bool:
    """规范化后比较附件是否变更；存量缺 entry_type/encoding 视为 text/utf-8。"""

    def norm(entries: list[dict[str, Any]]) -> list[tuple[str, int, str, str, str]]:
        return sorted(
            (
                str(e.get("path") or ""),
                int(e.get("size") or 0),
                str(e.get("content") or ""),
                str(e.get("entry_type") or "text"),
                str(e.get("encoding") or "utf-8"),
            )
            for e in entries
        )

    return norm(existing) != norm(incoming)
```

- [ ] **步骤 4：实现 schemas（`schemas.py`）**

`SkillUpdate` 之前新增：

```python
class SkillFileIn(BaseModel):
    """编辑提交的附件条目（全量替换语义）。"""

    path: str = Field(description="技能包内相对路径（posix 风格）")
    content: str = Field(default="", description="文本原文，或 base64（脚本）")
    entry_type: Literal["text", "script"] = "text"
    encoding: Literal["utf-8", "base64"] = "utf-8"
```

`SkillUpdate` 改为：

```python
class SkillUpdate(BaseModel):
    description: str | None = None
    content: str | None = None
    load_mode: Literal["always", "on_demand"] | None = None
    enabled: bool | None = None
    allow_scripts: bool | None = Field(default=None, description="是否允许沙箱执行捆绑脚本")
    files: list[SkillFileIn] | None = Field(default=None, description="None=不改；列表=全量替换")
```

`SkillOut` 增加：

```python
    allow_scripts: bool = False
```

- [ ] **步骤 5：实现 repository（`repository.py`）**

`repository.py` import 区加 `from .skill_import import files_differ`（无循环：skill_import 只依赖 skills）。

`update_skill`（584-600 行）整体替换为：

```python
async def update_skill(
    session: AsyncSession,
    skill: Skill,
    data: "SkillUpdate",
    files: list[dict[str, Any]] | None = None,
) -> Skill:
    """编辑技能：description / content / 附件变更即重置审核为 pending（内容变了必须重审）。"""
    content_changed = False
    if data.description is not None and data.description != skill.description:
        skill.description = data.description
        content_changed = True
    if data.content is not None and data.content != skill.content:
        skill.content = data.content
        content_changed = True
    if data.load_mode is not None and data.load_mode != skill.load_mode:
        skill.load_mode = data.load_mode
    if data.enabled is not None:
        skill.enabled = data.enabled
    if data.allow_scripts is not None:
        skill.allow_scripts = data.allow_scripts
    if files is not None and files_differ(skill.files or [], files):
        skill.files = files
        skill.file_count = len(files)
        content_changed = True
    if content_changed:
        skill.review_status = SkillReviewStatus.PENDING
        skill.reviewed_at = None
    await session.commit()
    await session.refresh(skill)
    return skill
```

（保留原函数体尾部已有的 commit/refresh 语义；若原实现即如此则仅替换中间字段处理段。）

- [ ] **步骤 6：实现路由（`registry.py`）**

`registry.py` import 区加 `from ..skill_import import normalize_file_entries`（按现有相对导入层级调整，与 `parse_skill_md` 等同区）。

`update_skill` 路由（418-441 行）改为：

```python
@router.put("/skills/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: int,
    data: SkillUpdate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    skill = await repo.get_skill(session, skill_id)
    if skill is None:
        raise HTTPException(404, "Skill 不存在")
    normalized_files: list[dict[str, Any]] | None = None
    try:
        validate_skill_fields(
            name=skill.name,
            description=data.description if data.description is not None else skill.description,
            content=data.content if data.content is not None else skill.content,
        )
        if data.files is not None:
            normalized_files = normalize_file_entries([f.model_dump() for f in data.files])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    affected = await repo.agents_using_skill(session, skill_id)
    updated = await repo.update_skill(session, skill, data, files=normalized_files)
    await repo.refresh_agents_for_skills(session, [skill_id])
    for agent in affected:
        await invalidate_agent(agent.id)
    return updated
```

（`SkillImportError` 是 `ValueError` 子类，被既有 `except ValueError` 收敛为 400。）

- [ ] **步骤 7：运行测试验证通过**

运行：`uv run pytest tests/test_skills_api.py -q`
预期：PASS（含全部既有用例）

- [ ] **步骤 8：Commit**

```bash
git add src/a2a_gateway/skill_import.py src/a2a_gateway/schemas.py src/a2a_gateway/repository.py src/a2a_gateway/routes/registry.py tests/test_skills_api.py
git commit -m "feat(skills): 附件全量编辑 + allow_scripts + 审核重置语义"
```

---

### 任务 6：快照透传 `allow_scripts`

**文件：**
- 修改：`src/a2a_gateway/repository.py:468-480`（`skill_snapshot`）
- 测试：`tests/test_skills_binding.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_skills_binding.py` 追加（自足用例，不依赖文件内其它助手）：

```python
from types import SimpleNamespace as _NS

from a2a_gateway.repository import skill_snapshot


def test_skill_snapshot_carries_allow_scripts():
    skill = _NS(
        id=3, name="s", description="d", content="c", load_mode="on_demand",
        files=[], review_status="approved", allow_scripts=True,
    )
    snap = skill_snapshot(skill)
    assert snap["allow_scripts"] is True


def test_skill_snapshot_defaults_allow_scripts_false():
    skill = _NS(
        id=3, name="s", description="d", content="c", load_mode="on_demand",
        files=[], review_status="approved", allow_scripts=False,
    )
    assert skill_snapshot(skill)["allow_scripts"] is False
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_skills_binding.py -k snapshot -q`
预期：FAIL，`KeyError: 'allow_scripts'`

- [ ] **步骤 3：实现**

`skill_snapshot` 的 return dict 中、`"load_mode"` 行之后加一行：

```python
        "allow_scripts": bool(skill.allow_scripts),
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_skills_binding.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/a2a_gateway/repository.py tests/test_skills_binding.py
git commit -m "feat(skills): 运行时快照透传 allow_scripts"
```

---

### 任务 7：前端类型扩展 + SKILL.md 文件上传

**文件：**
- 修改：`web/src/lib/adminApi.ts`（Skill 区，约 175-240 行）
- 修改：`web/src/lib/skillUtils.ts`
- 创建：`web/src/lib/skillUtils.test.ts`
- 修改：`web/src/components/admin/SkillImportDialog.tsx`

- [ ] **步骤 1：编写失败的测试**

创建 `web/src/lib/skillUtils.test.ts`：

```ts
import { describe, expect, it } from "vitest";
import { isSkillMdFileName } from "./skillUtils";

describe("isSkillMdFileName", () => {
  it("接受 .md / .markdown（大小写不敏感）", () => {
    expect(isSkillMdFileName("SKILL.md")).toBe(true);
    expect(isSkillMdFileName("skill.Markdown")).toBe(true);
  });
  it("拒绝其它扩展名与无扩展名", () => {
    expect(isSkillMdFileName("SKILL.txt")).toBe(false);
    expect(isSkillMdFileName("SKILL")).toBe(false);
    expect(isSkillMdFileName("")).toBe(false);
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd web && npm test`
预期：FAIL，`isSkillMdFileName` 未导出

- [ ] **步骤 3：实现**

`web/src/lib/skillUtils.ts` 尾部追加：

```ts
/** 粘贴 tab 的「从本地选择 SKILL.md」：判定所选文件名是否为 Markdown 文件。 */
export function isSkillMdFileName(name: string): boolean {
  return /\.(md|markdown)$/i.test(name);
}
```

`web/src/lib/adminApi.ts`：

1. `Skill` 接口的 `files` 字段改为：

```ts
  files: {
    path: string;
    size: number;
    content?: string;
    entry_type?: "text" | "script";
    encoding?: "utf-8" | "base64";
  }[];
```

2. `Skill` 接口增加 `allow_scripts: boolean;`（`enabled` 附近）。

3. `SkillImportPreviewItem` 增加 `scripts: string[];`（`files` 之后）。

4. `SkillCreatePayload` 附近新增并扩展 `SkillUpdatePayload`（在现有 `SkillUpdatePayload` 定义上追加字段；若尚无该接口则创建）：

```ts
export interface SkillFilePayload {
  path: string;
  content: string;
  entry_type?: "text" | "script";
  encoding?: "utf-8" | "base64";
}
```

并确保 `SkillUpdatePayload` 含 `allow_scripts?: boolean; files?: SkillFilePayload[];`。

- [ ] **步骤 4：SkillImportDialog 粘贴 tab 加文件选择**

`web/src/components/admin/SkillImportDialog.tsx`：

1. import 区加 `import { isSkillMdFileName } from "@/lib/skillUtils";`（与既有 `formatBytes` 导入同区合并）。
2. `tab === 0` 分支整体替换为：

```tsx
        {tab === 0 && (
          <>
            <Button variant="outlined" component="label" sx={{ mt: 2 }} disabled={busy}>
              从本地选择 SKILL.md
              <input
                type="file"
                accept=".md,.markdown"
                hidden
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  e.target.value = ""; // 允许重复选择同一文件
                  if (!file) return;
                  if (!isSkillMdFileName(file.name)) {
                    setError("请选择 .md / .markdown 文件");
                    return;
                  }
                  setError("");
                  setSkillMd(await file.text());
                }}
              />
            </Button>
            <TextField
              label="SKILL.md 全文"
              multiline
              minRows={8}
              fullWidth
              margin="normal"
              value={skillMd}
              onChange={(e) => setSkillMd(e.target.value)}
              placeholder={"---\nname: my-skill\ndescription: 说明\n---\n正文（选择文件后可继续修改）"}
            />
          </>
        )}
```

（要点：文件读入**填入文本域**而非直接提交——管理员可先改再预览；复用既有 `source=text` 预览/落库管线，后端零改动。）

- [ ] **步骤 5：运行测试与构建验证**

运行：`cd web && npm test && npm run build`
预期：全部 PASS / build 成功

- [ ] **步骤 6：Commit**

```bash
git add web/src/lib/adminApi.ts web/src/lib/skillUtils.ts web/src/lib/skillUtils.test.ts web/src/components/admin/SkillImportDialog.tsx
git commit -m "feat(web): SKILL.md 文件上传与 Skill 脚本类型扩展"
```

---

### 任务 8：全量回归

- [ ] **步骤 1：后端全量测试**

运行：`uv run pytest -q`
预期：全绿（既有 342+ 用例无回归）

- [ ] **步骤 2：类型检查（如环境可用）**

运行：`uv run basedpyright`
预期：0 error（项目基线 standard 档；若 dev 依赖未含 basedpyright 则记录并跳过）

- [ ] **步骤 3：前端测试 + 构建**

运行：`cd web && npm test && npm run build`
预期：全绿 + build 成功

- [ ] **步骤 4：迁移语法自检（无真库时的最低验证）**

运行：`uv run python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory.from_config(Config('alembic.ini')); [print(r.revision, r.down_revision) for r in s.walk_revisions()]"`
预期：链路含 `0011_skill_scripts` 且 `down_revision == 0010_skills`，无分叉报错

- [ ] **步骤 5：如有未提交改动则 Commit**

```bash
git add -A
git commit -m "chore(skills): Phase A 收尾"
```

---

## 自检记录

- 规格覆盖：§4.1/4.2/4.3/4.4 → 任务 1/2/3/6；§5 → 任务 3/4；§5.1 → 任务 7；§6 → 任务 5；§7 的 PUT 扩展 → 任务 5（`/scripts/run` 属 Phase C，不在本计划）；§10 的导入弹窗增强 → 任务 7（详情/编辑弹窗属 Phase D）
- 类型一致性：`normalize_file_entries`（任务 5 定义，任务 5 路由消费）；`files_differ`（任务 5 定义并消费）；`is_script_path`（任务 1 定义，任务 3/5 消费）；`allow_scripts` 快照键（任务 6 定义，Phase C 消费）
- 无占位符；所有测试代码与实现代码均完整给出
