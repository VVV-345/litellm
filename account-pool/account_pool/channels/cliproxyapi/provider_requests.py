"""组装供应商额度请求参数并解析失败信息，不持有客户端或凭据。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Final
from urllib.parse import urlsplit, urlunsplit

from account_pool.channels.cliproxyapi.protocol import _AuthFile, _CodexIdentity, _UpstreamError, _UpstreamErrorPayload
from account_pool.provider_quota import CodexAccountInfo

_ANTIGRAVITY_DAILY_BASE_URL: Final = "https://daily-cloudcode-pa.googleapis.com"
_ANTIGRAVITY_PROD_BASE_URL: Final = "https://cloudcode-pa.googleapis.com"
_CHATGPT_WEB_USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def _codex_auth_file_plan_type(auth_file: _AuthFile) -> str | None:
    explicit: Final = _normalize_codex_auth_file_plan_type(auth_file.auth_file_plan_type)
    if explicit is not None:
        return explicit
    normalized_name: Final = auth_file.name.rsplit(".", 1)[0].strip().lower().replace("_", "-").replace(" ", "-")
    if normalized_name.endswith(("-prolite", "-pro-lite")):
        return "prolite"
    if normalized_name.endswith(("-promax", "-pro-max")):
        return "promax"
    return None


def _normalize_codex_auth_file_plan_type(value: str | None) -> str | None:
    normalized: Final = (value or "").strip().lower().replace("_", "-").replace(" ", "-")
    if normalized in ("prolite", "pro-lite"):
        return "prolite"
    if normalized in ("promax", "pro-max"):
        return "promax"
    return None


def _safe_endpoint(url: str) -> str:
    parsed: Final = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _request_id(headers: Mapping[str, tuple[str, ...]]) -> str | None:
    normalized: Final = {key.lower(): values for key, values in headers.items()}
    return next(
        (values[0] for key in ("request-id", "x-request-id", "cf-ray") if (values := normalized.get(key))),
        None,
    )


def _upstream_code(body: str) -> str | None:
    try:
        payload: Final = _UpstreamErrorPayload.model_validate_json(body)
    except ValueError:
        return None
    candidate: Final = next(
        (
            value
            for value in (
                payload.error.code if isinstance(payload.error, _UpstreamError) else None,
                payload.error.type if isinstance(payload.error, _UpstreamError) else None,
                payload.detail.code if isinstance(payload.detail, _UpstreamError) else None,
                payload.detail.type if isinstance(payload.detail, _UpstreamError) else None,
                payload.code,
                payload.type,
            )
            if value is not None and value.strip()
        ),
        None,
    )
    return None if candidate is None else candidate.strip()[:120]


def _codex_usage_headers(account_id: str | None = None) -> Mapping[str, str]:
    return {
        "Authorization": "Bearer $TOKEN$",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": "https://chatgpt.com/",
        "User-Agent": _CHATGPT_WEB_USER_AGENT,
        "OpenAI-Beta": "codex-1",
        "oai-language": "zh-CN",
        "originator": "Codex Desktop",
        "sec-fetch-site": "none",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-dest": "empty",
        "priority": "u=4, i",
        **({"ChatGPT-Account-Id": account_id} if account_id is not None else {}),
    }


def _codex_subscription_headers(target_path: str, account_id: str | None = None) -> Mapping[str, str]:
    return {
        "Authorization": "Bearer $TOKEN$",
        "Accept": "application/json",
        "Referer": "https://chatgpt.com/",
        "User-Agent": _CHATGPT_WEB_USER_AGENT,
        "x-openai-target-path": target_path,
        "x-openai-target-route": target_path,
        **({"ChatGPT-Account-Id": account_id} if account_id is not None else {}),
    }


def _chatgpt_timezone_offset_minutes() -> int:
    offset: Final = datetime.now().astimezone().utcoffset()
    return 0 if offset is None else -round(offset.total_seconds() / 60)


def _codex_subscription_needs_fallback(
    account_info: CodexAccountInfo | None,
    identity: _CodexIdentity | None,
    observed_at: datetime,
) -> bool:
    expires_at: Final = (
        account_info.subscription_active_until
        if account_info is not None and account_info.subscription_active_until is not None
        else None
        if identity is None
        else identity.chatgpt_subscription_active_until
    )
    return expires_at is None or expires_at <= observed_at


def _antigravity_explicit_base_url(auth_file: _AuthFile) -> str | None:
    allowed: Final = frozenset((_ANTIGRAVITY_DAILY_BASE_URL, _ANTIGRAVITY_PROD_BASE_URL))
    candidate: Final = next(
        (
            value.strip().rstrip("/")
            for source in (auth_file.attributes, auth_file.metadata)
            if isinstance((value := source.get("base_url")), str) and value.strip()
        ),
        None,
    )
    return candidate if candidate in allowed else None


def _antigravity_base_urls(auth_file: _AuthFile) -> tuple[str, ...]:
    explicit: Final = _antigravity_explicit_base_url(auth_file)
    return (explicit,) if explicit is not None else (_ANTIGRAVITY_DAILY_BASE_URL, _ANTIGRAVITY_PROD_BASE_URL)
