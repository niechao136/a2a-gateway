"""A2A 客户端单元测试：错误分类、瞬时错误重试、不重复输出。"""

import httpx
import pytest

from a2a_gateway import a2a_client
from a2a_gateway.a2a_client import A2AClientWrapper, A2ATargetError
from a2a_gateway.schemas import A2ATarget


class FakeClient:
    """最小假客户端：send_message 立即结束（不产出内容）。"""

    def send_message(self, request):
        async def gen():
            return
            yield  # pragma: no cover

        return gen()


class ChunkedClient:
    """先产出一个片段再抛异常，用于验证「已产出内容不重试」。"""

    def send_message(self, request):
        async def gen():
            yield "partial"
            raise httpx.ReadError("stream broken")

        return gen()


def _wrapper() -> A2AClientWrapper:
    return A2AClientWrapper(A2ATarget(url="http://example.invalid/"))


async def test_classify_maps_error_kinds():
    assert A2AClientWrapper._classify(A2ATargetError("timeout", "t")).kind == "timeout"
    assert A2AClientWrapper._classify(httpx.ConnectTimeout("boom")).kind == "timeout"
    assert A2AClientWrapper._classify(httpx.ConnectError("boom")).kind == "network"
    assert A2AClientWrapper._classify(httpx.ReadError("boom")).kind == "network"
    assert A2AClientWrapper._classify(RuntimeError("x")).kind == "target_error"


async def test_retries_transient_errors_then_succeeds():
    wrapper = _wrapper()
    state = {"n": 0}

    async def flaky_ensure():
        state["n"] += 1
        if state["n"] < 3:
            raise httpx.ConnectError("boom")
        return FakeClient()

    wrapper._ensure_client = flaky_ensure  # type: ignore[method-assign]
    out = [c async for c in wrapper.stream_message("hi", backoff=0.01)]

    assert state["n"] == 3
    assert out == []


async def test_does_not_retry_target_error():
    wrapper = _wrapper()
    state = {"n": 0}

    async def bad_ensure():
        state["n"] += 1
        raise A2ATargetError("target_error", "internal failure")

    wrapper._ensure_client = bad_ensure  # type: ignore[method-assign]

    with pytest.raises(A2ATargetError) as excinfo:
        [c async for c in wrapper.stream_message("hi", backoff=0.01)]

    assert excinfo.value.kind == "target_error"
    assert state["n"] == 1


async def test_gives_up_after_retries_exhausted():
    wrapper = _wrapper()
    state = {"n": 0}

    async def always_fail():
        state["n"] += 1
        raise httpx.ConnectTimeout("timeout")

    wrapper._ensure_client = always_fail  # type: ignore[method-assign]

    with pytest.raises(A2ATargetError) as excinfo:
        [c async for c in wrapper.stream_message("hi", retries=2, backoff=0.01)]

    assert excinfo.value.kind == "timeout"
    assert state["n"] == 3  # 1 次原始 + 2 次重试


async def test_does_not_retry_after_partial_output(monkeypatch):
    # 让假客户端产出的字符串能被文本提取函数识别
    monkeypatch.setattr(
        a2a_client,
        "get_stream_response_text",
        lambda response: response if isinstance(response, str) else "",
    )

    wrapper = _wrapper()
    state = {"n": 0}

    async def ensure():
        state["n"] += 1
        return ChunkedClient()

    wrapper._ensure_client = ensure  # type: ignore[method-assign]

    received = []
    with pytest.raises(A2ATargetError):
        async for chunk in wrapper.stream_message("hi", backoff=0.01):
            received.append(chunk)

    assert received == ["partial"]
    assert state["n"] == 1  # 已有产出，不再重试


async def test_test_connection_reports_failure():
    wrapper = _wrapper()

    async def bad_ensure():
        raise A2ATargetError("network", "unreachable")

    wrapper._ensure_client = bad_ensure  # type: ignore[method-assign]
    ok, message = await wrapper.test_connection()

    assert ok is False
    assert "unreachable" in message
