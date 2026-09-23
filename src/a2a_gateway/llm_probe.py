"""LLM 连通性探针：向模型发一条极短消息，同时验证 base_url / api_key / model。

设计要点：
- httpx 直调 REST（不依赖 langchain 客户端），provider 分支构造请求
- transport 参数供测试注入 httpx.MockTransport
- 失败信息必须能区分：key 无效 / 模型不存在 / 地址不可达 / 超时
"""

from typing import Any

import httpx

ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
PING_MESSAGE = "ping"


def _summarize_body(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _error_message(status_code: int, body: str) -> str:
    if status_code in (401, 403):
        return f"认证失败（HTTP {status_code}）：API Key 无效或无权限；{_summarize_body(body)}"
    if status_code == 404:
        return (
            f"接口不存在（HTTP 404）：请检查 base_url 是否正确"
            f"（OpenAI 兼容地址通常以 /v1 结尾）；{_summarize_body(body)}"
        )
    if status_code == 400 and ("model" in body.lower()):
        return f"模型不存在或不可用（HTTP 400）：请检查模型标识；{_summarize_body(body)}"
    return f"服务返回错误（HTTP {status_code}）：{_summarize_body(body)}"


def _extract_openai_reply(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return ""
    return str(message.get("content") or "")


def _extract_anthropic_reply(data: dict[str, Any]) -> str:
    for item in data.get("content") or []:
        if isinstance(item, dict) and item.get("text"):
            return str(item["text"])
    return ""


async def probe_llm(
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    *,
    timeout: float = 10.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bool, str]:
    """发送极短测试消息，返回 (ok, message)。

    函数名刻意不用 test_ 前缀：避免被 pytest 误收集为测试用例。
    """
    provider = (provider or "").strip().lower()
    if provider not in ("openai", "anthropic"):
        return False, f"不支持的模型供应商：{provider}"

    if provider == "openai":
        url = f"{(base_url or '').rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": PING_MESSAGE}],
            "max_tokens": 1,
        }
        extract = _extract_openai_reply
    else:
        root = (base_url or "").rstrip("/") or ANTHROPIC_DEFAULT_BASE
        url = f"{root}/v1/messages"
        headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
        payload = {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": PING_MESSAGE}],
        }
        extract = _extract_anthropic_reply

    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException:
        return False, f"连接超时（{timeout:g}s）：请检查地址可达性"
    except httpx.ConnectError as exc:
        return False, f"无法连接到服务地址：{type(exc).__name__}: {exc}"

    if resp.status_code >= 400:
        return False, _error_message(resp.status_code, resp.text)

    try:
        data = resp.json()
    except ValueError:
        # 响应不是 JSON（如网关错误页），连接本身已成功
        return True, "连接成功（响应非 JSON，已忽略内容）"
    reply = extract(data)
    return True, f"连接成功，模型已回复：{_summarize_body(reply, 60)}"
