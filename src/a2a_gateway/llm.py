"""LLM 层：构造 ChatOpenAI，并兼容 Gemini 3 的 thought_signature 要求。

背景（Gemini 3 + OpenAI 兼容端点）：
模型返回的函数调用会带一个非标准字段
`tool_calls[i].extra_content.google.thought_signature`，下一轮请求必须**原样回传**，
否则返回 400 `Function call is missing a thought_signature in functionCall parts`。

而 langchain-openai 会在两处丢弃该字段：
- 入站流式解析：`_convert_delta_to_message_chunk` 只取 name/args/id/index
- 出站转换：`_convert_message_to_dict` 重建 tool_calls 时只保留 id/type/function

本模块通过一个受控适配层补齐：
- 入站（流式）：包装模块级 `_convert_delta_to_message_chunk`，把 extra_content 记入 additional_kwargs
- 入站（非流式）：子类覆盖 `_create_chat_result`
- 出站：子类覆盖 `_get_request_payload`，按 tool_call id 回填 extra_content

对非 Gemini 的 OpenAI 兼容端点完全透明（没有该字段时全部为空操作）。
"""

# 本模块需要访问 langchain-openai 的私有/无类型内部实现，故对该文件的动态类型检查放宽
# pyright: reportAny=false, reportExplicitAny=false
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportUnknownParameterType=false
# pyright: reportPrivateUsage=false, reportUnusedCallResult=false, reportImplicitOverride=false

import logging
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_openai.chat_models import base as _lc_base
from pydantic import SecretStr

from .config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# 存放「tool_call id -> extra_content」的 additional_kwargs 私有键
_SIG_KEY = "__google_extra_content__"
# 补丁安装标记（挂在 langchain-openai 模块上，保证只装一次）
_PATCH_FLAG = "_a2a_thought_signature_patched"

_original_delta_to_chunk = _lc_base._convert_delta_to_message_chunk


def _merge_signatures(message: Any, signatures: dict[str, Any]) -> None:
    """把 signatures 合并进消息的 additional_kwargs（失败不影响主流程）。"""
    if not signatures or message is None:
        return
    try:
        kwargs = dict(getattr(message, "additional_kwargs", None) or {})
        merged = dict(kwargs.get(_SIG_KEY) or {})
        merged.update(signatures)
        kwargs[_SIG_KEY] = merged
        try:
            message.additional_kwargs = kwargs
        except Exception:  # pydantic 不可变时退回 __dict__
            message.__dict__["additional_kwargs"] = kwargs
    except Exception:
        logger.debug("合并 thought_signature 失败", exc_info=True)


def _extract_signatures(raw_tool_calls: Any) -> dict[str, Any]:
    """从原始 tool_calls 列表中提取 extra_content，按 tool_call id 建索引。"""
    signatures: dict[str, Any] = {}
    if not raw_tool_calls:
        return signatures
    for idx, rtc in enumerate(raw_tool_calls):
        if not isinstance(rtc, dict):
            continue
        extra = rtc.get("extra_content")
        if extra:
            signatures[rtc.get("id") or f"#index-{rtc.get('index', idx)}"] = extra
    return signatures


def _patched_delta_to_message_chunk(_dict: Any, default_class: Any) -> Any:
    """在原有流式解析基础上，额外保留 tool_calls 里的 extra_content。"""
    chunk = _original_delta_to_chunk(_dict, default_class)
    try:
        if isinstance(_dict, dict):
            _merge_signatures(chunk, _extract_signatures(_dict.get("tool_calls")))
    except Exception:
        logger.debug("流式捕获 thought_signature 失败", exc_info=True)
    return chunk


def install_thought_signature_patch() -> bool:
    """安装流式入站补丁（幂等）。"""
    if getattr(_lc_base, _PATCH_FLAG, False):
        return True
    try:
        _lc_base._convert_delta_to_message_chunk = _patched_delta_to_message_chunk
        setattr(_lc_base, _PATCH_FLAG, True)
        logger.info("已启用 thought_signature 兼容补丁（langchain-openai 流式入站）")
        return True
    except Exception:
        logger.exception("安装 thought_signature 兼容补丁失败")
        return False


class ThoughtSignatureChatOpenAI(ChatOpenAI):
    """保留并回传 extra_content(google.thought_signature) 的 ChatOpenAI。"""

    def _create_chat_result(self, response: Any, generation_info: Any = None) -> Any:
        """非流式入站：从原始响应里捕获 extra_content。"""
        result = super()._create_chat_result(response, generation_info)
        try:
            raw = response if isinstance(response, dict) else response.model_dump(warnings=False)
            choices = raw.get("choices") or []
            for gens, choice in zip(result.generations, choices):
                signatures = _extract_signatures((choice.get("message") or {}).get("tool_calls"))
                for gen in gens:
                    _merge_signatures(getattr(gen, "message", None), signatures)
        except Exception:
            logger.debug("非流式捕获 thought_signature 失败", exc_info=True)
        return result

    def _get_request_payload(self, input_: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """出站：按 tool_call id 把 extra_content 回填到请求体。"""
        payload = super()._get_request_payload(input_, *args, **kwargs)
        try:
            lc_messages = self._convert_input(input_).to_messages()
            payload_messages = payload.get("messages") or []
            if len(lc_messages) != len(payload_messages):
                return payload
            for lc_msg, payload_msg in zip(lc_messages, payload_messages):
                if payload_msg.get("role") != "assistant":
                    continue
                signatures = (getattr(lc_msg, "additional_kwargs", None) or {}).get(_SIG_KEY)
                if not signatures:
                    continue
                for tool_call in payload_msg.get("tool_calls") or []:
                    extra = signatures.get(tool_call.get("id"))
                    if extra:
                        tool_call["extra_content"] = extra
        except Exception:
            logger.debug("回填 thought_signature 失败", exc_info=True)
        return payload


def build_llm() -> ChatOpenAI:
    """根据配置构造 LLM（OpenAI 兼容端点，含 Gemini 思考签名兼容）。"""
    install_thought_signature_patch()
    return ThoughtSignatureChatOpenAI(
        model=_settings.llm_model,
        api_key=SecretStr(_settings.llm_api_key),
        base_url=_settings.llm_base_url,
        streaming=True,
    )
