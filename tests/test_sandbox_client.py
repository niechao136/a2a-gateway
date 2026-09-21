"""sandbox_client 测试：MockTransport 注入，零真实网络；env + cache_clear 控制 Settings。"""

import httpx
import pytest

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
        return httpx.Response(
            200,
            json={
                "exit_code": 0,
                "stdout": "2\n",
                "stderr": "",
                "truncated": False,
                "timeout": False,
                "duration_ms": 5,
            },
        )

    result = await run_script(
        files=[
            {"path": "main.py", "content": "print(1+1)", "encoding": "utf-8", "entry_type": "text"}
        ],
        entry="main.py",
        argv=["--x", "1"],
        stdin="hi",
        timeout_s=45,
        transport=_client(handler),
    )
    assert result["stdout"] == "2\n"
    assert captured["auth"] == "Bearer tok-1"
    assert captured["url"].endswith("/v1/run")
    body = captured["body"].decode()
    assert '"entry":"main.py"' in body
    assert '"argv":["--x","1"]' in body
    assert '"timeout_s":45' in body  # 合法范围内原样透传
    assert '"stdin_b64":"' in body


async def test_error_classification(sandbox_env):
    def _status(code: int):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(code, json={"detail": "x"})

        return handler

    with pytest.raises(SandboxTimeout):
        await run_script(files=[], entry="a.py", transport=_client(_status(504)))
    with pytest.raises(SandboxRejected):
        await run_script(files=[], entry="a.py", transport=_client(_status(401)))
    with pytest.raises(SandboxRejected):
        await run_script(files=[], entry="a.py", transport=_client(_status(500)))

    def _network(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(SandboxUnavailable):
        await run_script(files=[], entry="a.py", transport=_client(_network))


def test_build_run_files_defaults():
    out = build_run_files(
        [
            {"path": "a.md", "content": "文本"},
            {"path": "s.py", "content": "QQ==", "encoding": "base64"},
        ]
    )
    assert out[0] == {"path": "a.md", "encoding": "utf-8", "content": "文本"}
    assert out[1] == {"path": "s.py", "encoding": "base64", "content": "QQ=="}
