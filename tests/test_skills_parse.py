"""parse_skill_md 解析与校验测试。"""

import pytest

from a2a_gateway.skills import (
    MAX_CONTENT_BYTES,
    MAX_DESCRIPTION_LEN,
    SCRIPT_SUFFIXES,
    is_script_path,
    parse_skill_md,
    validate_skill_fields,
)

VALID = "---\nname: travel-planning\ndescription: 行程规划方法论\nversion: 1\n---\n\n正文内容"


def test_parse_valid_keeps_extra_frontmatter():
    parsed = parse_skill_md(VALID)
    assert parsed["name"] == "travel-planning"
    assert parsed["description"] == "行程规划方法论"
    assert parsed["content"] == "正文内容"
    assert parsed["frontmatter"]["version"] == 1  # 额外字段保留
    assert parsed["size_bytes"] > 0


def test_parse_without_frontmatter_rejected():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_skill_md("# 只有正文")


def test_parse_unclosed_frontmatter_rejected():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_skill_md("---\nname: x\ndescription: y\n正文")


def test_parse_invalid_yaml_rejected():
    with pytest.raises(ValueError, match="YAML"):
        parse_skill_md("---\nname: [unclosed\ndescription: y\n---\n正文")


def test_parse_non_ascii_name_rejected():
    with pytest.raises(ValueError, match="name"):
        parse_skill_md("---\nname: 行程规划\ndescription: 描述\n---\n正文")


def test_parse_bad_symbol_name_rejected():
    with pytest.raises(ValueError, match="name"):
        parse_skill_md("---\nname: -bad-\ndescription: 描述\n---\n正文")


def test_parse_long_name_rejected():
    with pytest.raises(ValueError, match="name"):
        parse_skill_md("---\nname: " + "a" * 129 + "\ndescription: 描述\n---\n正文")


def test_parse_empty_name_rejected():
    with pytest.raises(ValueError, match="name"):
        parse_skill_md("---\ndescription: 描述\n---\n正文")


def test_parse_missing_description_rejected():
    with pytest.raises(ValueError, match="description"):
        parse_skill_md("---\nname: ok-name\n---\n正文")


def test_parse_long_description_rejected():
    desc = "d" * (MAX_DESCRIPTION_LEN + 1)
    with pytest.raises(ValueError, match="description"):
        parse_skill_md(f"---\nname: ok-name\ndescription: {desc}\n---\n正文")


def test_parse_oversized_content_rejected():
    body = "字" * (MAX_CONTENT_BYTES // 2)  # 中文 3 字节/字符，超 32KB
    with pytest.raises(ValueError, match="上限"):
        parse_skill_md(f"---\nname: ok-name\ndescription: 描述\n---\n{body}")


def test_validate_skill_fields_rejects_bad_name():
    with pytest.raises(ValueError, match="name"):
        validate_skill_fields(name="bad name", description="d", content="c")


def test_validate_skill_fields_accepts_ok():
    validate_skill_fields(name="ok.name-1", description="d", content="c")


def test_is_script_path():
    assert is_script_path("scripts/gen.py")
    assert is_script_path("run.SH")  # 大小写不敏感
    assert is_script_path("a/b/tool.JS")
    assert not is_script_path("references/a.md")
    assert not is_script_path("noext")
    assert not is_script_path("")


def test_script_constants():
    assert SCRIPT_SUFFIXES == {".py", ".sh", ".js"}
