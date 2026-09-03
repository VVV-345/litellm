"""本模块集中处理号池错误信息中的 URL 和凭据脱敏。"""

import re
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def safe_error(error: Exception) -> str:
    message: Final = str(error).strip() or error.__class__.__name__
    without_urls: Final = redact_urls(message)
    without_credentials: Final = re.sub(
        r"(?i)(bearer\s+|basic\s+|(?:access|refresh|id)?[_-]?token\s*[:=]\s*|(?:api[_-]?key|secret(?:[-_]?key)?|client[_-]?secret|password|code|state|proxy[_-]?url)\s*[:=]\s*)[^\s,;]+",
        r"\1[redacted]",
        without_urls,
    )
    return without_credentials[:500]


def redact_urls(message: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw_url: Final = match.group(0)
        try:
            parsed: Final = urlsplit(raw_url)
            safe_query: Final = urlencode(
                tuple((key, "[redacted]") for key, _ in parse_qsl(parsed.query, keep_blank_values=True))
            )
            safe_netloc: str = parsed.hostname or ""
            if parsed.port is not None:
                safe_netloc = f"{safe_netloc}:{parsed.port}"
            return urlunsplit((parsed.scheme, safe_netloc, parsed.path, safe_query, ""))
        except ValueError:
            return "[redacted-url]"

    return re.sub(r"https?://[^\s\]\[)>,;]+", replace, message)
