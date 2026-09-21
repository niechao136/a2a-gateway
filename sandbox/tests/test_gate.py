"""gate 用例：鉴权 / 请求体限流 / 转发（monkeypatch _forward，不依赖真 runner）。

unix socket 相关用例仅 POSIX（open_unix_connection 在 Windows 上不存在，gate 也只在
Linux 容器内运行）；鉴权 / 限流 / 协议用例跨平台。
"""

import sys

import httpx
import pytest

from sandbox import gate
from sandbox.protocol import RunResult

IS_POSIX = sys.platform != "win32"


@pytest.fixture
async def gate_client():
    transport = httpx.ASGITransport(app=gate.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _token(monkeypatch, value="tok-1"):
    monkeypatch.setattr(gate, "SANDBOX_TOKEN", value)


async def test_run_requires_token(gate_client, monkeypatch):
    _token(monkeypatch)
    resp = await gate_client.post("/v1/run", json={"files": [], "entry": "a.py"})
    assert resp.status_code == 401


async def test_run_forwards_to_runner(gate_client, monkeypatch):
    _token(monkeypatch)
    captured = {}

    async def fake_forward(req, timeout_s):
        captured["entry"] = req.entry
        captured["timeout_s"] = timeout_s
        return RunResult(exit_code=0, stdout="ok")

    monkeypatch.setattr(gate, "_forward", fake_forward)
    resp = await gate_client.post(
        "/v1/run",
        json={"files": [{"path": "a.py", "content": "print(1)"}], "entry": "a.py"},
        headers={"Authorization": "Bearer tok-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["stdout"] == "ok"
    assert captured["entry"] == "a.py"
    assert captured["timeout_s"] == 30  # 默认超时


async def test_run_rejects_oversized_body(gate_client, monkeypatch):
    _token(monkeypatch)
    monkeypatch.setattr(gate, "MAX_RUN_BODY_BYTES_LIMIT", 10)  # 测试专用收窄
    resp = await gate_client.post(
        "/v1/run",
        json={"files": [{"path": "a.py", "content": "x" * 100}], "entry": "a.py"},
        headers={"Authorization": "Bearer tok-1"},
    )
    assert resp.status_code == 413


async def test_run_bad_json_400(gate_client, monkeypatch):
    _token(monkeypatch)
    resp = await gate_client.post(
        "/v1/run",
        content=b"{oops",
        headers={"Authorization": "Bearer tok-1", "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.skipif(not IS_POSIX, reason="unix socket 仅 POSIX（gate 只跑在 Linux 容器）")
async def test_forward_unreachable_503(gate_client, monkeypatch, tmp_path):
    _token(monkeypatch)
    monkeypatch.setattr(gate, "RUNNER_SOCKET", str(tmp_path / "missing.sock"))
    resp = await gate_client.post(
        "/v1/run",
        json={"files": [], "entry": "a.py"},
        headers={"Authorization": "Bearer tok-1"},
    )
    assert resp.status_code == 503


@pytest.mark.skipif(not IS_POSIX, reason="unix socket 仅 POSIX（gate 只跑在 Linux 容器）")
async def test_healthz_down(gate_client, monkeypatch, tmp_path):
    monkeypatch.setattr(gate, "RUNNER_SOCKET", str(tmp_path / "missing.sock"))
    resp = await gate_client.get("/healthz")
    assert resp.status_code == 503
