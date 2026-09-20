"""Skill 绑定 / 快照 / 引用刷新测试（repository 纯函数部分 + 路由门禁见文件尾）。"""

from typing import Any, cast

import pytest

from a2a_gateway.models import Skill
from a2a_gateway.repository import (
    binding_content_bytes,
    skill_snapshot,
    validate_skill_bindings,
)
from a2a_gateway.skills import MAX_BINDING_CONTENT_BYTES


def _snapshot(**kw):
    base = {
        "id": 1,
        "name": "a-skill",
        "description": "d",
        "content": "正文",
        "load_mode": "on_demand",
        "files": [{"path": "x.md", "size": 12, "content": "附件内容"}],
        "review_status": "approved",
    }
    base.update(kw)
    return base


class _FakeSkill:
    """最小 ORM 替身（validate/snapshot 只读这些属性）。

    不继承 Skill：SQLAlchemy 子类会触发单表继承映射，纯函数测试用不上真实 ORM，
    交给纯函数时用 cast 声明「它满足 Skill 的属性契约」。
    """

    id: int
    name: str
    description: str
    content: str
    load_mode: str
    files: list[dict[str, Any]]
    review_status: str

    def __init__(self, **kw: Any):
        for key, value in _snapshot().items():
            setattr(self, key, value)
        for key, value in kw.items():
            setattr(self, key, value)


def _as_skill(fake: _FakeSkill) -> Skill:
    """替身交给纯函数：snapshot / 门禁只读属性，不碰 ORM 行为。"""
    return cast(Skill, fake)


def test_skill_snapshot_shape():
    snap = skill_snapshot(_as_skill(_FakeSkill()))
    # 键集恰为这七个：多一个会泄内部字段，少一个运行时会拿不到
    assert set(snap) == {
        "id",
        "name",
        "description",
        "content",
        "load_mode",
        "files",
        "review_status",
    }
    assert snap["review_status"] == "approved"
    assert snap["files"][0]["path"] == "x.md"


def test_binding_content_bytes_excludes_files():
    snap = skill_snapshot(_as_skill(_FakeSkill()))
    assert binding_content_bytes([snap]) == len("正文".encode("utf-8"))


def test_validate_binding_over_limit_rejected():
    # 「字」UTF-8 占 3 字节：MAX//3 个字仅 131070 字节，仍差 2 字节在限内，故 +1
    huge = _FakeSkill(content="字" * (MAX_BINDING_CONTENT_BYTES // 3 + 1))
    with pytest.raises(ValueError, match="总量"):
        validate_skill_bindings(records=[huge], statuses={huge.id: "approved"})


def test_validate_binding_pending_rejected():
    with pytest.raises(ValueError, match="pending"):
        validate_skill_bindings(records=[_FakeSkill()], statuses={1: "pending"})


def test_validate_binding_rejected_rejected():
    with pytest.raises(ValueError, match="rejected"):
        validate_skill_bindings(records=[_FakeSkill()], statuses={1: "rejected"})


def test_validate_binding_missing_skill_rejected():
    with pytest.raises(ValueError, match="不存在"):
        validate_skill_bindings(records=[], statuses={}, requested_ids=[1])


def test_validate_binding_ok_passes():
    validate_skill_bindings(records=[_FakeSkill()], statuses={1: "approved"}, requested_ids=[1])


# ---------------------------------------------------------------------------
# repository 层核心语义（离线：假会话 + ORM 替身，不连库）
# ---------------------------------------------------------------------------
import types as _types
from datetime import datetime as _datetime
from datetime import timezone as _timezone

from sqlalchemy.ext.asyncio import AsyncSession

from a2a_gateway import repository as repo
from a2a_gateway.models import AgentConfig, SkillReviewStatus
from a2a_gateway.routes import admin as admin_mod
from a2a_gateway.schemas import SkillUpdate


def _now() -> _datetime:
    return _datetime.now(_timezone.utc)


class _SkillRow:
    """Skill 行替身：resolve / 引用刷新只读这些属性（含 enabled 与 review_status）。

    属性用 setattr 注入，故在此声明契约供类型检查；不继承 Skill 以免触发单表继承映射。
    """

    id: int
    name: str
    description: str
    content: str
    frontmatter: dict[str, Any]
    files: list[dict[str, Any]]
    load_mode: str
    size_bytes: int
    file_count: int
    source: str
    source_ref: str
    review_status: SkillReviewStatus
    review_note: str
    reviewed_at: _datetime | None
    enabled: bool

    def __init__(self, **kw: Any):
        base: dict[str, Any] = {
            "id": 1,
            "name": "a-skill",
            "description": "d",
            "content": "正文",
            "frontmatter": {},
            "files": [],
            "load_mode": "on_demand",
            "size_bytes": 0,
            "file_count": 0,
            "source": "manual",
            "source_ref": "",
            "review_status": SkillReviewStatus.APPROVED,
            "review_note": "",
            "reviewed_at": None,
            "enabled": True,
        }
        base.update(kw)
        for key, value in base.items():
            setattr(self, key, value)


class _FakeResult:
    def __init__(self, rows: list[Any]):
        self._rows = list(rows)

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    """最小 AsyncSession 替身：只实现 repository 用到的 execute/add/commit/refresh。"""

    def __init__(self, rows: list[Any] | None = None):
        self._rows = list(rows or [])
        self.committed = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self._rows)

    def add(self, obj: Any) -> None:
        return None

    async def commit(self) -> None:
        self.committed += 1

    async def refresh(self, obj: Any) -> None:
        return None


def _sess(fake: _FakeSession) -> AsyncSession:
    """替身会话交给 repository：只走 execute/commit/refresh，不碰真实 DB。"""
    return cast(AsyncSession, fake)


def _as_row(row: _SkillRow) -> Skill:
    return cast(Skill, row)


def _agent_row(agent_id: int, skill_ids: list[int] | None) -> AgentConfig:
    return cast(
        AgentConfig,
        _types.SimpleNamespace(id=agent_id, skill_ids=skill_ids, skills=[]),
    )


# --- resolve_skills：enabled + approved 双过滤，保持勾选顺序 ---
async def test_resolve_skills_filters_and_keeps_order():
    rows = [
        _SkillRow(id=1, name="ok-1"),
        _SkillRow(id=2, name="ok-2"),
        _SkillRow(id=3, name="pending", review_status=SkillReviewStatus.PENDING),
        _SkillRow(id=4, name="disabled", enabled=False),
        _SkillRow(id=5, name="rejected", review_status=SkillReviewStatus.REJECTED),
    ]
    snaps = await repo.resolve_skills(_sess(_FakeSession(rows)), [2, 3, 1, 4, 5])
    # 只留 approved 且 enabled 的；顺序按勾选而非查库顺序
    assert [s["id"] for s in snaps] == [2, 1]
    assert [s["name"] for s in snaps] == ["ok-2", "ok-1"]


async def test_resolve_skills_empty_ids_short_circuits():
    assert await repo.resolve_skills(_sess(_FakeSession([_SkillRow()])), []) == []


async def test_resolve_skills_marks_status_by_value():
    rows = [_SkillRow(id=1)]
    snap = (await repo.resolve_skills(_sess(_FakeSession(rows)), [1]))[0]
    assert snap["review_status"] == "approved"


# --- update_skill / upsert_imported_skill：内容变更重置为 pending ---
async def test_update_skill_resets_review_on_content_change():
    skill = _SkillRow(review_status=SkillReviewStatus.APPROVED, reviewed_at=_now())
    await repo.update_skill(_sess(_FakeSession()), _as_row(skill), SkillUpdate(content="新正文"))
    assert skill.review_status == SkillReviewStatus.PENDING
    assert skill.reviewed_at is None


async def test_update_skill_resets_review_on_description_change():
    skill = _SkillRow(review_status=SkillReviewStatus.APPROVED, reviewed_at=_now())
    await repo.update_skill(_sess(_FakeSession()), _as_row(skill), SkillUpdate(description="新描述"))
    assert skill.review_status == SkillReviewStatus.PENDING


async def test_update_skill_keeps_review_without_content_change():
    skill = _SkillRow(
        load_mode="on_demand",
        enabled=True,
        review_status=SkillReviewStatus.APPROVED,
        reviewed_at=_now(),
    )
    reviewed = skill.reviewed_at
    await repo.update_skill(
        _sess(_FakeSession()), _as_row(skill), SkillUpdate(load_mode="always", enabled=False)
    )
    assert skill.review_status == SkillReviewStatus.APPROVED
    assert skill.reviewed_at == reviewed
    assert skill.load_mode == "always"
    assert skill.enabled is False


async def test_upsert_imported_skill_overwrite_resets_pending(monkeypatch):
    existing = _SkillRow(review_status=SkillReviewStatus.APPROVED, reviewed_at=_now())

    async def fake_by_name(session: AsyncSession, name: str) -> Skill | None:
        return _as_row(existing)

    monkeypatch.setattr(repo, "get_skill_by_name", fake_by_name)
    parsed = {
        "name": "a-skill",
        "description": "新描述",
        "content": "新正文",
        "frontmatter": {"k": 1},
        "files": [{"path": "a.md", "size": 1, "content": "x"}],
        "size_bytes": 9,
    }
    skill, created = await repo.upsert_imported_skill(
        _sess(_FakeSession()),
        parsed=parsed,
        load_mode="on_demand",
        source="zip",
        source_ref="r.zip",
        overwrite=True,
    )
    assert created is False
    assert skill is existing
    assert existing.content == "新正文"
    assert existing.review_status == SkillReviewStatus.PENDING
    assert existing.reviewed_at is None


async def test_upsert_imported_skill_skip_keeps_review(monkeypatch):
    existing = _SkillRow(content="旧正文", review_status=SkillReviewStatus.APPROVED, reviewed_at=_now())

    async def fake_by_name(session: AsyncSession, name: str) -> Skill | None:
        return _as_row(existing)

    monkeypatch.setattr(repo, "get_skill_by_name", fake_by_name)
    parsed = {"name": "a-skill", "description": "d", "content": "新正文"}
    _, created = await repo.upsert_imported_skill(
        _sess(_FakeSession()),
        parsed=parsed,
        load_mode="on_demand",
        source="zip",
        source_ref="",
        overwrite=False,
    )
    assert created is False
    assert existing.content == "旧正文"
    assert existing.review_status == SkillReviewStatus.APPROVED


async def test_upsert_imported_skill_creates_new_as_pending(monkeypatch):
    async def fake_by_name(session: AsyncSession, name: str) -> Skill | None:
        return None

    monkeypatch.setattr(repo, "get_skill_by_name", fake_by_name)
    parsed = {"name": "brand-new", "description": "d", "content": "正文", "files": []}
    skill, created = await repo.upsert_imported_skill(
        _sess(_FakeSession()),
        parsed=parsed,
        load_mode="always",
        source="text",
        source_ref="",
        overwrite=True,
    )
    assert created is True
    assert skill.name == "brand-new"
    assert skill.load_mode == "always"
    assert skill.review_status == SkillReviewStatus.PENDING


# --- 引用函数：agents_using_skill / refresh_agents_for_skills / detach_skill_from_agents ---
def _patch_list_agents(monkeypatch: pytest.MonkeyPatch, agents: list[AgentConfig]) -> None:
    async def fake_list(session: AsyncSession) -> list[AgentConfig]:
        return agents

    monkeypatch.setattr(repo, "list_agents", fake_list)


async def test_agents_using_skill(monkeypatch):
    agents = [_agent_row(1, [1, 2]), _agent_row(2, [2]), _agent_row(3, None)]
    _patch_list_agents(monkeypatch, agents)
    found = await repo.agents_using_skill(_sess(_FakeSession()), 2)
    assert [a.id for a in found] == [1, 2]


async def test_refresh_agents_for_skills(monkeypatch):
    rows = [
        _SkillRow(id=1, name="ok"),
        _SkillRow(id=2, name="pending", review_status=SkillReviewStatus.PENDING),
    ]
    session = _FakeSession(rows)
    agents = [_agent_row(1, [1]), _agent_row(2, [2]), _agent_row(3, [])]
    _patch_list_agents(monkeypatch, agents)
    changed = await repo.refresh_agents_for_skills(_sess(session), [1, 2])
    assert changed == 2
    assert [s["id"] for s in agents[0].skills] == [1]
    # pending 被 resolve 静默跳过 → 快照清空，但勾选保留（启用即恢复）
    assert agents[1].skills == []
    assert agents[2].skills == []
    assert session.committed == 1


async def test_refresh_agents_for_skills_empty_short_circuits(monkeypatch):
    session = _FakeSession()
    _patch_list_agents(monkeypatch, [_agent_row(1, [1])])
    assert await repo.refresh_agents_for_skills(_sess(session), []) == 0
    assert session.committed == 0


async def test_detach_skill_from_agents(monkeypatch):
    session = _FakeSession([_SkillRow(id=1), _SkillRow(id=2)])
    agents = [_agent_row(1, [1, 2]), _agent_row(2, [2])]
    _patch_list_agents(monkeypatch, agents)
    changed = await repo.detach_skill_from_agents(_sess(session), 1)
    assert changed == 1
    assert agents[0].skill_ids == [2]
    assert agents[1].skill_ids == [2]
    assert [s["id"] for s in agents[0].skills] == [2]
    assert session.committed == 1


# ---------------------------------------------------------------------------
# 绑定门禁（路由层）
# ---------------------------------------------------------------------------
def _agent_for_gate(**kw: Any):
    base: dict[str, Any] = {
        "id": 1, "slug": "demo", "name": "Demo", "description": "",
        "a2a_targets": [], "a2a_target_ids": [], "mcp_server_ids": [],
        "mcp_servers": [], "skill_ids": [5], "skills": [],
        "system_prompt": None, "status": "draft",
        "created_at": _now(), "updated_at": _now(),
    }
    base.update(kw)
    return _types.SimpleNamespace(**base)


async def test_validate_helper_builds_statuses_from_records():
    """statuses 必须由查出的 records 构造：漏掉某个 id 会静默放行。"""
    rows = [_SkillRow(id=1, name="待审", review_status=SkillReviewStatus.PENDING)]
    with pytest.raises(ValueError, match="pending"):
        await admin_mod._validate_skill_bindings(_sess(_FakeSession(rows)), [1])


async def test_validate_helper_allows_disabled():
    """宽松语义：enabled=false 只影响运行时解析，不进门禁。"""
    rows = [_SkillRow(id=1, enabled=False)]
    await admin_mod._validate_skill_bindings(_sess(_FakeSession(rows)), [1])


async def test_validate_helper_rejects_missing_record():
    with pytest.raises(ValueError, match="不存在"):
        await admin_mod._validate_skill_bindings(_sess(_FakeSession([])), [7])


async def test_validate_helper_skips_empty_binding():
    await admin_mod._validate_skill_bindings(_sess(_FakeSession()), [])


async def test_create_agent_rejects_pending_skill(auth_client, monkeypatch):
    captured: list[int] = []

    async def fake_get(session: AsyncSession, slug: str):
        return None

    async def fake_validate(session: AsyncSession, ids: list[int]):
        captured.extend(ids)
        raise ValueError("技能「a」尚未审核通过（pending），不能绑定")

    monkeypatch.setattr(admin_mod, "get_agent_by_slug", fake_get)
    monkeypatch.setattr(admin_mod, "_validate_skill_bindings", fake_validate)
    resp = await auth_client.post(
        "/api/admin/agents", json={"slug": "demo", "name": "Demo", "skill_ids": [5]}
    )
    assert resp.status_code == 400
    assert captured == [5]


async def test_save_agent_rejects_pending_skill(auth_client, monkeypatch):
    captured: list[int] = []

    async def fake_get(session, agent_id):
        return _agent_for_gate()

    async def fake_validate(session, ids):
        captured.extend(ids)
        raise ValueError("技能「a」尚未审核通过（pending），不能绑定")

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)
    monkeypatch.setattr(admin_mod, "_validate_skill_bindings", fake_validate)
    resp = await auth_client.put("/api/admin/agents/1", json={"skill_ids": [5]})
    assert resp.status_code == 400
    assert captured == [5]


async def test_save_agent_without_skill_ids_skips_gate(auth_client, monkeypatch):
    """未提交 skill_ids 即不改动绑定，不该触发门禁。"""
    calls: list[list[int]] = []

    async def fake_get(session, agent_id):
        return _agent_for_gate()

    async def fake_validate(session, ids):
        calls.append(list(ids))

    async def fake_update(session, agent, data):
        return _agent_for_gate()

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)
    monkeypatch.setattr(admin_mod, "_validate_skill_bindings", fake_validate)
    monkeypatch.setattr(admin_mod, "update_agent", fake_update)
    resp = await auth_client.put("/api/admin/agents/1", json={"name": "改名"})
    assert resp.status_code == 200
    assert calls == [[]]


async def test_publish_agent_rejects_pending_skill(auth_client, monkeypatch):
    captured: list[int] = []

    async def fake_get(session, agent_id):
        return _agent_for_gate()

    async def fake_validate(session, ids):
        captured.extend(ids)
        raise ValueError("技能「a」尚未审核通过（pending），不能绑定")

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)
    monkeypatch.setattr(admin_mod, "_validate_skill_bindings", fake_validate)
    resp = await auth_client.post("/api/admin/agents/1/publish")
    assert resp.status_code == 409
    # 发布无请求体，门禁校验的是库里已存的绑定
    assert captured == [5]


async def test_save_agent_allows_disabled_skill(auth_client, monkeypatch):
    """宽松语义：enabled=false 保存不拦（validate 只看 review_status）。"""
    async def fake_get(session, agent_id):
        return _agent_for_gate()

    async def fake_validate(session, ids):
        return None

    async def fake_update(session, agent, data):
        return _agent_for_gate()

    monkeypatch.setattr(admin_mod, "get_agent_by_id", fake_get)
    monkeypatch.setattr(admin_mod, "_validate_skill_bindings", fake_validate)
    monkeypatch.setattr(admin_mod, "update_agent", fake_update)
    resp = await auth_client.put("/api/admin/agents/1", json={"skill_ids": [5]})
    assert resp.status_code == 200
