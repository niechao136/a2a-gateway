"""模型管理：ORM schema 定义测试（无需 DB，纯元数据断言）。"""
from a2a_gateway.models import AgentConfig, LLMModel, LLMProvider


def test_provider_values():
    assert [m.value for m in LLMProvider] == ["openai", "anthropic"]


def test_llm_model_columns():
    cols = {c.name for c in LLMModel.__table__.columns}
    assert {
        "id", "name", "provider", "base_url", "api_key",
        "model", "description", "created_at", "updated_at",
    } <= cols


def test_agent_config_model_binding_columns():
    cols = {c.name for c in AgentConfig.__table__.columns}
    assert {"model_id", "model_snapshot"} <= cols
