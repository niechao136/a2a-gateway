"""告警通知（可选）：把关键失败推送到 Webhook。

覆盖场景：A2A 调用失败、Agent 加载失败、对话流式失败。

行为：
- 未配置 `ALERT_WEBHOOK_URL` 时：仅以 ERROR 级别写入日志（不额外依赖任何外部服务）
- 配置后：把 `{level, title, detail}` POST 到该 Webhook（可带 Bearer token）
- 所有异常都被吞掉，绝不因告警失败影响主流程
"""

import logging

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

_ALERT_TIMEOUT = 3.0


async def notify_alert(title: str, detail: str, *, level: str = "error") -> None:
    """发送告警（未配置 Webhook 时退化为日志）。"""
    url = _settings.alert_webhook_url
    if not url:
        logger.error("[告警] %s | %s", title, detail)
        return

    headers = {"Content-Type": "application/json"}
    if _settings.alert_webhook_token:
        headers["Authorization"] = f"Bearer {_settings.alert_webhook_token}"

    payload = {"level": level, "title": title, "detail": detail}
    try:
        async with httpx.AsyncClient(timeout=_ALERT_TIMEOUT) as client:
            await client.post(url, json=payload, headers=headers)
        logger.info("[告警] 已推送：%s", title)
    except Exception:
        logger.warning("[告警] 推送失败：%s", url, exc_info=True)
