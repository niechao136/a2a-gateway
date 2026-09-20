"""Skill 解析与上限常量（纯函数，无 IO）。

SKILL.md 格式：YAML frontmatter（name / description 必填）+ 正文。
name 只允许 ASCII（会写进 prompt 清单并作为 load_skill 入参值）；
中文名等富信息放 description。全部常量集中于此，供导入 / 门禁 / 注入共用。
"""

import re
from typing import TypedDict

import yaml

MAX_CONTENT_BYTES = 32768           # 单 SKILL.md 正文字节上限
MAX_DESCRIPTION_LEN = 1024          # description 字符上限
MAX_FILE_BYTES = 1048576            # 单附件字节上限
MAX_SKILL_BYTES = 4194304           # 单 skill 总量（正文 + 附件）
MAX_FILES = 100                     # 单 skill 附件数上限
MAX_BINDING_CONTENT_BYTES = 131072  # 单 Agent 绑定正文总量（不含附件）
MAX_INJECT_CHARS = 32768            # 单轮重注入字符上限
URL_FETCH_TIMEOUT = 10.0            # URL 抓取超时（秒）
URL_MAX_REDIRECTS = 3               # URL 重定向上限
MAX_IMPORT_BYTES = 4194304          # 单次导入响应体/上传包总量上限

NAME_PATTERN = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9._-]*[a-zA-Z0-9])?$")


class SkillParseError(ValueError):
    """SKILL.md 解析 / 校验失败（message 面向最终用户）。"""


class SkillDocument(TypedDict):
    """parse_skill_md 的产物键；运行时仍是普通 dict，可当 dict[str, object] 使用。

    用 TypedDict 而非裸 dict[str, object]：让下游取 name / content 时直接得到
    str，避免在每次读取处 cast（区别于 graph.py 中刻意保持宽松的配置字典）。
    """

    name: str
    description: str
    content: str
    frontmatter: dict[str, object]
    size_bytes: int


def _check_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 128 or not NAME_PATTERN.fullmatch(name):
        raise SkillParseError(
            "name 仅允许 ASCII 字母、数字与 . _ -（首尾不能是符号），长度 ≤ 128"
        )
    return name


def _check_description(description: str) -> str:
    description = description.strip()
    if not description:
        raise SkillParseError("description 不能为空（模型依据它决定是否加载）")
    if len(description) > MAX_DESCRIPTION_LEN:
        raise SkillParseError(f"description 超过 {MAX_DESCRIPTION_LEN} 字符上限")
    return description


def _check_content(content: str) -> str:
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise SkillParseError(f"正文超过 {MAX_CONTENT_BYTES} 字节上限")
    return content


def parse_skill_md(text: str) -> SkillDocument:
    """解析 SKILL.md：frontmatter + 正文分离并校验。

    @returns {"name", "description", "content", "frontmatter", "size_bytes"}
    """
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        raise SkillParseError("缺少 frontmatter（文件需以 --- 开头）")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillParseError("frontmatter 未闭合")
    raw_frontmatter, content = parts[1], parts[2]

    try:
        frontmatter = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as exc:
        raise SkillParseError(f"YAML 解析失败：{exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillParseError("frontmatter 必须是 YAML 映射")

    name = _check_name(str(frontmatter.get("name") or ""))
    description = _check_description(str(frontmatter.get("description") or ""))
    content = _check_content(content.lstrip("\n"))
    size_bytes = len(content.encode("utf-8")) + len(description.encode("utf-8"))
    return {
        "name": name,
        "description": description,
        "content": content,
        "frontmatter": frontmatter,
        "size_bytes": size_bytes,
    }


def validate_skill_fields(*, name: str, description: str, content: str) -> None:
    """CRUD 提交（非 SKILL.md 解析路径）的字段校验；不合法抛 SkillParseError。"""
    _check_name(name)
    _check_description(description)
    _check_content(content)
