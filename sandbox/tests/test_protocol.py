"""协议纯函数用例（跨平台，无子进程）。"""

import base64

import pytest

from sandbox.protocol import (
    ProtocolError,
    RunFile,
    assert_safe_rel_path,
    clamp_timeout,
    decode_files,
    resolve_interpreter,
    truncate_output,
)


def test_assert_safe_rel_path_rejects_escape():
    with pytest.raises(ProtocolError):
        assert_safe_rel_path("../escape.md")
    with pytest.raises(ProtocolError):
        assert_safe_rel_path("/abs.md")
    with pytest.raises(ProtocolError):
        assert_safe_rel_path("  ")
    assert assert_safe_rel_path("scripts/gen.py") == "scripts/gen.py"
    # 反斜杠归一化为 posix 分隔符（与 backend _assert_safe_rel_path 代码行为同口径）
    assert assert_safe_rel_path("a\\b.md") == "a/b.md"


def test_resolve_interpreter_whitelist():
    assert resolve_interpreter("main.py") == ["python3"]
    assert resolve_interpreter("RUN.SH") == ["bash"]
    assert resolve_interpreter("x/tool.JS") == ["node"]
    with pytest.raises(ProtocolError):
        resolve_interpreter("noext")
    with pytest.raises(ProtocolError):
        resolve_interpreter("bin/run.exe")


def test_decode_files_caps():
    files = [
        RunFile(path="a.py", content="print(1)"),
        RunFile(path="d/b.bin", encoding="base64", content=base64.b64encode(b"\x00\x01").decode()),
    ]
    decoded = decode_files(files)
    assert decoded[0][1] == b"print(1)"
    assert decoded[1][1] == b"\x00\x01"
    with pytest.raises(ProtocolError):
        decode_files([RunFile(path="x.py", encoding="base64", content="!!!")])  # 非法 base64
    with pytest.raises(ProtocolError):
        decode_files([RunFile(path=f"f{i}.py", content="x") for i in range(101)])


def test_clamp_timeout():
    assert clamp_timeout(0) == 1
    assert clamp_timeout(30) == 30
    assert clamp_timeout(999) == 120


def test_truncate_output():
    text, truncated = truncate_output("短输出".encode())
    assert text == "短输出" and not truncated
    big = "x" * 40000
    text, truncated = truncate_output(big.encode())
    assert truncated and len(text) == 32768
    # 非法 UTF-8 容错解码不抛错
    text, truncated = truncate_output(b"\xff\xfe")
    assert not truncated
