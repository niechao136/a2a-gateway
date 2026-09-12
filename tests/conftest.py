"""pytest 公共夹具。

测试全部在「无外部依赖」下运行：
- 不连接数据库（用 dependency_overrides / monkeypatch 替身）
- 不触发 FastAPI lifespan（httpx.ASGITransport 不会执行 startup，因此不会跑迁移）
- 不发起真实网络请求（httpx/A2A/LLM 全部 mock）
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from a2a_gateway.database import get_session
from a2a_gateway.deps import get_current_admin
from a2a_gateway.main import app
from a2a_gateway.models import AgentStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def make_agent():
    """构造一个满足 AgentOut 序列化要求的 Agent 替身。"""

    def _make(**overrides):
        base = {
            "id": 1,
            "slug": "demo",
            "name": "Demo Agent",
            "description": "",
            "a2a_targets": [{"url": "http://hermes.local:9900/", "token": "t"}],
            "a2a_target_ids": [1],
            "mcp_server_ids": [],
            "mcp_servers": [],
            "system_prompt": None,
            "status": AgentStatus.DRAFT,
            "created_at": _now(),
            "updated_at": _now(),
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    return _make


@pytest.fixture
def make_admin():
    def _make(**overrides):
        base = {"id": 1, "username": "admin", "password_hash": "x", "disabled": False}
        base.update(overrides)
        return SimpleNamespace(**base)

    return _make


@pytest.fixture
def make_api_key():
    def _make(**overrides):
        base = {
            "id": 1,
            "name": "默认 Key",
            "key": "a2a-test-key",
            "is_default": True,
            "enabled": True,
            "created_at": _now(),
            "updated_at": _now(),
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    return _make


async def _fake_session():
    """替身 DB 会话（路由层已被 monkeypatch，不会真正使用它）。"""
    yield None


@pytest.fixture
async def anon_client():
    """未认证客户端（仅替换数据库会话依赖）。"""
    app.dependency_overrides[get_session] = _fake_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_client(make_admin):
    """已认证的管理端客户端。"""
    admin = make_admin()

    async def _fake_admin():
        return admin

    app.dependency_overrides[get_session] = _fake_session
    app.dependency_overrides[get_current_admin] = _fake_admin
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
