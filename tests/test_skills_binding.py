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
