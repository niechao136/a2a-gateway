# A2A input-required 支持实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让网关具备协议级 input-required 闭环——下游 Agent（如 travel-agent）中断追问时，用户的补充能带 `task_id` 恢复下游**同一任务**；覆盖对话界面（链路 A）与 A2A Server 出口（链路 B）。

**架构：** `a2a_client` 新增结构化事件流（`TextChunk` / `InputRequired` / `Completed`）承载中断信号；工具层把中断写入新增的 `pending_a2a_tasks` 挂起表；路由层按挂起表分流——命中即透明转发恢复（不经 LLM），未命中走现有 LangGraph 并在轮末复查挂起表以产出 `interrupt`（SSE）/ `TASK_STATE_INPUT_REQUIRED`（A2A）。

**技术栈：** Python 3.11+（FastAPI / SQLAlchemy async / Alembic / a2a-sdk 1.x / LangGraph）、Next.js + MUI、pytest、vitest。

**规格：** `docs/superpowers/specs/2026-09-17-a2a-input-required-design.md`（执行者需与计划一起阅读）

## 全局约束

- 所有改动在 a2a-gateway 仓库（`d:/web/a2a-gateway`）；不改动 travel-agent。
- 后端测试必须离线：不连数据库、不连网络、不调 LLM（沿用 `tests/conftest.py` 的替身与 monkeypatch 风格）。
- `ruff` line-length=100；注释与用户可见文案用中文，风格与现有代码一致。
- 前端不新增依赖；只用已有 MUI 组件与 vitest。
- 每个任务结束运行对应测试命令并全绿后再 commit；commit 信息用中文（`feat:` / `fix:` / `docs:`，与仓库历史一致）。
- 挂起表 TTL 默认 86400 秒（`PENDING_A2A_TTL_SECONDS` 环境变量可覆盖）。
- 关键签名（全程保持一致）：`stream_message_events(text, *, task_id=None, context_id=None, retries=2, backoff=0.5)`；`PendingRecord(thread_id, agent_id, target_url, target_name, task_id, context_id, question)`；`get_agent_wrappers(agent)`；模块级单例 `default_pending_store`。

---

## 文件结构

**创建：**
- `src/a2a_gateway/pending_store.py` —— 挂起存储（协议 + DB 实现 + 单例 + TTL 纯函数）
- `alembic/versions/0009_pending_a2a_tasks.py` —— 挂起表迁移
- `tests/test_pending_store.py` —— 存储单测
- `tests/test_tools_a2a.py` —— 工具层（A2A 工具）单测
- `tests/test_chat_input_required.py` —— 链路 A 单测

**修改：**
- `src/a2a_gateway/a2a_client.py` —— 事件流与 task_id 透传
- `src/a2a_gateway/models.py` —— `PendingA2ATask` 模型
- `src/a2a_gateway/repository.py` —— 挂起 CRUD
- `src/a2a_gateway/config.py` —— TTL 配置
- `src/a2a_gateway/tools.py` —— 工具消费事件流 + 写挂起
- `src/a2a_gateway/graph.py` —— system prompt 约束
- `src/a2a_gateway/agent_factory.py` —— 传 agent_id；新增 `get_agent_wrappers`
- `src/a2a_gateway/routes/chat.py` —— 拦截 / 恢复 / interrupt / 清理
- `src/a2a_gateway/routes/a2a_server.py` —— 链路 B
- `web/src/lib/api.ts` —— `interrupt` 事件 + `parseSSEEvent`
- `web/src/components/ChatPage.tsx` —— 「等待补充」提示
- `tests/test_a2a_client.py`、`tests/test_a2a_server.py` —— 扩展现有用例
- `web/src/lib/api.test.ts` —— `parseSSEEvent` 用例
- `TODO.md` —— 勾选二期条目

---

### 任务 1：a2a_client 结构化事件流

**文件：**
- 修改：`src/a2a_gateway/a2a_client.py`
- 测试：`tests/test_a2a_client.py`

- [ ] **步骤 1.1 编写失败的测试**

把 `tests/test_a2a_client.py` 顶部 import 块替换为：

```python
from a2a_gateway.a2a_client import (
    A2AClientWrapper,
    A2ATargetError,
    Completed,
    InputRequired,
    TextChunk,
    _merge_interface_url,
    _origin_url,
)
```

在文件末尾追加：

```python
# ---------------------------------------------------------------------------
# 结构化事件流：中断信号（InputRequired）与 task 续接
# ---------------------------------------------------------------------------
class _ScriptedClient:
    """按脚本产出响应的假客户端，记录收到的请求。"""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def send_message(self, request):
        self.requests.append(request)

        async def gen():
            for response in self.responses:
                yield response

        return gen()


def _status_update(*, state, text=None, task_id="t-1", context_id="c-1") -> StreamResponse:
    resp = StreamResponse()
    resp.status_update.task_id = task_id
    resp.status_update.context_id = context_id
    resp.status_update.status.state = state
    if text is not None:
        resp.status_update.status.message.CopyFrom(new_text_message(text))
    return resp


async def test_stream_events_emits_text_then_input_required_from_task_snapshot():
    """线上实测形态：追问在 task 快照的 status.message，须产出 InputRequired。"""
    wrapper = _wrapper()
    fake = _ScriptedClient(
        [_task_response(state=a2a_pb2.TASK_STATE_INPUT_REQUIRED, message_text="请补充目的地")]
    )

    async def ensure():
        return fake

    wrapper._ensure_client = ensure  # type: ignore[method-assign]
    events = [e async for e in wrapper.stream_message_events("hi")]

    assert events == [
        TextChunk("请补充目的地"),
        InputRequired(task_id="t-1", context_id="c-1", question="请补充目的地"),
    ]


async def test_stream_events_emits_input_required_from_status_update():
    wrapper = _wrapper()
    fake = _ScriptedClient(
        [
            _status_update(text="先选个城市？", state=a2a_pb2.TASK_STATE_WORKING),
            _status_update(state=a2a_pb2.TASK_STATE_INPUT_REQUIRED, text="请补充日期与预算"),
        ]
    )

    async def ensure():
        return fake

    wrapper._ensure_client = ensure  # type: ignore[method-assign]
    events = [e async for e in wrapper.stream_message_events("hi")]

    assert events == [
        TextChunk("先选个城市？"),
        TextChunk("请补充日期与预算"),
        InputRequired(task_id="t-1", context_id="c-1", question="请补充日期与预算"),
    ]


async def test_stream_events_emits_completed_after_terminal_status():
    wrapper = _wrapper()
    fake = _ScriptedClient([_status_update(state=a2a_pb2.TASK_STATE_COMPLETED, task_id="t-9")])

    async def ensure():
        return fake

    wrapper._ensure_client = ensure  # type: ignore[method-assign]
    events = [e async for e in wrapper.stream_message_events("hi")]

    assert events == [Completed(task_id="t-9")]


async def test_stream_events_passes_task_id_and_context_id():
    """恢复语义：task_id / context_id 必须写入发送的消息。"""
    wrapper = _wrapper()
    fake = _ScriptedClient([_status_update(state=a2a_pb2.TASK_STATE_COMPLETED)])

    async def ensure():
        return fake

    wrapper._ensure_client = ensure  # type: ignore[method-assign]
    _ = [
        e
        async for e in wrapper.stream_message_events(
            "补充", task_id="t-1", context_id="c-1"
        )
    ]

    sent = fake.requests[0].message
    assert sent.task_id == "t-1"
    assert sent.context_id == "c-1"


async def test_stream_message_wrapper_still_yields_text_only():
    """旧 API（stream_message）保持只产出文本，中断信号不外漏。"""
    wrapper = _wrapper()
    fake = _ScriptedClient(
        [_task_response(state=a2a_pb2.TASK_STATE_INPUT_REQUIRED, message_text="请补充")]
    )

    async def ensure():
        return fake

    wrapper._ensure_client = ensure  # type: ignore[method-assign]
    out = [c async for c in wrapper.stream_message("hi")]

    assert out == ["请补充"]
```

- [ ] **步骤 1.2 运行测试验证失败**

运行：`uv run pytest tests/test_a2a_client.py -q`
预期：ImportError（`Completed` / `InputRequired` / `TextChunk` 不存在）。

- [ ] **步骤 1.3 实现事件流**

`src/a2a_gateway/a2a_client.py`：

1) import 区补 `from dataclasses import dataclass`；在 `TERMINAL_STATES` 定义之后加入：

```python
@dataclass(frozen=True)
class TextChunk:
    """下游产出的文本片段。"""

    text: str


@dataclass(frozen=True)
class InputRequired:
    """下游任务进入 input-required（等待用户补充信息）。"""

    task_id: str
    context_id: str
    question: str


@dataclass(frozen=True)
class Completed:
    """下游任务进入终态。"""

    task_id: str


StreamEvent = TextChunk | InputRequired | Completed
```

2) 在类内新增 `_state_events`（放在 `_extract` 之后）：

```python
    @staticmethod
    def _state_events(response: Any) -> list[StreamEvent]:
        """从响应派生状态事件：input-required（中断）与终态（完成）。

        文本由 ``_extract`` 负责；本方法只产出信号，调用方保证「先文本后信号」。
        """
        if response.HasField("task"):
            task = response.task
            if task.status.state == a2a_pb2.TASK_STATE_INPUT_REQUIRED:
                question = (
                    get_message_text(task.status.message)
                    if task.status.HasField("message")
                    else ""
                )
                return [
                    InputRequired(
                        task_id=task.id, context_id=task.context_id, question=question
                    )
                ]
            if task.status.state in TERMINAL_STATES:
                return [Completed(task_id=task.id)]
            return []
        if response.HasField("status_update"):
            update = response.status_update
            if update.status.state == a2a_pb2.TASK_STATE_INPUT_REQUIRED:
                question = (
                    get_message_text(update.status.message)
                    if update.status.HasField("message")
                    else ""
                )
                return [
                    InputRequired(
                        task_id=update.task_id,
                        context_id=update.context_id,
                        question=question,
                    )
                ]
            if update.status.state in TERMINAL_STATES:
                return [Completed(task_id=update.task_id)]
        return []
```

3) 把现有 `stream_message` 整体替换为 `stream_message_events` + 薄封装（重试、补拉、错误分类逻辑原样迁移）：

```python
    async def stream_message_events(
        self,
        text: str,
        *,
        task_id: str | None = None,
        context_id: str | None = None,
        retries: int = 2,
        backoff: float = 0.5,
    ) -> AsyncIterator[StreamEvent]:
        """向目标发送文本并流式产出结构化事件。

        - 普通文本 → ``TextChunk``
        - 下游要求补充信息（input-required）→ ``InputRequired``（带 task_id 供恢复）
        - 任务终态 → ``Completed``
        - 传入 ``task_id`` 表示「恢复已有任务」（A2A 协议：向任务追加用户输入）
        """
        message = a2a_pb2.Message(
            message_id=uuid.uuid4().hex,
            role=a2a_pb2.ROLE_USER,
            parts=[
                new_data_part({"query": text}, media_type="application/json"),
                new_text_part(text, media_type="text/plain"),
            ],
        )
        if task_id:
            message.task_id = task_id
        if context_id:
            message.context_id = context_id
        request = a2a_pb2.SendMessageRequest(message=message)

        for attempt in range(retries + 1):
            yielded = False
            # 终态兜底判定：已见产物 or 已见终态 task 快照 → 无需补拉
            saw_artifact = False
            saw_final_task = False
            terminal_task_id: str | None = None
            try:
                client = await self._ensure_client()
                async for response in client.send_message(request):
                    if response.HasField("task"):
                        task = response.task
                        if task.artifacts:
                            saw_artifact = True
                        if task.status.state in TERMINAL_STATES:
                            saw_final_task = True
                            terminal_task_id = task.id
                    elif response.HasField("status_update"):
                        if response.status_update.status.state in TERMINAL_STATES:
                            terminal_task_id = response.status_update.task_id
                    async for chunk in self._extract(response):
                        yielded = True
                        yield TextChunk(chunk)
                    for state_event in self._state_events(response):
                        yield state_event
                # 零产出兜底：目标可能只在任务的最终快照里放产物（流中没有 artifact 事件）
                if terminal_task_id and not saw_artifact and not saw_final_task:
                    async for chunk in self._fetch_task_artifacts(client, terminal_task_id):
                        yielded = True
                        yield TextChunk(chunk)
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

    async def stream_message(
        self, text: str, *, retries: int = 2, backoff: float = 0.5
    ) -> AsyncIterator[str]:
        """向目标发送文本并流式产出结果片段（兼容旧调用方：仅文本）。"""
        async for event in self.stream_message_events(text, retries=retries, backoff=backoff):
            if isinstance(event, TextChunk):
                yield event.text
```

- [ ] **步骤 1.4 运行测试验证通过**

运行：`uv run pytest tests/test_a2a_client.py -q && uv run pytest tests -q`
预期：全部 PASS（原有 `_extract` / 重试 / 补拉用例不回归）。

- [ ] **步骤 1.5 Commit**

```bash
git add src/a2a_gateway/a2a_client.py tests/test_a2a_client.py
git commit -m "feat: A2A 客户端结构化事件流（InputRequired/Completed + task_id 续接）"
```

---

### 任务 2：挂起表与存储

**文件：**
- 修改：`src/a2a_gateway/config.py`、`src/a2a_gateway/models.py`、`src/a2a_gateway/repository.py`
- 创建：`src/a2a_gateway/pending_store.py`、`alembic/versions/0009_pending_a2a_tasks.py`
- 测试：`tests/test_pending_store.py`

- [ ] **步骤 2.1 编写失败的测试**

创建 `tests/test_pending_store.py`：

```python
"""挂起任务存储测试：TTL 判定、记录转换与委托行为（不连数据库）。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from a2a_gateway import pending_store as ps_mod
from a2a_gateway.pending_store import DbPendingStore, PendingRecord, _is_expired


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _factory():
    return _FakeSession()


def _row(**overrides):
    base = {
        "thread_id": "th-1",
        "agent_id": 7,
        "target_url": "http://h:9900/",
        "target_name": "travel",
        "task_id": "t-1",
        "context_id": "c-1",
        "question": "请补充目的地",
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_is_expired_boundaries():
    now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    assert _is_expired(now - timedelta(seconds=30), 60, now=now) is False
    assert _is_expired(now - timedelta(seconds=61), 60, now=now) is True
    # 数据库返回 naive 时间时按 UTC 处理，不抛异常
    naive = datetime(2026, 9, 18, 10, 0)
    assert _is_expired(naive, 3600, now=now) is True


async def test_store_get_returns_record_for_fresh_row(monkeypatch):
    async def fake_get(session, thread_id):
        return _row()

    monkeypatch.setattr(ps_mod, "get_pending_a2a_task", fake_get)
    store = DbPendingStore(session_factory=_factory, ttl_seconds=3600)

    record = await store.get("th-1")

    assert record == PendingRecord(
        thread_id="th-1",
        agent_id=7,
        target_url="http://h:9900/",
        target_name="travel",
        task_id="t-1",
        context_id="c-1",
        question="请补充目的地",
    )


async def test_store_get_deletes_expired_row(monkeypatch):
    calls: dict = {}

    async def fake_get(session, thread_id):
        return _row(updated_at=datetime.now(timezone.utc) - timedelta(hours=25))

    async def fake_delete(session, thread_id):
        calls["deleted"] = thread_id
        return True

    monkeypatch.setattr(ps_mod, "get_pending_a2a_task", fake_get)
    monkeypatch.setattr(ps_mod, "delete_pending_a2a_task", fake_delete)
    store = DbPendingStore(session_factory=_factory, ttl_seconds=86400)

    assert await store.get("th-1") is None
    assert calls["deleted"] == "th-1"


async def test_store_get_returns_none_for_missing_row(monkeypatch):
    async def fake_get(session, thread_id):
        return None

    monkeypatch.setattr(ps_mod, "get_pending_a2a_task", fake_get)
    store = DbPendingStore(session_factory=_factory, ttl_seconds=3600)

    assert await store.get("nope") is None


async def test_store_upsert_and_delete_delegate(monkeypatch):
    captured: dict = {}

    async def fake_upsert(session, **kwargs):
        captured.update(kwargs)

    async def fake_delete(session, thread_id):
        captured["deleted"] = thread_id
        return True

    monkeypatch.setattr(ps_mod, "upsert_pending_a2a_task", fake_upsert)
    monkeypatch.setattr(ps_mod, "delete_pending_a2a_task", fake_delete)
    store = DbPendingStore(session_factory=_factory, ttl_seconds=3600)

    await store.upsert(
        thread_id="th-1",
        agent_id=7,
        target_url="http://h:9900/",
        target_name="travel",
        task_id="t-1",
        context_id="c-1",
        question="请补充",
    )
    await store.delete("th-1")

    assert captured["thread_id"] == "th-1"
    assert captured["agent_id"] == 7
    assert captured["question"] == "请补充"
    assert captured["deleted"] == "th-1"
```

- [ ] **步骤 2.2 运行测试验证失败**

运行：`uv run pytest tests/test_pending_store.py -q`
预期：`ModuleNotFoundError: No module named 'a2a_gateway.pending_store'`。

- [ ] **步骤 2.3 实现配置、模型、repository 与 store**

`config.py`（在语音服务配置块之前加）：

```python
    # 挂起任务（input-required）：超过该秒数未恢复的挂起在读取时视为失效并清理
    pending_a2a_ttl_seconds: int = Field(default=86400, alias="PENDING_A2A_TTL_SECONDS")
```

`models.py`（在 `Conversation` 类之后加；`ForeignKey`/`Text` 已在 import 区）：

```python
class PendingA2ATask(Base, BaseMixin):
    """挂起任务：网关会话 / 对外 task_id → 下游任务（task_id）的映射。

    链路 A（对话界面）以会话 thread_id 为键；链路 B（A2A Server）以对外 task_id
    为键（创建新任务时两者取同一标识）。下游完成即删除；读取时按 TTL 过期清理。
    """

    __tablename__ = "pending_a2a_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="CASCADE"), index=True
    )
    target_url: Mapped[str] = mapped_column(String(512))
    target_name: Mapped[str] = mapped_column(String(128), default="")
    task_id: Mapped[str] = mapped_column(String(128))
    context_id: Mapped[str] = mapped_column(String(128), default="")
    question: Mapped[str] = mapped_column(Text, default="")
```

`repository.py` 末尾追加（import 区把 `PendingA2ATask` 加进 `from .models import ...`；确认 `datetime`、`timezone`、`select` 已 import）：

```python
# ---------------------------------------------------------------------------
# 挂起任务（input-required）
# ---------------------------------------------------------------------------
async def get_pending_a2a_task(
    session: AsyncSession, thread_id: str
) -> PendingA2ATask | None:
    """按 thread_id 取挂起任务（TTL 判定由 store 层负责）。"""
    result = await session.execute(
        select(PendingA2ATask).where(PendingA2ATask.thread_id == thread_id)
    )
    return result.scalar_one_or_none()


async def upsert_pending_a2a_task(
    session: AsyncSession,
    *,
    thread_id: str,
    agent_id: int,
    target_url: str,
    target_name: str,
    task_id: str,
    context_id: str,
    question: str,
) -> None:
    """登记/刷新一条挂起任务（按 thread_id 冲突更新）。"""
    existing = await get_pending_a2a_task(session, thread_id)
    if existing is not None:
        existing.agent_id = agent_id
        existing.target_url = target_url
        existing.target_name = target_name
        existing.task_id = task_id
        existing.context_id = context_id
        existing.question = question
        existing.updated_at = datetime.now(timezone.utc)
    else:
        session.add(
            PendingA2ATask(
                thread_id=thread_id,
                agent_id=agent_id,
                target_url=target_url,
                target_name=target_name,
                task_id=task_id,
                context_id=context_id,
                question=question,
            )
        )
    await session.commit()


async def delete_pending_a2a_task(session: AsyncSession, thread_id: str) -> bool:
    """删除挂起任务；不存在时返回 False。"""
    row = await get_pending_a2a_task(session, thread_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True
```

创建 `src/a2a_gateway/pending_store.py`：

```python
"""挂起任务存储：网关会话 / 对外 task_id → 下游任务（task_id）的映射。

链路 A（对话界面）以会话 thread_id 为键；链路 B（A2A Server）以对外 task_id
为键（创建新任务时两者同值，见 routes/a2a_server.py）。

存储走 PostgreSQL（pending_a2a_tasks 表）；``PendingStore`` 协议允许测试注入替身。
"""

import logging
from datetime import datetime, timezone
from typing import NamedTuple, Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from .config import get_settings
from .database import AsyncSessionLocal
from .repository import (
    delete_pending_a2a_task,
    get_pending_a2a_task,
    upsert_pending_a2a_task,
)

logger = logging.getLogger(__name__)


class PendingRecord(NamedTuple):
    """脱离 ORM 会话的只读快照。"""

    thread_id: str
    agent_id: int
    target_url: str
    target_name: str
    task_id: str
    context_id: str
    question: str


def _is_expired(
    updated_at: datetime, ttl_seconds: int, *, now: datetime | None = None
) -> bool:
    """挂起是否已超过 TTL；naive 时间按 UTC 处理。"""
    current = now or datetime.now(timezone.utc)
    stamp = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    return (current - stamp).total_seconds() > ttl_seconds


class PendingStore(Protocol):
    """挂起存储协议（生产走 DB，测试注入替身）。"""

    async def upsert(
        self,
        *,
        thread_id: str,
        agent_id: int,
        target_url: str,
        target_name: str,
        task_id: str,
        context_id: str,
        question: str,
    ) -> None: ...

    async def get(self, thread_id: str) -> PendingRecord | None: ...

    async def delete(self, thread_id: str) -> None: ...


class DbPendingStore:
    """PostgreSQL 实现（按需开启短会话）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker = AsyncSessionLocal,
        ttl_seconds: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._ttl_seconds = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        if self._ttl_seconds is not None:
            return self._ttl_seconds
        return get_settings().pending_a2a_ttl_seconds

    async def upsert(
        self,
        *,
        thread_id: str,
        agent_id: int,
        target_url: str,
        target_name: str,
        task_id: str,
        context_id: str,
        question: str,
    ) -> None:
        async with self._session_factory() as session:
            await upsert_pending_a2a_task(
                session,
                thread_id=thread_id,
                agent_id=agent_id,
                target_url=target_url,
                target_name=target_name,
                task_id=task_id,
                context_id=context_id,
                question=question,
            )

    async def get(self, thread_id: str) -> PendingRecord | None:
        async with self._session_factory() as session:
            row = await get_pending_a2a_task(session, thread_id)
            if row is None:
                return None
            if _is_expired(row.updated_at, self.ttl_seconds):
                await delete_pending_a2a_task(session, thread_id)
                logger.info("挂起任务已超时清理 thread=%s", thread_id)
                return None
            return PendingRecord(
                thread_id=row.thread_id,
                agent_id=row.agent_id,
                target_url=row.target_url,
                target_name=row.target_name,
                task_id=row.task_id,
                context_id=row.context_id,
                question=row.question,
            )

    async def delete(self, thread_id: str) -> None:
        async with self._session_factory() as session:
            await delete_pending_a2a_task(session, thread_id)


# 路由层与工具层共用（测试 monkeypatch 本模块变量或注入替身）
default_pending_store: PendingStore = DbPendingStore()
```

创建迁移 `alembic/versions/0009_pending_a2a_tasks.py`：

```python
"""add pending_a2a_tasks: 挂起任务（input-required 中断 → 恢复映射）

Revision ID: 0009_pending_a2a_tasks
Revises: 0008_conversations
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_pending_a2a_tasks"
down_revision: str | None = "0008_conversations"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_a2a_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("target_url", sa.String(length=512), nullable=False),
        sa.Column("target_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("context_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", name="uq_pending_a2a_tasks_thread"),
    )
    op.create_index(
        op.f("ix_pending_a2a_tasks_thread_id"), "pending_a2a_tasks", ["thread_id"], unique=True
    )
    op.create_index(op.f("ix_pending_a2a_tasks_agent_id"), "pending_a2a_tasks", ["agent_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_pending_a2a_tasks_agent_id"), table_name="pending_a2a_tasks")
    op.drop_index(op.f("ix_pending_a2a_tasks_thread_id"), table_name="pending_a2a_tasks")
    op.drop_table("pending_a2a_tasks")
```

- [ ] **步骤 2.4 运行测试验证通过**

运行：`uv run pytest tests/test_pending_store.py -q && uv run pytest tests -q`
预期：全部 PASS。

- [ ] **步骤 2.5 Commit**

```bash
git add src/a2a_gateway/config.py src/a2a_gateway/models.py src/a2a_gateway/repository.py src/a2a_gateway/pending_store.py alembic/versions/0009_pending_a2a_tasks.py tests/test_pending_store.py
git commit -m "feat: 挂起任务表与存储（input-required 恢复映射）"
```

---

### 任务 3：工具层写入挂起

**文件：**
- 修改：`src/a2a_gateway/tools.py`、`src/a2a_gateway/graph.py`、`src/a2a_gateway/agent_factory.py`
- 测试：创建 `tests/test_tools_a2a.py`

- [ ] **步骤 3.1 编写失败的测试**

创建 `tests/test_tools_a2a.py`：

```python
"""A2A 工具层测试：中断写挂起、正常路径不写、无会话兜底。"""

from a2a_gateway import tools as tools_mod
from a2a_gateway.a2a_client import Completed, InputRequired, TextChunk
from a2a_gateway.schemas import A2ATarget
from a2a_gateway.tools import make_a2a_tools


class FakeStore:
    def __init__(self):
        self.upserts = []

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    async def get(self, thread_id):
        return None

    async def delete(self, thread_id):
        return None


class FakeWrapper:
    """替身 wrapper：按脚本产出事件，并记录调用参数。"""

    def __init__(self, target, events):
        self.target = target
        self._events = events
        self.calls = []

    async def stream_message_events(self, text, *, task_id=None, context_id=None, **kwargs):
        self.calls.append({"text": text, "task_id": task_id})
        for event in self._events:
            yield event


def _patch_wrapper(monkeypatch, events):
    """把 tools 模块里的 A2AClientWrapper 换成脚本化替身。"""

    def build(target):
        return FakeWrapper(target, events)

    monkeypatch.setattr(tools_mod, "A2AClientWrapper", build)


async def test_a2a_tool_writes_pending_on_input_required(monkeypatch):
    store = FakeStore()
    _patch_wrapper(
        monkeypatch,
        [
            TextChunk("请补充目的地"),
            InputRequired(task_id="t-1", context_id="c-1", question="请补充目的地"),
        ],
    )
    tools, _ = make_a2a_tools(
        [A2ATarget(url="http://h:9900/", name="travel")],
        agent_id=7,
        pending_store=store,
    )

    result = await tools[0].ainvoke(
        {"message": "去旅游"},
        config={"configurable": {"thread_id": "th-1"}},
    )

    assert result == "请补充目的地"
    assert store.upserts == [
        {
            "thread_id": "th-1",
            "agent_id": 7,
            "target_url": "http://h:9900/",
            "target_name": "travel",
            "task_id": "t-1",
            "context_id": "c-1",
            "question": "请补充目的地",
        }
    ]


async def test_a2a_tool_returns_joined_text_without_interrupt(monkeypatch):
    store = FakeStore()
    _patch_wrapper(monkeypatch, [TextChunk("a"), TextChunk("b"), Completed(task_id="t-1")])
    tools, _ = make_a2a_tools(
        [A2ATarget(url="http://h:9900/", name="travel")],
        agent_id=7,
        pending_store=store,
    )

    result = await tools[0].ainvoke(
        {"message": "去旅游"},
        config={"configurable": {"thread_id": "th-1"}},
    )

    assert result == "ab"
    assert store.upserts == []


async def test_a2a_tool_skips_pending_without_thread_id(monkeypatch):
    """拿不到会话 thread_id 时不登记挂起，但仍正常返回追问文本。"""
    store = FakeStore()
    _patch_wrapper(
        monkeypatch, [InputRequired(task_id="t-1", context_id="", question="请补充")]
    )
    tools, _ = make_a2a_tools(
        [A2ATarget(url="http://h:9900/", name="travel")],
        agent_id=7,
        pending_store=store,
    )

    result = await tools[0].ainvoke({"message": "去旅游"})

    assert result == "请补充"
    assert store.upserts == []
```

- [ ] **步骤 3.2 运行测试验证失败**

运行：`uv run pytest tests/test_tools_a2a.py -q`
预期：`TypeError: make_a2a_tools() got an unexpected keyword argument 'agent_id'`。

- [ ] **步骤 3.3 实现工具层**

`tools.py`：import 区增加：

```python
from langchain_core.runnables import RunnableConfig

from .a2a_client import A2AClientWrapper, A2ATargetError, InputRequired, TextChunk
from .pending_store import PendingStore, default_pending_store
```

替换 `_build_a2a_tool` 与 `make_a2a_tools`（多目标的命名冲突处理逻辑原样保留）：

```python
def _build_a2a_tool(
    name: str,
    description: str,
    wrapper: A2AClientWrapper,
    *,
    agent_id: int,
    pending_store: PendingStore,
) -> StructuredTool:
    async def _acall(message: str, config: RunnableConfig) -> str:
        """向绑定的 A2A 目标发送消息并聚合返回文本。

        下游要求补充信息（input-required）时，把挂起状态写入 pending_store，
        并把追问原样返回给模型转述；用户的下一条消息由路由层直接恢复该任务。
        """
        chunks: list[str] = []
        required: InputRequired | None = None
        try:
            async for event in wrapper.stream_message_events(message):
                if isinstance(event, TextChunk):
                    chunks.append(event.text)
                elif isinstance(event, InputRequired):
                    required = event
        except A2ATargetError as e:
            await notify_alert(
                "A2A 调用失败",
                f"target={wrapper.target.url} kind={e.kind} error={e.detail}",
            )
            return f"A2A 调用失败：{e}"

        if required is not None:
            thread_id = (config.get("configurable") or {}).get("thread_id") or ""
            if thread_id:
                await pending_store.upsert(
                    thread_id=thread_id,
                    agent_id=agent_id,
                    target_url=wrapper.target.url,
                    target_name=wrapper.target.name or "",
                    task_id=required.task_id,
                    context_id=required.context_id,
                    question=required.question,
                )
            return required.question or "（需要用户补充信息）"
        return "".join(chunks) if chunks else "（A2A 目标未返回内容）"

    def _call(message: str) -> str:
        raise RuntimeError("a2a_call 仅支持异步调用")

    return StructuredTool.from_function(
        coroutine=_acall,
        func=_call,
        name=name,
        description=description,
        args_schema=A2ACallArgs,
    )


def make_a2a_tools(
    targets: list[A2ATarget],
    *,
    agent_id: int = 0,
    pending_store: PendingStore | None = None,
) -> tuple[list[StructuredTool], list[A2AClientWrapper]]:
    """为每个 A2A 目标各构造一个调用工具。

    - 仅一个目标时沿用历史名称 `a2a_call`；多个目标时按目标名区分
    - 目标描述写进工具说明，让大模型判断该调用哪一个
    - `agent_id` 用于挂起登记（0 表示未知，仅影响链路 B 恢复时的归属校验）
    - `pending_store` 缺省用全局单例；测试可注入替身

    @returns (工具列表, 需要由调用方关闭的 client wrapper 列表)
    """
    store = pending_store or default_pending_store
    usable = [t for t in targets if t.url.strip()]
    wrappers = [A2AClientWrapper(t) for t in usable]
    ...（single / used / 循环体保持原样，循环内最后一个 append 改为：）
        tools.append(
            _build_a2a_tool(
                name,
                description,
                wrapper,
                agent_id=agent_id,
                pending_store=store,
            )
        )
    return tools, wrappers
```

注意：`A2AClientWrapper(t)` 必须继续通过模块级名字调用（测试 monkeypatch 依赖这一点）。

`graph.py` 的 `DEFAULT_SYSTEM_PROMPT` 末尾追加：

```python
    "若某个工具的结果是要求用户补充信息（例如追问目的地、日期），"
    "请把问题原样转述给用户并等待其回复，不要自行编造答案。"
```

`agent_factory.py` 中 `make_a2a_tools(targets)` 改为：

```python
    a2a_tools, wrappers = make_a2a_tools(targets, agent_id=agent.id)
```

- [ ] **步骤 3.4 运行测试验证通过**

运行：`uv run pytest tests/test_tools_a2a.py -q && uv run pytest tests -q`
预期：全部 PASS（含 `tests/test_mcp_client.py` 中对 A2A 工具的既有用例）。

- [ ] **步骤 3.5 Commit**

```bash
git add src/a2a_gateway/tools.py src/a2a_gateway/graph.py src/a2a_gateway/agent_factory.py tests/test_tools_a2a.py
git commit -m "feat: a2a_call 工具写入挂起任务并转述追问"
```

---

### 任务 4：链路 A 路由（拦截与恢复）

**文件：**
- 修改：`src/a2a_gateway/routes/chat.py`、`src/a2a_gateway/agent_factory.py`
- 测试：创建 `tests/test_chat_input_required.py`

- [ ] **步骤 4.1 编写失败的测试**

创建 `tests/test_chat_input_required.py`：

```python
"""链路 A（对话界面）input-required 测试：拦截恢复、interrupt 事件、历史追加与清理。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from a2a_gateway.a2a_client import A2ATargetError, Completed, InputRequired, TextChunk
from a2a_gateway.models import AgentStatus
from a2a_gateway.routes import chat as chat_mod


def _record(**overrides):
    base = {
        "thread_id": "t1",
        "agent_id": 1,
        "target_url": "http://h:9900/",
        "target_name": "travel",
        "task_id": "task-1",
        "context_id": "ctx-1",
        "question": "请补充目的地",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeStore:
    def __init__(self, pending=None):
        self.pending = pending
        self.upserts = []
        self.deleted = []

    async def get(self, thread_id):
        return self.pending

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        self.pending = _record(**kwargs)

    async def delete(self, thread_id):
        self.deleted.append(thread_id)
        self.pending = None


class FakeWrapper:
    def __init__(self, url, events, error=None):
        self.target = SimpleNamespace(url=url)
        self._events = events
        self._error = error
        self.calls = []

    async def stream_message_events(self, text, *, task_id=None, context_id=None, **kwargs):
        self.calls.append({"text": text, "task_id": task_id, "context_id": context_id})
        if self._error is not None:
            raise self._error
        for event in self._events:
            yield event


class FakeGraph:
    """恢复轮用：禁止调用 LLM 流，只允许追加历史。"""

    def __init__(self, on_stream=None):
        self.on_stream = on_stream
        self.updated = []

    async def astream_events(self, *args, **kwargs):
        if self.on_stream is None:
            raise AssertionError("恢复轮不应调用 LLM 图")
        self.on_stream()
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="请补充目的地")},
            "metadata": {"langgraph_node": "agent"},
        }

    async def aupdate_state(self, config, values, as_node=None):
        self.updated.append({"config": config, "values": values, "as_node": as_node})


@pytest.fixture(autouse=True)
def stub_conversations(monkeypatch):
    async def _no_conversation(session, thread_id):
        return None

    async def _ignore(*args, **kwargs):
        return None

    monkeypatch.setattr(chat_mod, "get_conversation", _no_conversation)
    monkeypatch.setattr(chat_mod, "upsert_conversation", _ignore)


def _patch_agent(monkeypatch, make_agent):
    async def fake_get(session, slug):
        return make_agent(status=AgentStatus.PUBLISHED)

    monkeypatch.setattr(chat_mod, "get_agent_by_slug", fake_get)


async def test_resume_forwards_with_task_id_and_skips_llm(anon_client, monkeypatch, make_agent):
    store = FakeStore(pending=_record())
    wrapper = FakeWrapper(
        "http://h:9900/", [TextChunk("行程内容"), Completed(task_id="task-1")]
    )
    graph = FakeGraph()

    async def fake_wrappers(agent):
        return [wrapper]

    async def fake_instance(agent):
        return graph

    _patch_agent(monkeypatch, make_agent)
    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "get_agent_wrappers", fake_wrappers)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post(
        "/api/chat", json={"message": "杭州 10/1-10/3 预算3000", "thread_id": "t1"}
    )

    body = resp.text
    assert "event: token" in body and "行程内容" in body
    assert "event: done" in body
    assert "event: interrupt" not in body
    assert wrapper.calls == [
        {"text": "杭州 10/1-10/3 预算3000", "task_id": "task-1", "context_id": "ctx-1"}
    ]
    assert store.deleted == ["t1"]
    # 历史追加：Human + AI 两条
    assert len(graph.updated) == 1
    assert [m.content for m in graph.updated[0]["values"]["messages"]] == [
        "杭州 10/1-10/3 预算3000",
        "行程内容",
    ]


async def test_resume_updates_pending_on_repeat_interrupt(anon_client, monkeypatch, make_agent):
    store = FakeStore(pending=_record(question="旧追问"))
    wrapper = FakeWrapper(
        "http://h:9900/",
        [InputRequired(task_id="task-1", context_id="ctx-1", question="请补充预算")],
    )

    async def fake_wrappers(agent):
        return [wrapper]

    async def fake_instance(agent):
        return FakeGraph()

    _patch_agent(monkeypatch, make_agent)
    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "get_agent_wrappers", fake_wrappers)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post("/api/chat", json={"message": "只有日期", "thread_id": "t1"})

    assert "event: interrupt" in resp.text
    assert "请补充预算" in resp.text
    assert store.upserts[0]["question"] == "请补充预算"
    assert store.upserts[0]["task_id"] == "task-1"


async def test_normal_flow_emits_interrupt_when_pending_appears(anon_client, monkeypatch, make_agent):
    """正常轮（走 LLM）轮末复查挂起表：工具层刚写入 → 发 interrupt 事件。"""
    store = FakeStore(pending=None)

    def on_stream():
        store.pending = _record()

    graph = FakeGraph(on_stream=on_stream)

    async def fake_instance(agent):
        return graph

    _patch_agent(monkeypatch, make_agent)
    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "get_agent_instance", fake_instance)

    resp = await anon_client.post("/api/chat", json={"message": "帮我规划南昌", "thread_id": "t1"})

    body = resp.text
    assert "event: token" in body
    assert "event: interrupt" in body
    assert "请补充目的地" in body


async def test_resume_clears_pending_on_target_error(anon_client, monkeypatch, make_agent):
    store = FakeStore(pending=_record())
    wrapper = FakeWrapper("http://h:9900/", [], error=A2ATargetError("network", "down"))

    async def fake_wrappers(agent):
        return [wrapper]

    async def fake_notify(*args, **kwargs):
        return None

    _patch_agent(monkeypatch, make_agent)
    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "get_agent_wrappers", fake_wrappers)
    monkeypatch.setattr(chat_mod, "notify_alert", fake_notify)

    resp = await anon_client.post("/api/chat", json={"message": "补充", "thread_id": "t1"})

    assert "event: error" in resp.text
    assert store.deleted == ["t1"]


async def test_delete_conversation_clears_pending(anon_client, monkeypatch):
    store = FakeStore()

    async def fake_delete(session, identity, thread_id):
        return True

    class FakeCheckpointer:
        async def adelete_thread(self, thread_id):
            return None

    async def fake_checkpointer():
        return FakeCheckpointer()

    monkeypatch.setattr(chat_mod, "default_pending_store", store)
    monkeypatch.setattr(chat_mod, "delete_conversation", fake_delete)
    monkeypatch.setattr(chat_mod, "get_checkpointer", fake_checkpointer)

    resp = await anon_client.delete("/api/chat/conversations/t1")

    assert resp.status_code == 200
    assert store.deleted == ["t1"]
```

- [ ] **步骤 4.2 运行测试验证失败**

运行：`uv run pytest tests/test_chat_input_required.py -q`
预期：FAIL（`default_pending_store` / `get_agent_wrappers` 不存在；恢复分支未实现）。

- [ ] **步骤 4.3 实现 chat 路由与 agent_factory**

`agent_factory.py` 新增（放在 `get_agent_instance` 之后）：

```python
async def get_agent_wrappers(agent: AgentConfig) -> list[A2AClientWrapper]:
    """获取 Agent 的 A2A 客户端包装列表（命中缓存；未构建则先构建图实例）。"""
    key = (agent.id, agent.updated_at.isoformat() if agent.updated_at else "")
    if key in _cache:
        return _cache[key][0]
    await get_agent_instance(agent)
    return _cache[key][0]
```

`routes/chat.py`：

1) import 区调整：

```python
from langchain_core.messages import AIMessage, HumanMessage
```

并新增：

```python
from ..a2a_client import A2ATargetError, InputRequired, TextChunk
from ..agent_factory import get_agent_instance, get_agent_wrappers, get_checkpointer
from ..pending_store import PendingRecord, default_pending_store
```

2) `_stream_graph_events` 在 `yield _sse("done", ...)` 之前插入轮末检查（try 块内）：

```python
        try:
            pending = await default_pending_store.get(thread_id)
        except Exception:
            logger.exception("查询挂起任务失败 thread=%s", thread_id)
            pending = None
        if pending is not None:
            yield _sse("interrupt", {"question": pending.question})
        yield _sse("done", {"thread_id": thread_id})
```

3) `_stream_chat` 加分流：

```python
async def _stream_chat(agent: AgentConfig, message: str, thread_id: str):
    """生成新对话的 SSE 事件流；命中挂起任务时走透明转发恢复。"""
    try:
        graph = await get_agent_instance(agent)
    except Exception as exc:
        logger.exception("Agent 实例构建失败 slug=%s", agent.slug)
        await notify_alert("Agent 加载失败", f"slug={agent.slug} error={exc}")
        yield _sse("error", {"detail": "Agent 加载失败"})
        return

    try:
        pending = await default_pending_store.get(thread_id)
    except Exception:
        logger.exception("查询挂起任务失败 thread=%s", thread_id)
        pending = None
    if pending is not None:
        async for evt in _resume_pending(agent, pending, message, thread_id):
            yield evt
        return

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    graph_input = {"messages": [HumanMessage(content=message)]}
    async for evt in _stream_graph_events(graph, config, graph_input, thread_id):
        yield evt
```

4) 新增 `_resume_pending` 与 `_append_history`（放在 `_stream_chat` 之后）：

```python
async def _resume_pending(
    agent: AgentConfig, pending: PendingRecord, message: str, thread_id: str
):
    """挂起任务的恢复轮：带 task_id 透明转发给下游（不经网关 LLM）。"""
    try:
        wrappers = await get_agent_wrappers(agent)
    except Exception:
        logger.exception("恢复轮加载 Agent 失败 slug=%s", agent.slug)
        await default_pending_store.delete(thread_id)
        yield _sse("error", {"detail": "Agent 加载失败，请重新发起对话"})
        yield _sse("done", {"thread_id": thread_id})
        return

    wrapper = next((w for w in wrappers if w.target.url == pending.target_url), None)
    if wrapper is None:
        await default_pending_store.delete(thread_id)
        yield _sse("error", {"detail": "下游目标配置已变化，请重新描述需求"})
        yield _sse("done", {"thread_id": thread_id})
        return

    collected: list[str] = []
    again: InputRequired | None = None
    try:
        async for event in wrapper.stream_message_events(
            message, task_id=pending.task_id, context_id=pending.context_id or None
        ):
            if isinstance(event, TextChunk):
                collected.append(event.text)
                yield _sse("token", {"content": event.text})
            elif isinstance(event, InputRequired):
                again = event
    except A2ATargetError as exc:
        logger.warning("恢复挂起任务失败 thread=%s: %s", thread_id, exc)
        await notify_alert(
            "挂起任务恢复失败",
            f"thread={thread_id} target={pending.target_url} error={exc}",
        )
        await default_pending_store.delete(thread_id)
        yield _sse("error", {"detail": "目标暂时不可用，请稍后重试或重新描述需求"})
        yield _sse("done", {"thread_id": thread_id})
        return

    if again is not None:
        await default_pending_store.upsert(
            thread_id=thread_id,
            agent_id=pending.agent_id,
            target_url=pending.target_url,
            target_name=pending.target_name,
            task_id=again.task_id,
            context_id=again.context_id,
            question=again.question,
        )
    else:
        await default_pending_store.delete(thread_id)

    await _append_history(agent, thread_id, message, "".join(collected))

    if again is not None:
        yield _sse("interrupt", {"question": again.question})
    yield _sse("done", {"thread_id": thread_id})


async def _append_history(
    agent: AgentConfig, thread_id: str, user_text: str, reply_text: str
) -> None:
    """把恢复轮的「用户补充 + 下游回复」追加进会话历史（失败不影响主流程）。"""
    try:
        graph = await get_agent_instance(agent)
        messages: list[Any] = [HumanMessage(content=user_text)]
        if reply_text:
            messages.append(AIMessage(content=reply_text))
        await graph.aupdate_state(
            {"configurable": {"thread_id": thread_id}},
            {"messages": messages},
            as_node="agent",
        )
    except Exception:
        logger.exception("追加恢复轮历史失败 thread=%s", thread_id)
```

5) `delete_chat_conversation` 在 checkpointer 清理之前加：

```python
    try:
        await default_pending_store.delete(thread_id)
    except Exception:
        logger.exception("清理挂起任务失败 thread=%s", thread_id)
```

- [ ] **步骤 4.4 运行测试验证通过**

运行：`uv run pytest tests/test_chat_input_required.py -q && uv run pytest tests -q`
预期：全部 PASS（既有 test_chat_api.py 不回归）。

- [ ] **步骤 4.5 Commit**

```bash
git add src/a2a_gateway/routes/chat.py src/a2a_gateway/agent_factory.py tests/test_chat_input_required.py
git commit -m "feat: 对话链路拦截挂起任务并透明转发恢复（含 interrupt 事件）"
```

---

### 任务 5：前端等待补充提示

**文件：**
- 修改：`web/src/lib/api.ts`、`web/src/components/ChatPage.tsx`
- 测试：`web/src/lib/api.test.ts`

- [ ] **步骤 5.1 编写失败的测试**

`web/src/lib/api.test.ts` 的 import 改为：

```ts
import { parseSSEBlock, parseSSEEvent, splitSSEBlocks } from "./api";
```

文件末尾追加：

```ts
describe("parseSSEEvent", () => {
  it("解析 interrupt 事件（等待用户补充信息）", () => {
    expect(parseSSEEvent("interrupt", '{"question":"请补充目的地"}')).toEqual({
      type: "interrupt",
      question: "请补充目的地",
    });
  });

  it("interrupt 缺少 question 字段时降级为空串", () => {
    expect(parseSSEEvent("interrupt", "{}")).toEqual({ type: "interrupt", question: "" });
  });

  it("解析 token 事件", () => {
    expect(parseSSEEvent("token", '{"content":"hi"}')).toEqual({ type: "token", content: "hi" });
  });

  it("未知事件返回 null", () => {
    expect(parseSSEEvent("ping", "{}")).toBeNull();
  });

  it("非法 JSON 返回 null", () => {
    expect(parseSSEEvent("token", "not-json")).toBeNull();
  });
});
```

- [ ] **步骤 5.2 运行测试验证失败**

运行：`cd web && npm test`
预期：FAIL（`parseSSEEvent` 未导出）。

- [ ] **步骤 5.3 实现 api.ts 与 ChatPage.tsx**

`web/src/lib/api.ts`：

1) `SSEEvent` 联合类型加一行（放在 `tool_end` 与 `done` 之间）：

```ts
  | { type: "interrupt"; question: string }
```

2) `parseSSEBlock` 之后新增：

```ts
/**
 * 把单个 SSE 事件的 event 名与 data 负载映射为 SSEEvent；
 * 未知事件或非法 JSON 返回 null。独立导出便于单测（consumeSSE 内部同样走这里）。
 */
export function parseSSEEvent(eventName: string, raw: string): SSEEvent | null {
  try {
    const parsed = JSON.parse(raw);
    switch (eventName) {
      case "token":
        return { type: "token", content: parsed.content || "" };
      case "tool_start":
        return { type: "tool_start", name: parsed.name || "" };
      case "tool_end":
        return {
          type: "tool_end",
          name: parsed.name || "",
          output: String(parsed.output ?? ""),
        };
      case "interrupt":
        return { type: "interrupt", question: String(parsed.question ?? "") };
      case "done":
        return { type: "done", thread_id: parsed.thread_id || "" };
      case "error":
        return { type: "error", detail: parsed.detail || "对话出错" };
      default:
        return null;
    }
  } catch {
    return null;
  }
}
```

3) `consumeSSE` 内部的 `try { const parsed = JSON.parse(raw); switch ... }` 块整体替换为：

```ts
      const event = parseSSEEvent(eventName, raw);
      if (!event) continue;
      if (event.type === "done") finalThreadId = event.thread_id || finalThreadId;
      onEvent(event);
```

`web/src/components/ChatPage.tsx`：

1) 状态：在其它 useState 附近加：

```ts
  const [waiting, setWaiting] = useState(false);
```

2) `consumeChatEvents` 的 `error` 分支之前加：

```ts
      } else if (e.type === "interrupt") {
        setWaiting(true);
```

3) `handleSend` 中设置消息状态的 `setMessages` 调用之前加：

```ts
      setWaiting(false);
```

4) JSX：在「错误提示」区块之前插入：

```tsx
        {/* 等待补充提示（下游任务追问用户） */}
        {waiting && (
          <Alert severity="info" sx={{ mx: 2, mb: 1 }}>
            等待你补充信息，请直接回复
          </Alert>
        )}
```

- [ ] **步骤 5.4 运行测试验证通过**

运行：`cd web && npm test && npm run lint`
预期：vitest 全绿、lint 通过。

- [ ] **步骤 5.5 Commit**

```bash
git add web/src/lib/api.ts web/src/lib/api.test.ts web/src/components/ChatPage.tsx
git commit -m "feat: 前端 interrupt 事件与等待补充提示"
```

---

### 任务 6：链路 B（A2A Server 出口）

**文件：**
- 修改：`src/a2a_gateway/routes/a2a_server.py`
- 测试：`tests/test_a2a_server.py`

- [ ] **步骤 6.1 编写失败的测试**

`tests/test_a2a_server.py` 顶部 import 增补：

```python
from types import SimpleNamespace

from a2a_gateway.a2a_client import Completed, TextChunk
```

文件末尾追加：

```python
# ---------------------------------------------------------------------------
# input-required：中断映射与任务恢复
# ---------------------------------------------------------------------------
class FakePendingStore:
    def __init__(self, pending=None):
        self.pending = pending
        self.upserts = []
        self.deleted = []

    async def get(self, thread_id):
        return self.pending

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        self.pending = _pending(**kwargs)

    async def delete(self, thread_id):
        self.deleted.append(thread_id)
        self.pending = None


def _pending(**overrides):
    base = {
        "thread_id": "task-1",
        "agent_id": 1,
        "target_url": "http://h:9900/",
        "target_name": "travel",
        "task_id": "task-1",
        "context_id": "ctx-1",
        "question": "请补充目的地",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeResumeWrapper:
    def __init__(self, url, events):
        self.target = SimpleNamespace(url=url)
        self._events = events
        self.calls = []

    async def stream_message_events(self, text, *, task_id=None, context_id=None, **kwargs):
        self.calls.append({"text": text, "task_id": task_id, "context_id": context_id})
        for event in self._events:
            yield event


def _parse_sse(text: str) -> list:
    return [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def _published(make_agent):
    async def fake_get_agent(session, slug):
        return make_agent(slug=slug, status=AgentStatus.PUBLISHED)

    return fake_get_agent


async def test_streaming_new_task_emits_input_required(
    anon_client, monkeypatch, make_agent, make_api_key
):
    """下游在本次调用中进入 input-required → 终帧应为 INPUT_REQUIRED 且带追问。"""
    _patch_api_key(monkeypatch, make_api_key)
    store = FakePendingStore()

    async def fake_stream(agent, text, thread_id):
        store.pending = _pending(thread_id=thread_id)
        yield "请补充目的地"

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", _published(make_agent))
    monkeypatch.setattr(a2a_server_mod, "_stream_agent_text", fake_stream)
    monkeypatch.setattr(a2a_server_mod, "default_pending_store", store)

    body = {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage",
            "params": {"message": {"messageId": "m1", "parts": [{"text": "帮我规划"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})

    events = _parse_sse(resp.text)
    final_status = events[-1]["result"]["statusUpdate"]["status"]
    assert final_status["state"] == "TASK_STATE_INPUT_REQUIRED"
    texts = "".join(p["text"] for p in final_status["message"]["parts"] if "text" in p)
    assert texts == "请补充目的地"


async def test_resume_streaming_with_task_id(
    anon_client, monkeypatch, make_agent, make_api_key
):
    """带 task_id 的请求命中挂起 → 透明转发恢复，不经 LLM。"""
    _patch_api_key(monkeypatch, make_api_key)
    store = FakePendingStore(pending=_pending())
    wrapper = FakeResumeWrapper(
        "http://h:9900/", [TextChunk("行程"), Completed(task_id="task-1")]
    )

    async def fake_wrappers(agent):
        return [wrapper]

    async def fake_stream(agent, text, thread_id):
        raise AssertionError("恢复轮不应经过 LLM 图")
        yield  # pragma: no cover

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", _published(make_agent))
    monkeypatch.setattr(a2a_server_mod, "default_pending_store", store)
    monkeypatch.setattr(a2a_server_mod, "get_agent_wrappers", fake_wrappers)
    monkeypatch.setattr(a2a_server_mod, "_stream_agent_text", fake_stream)

    body = {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage",
            "params": {"message": {"messageId": "m2", "taskId": "task-1",
                                   "parts": [{"text": "杭州 10/1-10/3 预算3000"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})

    events = _parse_sse(resp.text)
    assert events[-1]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert wrapper.calls == [
        {"text": "杭州 10/1-10/3 预算3000", "task_id": "task-1", "context_id": "ctx-1"}
    ]
    assert store.deleted == ["task-1"]


async def test_resume_unknown_task_returns_error(
    anon_client, monkeypatch, make_agent, make_api_key
):
    _patch_api_key(monkeypatch, make_api_key)
    store = FakePendingStore(pending=None)

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", _published(make_agent))
    monkeypatch.setattr(a2a_server_mod, "default_pending_store", store)

    body = {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage",
            "params": {"message": {"messageId": "m2", "taskId": "gone",
                                   "parts": [{"text": "补充"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})

    assert "error" in resp.json()


async def test_new_task_uses_same_id_for_task_and_context(
    anon_client, monkeypatch, make_agent, make_api_key
):
    """标识统一：新任务的对外 task_id 与 context_id 相同（挂起表主键可被恢复命中）。"""
    _patch_api_key(monkeypatch, make_api_key)

    async def fake_stream(agent, text, thread_id):
        yield "ok"

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", _published(make_agent))
    monkeypatch.setattr(a2a_server_mod, "_stream_agent_text", fake_stream)

    body = {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage",
            "params": {"message": {"messageId": "m1", "parts": [{"text": "hi"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})

    events = _parse_sse(resp.text)
    task = events[0]["result"]["task"]
    assert task["id"] == task["contextId"]


async def test_send_message_nonstream_returns_input_required_task(
    anon_client, monkeypatch, make_agent, make_api_key
):
    _patch_api_key(monkeypatch, make_api_key)
    store = FakePendingStore()

    async def fake_stream(agent, text, thread_id):
        store.pending = _pending(thread_id=thread_id)
        yield "请补充预算"

    monkeypatch.setattr(a2a_server_mod, "get_agent_by_slug", _published(make_agent))
    monkeypatch.setattr(a2a_server_mod, "_stream_agent_text", fake_stream)
    monkeypatch.setattr(a2a_server_mod, "default_pending_store", store)

    body = {"jsonrpc": "2.0", "id": 7, "method": "SendMessage",
            "params": {"message": {"messageId": "m1", "parts": [{"text": "帮我规划"}]}}}
    resp = await anon_client.post("/a2a/demo", json=body, headers={"X-Api-Key": TEST_KEY})

    task = resp.json()["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert task["id"] == task["contextId"]
```

- [ ] **步骤 6.2 运行测试验证失败**

运行：`uv run pytest tests/test_a2a_server.py -q`
预期：新增用例 FAIL（`default_pending_store` 未定义 / 状态仍为 COMPLETED）。

- [ ] **步骤 6.3 实现 a2a_server 链路 B**

`routes/a2a_server.py`：

1) import 区调整：

```python
from langchain_core.messages import AIMessage, HumanMessage
```

（原 `HumanMessage` 行合并），并新增：

```python
from ..a2a_client import A2ATargetError, InputRequired, TextChunk
from ..agent_factory import get_agent_instance, get_agent_wrappers
from ..notifier import notify_alert
from ..pending_store import PendingRecord, default_pending_store
```

`from a2a.types.a2a_pb2 import (...)` 中补充 `TASK_STATE_INPUT_REQUIRED` 与 `Task`。

2) `_rpc_error` 之后新增两个辅助：

```python
def _rpc_error_payload(request_id: Any, error: Any) -> dict[str, Any]:
    """与 ``_rpc_error`` 同构，但返回 dict（供需要返回值而非响应对象的路径使用）。"""
    return build_error_response(request_id, error)


def _stream_error(request_id: Any, detail: str) -> dict[str, str]:
    """SSE 流内错误帧（与既有「流式处理失败」格式一致）。"""
    return {
        "event": "error",
        "data": json.dumps(
            build_error_response(request_id, InvalidRequestError(message=detail)),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
```

3) `a2a_rpc` 中把原来的：

```python
    context_id = req.message.context_id or req.message.task_id or uuid.uuid4().hex
    task_id = req.message.task_id or uuid.uuid4().hex
```

替换为（并在其后加入恢复分流，位置在原 `try:` 之前）：

```python
    incoming_task_id = req.message.task_id or ""
    incoming_context_id = req.message.context_id or ""
    if incoming_task_id:
        task_id = incoming_task_id
        context_id = incoming_context_id or incoming_task_id
    else:
        # 标识统一：新任务的对外 task_id 与 context_id 同值，
        # 挂起表主键（thread_id）即可被后续恢复请求的 task_id 命中。
        task_id = context_id = incoming_context_id or uuid.uuid4().hex

    if incoming_task_id:
        pending = await default_pending_store.get(incoming_task_id)
        if pending is None or pending.agent_id != agent.id:
            return _rpc_error(request_id, TaskNotFoundError())
        try:
            if method in STREAMING_METHODS:
                return EventSourceResponse(
                    _rpc_resume_stream(request_id, agent, pending, text, task_id, context_id)
                )
            return JSONResponse(
                await _rpc_resume_message(request_id, agent, pending, text, task_id, context_id)
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("A2A 恢复处理失败 slug=%s task=%s", agent.slug, task_id)
            return _rpc_error(request_id, InvalidRequestError(message="Agent 处理失败"))
```

4) `_rpc_stream` 的收尾帧（固定的 COMPLETED）替换为按挂起表分流：

```python
        pending = await default_pending_store.get(context_id)
        if pending is not None:
            yield _sse_result(
                request_id,
                StreamResponse(
                    status_update=new_text_status_update_event(
                        task_id=task_id,
                        context_id=context_id,
                        state=TASK_STATE_INPUT_REQUIRED,
                        text=pending.question,
                    )
                ),
            )
        else:
            yield _sse_result(
                request_id,
                StreamResponse(
                    status_update=TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        status=TaskStatus(state=TASK_STATE_COMPLETED),
                    )
                ),
            )
```

5) `_rpc_send_message` 轮末分流：

```python
async def _rpc_send_message(
    agent: AgentConfig, text: str, context_id: str, task_id: str, request_id: Any
) -> dict[str, Any]:
    """非流式 SendMessage：等 Agent 跑完后返回；下游中断时返回 INPUT_REQUIRED 任务快照。"""
    chunks: list[str] = []
    async for chunk in _stream_agent_text(agent, text, context_id):
        chunks.append(chunk)

    pending = await default_pending_store.get(context_id)
    if pending is not None:
        task = Task(id=task_id, context_id=context_id)
        task.status.state = TASK_STATE_INPUT_REQUIRED
        task.status.message.CopyFrom(
            new_text_message(pending.question, context_id=context_id, task_id=task_id)
        )
        response = SendMessageResponse(task=task)
    else:
        message = new_text_message("".join(chunks), context_id=context_id, task_id=task_id)
        response = SendMessageResponse(message=message)
    result = MessageToDict(response)
    return {"jsonrpc": "2.0", "id": request_id, "result": result}
```

6) 文件末尾新增恢复实现：

```python
# ---------------------------------------------------------------------------
# input-required 恢复（链路 B）：带 task_id 的请求 → 透明转发下游
# ---------------------------------------------------------------------------
async def _match_wrapper(agent: AgentConfig, pending: PendingRecord) -> Any | None:
    """按挂起记录匹配 Agent 当前的 A2A wrapper（配置变化时返回 None）。"""
    try:
        wrappers = await get_agent_wrappers(agent)
    except Exception:
        logger.exception("加载 Agent wrappers 失败 slug=%s", agent.slug)
        return None
    return next((w for w in wrappers if w.target.url == pending.target_url), None)


async def _append_resume_history(
    agent: AgentConfig, thread_id: str, user_text: str, reply_text: str
) -> None:
    """把恢复轮的「用户补充 + 下游回复」追加进会话历史（失败不影响主流程）。"""
    try:
        graph = await get_agent_instance(agent)
        messages: list[Any] = [HumanMessage(content=user_text)]
        if reply_text:
            messages.append(AIMessage(content=reply_text))
        await graph.aupdate_state(
            {"configurable": {"thread_id": thread_id}},
            {"messages": messages},
            as_node="agent",
        )
    except Exception:
        logger.exception("追加恢复轮历史失败 thread=%s", thread_id)


async def _settle_pending(pending: PendingRecord, again: InputRequired | None) -> None:
    """恢复结束后的挂起表收尾：再次中断 → 刷新；完成 → 删除。"""
    if again is not None:
        await default_pending_store.upsert(
            thread_id=pending.thread_id,
            agent_id=pending.agent_id,
            target_url=pending.target_url,
            target_name=pending.target_name,
            task_id=again.task_id,
            context_id=again.context_id,
            question=again.question,
        )
    else:
        await default_pending_store.delete(pending.thread_id)


async def _rpc_resume_stream(
    request_id: Any,
    agent: AgentConfig,
    pending: PendingRecord,
    text: str,
    task_id: str,
    context_id: str,
) -> AsyncGenerator[dict[str, str], None]:
    """恢复挂起任务（流式）：不重发 Task 首帧，增量文本走 working 状态事件。"""
    wrapper = await _match_wrapper(agent, pending)
    if wrapper is None:
        await default_pending_store.delete(pending.thread_id)
        yield _stream_error(request_id, "下游目标配置已变化，请重新发起任务")
        return

    chunks: list[str] = []
    again: InputRequired | None = None
    try:
        async for event in wrapper.stream_message_events(
            text, task_id=pending.task_id, context_id=pending.context_id or None
        ):
            if isinstance(event, TextChunk):
                chunks.append(event.text)
                yield _sse_result(
                    request_id,
                    StreamResponse(
                        status_update=new_text_status_update_event(
                            task_id=task_id,
                            context_id=context_id,
                            state=TASK_STATE_WORKING,
                            text=event.text,
                        )
                    ),
                )
            elif isinstance(event, InputRequired):
                again = event
    except A2ATargetError as exc:
        logger.warning("恢复挂起任务失败 task=%s: %s", task_id, exc)
        await notify_alert(
            "A2A 恢复失败",
            f"task={pending.thread_id} target={pending.target_url} error={exc}",
        )
        await default_pending_store.delete(pending.thread_id)
        yield _stream_error(request_id, "目标暂时不可用，请稍后重试")
        return

    await _settle_pending(pending, again)
    await _append_resume_history(agent, context_id, text, "".join(chunks))

    if again is not None:
        yield _sse_result(
            request_id,
            StreamResponse(
                status_update=new_text_status_update_event(
                    task_id=task_id,
                    context_id=context_id,
                    state=TASK_STATE_INPUT_REQUIRED,
                    text=again.question,
                )
            ),
        )
    else:
        yield _sse_result(
            request_id,
            StreamResponse(
                status_update=TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TASK_STATE_COMPLETED),
                )
            ),
        )


async def _rpc_resume_message(
    request_id: Any,
    agent: AgentConfig,
    pending: PendingRecord,
    text: str,
    task_id: str,
    context_id: str,
) -> dict[str, Any]:
    """恢复挂起任务（非流式）：返回任务快照（INPUT_REQUIRED 或 COMPLETED）。"""
    wrapper = await _match_wrapper(agent, pending)
    if wrapper is None:
        await default_pending_store.delete(pending.thread_id)
        return _rpc_error_payload(
            request_id, InvalidRequestError(message="下游目标配置已变化，请重新发起任务")
        )

    chunks: list[str] = []
    again: InputRequired | None = None
    try:
        async for event in wrapper.stream_message_events(
            text, task_id=pending.task_id, context_id=pending.context_id or None
        ):
            if isinstance(event, TextChunk):
                chunks.append(event.text)
            elif isinstance(event, InputRequired):
                again = event
    except A2ATargetError as exc:
        logger.warning("恢复挂起任务失败 task=%s: %s", task_id, exc)
        await notify_alert(
            "A2A 恢复失败",
            f"task={pending.thread_id} target={pending.target_url} error={exc}",
        )
        await default_pending_store.delete(pending.thread_id)
        return _rpc_error_payload(
            request_id, InvalidRequestError(message="目标暂时不可用，请稍后重试")
        )

    await _settle_pending(pending, again)
    await _append_resume_history(agent, context_id, text, "".join(chunks))

    task = Task(id=task_id, context_id=context_id)
    if again is not None:
        task.status.state = TASK_STATE_INPUT_REQUIRED
        task.status.message.CopyFrom(
            new_text_message(again.question, context_id=context_id, task_id=task_id)
        )
    else:
        task.status.state = TASK_STATE_COMPLETED
        if chunks:
            task.status.message.CopyFrom(
                new_text_message("".join(chunks), context_id=context_id, task_id=task_id)
            )
    response = SendMessageResponse(task=task)
    return {"jsonrpc": "2.0", "id": request_id, "result": MessageToDict(response)}
```

（`AsyncGenerator` 已在 import 区；若缺失则补 `from collections.abc import AsyncGenerator`。）

- [ ] **步骤 6.4 运行测试验证通过**

运行：`uv run pytest tests/test_a2a_server.py -q && uv run pytest tests -q`
预期：全部 PASS（既有 `test_send_streaming_message_sse` 等用例不回归——事件序列仍为 Task → working → completed）。

- [ ] **步骤 6.5 Commit**

```bash
git add src/a2a_gateway/routes/a2a_server.py tests/test_a2a_server.py
git commit -m "feat: A2A Server 对外暴露 INPUT_REQUIRED 并支持 task_id 恢复"
```

---

### 任务 7：全量回归、联调验收与收尾

**文件：**
- 修改：`TODO.md`

- [ ] **步骤 7.1 全量测试与静态检查**

```bash
uv run pytest tests -q
uv run ruff check src tests
cd web && npm test && npm run lint
```
预期：全绿。

- [ ] **步骤 7.2 联调验收（链路 A，手动）**

1. 启动依赖并执行迁移：`docker compose up -d postgres` → `uv run alembic upgrade head`（确认 `pending_a2a_tasks` 表生成）。
2. 启动后端 `uv run python -m a2a_gateway.main`；确认目标 Agent 绑定了 travel-agent（`http://43.156.187.79:10101`）。
3. 浏览器访问聊天页面，发送「帮我规划南昌的旅行」→ 预期：收到追问 + 「等待你补充信息」提示条。
4. 回复「9/27-10/1，预算3000」→ 预期：行程输出、提示条消失。
5. 协议级恢复验证（devops-43 上执行）：对比验收前后 travel-agent 的线程数（不再新增新线程），且该 thread 的消息中出现两条 user 内容（原始需求 + 补充）。

- [ ] **步骤 7.3 联调验收（链路 B，手动）**

用脚本调用网关 `/a2a/{slug}`（带该 Agent 的 API Key）：
1. `SendStreamingMessage`（无 task_id）→ 下游中断时最后事件应为 `TASK_STATE_INPUT_REQUIRED` 且携带追问。
2. 带 `taskId` 重发用户补充 → 最终 `TASK_STATE_COMPLETED`，且 travel-agent 侧为同一任务恢复。

- [ ] **步骤 7.4 更新 TODO**

`TODO.md`：
- 第 99 行 `- [ ] 实现 A2A 调用中 input-required 状态的处理策略...` 改为 `- [x] ...（pending_a2a_tasks 挂起表 + 结构化事件流 + 双链路恢复）`
- 第 159 行改为 `- [x] ...（SSE interrupt 事件 + 等待补充提示）`

- [ ] **步骤 7.5 Commit**

```bash
git add TODO.md
git commit -m "docs: 标记 input-required 二期功能完成"
```

---

## 附：验收标准（来自规格）

1. 链路 A：追问展示 → 补充 → 下游同一 task 恢复（DB 验证同一 thread 出现 2 条用户消息）→ 行程输出；刷新页面历史完整。
2. 链路 B：外部 A2A 客户端收到 `TASK_STATE_INPUT_REQUIRED` → 带 task_id 重发 → `TASK_STATE_COMPLETED`。
3. 回归：无中断场景（普通对话、工具调用、MCP）行为不变；后端与前端测试全绿。
