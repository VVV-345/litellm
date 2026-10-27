"""移植 sub2api 的失败分类与重试边界；不调度账号、不持有凭据或请求正文。

SPDX-License-Identifier: LGPL-3.0-or-later
Adapted from Wei-Shaw/sub2api commits 698d142468ab15374bc22bd5247759e5b7352006,
c591412c42ab195dacdede9eb19f9cbfee221144, and e0fa4b94f9b962949da039c09e63968358324e26.
See account_pool_failover.LICENSE and account-pool/docs/sub2api-port.md.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import reduce
from types import MappingProxyType
from typing import Final, Literal, TypeAlias, cast

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from litellm.exceptions import MidStreamFallbackError

_STREAM_ERROR: Final = TypeAdapter(dict[str, JsonValue])

FailureReason: TypeAlias = Literal[
    "quota_exhausted",
    "rate_limit",
    "upstream_unavailable",
    "request_rejected",
    "model_unavailable",
    "account_unavailable",
    "subscription_expired",
    "policy_rejected",
]
QUOTA_CODES: Final = frozenset(("insufficient_quota", "quota_exceeded", "usage_limit_reached", "quota_exhausted"))
ACCOUNT_POOL_QUOTA_CODES: Final = frozenset(("account_pool_quota_exhausted",))
REQUEST_CODES: Final = frozenset(
    (
        "context_length_exceeded",
        "context_window_exceeded",
        "cyber_policy",
        "content_policy_violation",
        "invalid_request_error",
        "invalid_prompt",
        "thinking_signature_invalid",
        "invalid_encrypted_content",
    )
)
CAPACITY_CODES: Final = frozenset(("server_is_overloaded", "overloaded_error", "overloaded"))
FAILURE_REASONS: Final = frozenset(
    (
        "quota_exhausted",
        "rate_limit",
        "upstream_unavailable",
        "request_rejected",
        "model_unavailable",
        "account_unavailable",
        "subscription_expired",
        "policy_rejected",
    )
)
MAX_RETRY_DELAY_SECONDS: Final = 8
RETRY_WINDOW_SECONDS: Final = 120


@dataclass(frozen=True, slots=True)
class Failure:
    reason: FailureReason
    retryable: bool
    same_account: bool = False
    cooldown_seconds: int = 0
    capacity_shed: bool = False


def number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0
    try:
        parsed: Final = float(value)
        return parsed if math.isfinite(parsed) and parsed > 0 else 0
    except (ValueError, OverflowError):
        return 0


def is_capacity_shed(failure: Failure) -> bool:
    return (
        failure.capacity_shed
        and failure.reason == "upstream_unavailable"
        and failure.same_account
        and failure.cooldown_seconds == 0
    )


def _header_text(headers: Mapping[str, str], name: str) -> str:
    folded: Final = name.casefold()
    found: Final = next((value for key, value in headers.items() if key.casefold() == folded), "")
    return found.strip()


def _field(current: object, key: str) -> object:
    if not isinstance(current, Mapping):
        return ""
    value: Final = cast(Mapping[str, object], current).get(  # cast-ok: JSON objects are string-keyed mappings
        key, ""
    )
    return value


def _path(payload: Mapping[str, object], path: tuple[str, ...]) -> object:
    located: Final = reduce(_field, path, payload)
    return located


def _delay_seconds(raw: object, *, milliseconds: bool) -> float:
    if isinstance(raw, str):
        text: Final = raw.strip()
        if text == "":
            return 0
        numeric: Final = number(text)
        if numeric > 0:
            return numeric / 1000 if milliseconds else numeric
        if milliseconds:
            return 0
        try:
            parsed: Final = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError, OSError):
            return 0
        moment: Final = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        delay: Final = (moment - datetime.now(timezone.utc)).total_seconds()
        return delay if math.isfinite(delay) and delay > 0 else 0
    amount: Final = number(raw)
    if amount <= 0:
        return 0
    return amount / 1000 if milliseconds else amount


def capacity_request_scoped_retry_after(
    headers: Mapping[str, str],
    payload: Mapping[str, object] | None = None,
) -> int:
    body: Final = payload if payload is not None else MappingProxyType({})
    candidates: Final = (
        _delay_seconds(_header_text(headers, "retry-after"), milliseconds=False),
        _delay_seconds(_header_text(headers, "retry-after-ms"), milliseconds=True),
        _delay_seconds(_path(body, ("error", "headers", "retry-after")), milliseconds=False),
        _delay_seconds(_path(body, ("response", "error", "headers", "retry-after")), milliseconds=False),
        _delay_seconds(_path(body, ("error", "headers", "retry-after-ms")), milliseconds=True),
        _delay_seconds(_path(body, ("response", "error", "headers", "retry-after-ms")), milliseconds=True),
    )
    seconds: Final = next((item for item in candidates if item > 0), 0.0)
    return min(MAX_RETRY_DELAY_SECONDS, math.ceil(seconds)) if seconds > 0 else 0


_JSON_BODY: Final = TypeAdapter(JsonValue)
_CLOUDFLARE_CODE_FIELDS: Final = frozenset(("code", "error_code"))
_BROWSER_SIGNATURE_BANNED: Final = "browser_signature_banned"
_CLOUDFLARE_1010_TEXT: Final = "error code: 1010"
CLOUDFLARE_BOT_BLOCK_CODE: Final = "cloudflare_1010"


def _is_cloudflare_bot_block_code(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 1010
    return isinstance(value, str) and value.strip() == "1010"


def _browser_signature_banned(value: object) -> bool:
    return isinstance(value, str) and value.strip().casefold() == _BROWSER_SIGNATURE_BANNED


def _cloudflare_field(key: object, child: object, cloudflare_error: bool) -> bool:
    if isinstance(key, str):
        folded: Final = key.casefold()
        if folded == "error_name" and _browser_signature_banned(child):
            return True
        if cloudflare_error and folded in _CLOUDFLARE_CODE_FIELDS and _is_cloudflare_bot_block_code(child):
            return True
    return _contains_cloudflare_bot_block_code(child)


def _contains_cloudflare_bot_block_code(value: object) -> bool:
    if isinstance(value, Mapping):
        cloudflare_error: Final = any(
            isinstance(key, str) and key.casefold() == "cloudflare_error" and child is True
            for key, child in value.items()
        )
        return any(_cloudflare_field(key, child, cloudflare_error) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_cloudflare_bot_block_code(child) for child in value)
    return False


def cooldown_exempt_upstream_code(status: int, payload: object) -> str | None:
    if status == 403 and is_cloudflare_bot_block_response(payload):
        return CLOUDFLARE_BOT_BLOCK_CODE
    return None


def is_cloudflare_bot_block_response(body: object) -> bool:
    if isinstance(body, (bytes, bytearray)):
        text: Final = body.decode("utf-8", "replace")
        return is_cloudflare_bot_block_response(text)
    if not isinstance(body, str):
        return _contains_cloudflare_bot_block_code(body)
    try:
        parsed: Final = _JSON_BODY.validate_json(body)
    except ValidationError:
        return _CLOUDFLARE_1010_TEXT in body.strip().casefold()
    return _contains_cloudflare_bot_block_code(parsed)


def classify_failure(status: int, payload: Mapping[str, object], headers: Mapping[str, str]) -> Failure:
    nested: Final = payload.get("error")
    error: Final = (
        cast(  # cast-ok: Only known string keys are read from the checked mapping.
            Mapping[str, object], nested
        )
        if isinstance(nested, Mapping)
        else payload
    )
    codes: Final = frozenset(str(error.get(key, "")) for key in ("code", "type"))
    if codes & ACCOUNT_POOL_QUOTA_CODES:
        return Failure("quota_exhausted", True, cooldown_seconds=60)
    if codes & (REQUEST_CODES - frozenset(("invalid_request_error",))):
        return Failure("request_rejected", False)
    if "model_not_found" in codes:
        return Failure("model_unavailable", True)
    if codes & frozenset(("auth_unavailable", "auth_not_found", "model_cooldown")):
        return Failure("account_unavailable", True)
    if "invalid_request_error" in codes:
        return Failure("request_rejected", False)
    if status == 429:
        windows: Final = tuple(
            min(86400, math.ceil(number(headers.get(f"x-codex-{window}-reset-after-seconds"))))
            for window in ("primary", "secondary")
            if number(headers.get(f"x-codex-{window}-used-percent")) >= 100
        )
        reset: Final = min(
            86400,
            math.ceil(
                max(
                    number(error.get("resets_in_seconds")),
                    number(error.get("resets_at")) - time.time(),
                    0,
                )
            ),
        )
        if codes & QUOTA_CODES or windows or reset:
            return Failure(
                "quota_exhausted",
                True,
                cooldown_seconds=max((*windows, reset, 60 if not reset and not any(windows) else 0)),
            )
        return Failure("rate_limit", True, same_account=True)
    if status in (408, 500, 502, 503, 504, 529):
        capacity: Final = bool(codes & CAPACITY_CODES)
        return Failure(
            "upstream_unavailable",
            True,
            same_account=True,
            cooldown_seconds=0 if capacity else 1,
            capacity_shed=capacity,
        )
    if status == 403 and is_cloudflare_bot_block_response(payload):
        return Failure("upstream_unavailable", True, same_account=True, cooldown_seconds=0)
    if status in (401, 402, 403):
        return Failure("account_unavailable", True)
    return Failure("request_rejected", False)


def failure_headers(reason: FailureReason, *, exhausted: bool = False) -> Mapping[str, str]:
    if exhausted:
        return MappingProxyType({"x-account-pool-failure": reason, "x-account-pool-rebuild": "retry-exhausted"})
    return MappingProxyType({"x-account-pool-failure": reason})


def exception_failure(error: BaseException, depth: int = 0) -> FailureReason | None:
    reason: Final = exception_header(error, "x-account-pool-failure", depth)
    if reason in FAILURE_REASONS:
        return reason
    if (
        not isinstance(error, MidStreamFallbackError)
        or not error.is_pre_first_chunk
        or error.generated_content
    ):
        return None
    try:
        payload: Final = _STREAM_ERROR.validate_json(error.response.content)
    except (ValidationError, httpx.ResponseNotRead):
        return None
    failure: Final = classify_failure(error.status_code, payload, error.response.headers)
    return failure.reason if failure.reason == "quota_exhausted" else None


def exception_header(error: BaseException, name: str, depth: int = 0) -> str | None:
    response: Final = getattr(error, "response", None)
    headers: Final = getattr(error, "headers", None)
    mappings: Final[tuple[Mapping[str, object], ...]] = (
        *(
            (
                cast(  # cast-ok: Header values are narrowed to strings before returning.
                    Mapping[str, object], headers
                ),
            )
            if isinstance(headers, Mapping)
            else ()
        ),
        *((response.headers,) if isinstance(response, httpx.Response) else ()),
    )
    found: Final = next((value for mapping in mappings if isinstance(value := mapping.get(name), str)), None)
    if found is not None:
        return found
    previous: Final = error.__cause__ or error.__context__
    return exception_header(previous, name, depth + 1) if previous is not None and depth < 5 else None
