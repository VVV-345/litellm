"""共享的额度窗口语义，内存与 Redis 后端必须一致解释。"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from account_pool.models import QuotaWindowConfig, RuntimeQuotaKind, RuntimeQuotaScope

if TYPE_CHECKING:
    from account_pool.quota.runtime import RuntimeQuotaWindow


def window_matches_request(
    config: QuotaWindowConfig,
    public_model: str,
    billing_route_id: str | None,
) -> bool:
    if config.scope == RuntimeQuotaScope.CHANNEL:
        return True
    if config.scope == RuntimeQuotaScope.MODEL:
        return config.subject_id == public_model
    return billing_route_id is not None and config.subject_id == billing_route_id


def reservation_amount(kind: RuntimeQuotaKind, estimated_tokens: int) -> Decimal:
    if kind == RuntimeQuotaKind.REQUESTS:
        return Decimal("1")
    if kind == RuntimeQuotaKind.TOKENS:
        return Decimal(estimated_tokens)
    return Decimal("0")


def matching_quota_windows(
    windows: tuple[RuntimeQuotaWindow, ...],
    public_model: str,
    billing_route_id: str | None,
) -> tuple[RuntimeQuotaWindow, ...]:
    return tuple(window for window in windows if window_matches_request(window.config, public_model, billing_route_id))
