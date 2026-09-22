"""聊天平台适配器抽象：验签、入站归一化、接入握手、出站发送。

主链路（routes/connectors.py + pipeline.py）只面向本模块的类型，
平台差异全部封装在各适配器实现内。
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass


class VerifyError(Exception):
    """平台验签失败（webhook 路由捕获后返回 401）。"""


@dataclass
class InboundMessage:
    """归一化后的入站消息（已通过过滤：私聊文本 / 群聊 @机器人 文本）。"""

    platform: str
    chat_id: str
    chat_type: str  # private / group
    user_id: str
    user_name: str
    text: str
    event_id: str  # 平台事件/消息 id，管线去重键


def chunk_text(text: str, limit: int) -> list[str]:
    """按字符上限切分文本；优先在换行处断开，超长无换行则硬切。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < int(limit * 0.5):
            cut = limit
        head = rest[:cut].strip()
        if head:
            chunks.append(head)
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return chunks


class PlatformAdapter(ABC):
    """平台适配器接口。方法均为 async：飞书需要异步取机器人信息做群聊过滤。"""

    platform: str = ""

    @abstractmethod
    async def build_challenge(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> dict | None:
        """平台接入握手应答体（如 {"challenge": ...}）；非握手事件返回 None。

        Slack 的 url_verification 自带签名，实现内部需先验签；
        验签失败同样抛 VerifyError。
        """

    @abstractmethod
    async def verify_and_parse(
        self, body: bytes, headers: Mapping[str, str], credentials: dict
    ) -> list[InboundMessage]:
        """验签并解析出待处理消息；验签失败抛 VerifyError。

        仅产出通过过滤的消息：私聊文本直接收；群聊仅收 @机器人 的文本；
        bot 自身 / 其他 bot / 非文本消息一律忽略。
        """

    @abstractmethod
    async def send(self, credentials: dict, chat_id: str, text: str) -> None:
        """发送文本消息；超长文本自动分段（chunk_text）。失败抛异常由调用方告警。"""
