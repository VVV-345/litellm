"""计算授权到期时间并替换回调 state，不消费或持久化授权状态。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from account_pool.domain import AuthorizationFlow, utc_now


def _authorization_expires_at(flow: AuthorizationFlow, expires_in_seconds: int | None) -> datetime:
    duration: Final = (
        expires_in_seconds if flow is AuthorizationFlow.DEVICE_CODE and expires_in_seconds is not None else 300
    )
    return utc_now() + timedelta(seconds=min(max(duration, 1), 3600))


def _replace_state(authorization_url: str, state: str) -> str:
    """只替换 OAuth URL 的 state 参数，保留上游其余参数并避免把 state 拼进日志。"""
    try:
        parsed: Final = urlsplit(authorization_url)
        query: Final = tuple(
            (key, state if key == "state" else value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        )
        final_query: Final = query if any(key == "state" for key, _ in query) else (*query, ("state", state))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(final_query), parsed.fragment))
    except ValueError:
        return authorization_url
