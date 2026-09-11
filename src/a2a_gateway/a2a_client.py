"""A2A Client 封装：基于 a2a-sdk，向绑定的 A2A 目标发送消息并流式接收结果。

错误分类（供日志/前端区分"配置问题 / 网络问题 / 目标内部错误"）：
- AgentCardResolutionError / 连接失败 → 网络或配置问题（URL 不可达）
- A2AClientTimeoutError → 超时
- A2AClientError → 目标 Agent 内部错误
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx

from a2a.client import (
    A2AClientError,
    A2AClientTimeoutError,
    AgentCardResolutionError,
    Client,
    ClientConfig,
    create_client,
)
from a2a.client.errors import AgentCardResolutionError as _CardErr
from a2a.helpers.proto_helpers import (
    get_stream_response_text,
    new_text_message,
)
from a2a.types.a2a_pb2 import Role, SendMessageRequest

from .schemas import A2ATarget

logger = logging.getLogger(__name__)


class A2ATargetError(Exception):
    """A2A 调用失败的统一异常，附带错误分类。"""

    def __init__(self, kind: str, detail: str):
        self.kind = kind  # network / timeout / config / target_error
        self.detail = detail
        super().__init__(f"[{kind}] {detail}")


class A2AClientWrapper:
    """对单个 A2A 目标的客户端封装。"""

    def __init__(self, target: A2ATarget):
        self.target = target
        self._httpx_client: httpx.AsyncClient | None = None
        self._client: Client | None = None

    async def _ensure_client(self) -> Client:
        if self._client is not None:
            return self._client
        headers: dict[str, str] = {}
        if self.target.token:
            headers["Authorization"] = f"Bearer {self.target.token}"
        self._httpx_client = httpx.AsyncClient(headers=headers, timeout=60.0)
        config = ClientConfig(httpx_client=self._httpx_client)
        try:
            self._client = await create_client(self.target.url, config)
        except (AgentCardResolutionError, _CardErr) as e:
            raise A2ATargetError(
                "network",
                f"A2A 目标 {self.target.url} 不可达或未发布 Agent Card：{e}",
            ) from e
        except httpx.ConnectError as e:
            raise A2ATargetError("network", f"无法连接到 {self.target.url}：{e}") from e
        return self._client

    @staticmethod
    def _classify(error: Exception) -> A2ATargetError:
        """把底层异常统一转换为带分类的 A2ATargetError。"""
        if isinstance(error, A2ATargetError):
            return error
        if isinstance(error, A2AClientTimeoutError):
            return A2ATargetError("timeout", f"A2A 调用超时：{error}")
        if isinstance(error, A2AClientError):
            return A2ATargetError("target_error", f"目标 Agent 内部错误：{error}")
        if isinstance(error, httpx.HTTPError):
            return A2ATargetError("network", f"A2A 网络错误：{error}")
        return A2ATargetError("target_error", f"A2A 调用异常：{error}")

    async def stream_message(
        self,
        text: str,
        *,
        retries: int = 2,
        backoff: float = 0.5,
    ) -> AsyncIterator[str]:
        """向目标发送文本消息，流式返回文本片段。

        对「网络 / 超时」类瞬时错误做有限重试；一旦已有内容产出则不再重试，
        避免向调用方重复输出。目标内部错误（target_error）不重试。
        """
        message = new_text_message(text, role=Role.ROLE_USER)
        request = SendMessageRequest(message=message)

        for attempt in range(retries + 1):
            yielded = False
            try:
                client = await self._ensure_client()
                async for response in client.send_message(request):
                    chunk = get_stream_response_text(response)
                    if chunk:
                        yielded = True
                        yield chunk
                return
            except Exception as error:  # noqa: BLE001 — 统一分类后决定是否重试
                classified = self._classify(error)
                if (
                    yielded
                    or attempt >= retries
                    or classified.kind not in ("network", "timeout")
                ):
                    raise classified from error
                delay = backoff * (attempt + 1)
                logger.warning(
                    "A2A 调用失败（%s），%.1fs 后进行第 %d 次重试：%s",
                    classified.kind,
                    delay,
                    attempt + 1,
                    classified.detail,
                )
                await asyncio.sleep(delay)

    async def test_connection(self) -> tuple[bool, str]:
        """连通性测试，返回 (是否成功, 说明)。"""
        try:
            client = await self._ensure_client()
            return True, f"已成功连接 {self.target.url}"
        except A2ATargetError as e:
            return False, str(e)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._httpx_client is not None:
            await self._httpx_client.aclose()
            self._httpx_client = None
