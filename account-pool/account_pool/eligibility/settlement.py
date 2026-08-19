"""把安全结算分类映射为对应 scope 的资格证据转换。"""

from typing import Final

from account_pool.eligibility.models import (
    EligibilityExclusion,
    EligibilityScope,
    EligibilitySource,
    EligibilityState,
)
from account_pool.eligibility.state import activate_exclusion, clear_candidate, upsert_exclusion
from account_pool.health.settlement import HealthTransitionAction, SettlementHealthTransition
from account_pool.models import Lease


def exclusions_after_settlement(
    exclusions: tuple[EligibilityExclusion, ...],
    lease: Lease,
    transition: SettlementHealthTransition,
    now: float,
    transient_threshold_reached: bool,
) -> tuple[EligibilityExclusion, ...]:
    if transition.action == HealthTransitionAction.SUCCESS:
        cleared: Final = clear_candidate(
            exclusions=exclusions,
            account_id=lease.account_id,
            model=lease.public_model,
            deployment_id=lease.deployment_id,
            billing_route_id=lease.billing_route_id,
        )
        return tuple(exclusion for exclusion in cleared if exclusion.state != EligibilityState.CLEARED)
    if transition.action == HealthTransitionAction.TRANSIENT_FAILURE and not transient_threshold_reached:
        return exclusions
    replacement: Final = settlement_exclusion(lease=lease, transition=transition, now=now)
    return exclusions if replacement is None else upsert_exclusion(exclusions, replacement)


def settlement_exclusion(
    lease: Lease,
    transition: SettlementHealthTransition,
    now: float,
) -> EligibilityExclusion | None:
    reason: Final = transition.reason_code
    if reason is None or reason == "request_rejected" or transition.action == HealthTransitionAction.SUCCESS:
        return None
    scope_and_source: Final = (
        (EligibilityScope.CHANNEL, EligibilitySource.HEALTH)
        if transition.action in (HealthTransitionAction.DISABLE, HealthTransitionAction.TRANSIENT_FAILURE)
        else (EligibilityScope.DEPLOYMENT, EligibilitySource.HEALTH)
        if transition.action == HealthTransitionAction.OBSERVE
        else (
            EligibilityScope.BILLING_ROUTE
            if lease.billing_route_id is not None and reason in {"balance_signal_unscoped", "quota_signal_unscoped"}
            else EligibilityScope.DEPLOYMENT,
            EligibilitySource.CAPACITY if reason == "concurrency_limited" else EligibilitySource.RESTRICTION,
        )
    )
    scope, source = scope_and_source
    return activate_exclusion(
        scope=scope,
        source=source,
        account_id=lease.account_id,
        model=lease.public_model,
        deployment_id=lease.deployment_id,
        billing_route_id=lease.billing_route_id,
        reason_code=reason,
        starts_at=now,
        retry_at=transition.cooldown_until,
    )
