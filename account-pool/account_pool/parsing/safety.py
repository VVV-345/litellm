"""集中判断解析数据是否包含禁止持久化或导出的敏感内容。"""

from typing import Final

_FORBIDDEN_CONTENT: Final = (
    "api_key",
    "api-key",
    "authorization",
    "bearer ",
    "cookie",
    "credential_ref",
    "key_fingerprint",
    "http://",
    "https://",
)


def has_safe_parser_content(serialized: str) -> bool:
    normalized: Final = serialized.casefold()
    return not any(forbidden in normalized for forbidden in _FORBIDDEN_CONTENT)
