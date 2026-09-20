"""Skill 数据契约（pydantic schema）测试。"""

import pytest
from pydantic import ValidationError

from a2a_gateway.schemas import AgentCreate, SkillCreate, SkillReviewRequest


def test_skill_create_rejects_bad_load_mode():
    with pytest.raises(ValidationError):
        SkillCreate(name="ok", description="d", content="c", load_mode="always2")  # type: ignore[arg-type]


def test_skill_review_rejects_unknown_status():
    with pytest.raises(ValidationError):
        SkillReviewRequest(status="published")  # type: ignore[arg-type]


def test_agent_create_carries_skill_ids():
    agent = AgentCreate(slug="s", name="n", skill_ids=[1, 2])
    assert agent.skill_ids == [1, 2]
