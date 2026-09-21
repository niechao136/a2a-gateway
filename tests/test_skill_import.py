"""skill_import 来源解析与安全校验测试（全部离线）。"""

import base64
import io
import zipfile
import zlib

import httpx
import pytest

from a2a_gateway import skill_import as si
from a2a_gateway.skill_import import SkillImportError

SKILL_MD = "---\nname: {name}\ndescription: 测试技能\n---\n正文"


def _zip_bytes(
    entries: list[tuple[str, bytes]], compression: int = zipfile.ZIP_STORED
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
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


def _encrypted_zip_bytes() -> bytes:
    """zipfile 不能写加密包，故把普通 zip 的「已加密」标志位置 1 复现真实场景。"""
    data = bytearray(_zip_bytes([("s/SKILL.md", SKILL_MD.format(name="s").encode())]))
    data[data.find(b"PK\x01\x02") + 8] |= 0x01  # 中心目录 general purpose bit flag
    data[data.find(b"PK\x03\x04") + 6] |= 0x01  # 本地文件头同名标志位
    return bytes(data)


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


def test_parse_zip_base64_invalid_rejected():
    with pytest.raises(SkillImportError, match="base64"):
        si.parse_zip_base64("not-a-valid-base64!!")


def test_parse_zip_base64_tolerates_whitespace():
    b64 = base64.b64encode(
        _zip_bytes([("s/SKILL.md", SKILL_MD.format(name="s").encode())])
    ).decode()
    wrapped = "\n".join(b64[i : i + 76] for i in range(0, len(b64), 76))
    assert si.parse_zip_base64(wrapped)[0]["name"] == "s"


def test_parse_zip_rejects_oversize_payload():
    """入口限流：任何调用方直接喂超大 zip 也要被拦。"""
    with pytest.raises(SkillImportError, match="上限"):
        si.parse_zip(b"\x00" * (si.MAX_IMPORT_BYTES + 1))


def test_zip_too_many_entries_rejected():
    entries = [("s/SKILL.md", SKILL_MD.format(name="s").encode())] + [
        (f"s/f{i}.md", b"x") for i in range(si.MAX_FILES)
    ]
    with pytest.raises(SkillImportError, match="上限"):
        si.parse_zip(_zip_bytes(entries))


def test_zip_total_bytes_rejected():
    blob = b"\x00" * si.MAX_FILE_BYTES
    data = _zip_bytes(
        [("s/SKILL.md", SKILL_MD.format(name="s").encode())]
        + [(f"s/big{i}.md", blob) for i in range(5)],
        compression=zipfile.ZIP_DEFLATED,
    )
    with pytest.raises(SkillImportError, match="上限"):
        si.parse_zip(data)


def test_zip_bomb_rejected_before_decompress(monkeypatch):
    """压缩炸弹：按中心目录声明大小先拦截，超限成员绝不解包。"""

    def forbidden(self, *args, **kwargs):
        raise AssertionError("超限成员被解包了")

    data = _zip_bytes(
        [
            ("s/bomb.md", b"\x00" * (si.MAX_FILE_BYTES + 1)),
            ("s/SKILL.md", SKILL_MD.format(name="s").encode()),
        ],
        compression=zipfile.ZIP_DEFLATED,
    )
    monkeypatch.setattr(si.zipfile.ZipFile, "open", forbidden)
    with pytest.raises(SkillImportError, match="上限"):
        si.parse_zip(data)


def test_zip_encrypted_rejected():
    """加密 zip：zipfile.open 抛 RuntimeError，必须转成友好错误而非 500。"""
    with pytest.raises(SkillImportError, match="无法解析"):
        si.parse_zip(_encrypted_zip_bytes())


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("encrypted"),
        NotImplementedError("compression method"),
        EOFError("truncated"),
        zlib.error("bad stream"),
    ],
)
def test_zip_unpack_errors_wrapped(monkeypatch, exc):
    """解包期各类异常一律收敛为 SkillImportError。"""

    def boom(self, *args, **kwargs):
        raise exc

    data = _zip_bytes([("s/SKILL.md", SKILL_MD.format(name="s").encode())])
    monkeypatch.setattr(si.zipfile.ZipFile, "open", boom)
    with pytest.raises(SkillImportError, match="无法解析"):
        si.parse_zip(data)


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


def test_dir_real_binary_skipped():
    """真实二进制（含 NUL）应被跳过并记入 skipped_binary。"""
    groups = si.parse_dir_files(
        [
            {"path": "d/SKILL.md", "content": SKILL_MD.format(name="d")},
            {"path": "d/logo.png", "content": "\x89PNG\r\n\x1a\n\x00\x01\x02"},
            {"path": "d/notes.md", "content": "纯文本"},
        ]
    )
    assert [f["path"] for f in groups[0]["files"]] == ["notes.md"]
    assert groups[0]["skipped_binary"] == ["d/logo.png"]


def test_dir_empty_path_rejected():
    with pytest.raises(SkillImportError, match="path"):
        si.parse_dir_files(
            [
                {"path": "d/SKILL.md", "content": SKILL_MD.format(name="d")},
                {"path": "", "content": "x"},
            ]
        )


def test_dir_too_many_entries_rejected():
    files = [{"path": "d/SKILL.md", "content": SKILL_MD.format(name="d")}] + [
        {"path": f"d/f{i}.md", "content": "x"} for i in range(si.MAX_FILES)
    ]
    with pytest.raises(SkillImportError, match="上限"):
        si.parse_dir_files(files)


def test_dir_oversize_entry_rejected():
    files = [
        {"path": "d/SKILL.md", "content": SKILL_MD.format(name="d")},
        {"path": "d/big.md", "content": "b" * (si.MAX_FILE_BYTES + 1)},
    ]
    with pytest.raises(SkillImportError, match="上限"):
        si.parse_dir_files(files)


def test_dir_total_bytes_rejected():
    blob = "b" * si.MAX_FILE_BYTES
    files = [{"path": "d/SKILL.md", "content": SKILL_MD.format(name="d")}] + [
        {"path": f"d/big{i}.md", "content": blob} for i in range(5)
    ]
    with pytest.raises(SkillImportError, match="上限"):
        si.parse_dir_files(files)


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


async def test_fetch_url_rejects_ipv4_mapped_loopback(monkeypatch):
    """::ffff:127.0.0.1 等映射地址必须按 IPv4 口径判定（is_loopback 对映射地址恒为 False）。"""
    monkeypatch.setattr(
        si.socket,
        "getaddrinfo",
        lambda *a, **k: [(10, 1, 6, "", ("::ffff:127.0.0.1", 0, 0, 0))],
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


# ---------------------------------------------------------------------------
# 脚本附件入库（entry_type=script，base64 编码；规格 §5）
# ---------------------------------------------------------------------------
def test_zip_ingests_scripts():
    data = _zip_bytes(
        [
            ("s/SKILL.md", SKILL_MD.format(name="s").encode()),
            ("s/scripts/gen.py", b"print('hi')\n"),
            ("s/references/a.md", "文本".encode()),
        ]
    )
    groups = si.parse_zip(data)
    entries = {f["path"]: f for f in groups[0]["files"]}
    script = entries["scripts/gen.py"]
    assert script["entry_type"] == "script"
    assert script["encoding"] == "base64"
    assert base64.b64decode(script["content"]) == b"print('hi')\n"
    assert script["size"] == len(b"print('hi')\n")
    assert entries["references/a.md"]["entry_type"] == "text"
    assert entries["references/a.md"]["encoding"] == "utf-8"
    assert groups[0]["skipped_binary"] == []


def test_zip_skips_oversized_script(monkeypatch):
    monkeypatch.setattr(si, "MAX_SCRIPT_BYTES", 4)
    data = _zip_bytes(
        [
            ("s/SKILL.md", SKILL_MD.format(name="s").encode()),
            ("s/scripts/big.py", b"print(123456)\n"),
        ]
    )
    groups = si.parse_zip(data)
    # 超限脚本不降级为文本附件，直接标注跳过
    assert groups[0]["files"] == []
    assert groups[0]["skipped_binary"] == ["s/scripts/big.py"]


def test_zip_non_script_binary_still_skipped():
    png = b"\x89PNG\r\n\x1a\n\x00\x00"
    data = _zip_bytes(
        [
            ("s/SKILL.md", SKILL_MD.format(name="s").encode()),
            ("s/assets/logo.png", png),
        ]
    )
    groups = si.parse_zip(data)
    assert groups[0]["files"] == []
    assert groups[0]["skipped_binary"] == ["s/assets/logo.png"]


def test_zip_script_suffix_case_insensitive():
    data = _zip_bytes(
        [
            ("s/SKILL.md", SKILL_MD.format(name="s").encode()),
            ("s/run.PY", b"print(1)\n"),
        ]
    )
    groups = si.parse_zip(data)
    assert groups[0]["files"][0]["entry_type"] == "script"


def test_dir_files_ingests_scripts():
    groups = si.parse_dir_files(
        [
            {"path": "s/SKILL.md", "content": SKILL_MD.format(name="s")},
            {"path": "s/scripts/gen.py", "content": "print('hi')\n"},
        ]
    )
    assert groups[0]["files"][0]["entry_type"] == "script"
    assert groups[0]["files"][0]["encoding"] == "base64"
