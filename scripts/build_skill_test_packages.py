"""构建 Skill 导入 / 脚本执行链路的测试包（zip 来源的正例 / 边界 / 安全用例）。

用法（仓库根目录）::

    uv run python scripts/build_skill_test_packages.py              # 构建 + 离线自检
    uv run python scripts/build_skill_test_packages.py --execute    # 额外经沙箱真跑示例脚本

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
| scripts-bundle.zip | 脚本正例：2 个含脚本技能（6 个脚本），脚本以 entry_type=script（base64）入库，可试跑 |
| script-oversized.zip | 单脚本超 256KB：跳过并标注（不降级为文本附件） |
| script-non-whitelist.zip | 白名单外后缀（.rb）按文本附件收录，不进脚本通道 |

自检分四段：正例技能包逐文件解析 → 负例样本 → 目录来源与 zip 产物 → 脚本示例
（入库 base64 往返 → 目录/编辑/沙箱 payload 口径一致 → 语法自检 → 本地冒烟）。
默认全程离线：`.sh` 在 Windows 宿主或本机无 bash/node 时跳过冒烟；只有 `--execute`
才经 `sandbox_client` 把示例脚本送进沙箱真跑（需 SANDBOX_URL 指向可达的 gate，
常规验收路径是管理端「Skill 详情 → 试跑」，样例见 `examples/skills-scripts/`）。
全部通过才以 0 退出。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "examples" / "skills"
INVALID_DIR = ROOT / "examples" / "skills-invalid"
SCRIPTS_DIR = ROOT / "examples" / "skills-scripts"
OUT_DIR = ROOT / "examples" / "skills-dist"

EXPECTED_SKILLS = ("budget-estimation", "reply-tone-guide", "trip-planning", "visa-doc-checklist")

# 脚本示例清单：技能目录 → 脚本相对路径（构建产物 / 自检 / 真跑共用同一份事实）
SCRIPT_DEMO: dict[str, tuple[str, ...]] = {
    "sandbox-probe": ("scripts/exit_nonzero.py", "scripts/noisy.py", "scripts/slow.py"),
    "trip-toolkit": (
        "scripts/budget_estimate.py",
        "scripts/itinerary_skeleton.sh",
        "scripts/packing_list.js",
    ),
}

LOCAL_SMOKE_TIMEOUT_S = 10  # 本地冒烟缺省超时（slow.py 用用例自带的 timeout_s）

# 示例脚本的运行用例：本地冒烟与沙箱真跑共用。
#   exit_code / timeout_s     —— 退出码预期与沙箱超时
#   stdout_has / stderr_has   —— 输出关键字
#   stdout_min_bytes          —— 未截断时的最小 stdout（noisy.py 用于证明越过 32KB）
#   expect_truncated          —— 沙箱应标注 truncated（本地不截断）
#   expect_timeout            —— 沙箱应超时 kill（本地用 local_timeout）
#   local_timeout             —— 本地冒烟应超时（脚本长时间运行属预期）
DEMO_RUNS: tuple[dict[str, Any], ...] = (
    {
        "skill": "trip-toolkit",
        "script": "scripts/budget_estimate.py",
        "argv": ["--city", "东京", "--nights", "5", "--travelers", "2"],
        "exit_code": 0,
        "stdout_has": "建议预算",
    },
    {
        "skill": "trip-toolkit",
        "script": "scripts/itinerary_skeleton.sh",
        "argv": ["东京", "2"],
        "stdin": "浅草寺\n明治神宫\n筑地市场\n",
        "exit_code": 0,
        "stdout_has": "Day 2",
    },
    {
        "skill": "trip-toolkit",
        "script": "scripts/packing_list.js",
        "argv": ["--city", "东京", "--season", "winter", "--days", "5"],
        "exit_code": 0,
        "stdout_has": "羽绒服",
    },
    {
        "skill": "sandbox-probe",
        "script": "scripts/exit_nonzero.py",
        "exit_code": 3,
        "stdout_has": "stdout",
        "stderr_has": "stderr",
    },
    {
        "skill": "sandbox-probe",
        "script": "scripts/noisy.py",
        "exit_code": 0,
        "stdout_has": "bench",
        "stdout_min_bytes": 32769,
        "expect_truncated": True,
    },
    {
        "skill": "sandbox-probe",
        "script": "scripts/slow.py",
        "timeout_s": 3,
        "local_timeout": True,
        "expect_timeout": True,
    },
)

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


def _skill_md(name: str, description: str) -> bytes:
    """极简合法 SKILL.md（构造脚本类边界样本用）。"""
    return f"---\nname: {name}\ndescription: {description}\n---\n\n正文\n".encode("utf-8")


def _oversized_script() -> bytes:
    """超过 MAX_SCRIPT_BYTES(262144) 的脚本：45000 行 × 6 字节 = 270000 字节。"""
    return b"x = 1\n" * 45000


def build() -> dict[str, bytes]:
    """生成全部 zip 产物：{文件名: 字节}。"""
    return {
        "skills-bundle.zip": _zip_bytes(_walk_entries(SKILLS_DIR)),
        "scripts-bundle.zip": _zip_bytes(_walk_entries(SCRIPTS_DIR)),
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
        "script-oversized.zip": _zip_bytes(
            [
                ("huge-skill/SKILL.md", _skill_md("huge-skill", "单脚本超 256KB 的边界样本")),
                ("huge-skill/scripts/huge.py", _oversized_script()),
            ]
        ),
        "script-non-whitelist.zip": _zip_bytes(
            [
                ("legacy-tools/SKILL.md", _skill_md("legacy-tools", "白名单外后缀的边界样本")),
                ("legacy-tools/scripts/legacy.rb", b"puts 'legacy'\n"),
                ("legacy-tools/references/readme.md", "# Ruby 脚本按文本附件收录\n".encode("utf-8")),
            ]
        ),
    }


class _Reporter:
    """自检输出与失败收集（四段自检共用；skip = 环境不支持的检查）。"""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, label: str, detail: str) -> None:
        print(f"  [OK]   {label}：{detail}")

    def bad(self, label: str, detail: str) -> None:
        self.failures.append(f"{label}：{detail}")
        print(f"  [FAIL] {label}：{detail}")

    def skip(self, label: str, detail: str) -> None:
        print(f"  [SKIP] {label}：{detail}")


def verify() -> list[str]:
    """用被测代码回读全部样本并断言预期；返回失败说明列表（空 = 全通过）。"""
    from a2a_gateway.skill_import import parse_dir_files, parse_zip
    from a2a_gateway.skills import parse_skill_md

    report = _Reporter()
    ok, bad = report.ok, report.bad

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
    print("\n[1/4] 正例技能包（examples/skills）")
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
    print("\n[2/4] 负例样本（examples/skills-invalid，粘贴到「导入 Skill → 粘贴」）")
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
    print("\n[3/4] 目录来源与 zip 产物（examples/skills-dist）")
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

    # 脚本类边界样本：超限跳过 / 白名单外后缀按文本收录
    group = parse_zip(zip_blob("script-oversized.zip"))[0]
    if not group.get("files") and group.get("skipped_binary") == ["huge-skill/scripts/huge.py"]:
        ok("script-oversized.zip", "超限脚本被跳过并标注（未降级为文本附件）")
    else:
        bad(
            "script-oversized.zip",
            f"归类不符：files={group.get('files')}，skipped={group.get('skipped_binary')}",
        )

    group = parse_zip(zip_blob("script-non-whitelist.zip"))[0]
    ruby = _entries_by_path(group).get("scripts/legacy.rb")
    if ruby and ruby.get("entry_type") == "text" and ruby.get("encoding") == "utf-8":
        ok("script-non-whitelist.zip", "白名单外后缀按文本附件收录（不进脚本通道）")
    else:
        bad("script-non-whitelist.zip", f"归类不符：{ruby}")

    # ---- 4. 脚本执行示例（examples/skills-scripts）----
    print("\n[4/4] 脚本示例（examples/skills-scripts → scripts-bundle.zip）")
    verify_scripts(report)

    return report.failures


# ---------------------------------------------------------------------------
# 脚本示例自检（examples/skills-scripts）：入库 → 各来源口径一致 → 语法/冒烟
# ---------------------------------------------------------------------------
LOCAL_INTERPRETER = {".sh": "bash", ".js": "node"}  # .py 用当前解释器（sys.executable）


def _entries_by_path(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """附件清单 → {相对路径: 条目}。"""
    return {str(f.get("path")): f for f in group.get("files") or []}


def _signatures(group: dict[str, Any]) -> list[str]:
    """附件指纹（路径 + 类型），用于比较不同来源的归组是否一致。"""
    return sorted(
        f"{f.get('path')}|{f.get('entry_type') or 'text'}" for f in group.get("files") or []
    )


def verify_scripts(report: _Reporter) -> None:
    """脚本示例：zip 入库 → 目录来源 → 编辑提交 → 沙箱 payload → 语法与本地冒烟。"""
    from a2a_gateway.sandbox_client import build_run_files
    from a2a_gateway.skill_import import normalize_file_entries, parse_dir_files, parse_zip

    groups = {
        str(g.get("name")): g for g in parse_zip((OUT_DIR / "scripts-bundle.zip").read_bytes())
    }
    if sorted(groups) != sorted(SCRIPT_DEMO):
        report.bad(
            "scripts-bundle.zip",
            f"技能清单不符：{sorted(groups)}，期望 {sorted(SCRIPT_DEMO)}",
        )

    # 4.1 脚本入库：entry_type=script + base64 往返一致（文本附件仍 utf-8）
    for skill, scripts in sorted(SCRIPT_DEMO.items()):
        group = groups.get(skill)
        if group is None:
            continue
        files = _entries_by_path(group)
        for rel in scripts:
            entry = files.get(rel)
            raw = (SCRIPTS_DIR / skill / rel).read_bytes()
            if entry is None:
                report.bad(f"{skill}/{rel}", "脚本未入库（应为 entry_type=script）")
                continue
            try:
                decoded = base64.b64decode(str(entry.get("content") or ""))
            except ValueError:
                report.bad(f"{skill}/{rel}", "content 不是合法 base64")
                continue
            if str(entry.get("entry_type")) != "script" or str(entry.get("encoding")) != "base64":
                report.bad(
                    f"{skill}/{rel}",
                    f"类型/编码不符：{entry.get('entry_type')}/{entry.get('encoding')}",
                )
            elif decoded != raw or int(entry.get("size") or 0) != len(raw):
                report.bad(
                    f"{skill}/{rel}",
                    f"base64 往返不一致：入库 {entry.get('size')} B / 源文件 {len(raw)} B",
                )
            else:
                report.ok(f"{skill}/{rel}", f"脚本 {len(raw)} B，base64 往返一致")
        texts = [path for path, item in files.items() if str(item.get("entry_type")) != "script"]
        if texts and all(str(files[path].get("encoding")) == "utf-8" for path in texts):
            report.ok(f"{skill} 文本附件", f"{len(texts)} 个均为 utf-8：{sorted(texts)}")

    # 4.2 目录来源（浏览器目录上传）与 zip 来源必须归组一致
    dir_groups = {
        str(g.get("name")): g
        for g in parse_dir_files(
            [
                {"path": rel, "content": blob.decode("utf-8")}
                for rel, blob in _walk_entries(SCRIPTS_DIR)
            ]
        )
    }
    drift = sorted(
        name
        for name in set(dir_groups) | set(groups)
        if _signatures(dir_groups.get(name, {})) != _signatures(groups.get(name, {}))
    )
    if drift:
        report.bad("目录来源归组", f"与 zip 来源不一致：{drift}")
    else:
        report.ok("目录来源归组", f"与 zip 来源一致（{len(dir_groups)} 个技能）")

    # 4.3 编辑提交（files 全量替换）必须接受同一批附件
    for skill, group in sorted(groups.items()):
        try:
            normalized = normalize_file_entries(list(group.get("files") or []))
        except ValueError as exc:
            report.bad(f"normalize_file_entries（{skill}）", f"编辑提交被拒：{exc}")
        else:
            report.ok(f"normalize_file_entries（{skill}）", f"接受 {len(normalized)} 个附件")

    # 4.4 沙箱请求组装：脚本保持 base64、文本保持 utf-8，路径不改写
    for skill, group in sorted(groups.items()):
        source = _entries_by_path(group)
        payload = {str(f["path"]): f for f in build_run_files(list(group.get("files") or []))}
        changed = [
            path
            for path, item in source.items()
            if payload.get(path, {}).get("encoding") != (item.get("encoding") or "utf-8")
            or payload.get(path, {}).get("content") != (item.get("content") or "")
        ]
        if changed or sorted(payload) != sorted(source):
            report.bad(f"build_run_files（{skill}）", f"条目漂移：{changed or sorted(payload)}")
        else:
            report.ok(
                f"build_run_files（{skill}）",
                f"{len(payload)} 个条目原样透传（脚本 base64 / 文本 utf-8）",
            )

    # 4.5 语法自检（含 CRLF 检查）+ 本地冒烟；沙箱真跑见 --execute
    for skill, scripts in sorted(SCRIPT_DEMO.items()):
        for rel in scripts:
            _check_syntax(report, skill, rel)
    for case in DEMO_RUNS:
        smoke_local(report, case)


def _check_syntax(report: _Reporter, skill: str, rel: str) -> None:
    """静态检查：CRLF 行尾 + 解释器语法（只解析，不执行）。"""
    label = f"语法 {skill}/{rel}"
    path = SCRIPTS_DIR / skill / rel
    raw = path.read_bytes()
    if b"\r\n" in raw:
        report.bad(
            label,
            "含 CRLF 行尾（沙箱内 bash/node 会报语法错，必须为 LF；"
            "见仓库根 .gitattributes 的 examples/skills-scripts/** 规则）",
        )
        return
    suffix = path.suffix.lower()
    if suffix == ".py":
        try:
            compile(path.read_text(encoding="utf-8"), f"{skill}/{rel}", "exec")
        except SyntaxError as exc:
            report.bad(label, f"Python 语法错误：{exc}")
        else:
            report.ok(label, "Python compile 通过（未执行）")
        return
    tool = LOCAL_INTERPRETER.get(suffix)
    if tool is None or shutil.which(tool) is None:
        report.skip(label, f"本机无 {suffix} 解释器，跳过静态检查（沙箱镜像内已预置）")
        return
    # .sh 走 `bash -n` 从 stdin 读脚本：Windows 上 WSL bash 无法直接吃 Windows 路径
    command = [tool, "-n"] if suffix == ".sh" else [tool, "--check", str(path)]
    proc = subprocess.run(command, input=raw, capture_output=True, timeout=30)
    if proc.returncode == 0:
        report.ok(label, f"{tool} 语法检查通过（未执行）")
    else:
        detail = (proc.stderr or proc.stdout).decode("utf-8", errors="replace").strip()
        report.bad(label, f"{tool} 语法检查失败：{detail.splitlines()[:2]}")


def smoke_local(report: _Reporter, case: dict[str, Any]) -> None:
    """本地冒烟：用宿主解释器直跑示例脚本（不经沙箱），验证脚本本身可用。"""
    skill, rel = str(case["skill"]), str(case["script"])
    label = f"本地冒烟 {skill}/{rel}"
    path = SCRIPTS_DIR / skill / rel
    suffix = path.suffix.lower()
    if suffix == ".py":
        command = [sys.executable, str(path)]
    else:
        tool = LOCAL_INTERPRETER[suffix]
        if shutil.which(tool) is None:
            report.skip(label, f"本机无 {tool}，跳过（沙箱镜像内有，--execute 可覆盖）")
            return
        if suffix == ".sh" and sys.platform == "win32":
            report.skip(label, "Windows 宿主的 WSL bash 无法以 Windows 路径为 cwd，跳过")
            return
        command = [tool, str(path)]
    command += [str(item) for item in case.get("argv") or []]

    timeout_s = int(case.get("timeout_s") or LOCAL_SMOKE_TIMEOUT_S)
    # 子进程输出统一按 UTF-8 取：Python 在 Windows 管道下默认走 ANSI 代码页（与沙箱口径不一致）
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        proc = subprocess.run(
            command,
            cwd=SCRIPTS_DIR / skill,
            input=str(case.get("stdin") or "").encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        if case.get("local_timeout"):
            report.ok(label, f"按预期长时间运行（{timeout_s}s 未结束，沙箱侧会 kill）")
        else:
            report.bad(label, f"超过 {timeout_s}s 未结束")
        return

    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    expected_code = int(case.get("exit_code", 0))
    if case.get("local_timeout"):
        report.bad(label, f"预期长时间运行，实际已退出（exit={proc.returncode}）")
    elif proc.returncode != expected_code:
        report.bad(
            label,
            f"退出码 {proc.returncode}，期望 {expected_code}；stderr={stderr.strip()[:120]}",
        )
    elif case.get("stdout_has") and str(case["stdout_has"]) not in stdout:
        report.bad(label, f"stdout 未包含「{case['stdout_has']}」：{stdout.strip()[:120]}")
    elif case.get("stderr_has") and str(case["stderr_has"]) not in stderr:
        report.bad(label, f"stderr 未包含「{case['stderr_has']}」：{stderr.strip()[:120]}")
    elif case.get("stdout_min_bytes") and len(proc.stdout) < int(case["stdout_min_bytes"]):
        report.bad(label, f"stdout 仅 {len(proc.stdout)} B，期望 ≥ {case['stdout_min_bytes']} B")
    else:
        report.ok(
            label, f"exit={proc.returncode}，stdout {len(proc.stdout)} B / stderr {len(proc.stderr)} B"
        )


def _sandbox_problems(case: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """按用例预期核对沙箱结果（timeout / truncated / exit_code / stdout / stderr）。"""
    from a2a_gateway.skills import MAX_SCRIPT_OUTPUT_BYTES

    problems: list[str] = []
    if bool(result.get("timeout")) != bool(case.get("expect_timeout")):
        problems.append(f"timeout={result.get('timeout')}，期望 {bool(case.get('expect_timeout'))}")
    if bool(result.get("truncated")) != bool(case.get("expect_truncated")):
        problems.append(
            f"truncated={result.get('truncated')}，期望 {bool(case.get('expect_truncated'))}"
        )
    expected_code = int(case.get("exit_code", 0))
    if not case.get("expect_timeout") and result.get("exit_code") != expected_code:
        problems.append(f"exit_code={result.get('exit_code')}，期望 {expected_code}")

    stdout, stderr = str(result.get("stdout") or ""), str(result.get("stderr") or "")
    if case.get("stdout_has") and str(case["stdout_has"]) not in stdout:
        problems.append(f"stdout 未包含「{case['stdout_has']}」")
    if case.get("stderr_has") and str(case["stderr_has"]) not in stderr:
        problems.append(f"stderr 未包含「{case['stderr_has']}」")
    stdout_bytes = len(stdout.encode("utf-8"))
    if case.get("expect_truncated"):
        if stdout_bytes != MAX_SCRIPT_OUTPUT_BYTES:
            problems.append(
                f"截断后 stdout 应为 {MAX_SCRIPT_OUTPUT_BYTES} B，实际 {stdout_bytes} B"
            )
    elif case.get("stdout_min_bytes") and stdout_bytes < int(case["stdout_min_bytes"]):
        problems.append(f"stdout 仅 {stdout_bytes} B，期望 ≥ {case['stdout_min_bytes']} B")
    if result.get("error"):
        problems.append(f"执行器错误：{result['error']}")
    return problems


async def execute_demos(report: _Reporter) -> None:
    """--execute：把示例脚本经 `sandbox_client` 送进沙箱 gate 真跑（需 SANDBOX_URL 可达）。"""
    from a2a_gateway.sandbox_client import (
        SandboxError,
        SandboxTimeout,
        run_script,
        sandbox_enabled,
    )
    from a2a_gateway.skill_import import parse_zip

    if not sandbox_enabled():
        report.skip(
            "沙箱真跑",
            "未配置 SANDBOX_URL（脚本执行未启用）；请用管理端「Skill 详情 → 试跑」验证，"
            "或在能访问 gate 的环境里设 SANDBOX_URL=http://sandbox-gate:8100 后重跑本脚本",
        )
        return

    groups = {
        str(g.get("name")): g for g in parse_zip((OUT_DIR / "scripts-bundle.zip").read_bytes())
    }
    for case in DEMO_RUNS:
        skill, rel = str(case["skill"]), str(case["script"])
        label = f"沙箱执行 {skill}/{rel}"
        group = groups.get(skill)
        if group is None:
            report.bad(label, "解析结果缺少该技能（scripts-bundle.zip 未构建或已损坏）")
            continue
        try:
            result = await run_script(
                files=list(group.get("files") or []),
                entry=rel,
                argv=[str(item) for item in case.get("argv") or []],
                stdin=str(case.get("stdin") or ""),
                timeout_s=int(case.get("timeout_s") or 30),
            )
        except SandboxTimeout as exc:
            if case.get("expect_timeout"):
                report.ok(label, f"按预期超时（{exc}）")
            else:
                report.bad(label, f"超时但用例不期望：{exc}")
            continue
        except SandboxError as exc:
            report.bad(label, f"沙箱调用失败：{exc}")
            continue
        problems = _sandbox_problems(case, result)
        if problems:
            report.bad(label, "；".join(problems))
        else:
            report.ok(
                label,
                f"exit_code={result.get('exit_code')}，{result.get('duration_ms')} ms，"
                f"stdout {len(str(result.get('stdout') or '').encode('utf-8'))} B",
            )


HINTS = {
    "skills-bundle.zip": "多技能正例：预览 4 条 → 只勾选 2 个落库",
    "binary-attachment.zip": "二进制附件：预览应显示「已跳过 1 个非文本文件」",
    "invalid-skill.zip": "非法 SKILL.md：来源级 error，接口不能 500",
    "oversized-body.zip": "正文超 32KB：来源级 error",
    "no-skill-md.zip": "无 SKILL.md：来源级 error",
    "zip-slip.zip": "zip slip：应被拒绝",
    "symlink-entry.zip": "符号链接条目：应被拒绝",
    "too-many-files.zip": "条目超上限：应被拒绝",
    "scripts-bundle.zip": "脚本正例：2 个技能共 6 个脚本，导入后详情弹窗可直接试跑",
    "script-oversized.zip": "脚本超 256KB：跳过并标注，不降级为文本附件",
    "script-non-whitelist.zip": "白名单外后缀（.rb）：按文本附件收录，不可执行",
}


def main(argv: list[str] | None = None) -> int:
    # Windows GBK 控制台：脚本输出含中文时只替换不可编码字符，避免整轮自检崩在编码上
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description="构建 skill 导入 / 脚本执行测试包并自检（产物：examples/skills-dist/）"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="额外把示例脚本经沙箱真跑（需 SANDBOX_URL 可达；默认只做离线自检）",
    )
    args = parser.parse_args(argv)

    print(f"构建 skill 测试包 → {OUT_DIR}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, blob in build().items():
        (OUT_DIR / filename).write_bytes(blob)
        print(f"  - {filename}（{len(blob)} B）  # {HINTS[filename]}")

    failures = verify()
    if args.execute:
        print("\n[额外] 沙箱真跑（--execute）")
        report = _Reporter()
        asyncio.run(execute_demos(report))
        failures += report.failures

    if failures:
        print(f"\n自检失败 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n自检全部通过：正例可正常解析并入库脚本，负例均按预期被拒绝。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
