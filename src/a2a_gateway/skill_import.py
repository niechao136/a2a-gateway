"""Skill 导入管线：zip / 目录 / URL 来源的解析与安全校验。

统一产出 [{"name", "description", "content", "frontmatter", "size_bytes",
            "files": [{"path","size","content"}], "skipped_binary": [str]}]，
解析与字段校验复用 skills.parse_skill_md；粘贴（text）来源由路由层直接调 parse_skill_md。

安全要求（规格 §4.2）：zip slip 拒绝、符号链接拒绝、压缩炸弹限制、
目录路径规范化、SSRF 逐 IP 校验（DNS 解析后）、响应体 4MB 上限。后端零文件系统访问。

上限口径（复用 skills 常量）：MAX_FILES 在本模块计「来源总条目数」（skills.py 注释的
「单 skill 附件数上限」是门禁侧口径）；MAX_FILE_BYTES 计单条目解压后字节；
MAX_SKILL_BYTES 计来源解压后总量；MAX_IMPORT_BYTES 是入口（上传包 / 响应体）硬上限。
"""

import base64
import binascii
import io
import ipaddress
import posixpath
import socket
import stat
import zipfile
import zlib
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .skills import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_IMPORT_BYTES,
    MAX_SCRIPT_BYTES,
    MAX_SKILL_BYTES,
    SCRIPT_SUFFIXES,
    URL_FETCH_TIMEOUT,
    URL_MAX_REDIRECTS,
    is_script_path,
    parse_skill_md,
)

_READ_CHUNK = 65536  # 分块解压的单次读取量（避免整体解压进内存）

_UNPACK_ERRORS = (
    zipfile.BadZipFile,
    RuntimeError,  # 加密条目（zipfile 要求口令）
    NotImplementedError,  # 不支持的压缩算法
    EOFError,  # 数据流被截断
    zlib.error,  # 损坏的 deflate 流
)


class SkillImportError(ValueError):
    """导入来源解析 / 安全校验失败（message 面向最终用户）。"""


def is_text_blob(blob: bytes) -> bool:
    """二进制判定：含 NUL 或无法按 UTF-8 解码即视为二进制（跳过并标注）。"""
    if b"\x00" in blob:
        return False
    try:
        blob.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _assert_safe_rel_path(raw: str) -> str:
    """规范化相对路径；空路径 / 绝对路径 / 含 .. / 反斜杠一律拒绝（zip slip / 目录穿越）。

    空路径必须显式拒绝：否则 normpath("") 会返回 "."，生成名为 "." 的附件。
    """
    path = raw.replace("\\", "/")
    if not path.strip():
        raise SkillImportError("来源文件路径非法：条目缺少 path（路径不能为空）")
    if path.startswith("/"):
        raise SkillImportError(f"来源文件路径非法：{raw}")
    normalized = posixpath.normpath(path)
    if normalized == ".." or normalized.startswith(("/", "../")):
        raise SkillImportError(f"来源文件路径非法：{raw}")
    return normalized


def _check_zip_entry(zi: zipfile.ZipInfo) -> None:
    """拒绝符号链接 / 设备 / 管道等非常规条目（zip 电梯 / 越权读取攻击面）。"""
    mode = (zi.external_attr >> 16) & 0o170000
    if mode not in (0, stat.S_IFDIR, stat.S_IFREG):  # 0 常见于 Windows 创建的 zip
        raise SkillImportError(f"zip 条目非常规文件（可能是符号链接）：{zi.filename}")


def _group_entries(entries: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """把 (相对路径, 文本) 清单按 SKILL.md 所在目录分组为多个 skill。

    @returns 每组含 parse_skill_md 的结果 + files（相对 SKILL.md 目录的路径）。
    """
    skill_roots: dict[str, str] = {}
    for name, text in entries:
        p = PurePosixPath(name)
        if p.name == "SKILL.md":
            skill_roots[str(p.parent) if str(p.parent) != "." else ""] = text
    if not skill_roots:
        raise SkillImportError("来源中未找到任何 SKILL.md")

    results: list[dict[str, Any]] = []
    for root, md_text in skill_roots.items():
        prefix = f"{root}/" if root else ""
        parsed = parse_skill_md(md_text)
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
        results.append({**parsed, "files": files, "skipped_binary": []})
    return results


def _read_entry_capped(
    zf: zipfile.ZipFile, zi: zipfile.ZipInfo, name: str, remaining: int
) -> bytes:
    """分块读取成员（不整体解压进内存）；实际读取量超单文件 / 总量上限即抛。

    中心目录里的 file_size 可能被伪造，故声明大小与实际读取量双重校验。
    """
    parts: list[bytes] = []
    read = 0
    with zf.open(zi) as fp:
        while True:
            chunk = fp.read(_READ_CHUNK)
            if not chunk:
                break
            read += len(chunk)
            if read > MAX_FILE_BYTES:
                raise SkillImportError(f"单文件超过 {MAX_FILE_BYTES} 字节上限：{name}")
            if read > remaining:
                raise SkillImportError(f"解压总量超过 {MAX_SKILL_BYTES} 字节上限")
            parts.append(chunk)
    return b"".join(parts)


def _zip_entries(data: bytes) -> list[tuple[str, bytes]]:
    """安全解包：路径校验 + 条目类型校验 + 文件数/单文件/总量限制。

    限制全部前置到「读之前」：先用中心目录的 file_size 判定，再分块读，
    避免压缩炸弹先把成员整体解压进内存才触发检查。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_FILES:
                raise SkillImportError(f"文件数超过 {MAX_FILES} 上限")
            entries: list[tuple[str, bytes]] = []
            total = 0
            for zi in infos:
                name = _assert_safe_rel_path(zi.filename)
                _check_zip_entry(zi)
                if zi.is_dir():
                    continue
                if zi.file_size > MAX_FILE_BYTES:
                    raise SkillImportError(f"单文件超过 {MAX_FILE_BYTES} 字节上限：{name}")
                if total + zi.file_size > MAX_SKILL_BYTES:
                    raise SkillImportError(f"解压总量超过 {MAX_SKILL_BYTES} 字节上限")
                blob = _read_entry_capped(zf, zi, name, MAX_SKILL_BYTES - total)
                total += len(blob)
                entries.append((name, blob))
            return entries
    except _UNPACK_ERRORS as exc:
        raise SkillImportError("zip 文件无法解析（已损坏、加密或压缩算法不支持）") from exc


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


def _ordered_roots(text_entries: list[tuple[str, str]]) -> list[str]:
    """与 _group_entries 结果同序的 SKILL.md 根目录清单。"""
    roots: list[str] = []
    for name, _ in text_entries:
        parent = PurePosixPath(name).parent
        if PurePosixPath(name).name == "SKILL.md":
            roots.append(str(parent) if str(parent) != "." else "")
    return roots


def _assign_skipped(groups: list[dict[str, Any]], roots: list[str], skipped: list[str]) -> None:
    """把二进制文件名按「最长前缀 skill 根目录」归到对应组的 skipped_binary。"""
    for name in skipped:
        best, best_len = -1, -1
        for idx, root in enumerate(roots):
            prefix = f"{root}/" if root else ""
            if name.startswith(prefix) and len(root) > best_len:
                best, best_len = idx, len(root)
        if best >= 0:
            groups[best]["skipped_binary"].append(name)


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


def parse_zip(data: bytes) -> list[dict[str, Any]]:
    """解析 zip：入口限流 → 安全过滤 → 按 SKILL.md 分组 → 解析 → 二进制标注。"""
    if len(data) > MAX_IMPORT_BYTES:
        raise SkillImportError(f"zip 超过 {MAX_IMPORT_BYTES} 字节上限")
    raw_entries = _zip_entries(data)
    text_entries, script_entries, skipped = _split_entries(raw_entries)
    return _build_groups(text_entries, script_entries, skipped)


def parse_zip_base64(zip_b64: str) -> list[dict[str, Any]]:
    """base64 编码的 zip（前端以 JSON 提交）→ skill 组清单。

    容错：忽略空白；其余非法字符 / 长度一律报「解码失败」（解析入口另有总量限流）。
    """
    try:
        data = base64.b64decode("".join(zip_b64.split()), validate=True)
    except ValueError as exc:  # binascii.Error 是 ValueError 子类
        raise SkillImportError("zip base64 解码失败") from exc
    return parse_zip(data)


def parse_dir_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """浏览器目录上传（[{path, content}] 文本数组）→ skill 组清单。

    与 zip 来源同口径限流：总条目数 MAX_FILES / 单条目 MAX_FILE_BYTES / 总量 MAX_SKILL_BYTES。
    """
    if len(files) > MAX_FILES:
        raise SkillImportError(f"文件数超过 {MAX_FILES} 上限")
    entries: list[tuple[str, bytes]] = []
    total = 0
    for item in files:
        name = _assert_safe_rel_path(str(item.get("path") or ""))
        blob = str(item.get("content") or "").encode("utf-8")
        if len(blob) > MAX_FILE_BYTES:
            raise SkillImportError(f"单文件超过 {MAX_FILE_BYTES} 字节上限：{name}")
        total += len(blob)
        if total > MAX_SKILL_BYTES:
            raise SkillImportError(f"导入总量超过 {MAX_SKILL_BYTES} 字节上限")
        entries.append((name, blob))
    text_entries, script_entries, skipped = _split_entries(entries)
    return _build_groups(text_entries, script_entries, skipped)


def _assert_public_host(host: str) -> None:
    """DNS 解析后逐 IP 校验；private / loopback / link-local / reserved /
    multicast / unspecified 一律拒绝（SSRF 防护）。

    已知残留风险：只在「解析期」拦截，校验与 httpx 实际建连之间仍有 DNS 重绑定窗口
    （规格 §12 列为可接受风险，v1 不处理）。
    """
    if not host:
        raise SkillImportError("URL 缺少主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise SkillImportError(f"无法解析主机：{host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            # ::ffff:127.0.0.1 之类映射地址按 IPv4 口径判定：其 is_loopback 恒为 False
            ip = ip.ipv4_mapped
        if any(
            (
                ip.is_private,
                ip.is_loopback,
                ip.is_link_local,
                ip.is_reserved,
                ip.is_multicast,
                ip.is_unspecified,
            )
        ):
            raise SkillImportError(f"URL 指向内网地址，已拒绝：{host}")


async def fetch_url(
    url: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> bytes:
    """抓取 URL（raw 单文件或 zip）；每跳重定向重新校验；`transport` 仅供测试注入。

    SSRF 防护仅做「解析期拦截」：`_assert_public_host` 先解析再校验，随后 httpx 自行建连，
    两者之间的 DNS 重绑定窗口未消除（规格 §12 列为可接受残留风险，v1 不处理）。
    """
    if urlparse(url).scheme not in ("http", "https"):
        raise SkillImportError("URL 仅支持 http(s)")
    current = url
    for _ in range(URL_MAX_REDIRECTS + 1):
        host = urlparse(current).hostname or ""
        _assert_public_host(host)
        async with httpx.AsyncClient(
            timeout=URL_FETCH_TIMEOUT, follow_redirects=False, transport=transport
        ) as client:
            try:
                resp = await client.get(current)
            except httpx.HTTPError as exc:
                raise SkillImportError(f"URL 抓取失败：{type(exc).__name__}") from exc
        if resp.is_redirect:
            location = resp.headers.get("location") or ""
            if not location:
                raise SkillImportError("重定向缺少 Location")
            current = urljoin(current, location)
            continue
        if not 200 <= resp.status_code < 300:
            raise SkillImportError(f"URL 返回非 2xx 状态：{resp.status_code}")
        if len(resp.content) > MAX_IMPORT_BYTES:
            raise SkillImportError("响应体超过 4MB 上限")
        return resp.content
    raise SkillImportError(f"重定向超过 {URL_MAX_REDIRECTS} 跳上限")


# ---------------------------------------------------------------------------
# 编辑提交的附件全量校验（与导入同口径；规格 §6.1）
# ---------------------------------------------------------------------------
def normalize_file_entries(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """编辑提交的附件全量规范化与校验。

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
                {
                    "path": path,
                    "size": size,
                    "content": text,
                    "entry_type": "text",
                    "encoding": "utf-8",
                }
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
