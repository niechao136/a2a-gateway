"""告警组件测试：未配置退化日志、配置后推送 payload、推送失败被吞掉。"""

import logging

import httpx

from a2a_gateway import notifier


async def test_no_webhook_falls_back_to_log(monkeypatch, caplog):
    monkeypatch.setattr(notifier._settings, "alert_webhook_url", "")
    with caplog.at_level(logging.ERROR, logger="a2a_gateway.notifier"):
        await notifier.notify_alert("A2A 调用失败", "target=http://x/ kind=network")
    assert any("A2A 调用失败" in record.getMessage() for record in caplog.records)


async def test_posts_payload_when_configured(monkeypatch):
    sent: dict = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            sent.update({"url": url, "json": json, "headers": headers})
            return httpx.Response(200)

    monkeypatch.setattr(notifier._settings, "alert_webhook_url", "http://alert.local/hook")
    monkeypatch.setattr(notifier._settings, "alert_webhook_token", "secret-token")
    monkeypatch.setattr(notifier, "httpx", type("H", (), {"AsyncClient": FakeAsyncClient}))

    await notifier.notify_alert("Agent 加载失败", "slug=/", level="error")

    assert sent["url"] == "http://alert.local/hook"
    assert sent["json"] == {
        "level": "error",
        "title": "Agent 加载失败",
        "detail": "slug=/",
    }
    assert sent["headers"]["Authorization"] == "Bearer secret-token"


async def test_push_failure_is_swallowed(monkeypatch):
    class BrokenClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("webhook down")

    monkeypatch.setattr(notifier._settings, "alert_webhook_url", "http://alert.local/hook")
    monkeypatch.setattr(notifier, "httpx", type("H", (), {"AsyncClient": BrokenClient}))

    # 不应抛出任何异常
    await notifier.notify_alert("x", "y")
