"""skill_import 来源解析与安全校验测试（全部离线）。"""

import base64
import io
import zipfile

import httpx
import pytest

from a2a_gateway import skill_import as si
from a2a_gateway.skill_import import SkillImportError

SKILL_MD = "---\nname: {name}\ndescription: 测试技能\n---\n正文"


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, blob in entries:
            zf.writestr(name, blob)
    return buf.getvalue()


def _symlink_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("evil-skill/link.md")
        info.create_system = 3  # unix
        info.external_attr = (0o120777 << 16)  # S_IFLNK
        zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# zip
# ---------------------------------------------------------------------------
def test_zip_multi_skill_groups():
    data = _zip_bytes(
        [
            ("a/SKILL.md", SKILL_MD.format(name="skill-a").encode()),
            ("a/references/x.md", "附件A".encode()),
            ("b/SKILL.md", SKILL_MD.format(name="skill-b").encode()),
        ]
    )
    groups = si.parse_zip(data)
    by_name = {g["name"]: g for g in groups}
    assert set(by_name) == {"skill-a", "skill-b"}
    assert by_name["skill-a"]["files"][0]["path"] == "references/x.md"


def test_zip_without_skill_md_rejected():
    with pytest.raises(SkillImportError, match="SKILL.md"):
        si.parse_zip(_zip_bytes([("readme.md", b"x")]))


def test_zip_slip_rejected():
    data = _zip_bytes([("../evil/SKILL.md", SKILL_MD.format(name="evil").encode())])
    with pytest.raises(SkillImportError, match="路径非法"):
        si.parse_zip(data)


def test_zip_absolute_path_rejected():
    data = _zip_bytes([("/etc/SKILL.md", SKILL_MD.format(name="evil").encode())])
    with pytest.raises(SkillImportError, match="路径非法"):
        si.parse_zip(data)


def test_zip_symlink_rejected():
    with pytest.raises(SkillImportError, match="非常规"):
        si.parse_zip(_symlink_zip_bytes())


def test_zip_oversize_entry_rejected():
    big = ("b" * (si.MAX_FILE_BYTES + 1)).encode()
    data = _zip_bytes(
        [("s/SKILL.md", SKILL_MD.format(name="s").encode()), ("s/big.md", big)]
    )
    with pytest.raises(SkillImportError, match="上限"):
        si.parse_zip(data)


def test_zip_binary_attachment_skipped_and_noted():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02"
    data = _zip_bytes(
        [
            ("s/SKILL.md", SKILL_MD.format(name="s").encode()),
            ("s/assets/logo.png", png),
            ("s/notes.md", "纯文本".encode()),
        ]
    )
    groups = si.parse_zip(data)
    assert [f["path"] for f in groups[0]["files"]] == ["notes.md"]
    assert groups[0]["skipped_binary"] == ["s/assets/logo.png"]


def test_parse_base64_zip():
    data = _zip_bytes([("s/SKILL.md", SKILL_MD.format(name="s").encode())])
    groups = si.parse_zip_base64(base64.b64encode(data).decode())
    assert groups[0]["name"] == "s"


# ---------------------------------------------------------------------------
# 目录（浏览器 webkitdirectory 上传的文件数组）
# ---------------------------------------------------------------------------
def test_dir_files_grouped():
    files = [
        {"path": "demo/SKILL.md", "content": SKILL_MD.format(name="demo")},
        {"path": "demo/references/a.md", "content": "附件内容"},
    ]
    groups = si.parse_dir_files(files)
    assert groups[0]["name"] == "demo"
    assert groups[0]["files"][0]["path"] == "references/a.md"


def test_dir_dots_rejected():
    with pytest.raises(SkillImportError, match="路径非法"):
        si.parse_dir_files([{"path": "../escape/SKILL.md", "content": SKILL_MD}])


def test_dir_without_skill_md_rejected():
    with pytest.raises(SkillImportError, match="SKILL.md"):
        si.parse_dir_files([{"path": "no-md/a.md", "content": "x"}])


def test_dir_normalizes_path():
    groups = si.parse_dir_files(
        [{"path": "d/./sub/../SKILL.md", "content": SKILL_MD.format(name="d")}]
    )
    assert groups[0]["name"] == "d"


def test_dir_binary_skipped():
    groups = si.parse_dir_files(
        [
            {"path": "d/SKILL.md", "content": SKILL_MD.format(name="d")},
            {"path": "d/logo.png", "content": "text-actually"},  # 纯文本，不会被跳过
        ]
    )
    assert groups[0]["skipped_binary"] == []


# ---------------------------------------------------------------------------
# URL（SSRF 防护；MockTransport + monkeypatch getaddrinfo，不发真实请求）
# ---------------------------------------------------------------------------
PUBLIC_DNS = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]  # noqa: E731


def _transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    queue = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(queue)

    return httpx.MockTransport(handler)


async def test_fetch_url_ok(monkeypatch):
    monkeypatch.setattr(si.socket, "getaddrinfo", PUBLIC_DNS)
    body = b"---\nname: u\ndescription: d\n---\n"
    got = await si.fetch_url(
        "https://raw.example.com/s/SKILL.md", transport=_transport([httpx.Response(200, content=body)])
    )
    assert got == body


async def test_fetch_url_rejects_non_http():
    with pytest.raises(SkillImportError, match="http"):
        await si.fetch_url("ftp://example.com/x")


async def test_fetch_url_rejects_private_ip(monkeypatch):
    monkeypatch.setattr(
        si.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 0))]
    )
    with pytest.raises(SkillImportError, match="内网"):
        await si.fetch_url(
            "http://internal.example.com/x", transport=_transport([httpx.Response(200, content=b"x")])
        )


async def test_fetch_url_rejects_loopback(monkeypatch):
    monkeypatch.setattr(
        si.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))]
    )
    with pytest.raises(SkillImportError, match="内网"):
        await si.fetch_url(
            "http://localhost/x", transport=_transport([httpx.Response(200, content=b"x")])
        )


async def test_fetch_url_rejects_metadata_ip(monkeypatch):
    monkeypatch.setattr(
        si.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 0))]
    )
    with pytest.raises(SkillImportError, match="内网"):
        await si.fetch_url(
            "http://metadata.example.org/latest/meta-data/",
            transport=_transport([httpx.Response(200, content=b"x")]),
        )


async def test_fetch_url_rejects_non_2xx(monkeypatch):
    monkeypatch.setattr(si.socket, "getaddrinfo", PUBLIC_DNS)
    with pytest.raises(SkillImportError, match="2xx"):
        await si.fetch_url(
            "http://example.com/x", transport=_transport([httpx.Response(404, content=b"nope")])
        )


async def test_fetch_url_rejects_redirect_loop(monkeypatch):
    monkeypatch.setattr(si.socket, "getaddrinfo", PUBLIC_DNS)
    redirect = httpx.Response(302, headers={"Location": "http://example.com/next"})
    with pytest.raises(SkillImportError, match="重定向"):
        await si.fetch_url(
            "http://example.com/start", transport=_transport([redirect] * 5)
        )


async def test_fetch_url_follows_redirect_then_2xx(monkeypatch):
    monkeypatch.setattr(si.socket, "getaddrinfo", PUBLIC_DNS)
    body = b"---\nname: u\ndescription: d\n---\n"
    got = await si.fetch_url(
        "http://example.com/start",
        transport=_transport(
            [httpx.Response(302, headers={"Location": "/real.md"}), httpx.Response(200, content=body)]
        ),
    )
    assert got == body


async def test_fetch_url_rejects_oversize_body(monkeypatch):
    monkeypatch.setattr(si.socket, "getaddrinfo", PUBLIC_DNS)
    with pytest.raises(SkillImportError, match="4MB"):
        await si.fetch_url(
            "http://example.com/x",
            transport=_transport([httpx.Response(200, content=b"x" * (si.MAX_IMPORT_BYTES + 1))]),
        )
