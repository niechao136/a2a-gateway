"""Gemini thought_signature 兼容层测试：入站捕获 + 出站回填。"""

from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai.chat_models import base as lc_base

from a2a_gateway.llm import ThoughtSignatureChatOpenAI, install_thought_signature_patch

_SIG_KEY = "__google_extra_content__"
SIG = {"google": {"thought_signature": "SIG-123"}}


def _llm() -> ThoughtSignatureChatOpenAI:
    return ThoughtSignatureChatOpenAI(model="x", api_key="k", base_url="http://127.0.0.1:1/v1")


def test_inbound_stream_captures_signature():
    install_thought_signature_patch()
    delta = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "a2a_call", "arguments": "{}"},
                "extra_content": SIG,
            }
        ],
    }
    chunk = lc_base._convert_delta_to_message_chunk(delta, lc_base.AIMessageChunk)
    assert chunk.additional_kwargs[_SIG_KEY] == {"call_1": SIG}


def test_outbound_refills_signature():
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "a2a_call", "args": {"message": "hi"}, "id": "call_1"}],
        additional_kwargs={_SIG_KEY: {"call_1": SIG}},
    )
    payload = _llm()._get_request_payload([HumanMessage(content="hi"), ai])
    assistant = [m for m in payload["messages"] if m.get("role") == "assistant"][-1]
    assert assistant["tool_calls"][0]["extra_content"] == SIG


def test_outbound_without_signature_is_unchanged():
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "a2a_call", "args": {"message": "hi"}, "id": "call_9"}],
    )
    payload = _llm()._get_request_payload([HumanMessage(content="hi"), ai])
    assistant = [m for m in payload["messages"] if m.get("role") == "assistant"][-1]
    assert "extra_content" not in assistant["tool_calls"][0]


def test_install_patch_is_idempotent():
    assert install_thought_signature_patch() is True
    assert install_thought_signature_patch() is True
