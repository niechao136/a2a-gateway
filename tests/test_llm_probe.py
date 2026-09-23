"""LLM 连通性探针：用 httpx.MockTransport 模拟 OpenAI 兼容 / Anthropic 端点。"""
import json

import httpx

from a2a_gateway.llm_probe import probe_llm


def _mock_transport(handler):
    return httpx.MockTransport(handler)


async def test_openai_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-chat"
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    ok, message = await probe_llm(
        "openai", "https://api.deepseek.com/v1", "sk-test", "deepseek-chat",
        transport=_mock_transport(handler),
    )
    assert ok is True
    assert "pong" in message


async def test_anthropic_success_uses_messages_api():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/messages")
        assert request.headers["x-api-key"] == "sk-ant"
        body = json.loads(request.content)
        assert body["max_tokens"] == 1
        return httpx.Response(200, json={"content": [{"type": "text", "text": "pong"}]})

    ok, message = await probe_llm(
        "anthropic", "", "sk-ant", "claude-sonnet-4-5",
        transport=_mock_transport(handler),
    )
    assert ok is True
    assert "pong" in message


async def test_unauthorized_maps_to_key_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    ok, message = await probe_llm(
        "openai", "https://x/v1", "bad", "m", transport=_mock_transport(handler)
    )
    assert ok is False
    assert "API Key" in message


async def test_not_found_maps_to_url_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    ok, message = await probe_llm(
        "openai", "https://x/wrong", "k", "m", transport=_mock_transport(handler)
    )
    assert ok is False
    assert "base_url" in message


async def test_unknown_model_maps_to_model_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "model not found: nope"}})

    ok, message = await probe_llm(
        "openai", "https://x/v1", "k", "nope", transport=_mock_transport(handler)
    )
    assert ok is False
    assert "模型" in message


async def test_connect_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    ok, message = await probe_llm(
        "openai", "http://127.0.0.1:1/v1", "k", "m", transport=_mock_transport(handler)
    )
    assert ok is False
    assert "无法连接" in message


async def test_unknown_provider_rejected():
    ok, message = await probe_llm("palm", "", "k", "m")
    assert ok is False
    assert "不支持" in message
