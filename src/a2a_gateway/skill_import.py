"""Skill 导入管线：zip / 目录 / URL 来源的解析与安全校验。

统一产出 [{"name", "description", "content", "frontmatter", "size_bytes",
            "files": [{"path","size","content"}], "skipped_binary": [str]}]，
解析与字段校验复用 skills.parse_skill_md；粘贴（text）来源由路由层直接调 parse_skill_md。

安全要求（规格 §4.2）：zip slip 拒绝、符号链接拒绝、压缩炸弹限制、
目录路径规范化、SSRF 逐 IP 校验（DNS 解析后）、响应体 4MB 上限。后端零文件系统访问。
"""

import base64
import io
import ipaddress
import posixpath
import socket
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .skills import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_IMPORT_BYTES,
    MAX_SKILL_BYTES,
    URL_FETCH_TIMEOUT,
    URL_MAX_REDIRECTS,
    parse_skill_md,
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
    """规范化相对路径；绝对路径 / 含 .. / 反斜杠一律拒绝（zip slip / 目录穿越）。"""
    path = raw.replace("\\", "/")
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
            }
            for name, text in entries
            if name.startswith(prefix) and name != f"{prefix}SKILL.md"
        ]
        results.append({**parsed, "files": files, "skipped_binary": []})
    return results


def _zip_entries(data: bytes) -> list[tuple[str, bytes]]:
    """安全解包：路径校验 + 条目类型校验 + 文件数/单文件/总量限制。"""
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
                blob = zf.read(zi)
                total += len(blob)
                if total > MAX_SKILL_BYTES:
                    raise SkillImportError(f"解压总量超过 {MAX_SKILL_BYTES} 字节上限")
                if len(blob) > MAX_FILE_BYTES:
                    raise SkillImportError(f"单文件超过 {MAX_FILE_BYTES} 字节上限：{name}")
                entries.append((name, blob))
            return entries
    except zipfile.BadZipFile as exc:
        raise SkillImportError("zip 文件无法解析") from exc


def _split_binary(
    entries: list[tuple[str, bytes]],
) -> tuple[list[tuple[str, str]], list[str]]:
    """拆分为 (文本条目, 被跳过的二进制文件名)；解码失败不抛错，只标注后跳过。"""
    text_entries: list[tuple[str, str]] = []
    skipped: list[str] = []
    for name, blob in entries:
        if is_text_blob(blob):
            text_entries.append((name, blob.decode("utf-8")))
        else:
            skipped.append(name)
    return text_entries, skipped


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


def _build_groups(text_entries: list[tuple[str, str]], skipped: list[str]) -> list[dict[str, Any]]:
    """分组 → 解析 → 标注跳过的二进制附件。"""
    groups = _group_entries(text_entries)
    _assign_skipped(groups, _ordered_roots(text_entries), skipped)
    return groups


def parse_zip(data: bytes) -> list[dict[str, Any]]:
    """解析 zip：安全过滤 → 按 SKILL.md 分组 → 解析 → 二进制标注。"""
    raw_entries = _zip_entries(data)
    text_entries, skipped = _split_binary(raw_entries)
    return _build_groups(text_entries, skipped)


def parse_zip_base64(zip_b64: str) -> list[dict[str, Any]]:
    """base64 编码的 zip（前端以 JSON 提交）→ skill 组清单。"""
    try:
        data = base64.b64decode(zip_b64, validate=False)
    except Exception as exc:
        raise SkillImportError("zip base64 解码失败") from exc
    if len(data) > MAX_IMPORT_BYTES:
        raise SkillImportError(f"zip 超过 {MAX_IMPORT_BYTES} 字节上限")
    return parse_zip(data)


def parse_dir_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """浏览器目录上传（[{path, content}] 文本数组）→ skill 组清单。"""
    entries: list[tuple[str, bytes]] = []
    for item in files:
        name = _assert_safe_rel_path(str(item.get("path") or ""))
        entries.append((name, str(item.get("content") or "").encode("utf-8")))
    text_entries, skipped = _split_binary(entries)
    return _build_groups(text_entries, skipped)


def _assert_public_host(host: str) -> None:
    """DNS 解析后逐 IP 校验；private / loopback / link-local / reserved /
    multicast / unspecified 一律拒绝（SSRF 防护，含 DNS 重绑定缓解）。"""
    if not host:
        raise SkillImportError("URL 缺少主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise SkillImportError(f"无法解析主机：{host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
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
    """抓取 URL（raw 单文件或 zip）；每跳重定向重新校验；`transport` 仅供测试注入。"""
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
