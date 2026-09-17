"""A2A 客户端单元测试：错误分类、瞬时错误重试、不重复输出、接口地址改写、文本提取。"""

from typing import Any

import httpx
import pytest
from a2a.helpers.proto_helpers import new_text_message
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import StreamResponse

from a2a_gateway import a2a_client
from a2a_gateway.a2a_client import (
    A2AClientWrapper,
    A2ATargetError,
    _merge_interface_url,
    _origin_url,
)
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
            yield StreamResponse(message=new_text_message("partial"))
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


async def test_does_not_retry_after_partial_output():
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


# ---------------------------------------------------------------------------
# 接口地址改写：保留卡片声明的 RPC 路径（修复「POST 到根路径 404」）
# ---------------------------------------------------------------------------
def test_merge_interface_url_keeps_card_rpc_path():
    """目标只填基础地址时，保留卡片声明的 RPC 路径。"""
    assert (
        _merge_interface_url("http://localhost:9901/a2a", "http://43.156.187.79:10101")
        == "http://43.156.187.79:10101/a2a"
    )


def test_merge_interface_url_prefers_explicit_target_path():
    """目标显式带路径时视为端点地址，原样使用。"""
    assert (
        _merge_interface_url("http://localhost:9901/a2a", "http://host:10101/custom")
        == "http://host:10101/custom"
    )


def test_merge_interface_url_keeps_query_auth():
    """基础地址上挂着 query 鉴权参数时不能丢。"""
    assert (
        _merge_interface_url("http://localhost:9901/a2a", "http://host:10101?access_token=tk")
        == "http://host:10101/a2a?access_token=tk"
    )


def test_origin_url_strips_path_and_keeps_query():
    assert _origin_url("http://host:10101/a2a") == "http://host:10101"
    assert (
        _origin_url("http://host:10101/a2a?access_token=tk")
        == "http://host:10101?access_token=tk"
    )
    assert _origin_url("http://host:10101") == "http://host:10101"
    assert _origin_url("http://host:10101/") == "http://host:10101/"


class _Iface:
    def __init__(self, url: str):
        self.url = url


class _Card:
    def __init__(self, iface_url: str):
        self.supported_interfaces = [_Iface(iface_url)]


def _not_found(url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        "404 Not Found", request=request, response=httpx.Response(404, request=request)
    )


def _patch_card_stack(monkeypatch, resolver_cls) -> dict[str, Any]:
    """把 A2ACardResolver 换成假实现，并记录 ClientFactory 收到的卡片。"""
    captured: dict[str, Any] = {}

    class Factory:
        def __init__(self, config):
            pass

        def create(self, card):
            captured["card"] = card
            return FakeClient()

    monkeypatch.setattr(a2a_client, "A2ACardResolver", resolver_cls)
    monkeypatch.setattr(a2a_client, "ClientFactory", Factory)
    return captured


async def test_ensure_client_keeps_card_path_when_target_has_none(monkeypatch):
    """线上 404 的复现：卡片声明 /a2a，目标只填 origin，不能被改写成根路径。"""
    calls: list[str] = []

    class Resolver:
        def __init__(self, http, url):
            self.url = url

        async def get_agent_card(self):
            calls.append(self.url)
            return _Card("http://localhost:9901/a2a")

    captured = _patch_card_stack(monkeypatch, Resolver)
    wrapper = A2AClientWrapper(A2ATarget(url="http://43.156.187.79:10101"))

    await wrapper._ensure_client()

    assert calls == ["http://43.156.187.79:10101"]
    assert captured["card"].supported_interfaces[0].url == "http://43.156.187.79:10101/a2a"
    await wrapper.close()


async def test_ensure_client_falls_back_to_origin_when_target_has_path(monkeypatch):
    """目标误填成 RPC 端点（带路径）时，卡片解析回退到 origin。"""
    calls: list[str] = []

    class Resolver:
        def __init__(self, http, url):
            self.url = url

        async def get_agent_card(self):
            calls.append(self.url)
            if self.url != "http://43.156.187.79:10101":
                raise _not_found(self.url)
            return _Card("http://43.156.187.79:10101/a2a")

    captured = _patch_card_stack(monkeypatch, Resolver)
    wrapper = A2AClientWrapper(A2ATarget(url="http://43.156.187.79:10101/a2a"))

    await wrapper._ensure_client()

    assert calls == ["http://43.156.187.79:10101/a2a", "http://43.156.187.79:10101"]
    assert captured["card"].supported_interfaces[0].url == "http://43.156.187.79:10101/a2a"
    await wrapper.close()


async def test_ensure_client_reports_network_error_when_card_unreachable(monkeypatch):
    """回退后仍解析失败 → 归类为 network（配置/网络问题），而不是 target_error。"""

    class Resolver:
        def __init__(self, http, url):
            self.url = url

        async def get_agent_card(self):
            raise _not_found(self.url)

    _patch_card_stack(monkeypatch, Resolver)
    wrapper = A2AClientWrapper(A2ATarget(url="http://host:10101/a2a"))

    with pytest.raises(A2ATargetError) as excinfo:
        await wrapper._ensure_client()

    assert excinfo.value.kind == "network"


# ---------------------------------------------------------------------------
# 响应文本提取：task 快照里的追问 / 结果内容不能丢
# ---------------------------------------------------------------------------
async def _collect(response: StreamResponse) -> list[str]:
    return [chunk async for chunk in A2AClientWrapper._extract(response)]


def _task_response(
    *, state: int, message_text: str | None = None, artifact_text: str | None = None
) -> StreamResponse:
    task = a2a_pb2.Task(id="t-1", context_id="c-1")
    task.status.state = state
    if message_text is not None:
        task.status.message.CopyFrom(new_text_message(message_text))
    if artifact_text is not None:
        artifact = task.artifacts.add()
        artifact.name = "itinerary"
        artifact.parts.add().text = artifact_text
    return StreamResponse(task=task)


async def test_extract_reads_input_required_message_from_task():
    """线上实测形态：追问文本在 task.status.message，不能被丢弃。"""
    resp = _task_response(state=a2a_pb2.TASK_STATE_INPUT_REQUIRED, message_text="请补充目的地")

    assert await _collect(resp) == ["请补充目的地"]


async def test_extract_reads_completed_task_message_and_artifacts():
    resp = _task_response(
        state=a2a_pb2.TASK_STATE_COMPLETED,
        message_text="行程方案已生成完毕。",
        artifact_text="D1：西湖",
    )

    assert await _collect(resp) == ["行程方案已生成完毕。", "D1：西湖"]


async def test_extract_keeps_status_and_artifact_update_paths():
    status_resp = StreamResponse()
    status_resp.status_update.status.message.CopyFrom(new_text_message("处理中"))
    assert await _collect(status_resp) == ["处理中"]

    artifact_resp = StreamResponse()
    artifact_resp.artifact_update.artifact.parts.add().text = "行程"
    assert await _collect(artifact_resp) == ["行程"]
