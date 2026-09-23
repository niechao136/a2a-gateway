"""模型绑定纯函数测试：快照构造、脱敏、校验（无 DB）。"""
from types import SimpleNamespace

import pytest

from a2a_gateway.models import LLMProvider
from a2a_gateway.repository import llm_model_snapshot
from a2a_gateway.schemas import mask_secret, validate_llm_model


def _model(**kw):
    base = {
        "name": "DeepSeek V3",
        "provider": LLMProvider.OPENAI,
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-12345678",
        "model": "deepseek-chat",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_llm_model_snapshot_fields():
    snap = llm_model_snapshot(_model(), temperature=0.5, max_tokens=1024)
    assert snap == {
        "provider": "openai",
        "name": "DeepSeek V3",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-12345678",
        "model": "deepseek-chat",
        "temperature": 0.5,
        "max_tokens": 1024,
    }


def test_llm_model_snapshot_accepts_enum_and_string_provider():
    assert llm_model_snapshot(_model(provider=LLMProvider.ANTHROPIC))["provider"] == "anthropic"
    assert llm_model_snapshot(_model(provider="openai"))["provider"] == "openai"


def test_mask_secret():
    assert mask_secret("") == ""
    assert mask_secret("short") == "***"
    masked = mask_secret("sk-abcdefghij")
    assert masked.startswith("sk-") and masked.endswith("hij")
    assert "abcdef" not in masked


def test_validate_llm_model():
    validate_llm_model("openai", "https://x/v1", "m")  # 不抛
    validate_llm_model("anthropic", "", "m")  # anthropic 允许空 base_url
    with pytest.raises(ValueError, match="base_url"):
        validate_llm_model("openai", "", "m")
    with pytest.raises(ValueError, match="模型标识"):
        validate_llm_model("openai", "https://x/v1", " ")
    with pytest.raises(ValueError, match="不支持的模型供应商"):
        validate_llm_model("palm", "https://x", "m")
