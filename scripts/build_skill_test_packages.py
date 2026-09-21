"""构建 Skill 导入链路的测试包（zip 来源的正例 / 边界 / 安全用例）。

用法（仓库根目录）::

    uv run python scripts/build_skill_test_packages.py

产物写入 `examples/skills-dist/`（已在 .gitignore 中忽略），供前端
「Skill 管理 → 导入 Skill → zip」上传：

| 包 | 预期表现 |
| --- | --- |
| skills-bundle.zip | 正例：预览列出 4 个 skill，可只勾选其中两个落库 |
| binary-attachment.zip | 预览标注「已跳过 1 个非文本文件」，文本附件照常归类 |
| invalid-skill.zip | 来源级 error（缺 frontmatter），接口仍 200、不能 500 |
| oversized-body.zip | 来源级 error：正文超过 32KB 上限 |
| no-skill-md.zip | 来源级 error：未找到任何 SKILL.md |
| zip-slip.zip | 拒绝：条目路径含 ../（zip slip） |
| symlink-entry.zip | 拒绝：条目是符号链接 |
| too-many-files.zip | 拒绝：条目数超过上限 100 |

脚本末尾用被测代码自身（`a2a_gateway.skills` / `a2a_gateway.skill_import`）
回读全部样本并断言预期；全部通过才以 0 退出。
"""

from __future__ import annotations

import base64
import io
import stat
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "examples" / "skills"
INVALID_DIR = ROOT / "examples" / "skills-invalid"
OUT_DIR = ROOT / "examples" / "skills-dist"

EXPECTED_SKILLS = ("budget-estimation", "reply-tone-guide", "trip-planning", "visa-doc-checklist")

# 1x1 透明 PNG：二进制附件样本（验证「跳过并标注」）
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# 故意缺 frontmatter 的 SKILL.md：zip 内解析失败必须被收口成来源级 error
BROKEN_MD = "这份 SKILL.md 没有 frontmatter，解析必须失败。\n"

# 路径合法、正文合法的极简 SKILL.md（用于越权路径样本的载荷）
SIMPLE_MD = "---\nname: escaped-demo\ndescription: 越权路径样本\n---\n\n正文\n"


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    """把 [(条目名, 字节)] 打成 zip（条目名不做任何净化，便于构造恶意样本）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in entries:
            zf.writestr(name, blob)
    return buf.getvalue()


def _symlink_zip_bytes() -> bytes:
    """含符号链接条目的 zip（external_attr 标记 S_IFLNK）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link-demo/SKILL.md")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "fake-target")
    return buf.getvalue()


def _walk_entries(root: Path) -> list[tuple[str, bytes]]:
    """目录 → [(posix 相对路径, 字节)]，按路径排序保证产物稳定。"""
    return [
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _oversized_md() -> str:
    """正文超 MAX_CONTENT_BYTES(32768) 的 SKILL.md：中文约 3 字节/字。"""
    return f"---\nname: oversized-demo\ndescription: 正文超过上限\n---\n{'行' * 12000}"


def _binary_demo_md() -> str:
    """含二进制附件的样本技能（zip 来源专用，不放进 examples/skills）。"""
    return (
        "---\n"
        "name: binary-demo\n"
        "description: 含二进制附件的导入样本（zip 来源专用）\n"
        "---\n\n"
        "# 二进制附件样本\n\n"
        "本技能带一个文本附件（references/notes.md）与一张 PNG 截图；\n"
        "导入预览应标注「已跳过 1 个非文本文件」，文本附件照常归类。\n"
    )


def build() -> dict[str, bytes]:
    """生成全部 zip 产物：{文件名: 字节}。"""
    return {
        "skills-bundle.zip": _zip_bytes(_walk_entries(SKILLS_DIR)),
        "binary-attachment.zip": _zip_bytes(
            [
                ("binary-demo/SKILL.md", _binary_demo_md().encode("utf-8")),
                ("binary-demo/references/notes.md", "# 文本附件\n\n应被正常收录。\n".encode()),
                ("binary-demo/assets/cover.png", PNG_1PX),
            ]
        ),
        "invalid-skill.zip": _zip_bytes([("broken/SKILL.md", BROKEN_MD.encode("utf-8"))]),
        "oversized-body.zip": _zip_bytes([("oversized/SKILL.md", _oversized_md().encode("utf-8"))]),
        "no-skill-md.zip": _zip_bytes([("readme/README.md", b"# no skill md\n")]),
        "zip-slip.zip": _zip_bytes([("../escape/SKILL.md", SIMPLE_MD.encode("utf-8"))]),
        "symlink-entry.zip": _symlink_zip_bytes(),
        "too-many-files.zip": _zip_bytes([(f"many/f{index:03d}.md", b"x") for index in range(101)]),
    }


def verify() -> list[str]:
    """用被测代码回读全部样本并断言预期；返回失败说明列表（空 = 全通过）。"""
    from a2a_gateway.skill_import import parse_dir_files, parse_zip
    from a2a_gateway.skills import parse_skill_md

    failures: list[str] = []

    def ok(label: str, detail: str) -> None:
        print(f"  [OK]   {label}：{detail}")

    def bad(label: str, detail: str) -> None:
        failures.append(f"{label}：{detail}")
        print(f"  [FAIL] {label}：{detail}")

    def rejected(label: str, action: Callable[[], object], needle: str) -> None:
        try:
            action()
        except ValueError as exc:
            if needle in str(exc):
                ok(label, f"按预期拒绝（{exc}）")
            else:
                bad(label, f"拒绝原因不符：期望包含「{needle}」，实际「{exc}」")
        else:
            bad(label, "应当被拒绝，但解析通过了")

    # ---- 1. 正例技能包逐文件解析 ----
    print("\n[1/3] 正例技能包（examples/skills）")
    attachments: dict[str, int] = {}
    for md_file in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        dir_name = md_file.parent.name
        try:
            doc = parse_skill_md(md_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            bad(f"技能 {dir_name}", f"解析失败：{exc}")
            continue
        if str(doc["name"]) != dir_name:
            bad(f"技能 {dir_name}", f"frontmatter name 与目录名不一致：{doc['name']}")
            continue
        count = len(
            [p for p in md_file.parent.rglob("*") if p.is_file() and p.name != "SKILL.md"]
        )
        attachments[dir_name] = count
        ok(f"技能 {dir_name}", f"正文 {doc['size_bytes']} B，附件 {count} 个")
    missing = sorted(set(EXPECTED_SKILLS) - set(attachments))
    if missing:
        bad("正例清单", f"缺少技能：{missing}")
    else:
        ok("正例清单", f"共 {len(attachments)} 个技能，与预期一致")

    # ---- 2. 负例样本（粘贴来源逐文件解析）----
    print("\n[2/3] 负例样本（examples/skills-invalid，粘贴到「导入 Skill → 粘贴」）")
    needles = {
        "01-missing-frontmatter.md": "frontmatter",
        "02-unclosed-frontmatter.md": "frontmatter",
        "03-invalid-yaml.md": "YAML",
        "04-non-ascii-name.md": "name",
        "05-empty-description.md": "description",
        "06-bad-name-symbol.md": "name",
    }
    for sample in sorted(INVALID_DIR.glob("*.md")):
        rejected(
            sample.name,
            lambda s=sample: parse_skill_md(s.read_text(encoding="utf-8")),
            needles.get(sample.name, ""),
        )

    # ---- 3. 目录来源与 zip 产物 ----
    print("\n[3/3] 目录来源与 zip 产物（examples/skills-dist）")
    dir_entries = [
        {"path": rel, "content": blob.decode("utf-8")} for rel, blob in _walk_entries(SKILLS_DIR)
    ]
    dir_groups = parse_dir_files(dir_entries)
    got = {str(g.get("name")): len(g.get("files") or []) for g in dir_groups}
    if got == attachments:
        ok("parse_dir_files（目录上传）", f"分组与附件数一致：{got}")
    else:
        bad("parse_dir_files（目录上传）", f"分组不符：{got}，期望 {attachments}")

    def zip_blob(name: str) -> bytes:
        return (OUT_DIR / name).read_bytes()

    groups = parse_zip(zip_blob("skills-bundle.zip"))
    names = sorted(str(g.get("name")) for g in groups)
    if names == list(EXPECTED_SKILLS):
        ok("skills-bundle.zip", f"解析出 {len(groups)} 个技能：{names}")
    else:
        bad("skills-bundle.zip", f"技能清单不符：{names}")

    groups = parse_zip(zip_blob("binary-attachment.zip"))
    # files.path 是「相对技能根目录」的路径；skipped_binary 保留来源完整路径（现有测试同口径）
    if len(groups) == 1:
        skipped = [str(p) for p in groups[0].get("skipped_binary") or []]
        files = [str(f.get("path")) for f in groups[0].get("files") or []]
        if skipped == ["binary-demo/assets/cover.png"] and files == ["references/notes.md"]:
            ok("binary-attachment.zip", f"文本附件 {files}，已跳过 {skipped}")
        else:
            bad("binary-attachment.zip", f"归类不符：files={files}，skipped={skipped}")
    else:
        bad("binary-attachment.zip", f"应解析出 1 个技能，实际 {len(groups)} 个")

    rejected(
        "invalid-skill.zip", lambda: parse_zip(zip_blob("invalid-skill.zip")), "frontmatter"
    )
    rejected("oversized-body.zip", lambda: parse_zip(zip_blob("oversized-body.zip")), "上限")
    rejected(
        "no-skill-md.zip", lambda: parse_zip(zip_blob("no-skill-md.zip")), "未找到任何 SKILL.md"
    )
    rejected("zip-slip.zip", lambda: parse_zip(zip_blob("zip-slip.zip")), "路径非法")
    rejected("symlink-entry.zip", lambda: parse_zip(zip_blob("symlink-entry.zip")), "非常规文件")
    rejected(
        "too-many-files.zip", lambda: parse_zip(zip_blob("too-many-files.zip")), "文件数超过"
    )
    return failures


HINTS = {
    "skills-bundle.zip": "多技能正例：预览 4 条 → 只勾选 2 个落库",
    "binary-attachment.zip": "二进制附件：预览应显示「已跳过 1 个非文本文件」",
    "invalid-skill.zip": "非法 SKILL.md：来源级 error，接口不能 500",
    "oversized-body.zip": "正文超 32KB：来源级 error",
    "no-skill-md.zip": "无 SKILL.md：来源级 error",
    "zip-slip.zip": "zip slip：应被拒绝",
    "symlink-entry.zip": "符号链接条目：应被拒绝",
    "too-many-files.zip": "条目超上限：应被拒绝",
}


def main() -> int:
    print(f"构建 skill 测试包 → {OUT_DIR}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, blob in build().items():
        (OUT_DIR / filename).write_bytes(blob)
        print(f"  - {filename}（{len(blob)} B）  # {HINTS[filename]}")

    failures = verify()
    if failures:
        print(f"\n自检失败 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n自检全部通过：正例可正常解析，负例均按预期被拒绝。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
