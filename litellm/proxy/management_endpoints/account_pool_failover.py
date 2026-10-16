"""移植 sub2api 的失败分类与重试边界；不调度账号、不持有凭据或请求正文。

SPDX-License-Identifier: LGPL-3.0-or-later
Adapted from Wei-Shaw/sub2api commit 698d142468ab15374bc22bd5247759e5b7352006.
See account_pool_failover.LICENSE and account-pool/docs/sub2api-port.md.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, TypeAlias, cast

import httpx

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


def number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0
    try:
        parsed: Final = float(value)
        return parsed if math.isfinite(parsed) and parsed > 0 else 0
    except (ValueError, OverflowError):
        return 0


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
        return Failure(
            "upstream_unavailable", True, same_account=True, cooldown_seconds=0 if codes & CAPACITY_CODES else 1
        )
    if status in (401, 402, 403):
        return Failure("account_unavailable", True)
    return Failure("request_rejected", False)


def failure_headers(reason: FailureReason, *, exhausted: bool = False) -> Mapping[str, str]:
    if exhausted:
        return MappingProxyType({"x-account-pool-failure": reason, "x-account-pool-rebuild": "retry-exhausted"})
    return MappingProxyType({"x-account-pool-failure": reason})


def exception_failure(error: BaseException, depth: int = 0) -> FailureReason | None:
    reason: Final = exception_header(error, "x-account-pool-failure", depth)
    return reason if reason in FAILURE_REASONS else None


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
