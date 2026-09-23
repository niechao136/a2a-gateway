"""build_llm_from_snapshot / resolve_llm：provider 分支与参数覆盖。"""
import pytest

from a2a_gateway.llm import (
    ThoughtSignatureChatOpenAI,
    build_llm_from_snapshot,
    resolve_llm,
)


def _snapshot(**overrides):
    base = {
        "provider": "openai",
        "name": "DeepSeek V3",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-test",
        "model": "deepseek-chat",
        "temperature": None,
        "max_tokens": None,
    }
    base.update(overrides)
    return base


def test_openai_branch_returns_patched_client():
    llm = build_llm_from_snapshot(_snapshot())
    assert isinstance(llm, ThoughtSignatureChatOpenAI)
    assert llm.model_name == "deepseek-chat"
    assert llm.streaming is True


def test_openai_param_overrides():
    llm = build_llm_from_snapshot(_snapshot(temperature=0.3, max_tokens=2048))
    assert llm.temperature == 0.3
    assert llm.max_tokens == 2048


def test_openai_without_overrides_keeps_defaults():
    llm = build_llm_from_snapshot(_snapshot())
    assert llm.temperature is None


def test_anthropic_branch():
    llm = build_llm_from_snapshot(
        _snapshot(provider="anthropic", base_url="", model="claude-sonnet-4-5", api_key="sk-ant")
    )
    assert type(llm).__name__ == "ChatAnthropic"
    # langchain-anthropic 1.x 字段名为 model（model_name 仅作别名入参）
    assert llm.model == "claude-sonnet-4-5"


def test_unknown_provider_raises_chinese():
    with pytest.raises(ValueError, match="不支持的模型供应商"):
        build_llm_from_snapshot(_snapshot(provider="gemini"))


def test_missing_api_key_raises_chinese():
    # 空 key 必须在进入底层客户端前给中文错误（底层抛英文 OpenAIError）
    with pytest.raises(ValueError, match="未配置 API Key"):
        build_llm_from_snapshot(_snapshot(api_key=""))


def test_resolve_llm_none_falls_back_to_global(monkeypatch):
    # 全局回落路径读 LLM_* 环境变量；测试环境无真实 key 时底层客户端会拒绝构造，
    # 注入假 key 以验证回落分支本身
    from a2a_gateway import llm as llm_mod

    monkeypatch.setattr(llm_mod._settings, "llm_api_key", "sk-env-test")
    llm = resolve_llm(None)
    assert isinstance(llm, ThoughtSignatureChatOpenAI)


def test_resolve_llm_with_snapshot():
    llm = resolve_llm(_snapshot(model="custom-model"))
    # isinstance 缩窄联合返回类型（ChatAnthropic 的模型字段名是 model，两者无共同属性）
    assert isinstance(llm, ThoughtSignatureChatOpenAI)
    assert llm.model_name == "custom-model"


def test_resolve_llm_bad_provider_from_snapshot_raises():
    # 快照存在但 provider 非法时应显式报错，而不是静默回落全局配置
    with pytest.raises(ValueError, match="不支持的模型供应商"):
        resolve_llm(_snapshot(provider="palm"))
