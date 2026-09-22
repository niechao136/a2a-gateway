"""平台注册表：platform 字符串 → 适配器实例。"""

from .base import PlatformAdapter
from .feishu import FeishuAdapter
from .slack import SlackAdapter
from .telegram import TelegramAdapter

_ADAPTERS: dict[str, PlatformAdapter] = {
    adapter.platform: adapter for adapter in (TelegramAdapter(), SlackAdapter(), FeishuAdapter())
}


def get_adapter(platform: str) -> PlatformAdapter:
    """未知平台抛 KeyError（webhook 路由转 404）。"""
    adapter = _ADAPTERS.get(platform)
    if adapter is None:
        raise KeyError(platform)
    return adapter
