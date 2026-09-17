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
from urllib.parse import urlsplit, urlunsplit

import httpx
from a2a.client import (
    A2ACardResolver,
    A2AClientError,
    A2AClientTimeoutError,
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
from google.protobuf import json_format

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


def _origin_url(url: str) -> str:
    """取 URL 的 scheme://host[:port] 部分；本就不带路径时原样返回（查询串保留）。"""
    parts = urlsplit(url)
    if not parts.path or parts.path == "/":
        return url
    return urlunsplit((parts.scheme, parts.netloc, "", parts.query, ""))


def _merge_interface_url(iface_url: str, target_url: str) -> str:
    """把卡片声明的接口地址改写为网关实际可达的地址。

    卡片里的 url 常是目标内部地址（如 http://localhost:9901/a2a），需要用配置的
    target_url 替换；但 target_url 通常只填基础地址（http://host:port），若整体
    覆盖会丢掉卡片声明的 RPC 路径（如 /a2a），请求便会打到根路径而 404。

    规则：
    - target_url 自带路径（非 /）→ 视为用户显式指定的端点地址，原样使用；
    - 否则 → 以 target_url 的 scheme/host/port 为基准，路径取卡片声明，
      并保留 target_url 上的查询串（query 鉴权参数挂在这里）。
    """
    target = urlsplit(target_url)
    card = urlsplit(iface_url)
    if target.path and target.path != "/":
        return target_url
    return urlunsplit((target.scheme, target.netloc, card.path or "", target.query, ""))


async def _fetch_agent_card(http: httpx.AsyncClient, url: str):
    """解析 Agent Card；带路径的 URL 解析失败时回退到 origin 再试一次。

    常见误配：把 RPC 端点（如 http://host:10101/a2a）当作服务地址填入，
    此时 well-known 路径被拼成 /a2a/.well-known/... 而 404；卡片实际挂在
    origin 下，回退即可解析。
    """
    try:
        return await A2ACardResolver(http, url).get_agent_card()
    except (AgentCardResolutionError, httpx.HTTPError):
        origin = _origin_url(url)
        if origin == url:
            raise
        logger.info("A2A 目标卡片解析失败（%s），回退到 %s 重试", url, origin)
        return await A2ACardResolver(http, origin).get_agent_card()


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
            card = await _fetch_agent_card(http, url)
        except (AgentCardResolutionError, httpx.HTTPError) as e:
            await http.aclose()
            raise A2ATargetError("network", f"A2A 目标 {url} 不可达或未发布 Agent Card：{e}") from e
        # 目标 Agent Card 中声明的回连地址通常是其内部地址（如 http://localhost:9901），
        # 网关容器据此回连会连到自己而失败。A2A 1.0 的 url 位于 supported_interfaces，
        # 逐个改写为实际可达地址：只替换 host，保留卡片声明的 RPC 路径（如 /a2a），
        # 否则请求会打到根路径而 404。
        for _iface in card.supported_interfaces:
            _iface.url = _merge_interface_url(_iface.url, url)
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
        """从 a2a-sdk 1.x 的流式响应中提取文本片段。

        注意：目标 Agent 的最终结果常被 SDK 聚合成**单个 task 快照**返回——
        追问文本在 task.status.message、结果内容在 task.artifacts；若对 task
        直接跳过，会丢掉全部内容（前端只能显示"未返回内容"）。
        """
        if response.HasField("task"):
            task = response.task
            if task.status.HasField("message"):
                text = get_message_text(task.status.message)
                if text:
                    yield text
            for artifact in task.artifacts:
                async for chunk in A2AClientWrapper._extract_artifact(artifact):
                    yield chunk
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
            async for chunk in A2AClientWrapper._extract_artifact(response.artifact_update.artifact):
                yield chunk
        elif response.HasField("message"):
            text = get_message_text(response.message)
            if text:
                yield text

    @staticmethod
    async def _extract_artifact(artifact: Any) -> AsyncIterator[str]:
        """提取 artifact 的文本片段与 data part（JSON 序列化）。"""
        text = get_artifact_text(artifact)
        if text:
            yield text
        for part in artifact.parts:
            if part.HasField("data"):
                yield json.dumps(json_format.MessageToDict(part.data), ensure_ascii=False)

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
