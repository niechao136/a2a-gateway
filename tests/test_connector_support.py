"""连接器 schemas（凭据校验/合并/脱敏）与 thread_id 映射的纯函数测试。"""

import pytest
from pydantic import ValidationError

from a2a_gateway.repository import build_connector_thread_id
from a2a_gateway.schemas import (
    ConnectorCreate,
    mask_connector_credentials,
    merge_connector_credentials,
    validate_connector_credentials,
)


def test_thread_id_stable():
    a = build_connector_thread_id(3, "feishu", "oc_abc")
    b = build_connector_thread_id(3, "feishu", "oc_abc")
    assert a == b == "conn-3-feishu-oc_abc"


def test_thread_id_long_chat_hashed():
    tid = build_connector_thread_id(1, "feishu", "oc_" + "x" * 200)
    assert len(tid) <= 128
    assert tid.startswith("conn-1-feishu-")


def test_credentials_validated_by_platform():
    creds = validate_connector_credentials("telegram", {"bot_token": "123:abc"})
    assert creds == {"bot_token": "123:abc", "secret_token": "", "bot_username": ""}
    with pytest.raises(ValueError):
        validate_connector_credentials("discord", {})


def test_credentials_reject_unknown_keys():
    with pytest.raises(ValidationError):
        validate_connector_credentials("feishu", {"nope": 1})


def test_merge_credentials_keeps_blank_fields():
    merged = merge_connector_credentials(
        "slack",
        {"bot_token": "xoxb-old", "signing_secret": "s1"},
        {"bot_token": "", "signing_secret": "s2"},
    )
    assert merged == {"bot_token": "xoxb-old", "signing_secret": "s2"}


def test_mask_connector_credentials():
    masked = mask_connector_credentials("feishu", {"app_id": "cli_a", "app_secret": "s"})
    assert masked["app_id"] == "••••"
    assert masked["app_secret"] == "••••"
    assert masked["encrypt_key"] == ""


def test_connector_create_validates_credentials():
    with pytest.raises(ValidationError):
        ConnectorCreate(name="n", platform="feishu", agent_id=1, credentials={"nope": 1})
