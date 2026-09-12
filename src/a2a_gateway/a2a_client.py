"""A2A 客户端封装（基于 a2a-sdk 1.x）。

向绑定的 A2A 目标发送消息并流式接收结果。错误会被分类，方便日志与前端区分
“配置问题 / 网络问题 / 目标内部错误”：
- 连接失败 / Agent Card 解析失败 → 网络或配置问题（URL 不可达、未发布）
- A2AClientTimeoutError      → 超时
- A2AClientError             → 目标 Agent 内部错误
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from google.protobuf import json_format

from a2a.client import (
    A2AClientError,
    A2AClientTimeoutError,
    A2ACardResolver,
    AgentCardResolutionError,
    ClientConfig,
    ClientFactory,
)
from a2a.helpers.proto_helpers import (
    get_artifact_text,
    get_message_text,
    new_data_part,
    new_text_part,
)
from a2a.types import a2a_pb2
from a2a.utils.constants import TransportProtocol

from .auth_scheme import apply_query_auth, build_headers
from .schemas import A2ATarget

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset(
    {
        a2a_pb2.TASK_STATE_COMPLETED,
        a2a_pb2.TASK_STATE_FAILED,
        a2a_pb2.TASK_STATE_CANCELED,
        a2a_pb2.TASK_STATE_REJECTED,
    }
)


class A2ATargetError(Exception):
    """A2A 调用失败，携带错误类别（network / timeout / target_error）。"""

    def __init__(self, kind: str, detail: str):
        self.kind = kind
        self.detail = detail
        super().__init__(f"[{kind}] {detail}")


class A2AClientWrapper:
    def __init__(self, target: A2ATarget):
        self.target = target
        self._httpx_client: httpx.AsyncClient | None = None
        self._client = None

    async def _ensure_client(self):
        """解析目标 Agent Card 并构建 a2a-sdk 1.x 客户端。"""
        if self._client is not None:
            return self._client
        headers = build_headers(self.target.auth_type, self.target.auth_name, self.target.token)
        url = apply_query_auth(self.target.url, self.target.auth_type, self.target.auth_name, self.target.token)
        http = httpx.AsyncClient(headers=headers, timeout=60.0)
        try:
            resolver = A2ACardResolver(http, url)
            card = await resolver.get_agent_card()
        except (AgentCardResolutionError, httpx.HTTPError) as e:
            await http.aclose()
            raise A2ATargetError("network", f"A2A 目标 {url} 不可达或未发布 Agent Card：{e}") from e
        # 目标 Agent Card 中声明的回连地址通常是其内部地址（如 http://localhost:9901），
        # 网关容器据此回连会连到自己而失败。A2A 1.0 的 url 位于 supported_interfaces，
        # 逐个改写为实际可达的 target.url。
        for _iface in card.supported_interfaces:
            _iface.url = url
        config = ClientConfig(
            streaming=True,
            polling=False,
            httpx_client=http,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
        )
        self._client = ClientFactory(config).create(card)
        self._httpx_client = http
        return self._client

    @staticmethod
    def _classify(error: Exception) -> A2ATargetError:
        if isinstance(error, A2ATargetError):
            return error
        if isinstance(error, (A2AClientTimeoutError, httpx.TimeoutException)):
            return A2ATargetError("timeout", f"A2A 调用超时：{error}")
        if isinstance(error, A2AClientError):
            return A2ATargetError("target_error", f"目标 Agent 内部错误：{error}")
        if isinstance(error, httpx.HTTPError):
            return A2ATargetError("network", f"A2A 网络错误：{error}")
        return A2ATargetError("target_error", f"A2A 调用异常：{error}")

    async def stream_message(self, text: str, *, retries: int = 2, backoff: float = 0.5) -> AsyncIterator[str]:
        """向目标发送文本并流式产出结果片段。"""
        message = a2a_pb2.Message(
            message_id=uuid.uuid4().hex,
            role=a2a_pb2.ROLE_USER,
            parts=[
                new_data_part({"query": text}, media_type="application/json"),
                new_text_part(text, media_type="text/plain"),
            ],
        )
        request = a2a_pb2.SendMessageRequest(message=message)

        for attempt in range(retries + 1):
            yielded = False
            try:
                client = await self._ensure_client()
                async for response in client.send_message(request):
                    async for chunk in self._extract(response):
                        yielded = True
                        yield chunk
                return
            except Exception as error:
                classified = self._classify(error)
                # 已产出部分结果或不可重试（非网络/超时）时直接抛出
                if yielded or attempt >= retries or classified.kind not in ("network", "timeout"):
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

    @staticmethod
    async def _extract(response: Any) -> AsyncIterator[str]:
        """从 a2a-sdk 1.x 的流式响应中提取文本片段。"""
        if response.HasField("task"):
            return
        if response.HasField("status_update"):
            update = response.status_update
            if update.status.HasField("message"):
                text = get_message_text(update.status.message)
                if text:
                    yield text
            if update.status.state in TERMINAL_STATES:
                return
        elif response.HasField("artifact_update"):
            artifact = response.artifact_update.artifact
            text = get_artifact_text(artifact)
            if text:
                yield text
            for part in artifact.parts:
                if part.HasField("data"):
                    yield json.dumps(json_format.MessageToDict(part.data), ensure_ascii=False)
        elif response.HasField("message"):
            text = get_message_text(response.message)
            if text:
                yield text

    async def test_connection(self) -> tuple[bool, str]:
        try:
            await self._ensure_client()
            return True, f"已成功连接 {self.target.url}"
        except A2ATargetError as e:
            return False, str(e)

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.debug("关闭 A2A 客户端时出错", exc_info=True)
            self._client = None
        if self._httpx_client is not None:
            try:
                await self._httpx_client.aclose()
            except Exception:
                pass
            self._httpx_client = None
