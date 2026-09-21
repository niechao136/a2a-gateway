"""Skill 数据契约（pydantic schema）测试。"""

import pytest
from pydantic import ValidationError

from a2a_gateway.models import Skill
from a2a_gateway.schemas import AgentCreate, SkillCreate, SkillReviewRequest


def test_skill_model_has_allow_scripts_column():
    col = Skill.__table__.c["allow_scripts"]
    assert col.default.arg is False          # python 端默认
    assert col.server_default is not None    # DB 端默认（迁移侧 FALSE）


def test_skill_create_rejects_bad_load_mode():
    with pytest.raises(ValidationError):
        SkillCreate(name="ok", description="d", content="c", load_mode="always2")  # type: ignore[arg-type]


def test_skill_review_rejects_unknown_status():
    with pytest.raises(ValidationError):
        SkillReviewRequest(status="published")  # type: ignore[arg-type]


def test_agent_create_carries_skill_ids():
    agent = AgentCreate(slug="s", name="n", skill_ids=[1, 2])
    assert agent.skill_ids == [1, 2]
