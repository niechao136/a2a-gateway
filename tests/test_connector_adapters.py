"""平台适配器测试：公共工具 + 各平台验签/归一化/过滤/分段。"""

import hashlib
import hmac
import json
import time

import pytest

from a2a_gateway.connectors.base import VerifyError, chunk_text


# ---------------------------------------------------------------------------
# chunk_text（公共分段）
# ---------------------------------------------------------------------------
def test_chunk_text_short():
    assert chunk_text("你好", 100) == ["你好"]


def test_chunk_text_empty():
    assert chunk_text("   ", 100) == []


def test_chunk_text_splits_at_limit():
    text = "x" * 250
    chunks = chunk_text(text, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_chunk_text_prefers_newline():
    text = "a" * 60 + "\n" + "b" * 60
    chunks = chunk_text(text, 100)
    assert chunks[0] == "a" * 60
    assert chunks[1] == "b" * 60


def test_verify_error_exists():
    with pytest.raises(VerifyError):
        raise VerifyError("bad signature")
