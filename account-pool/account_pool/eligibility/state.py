"""实现资格证据的创建、匹配、半开、清除和配置保留规则。"""

from typing import Final

from account_pool.eligibility.models import (
    EligibilityExclusion,
    EligibilityScope,
    EligibilitySource,
    EligibilityState,
    EligibilitySubject,
)
from account_pool.models import AccountConfig


def activate_exclusion(
    scope: EligibilityScope,
    source: EligibilitySource,
    account_id: str,
    model: str | None,
    deployment_id: str | None,
    billing_route_id: str | None,
    reason_code: str,
    starts_at: float,
    retry_at: float | None,
) -> EligibilityExclusion:
    scoped_model: Final = None if scope == EligibilityScope.CHANNEL else model
    scoped_deployment: Final = (
        deployment_id if scope in (EligibilityScope.DEPLOYMENT, EligibilityScope.BILLING_ROUTE) else None
    )
    scoped_billing_route: Final = billing_route_id if scope == EligibilityScope.BILLING_ROUTE else None
    return EligibilityExclusion(
        scope=scope,
        source=source,
        account_id=account_id,
        model=scoped_model,
        deployment_id=scoped_deployment,
        billing_route_id=scoped_billing_route,
        reason_code=reason_code,
        starts_at=starts_at,
        retry_at=retry_at,
    )


def effective_state(exclusion: EligibilityExclusion, now: float) -> EligibilityState:
    if exclusion.state == EligibilityState.CLEARED:
        return EligibilityState.CLEARED
    if exclusion.retry_at is not None and exclusion.retry_at <= now:
        return EligibilityState.HALF_OPEN
    return exclusion.state


def candidate_exclusion(
    exclusions: tuple[EligibilityExclusion, ...],
    account_id: str,
    model: str,
    deployment_id: str,
    billing_route_id: str | None,
    now: float,
) -> EligibilityExclusion | None:
    matching: Final = _candidate_evidence(
        exclusions=exclusions,
        account_id=account_id,
        model=model,
        deployment_id=deployment_id,
        billing_route_id=billing_route_id,
        now=now,
    )
    return next(
        (exclusion for exclusion in matching if effective_state(exclusion, now) == EligibilityState.ACTIVE), None
    )


def candidate_evidence(
    exclusions: tuple[EligibilityExclusion, ...],
    account_id: str,
    model: str,
    deployment_id: str,
    billing_route_id: str | None,
    now: float,
) -> EligibilityExclusion | None:
    matching: Final = _candidate_evidence(
        exclusions=exclusions,
        account_id=account_id,
        model=model,
        deployment_id=deployment_id,
        billing_route_id=billing_route_id,
        now=now,
    )
    return next(iter(matching), None)


def clear_candidate(
    exclusions: tuple[EligibilityExclusion, ...],
    account_id: str,
    model: str,
    deployment_id: str,
    billing_route_id: str | None,
) -> tuple[EligibilityExclusion, ...]:
    return tuple(
        exclusion.model_copy(update={"state": EligibilityState.CLEARED})
        if _matches_candidate(exclusion, account_id, model, deployment_id, billing_route_id)
        and exclusion.state != EligibilityState.CLEARED
        else exclusion
        for exclusion in exclusions
    )


def upsert_exclusion(
    exclusions: tuple[EligibilityExclusion, ...],
    replacement: EligibilityExclusion,
) -> tuple[EligibilityExclusion, ...]:
    key: Final = _scope_key(replacement)
    retained: Final = tuple(exclusion for exclusion in exclusions if _scope_key(exclusion) != key)
    return (*retained, replacement)


def retain_configured_exclusions(
    exclusions: tuple[EligibilityExclusion, ...],
    accounts: tuple[AccountConfig, ...],
) -> tuple[EligibilityExclusion, ...]:
    subjects: Final = frozenset(configured_subjects(accounts))
    return tuple(exclusion for exclusion in exclusions if exclusion_subject(exclusion) in subjects)


def configured_subjects(accounts: tuple[AccountConfig, ...]) -> tuple[EligibilitySubject, ...]:
    subjects: Final = (
        *(EligibilitySubject(scope=EligibilityScope.CHANNEL, account_id=account.id) for account in accounts),
        *(
            EligibilitySubject(scope=EligibilityScope.MODEL, account_id=account.id, model=deployment.public_model)
            for account in accounts
            for deployment in account.deployments
        ),
        *(
            EligibilitySubject(
                scope=EligibilityScope.DEPLOYMENT,
                account_id=account.id,
                model=deployment.public_model,
                deployment_id=deployment.litellm_model_id,
            )
            for account in accounts
            for deployment in account.deployments
        ),
        *(
            EligibilitySubject(
                scope=EligibilityScope.BILLING_ROUTE,
                account_id=account.id,
                model=deployment.public_model,
                deployment_id=deployment.litellm_model_id,
                billing_route_id=deployment.billing_route_id,
            )
            for account in accounts
            for deployment in account.deployments
            if deployment.billing_route_id is not None
        ),
    )
    return tuple(dict.fromkeys(subjects))


def exclusion_subject(exclusion: EligibilityExclusion) -> EligibilitySubject:
    return EligibilitySubject(
        scope=exclusion.scope,
        account_id=exclusion.account_id,
        model=exclusion.model,
        deployment_id=exclusion.deployment_id,
        billing_route_id=exclusion.billing_route_id,
    )


def _matches_candidate(
    exclusion: EligibilityExclusion,
    account_id: str,
    model: str,
    deployment_id: str,
    billing_route_id: str | None,
) -> bool:
    if exclusion.account_id != account_id:
        return False
    if exclusion.scope == EligibilityScope.CHANNEL:
        return True
    if exclusion.model != model:
        return False
    if exclusion.scope == EligibilityScope.MODEL:
        return True
    if exclusion.deployment_id != deployment_id:
        return False
    return exclusion.scope == EligibilityScope.DEPLOYMENT or exclusion.billing_route_id == billing_route_id


def _candidate_evidence(
    exclusions: tuple[EligibilityExclusion, ...],
    account_id: str,
    model: str,
    deployment_id: str,
    billing_route_id: str | None,
    now: float,
) -> tuple[EligibilityExclusion, ...]:
    state_rank: Final = {EligibilityState.ACTIVE: 0, EligibilityState.HALF_OPEN: 1, EligibilityState.CLEARED: 2}
    scope_rank: Final = {
        EligibilityScope.CHANNEL: 0,
        EligibilityScope.MODEL: 1,
        EligibilityScope.DEPLOYMENT: 2,
        EligibilityScope.BILLING_ROUTE: 3,
    }
    return tuple(
        sorted(
            (
                exclusion
                for exclusion in exclusions
                if _matches_candidate(exclusion, account_id, model, deployment_id, billing_route_id)
                and effective_state(exclusion, now) != EligibilityState.CLEARED
            ),
            key=lambda exclusion: (
                state_rank[effective_state(exclusion, now)],
                scope_rank[exclusion.scope],
                exclusion.starts_at,
                exclusion.reason_code,
            ),
        )
    )


def _scope_key(exclusion: EligibilityExclusion) -> tuple[str, str, str | None, str | None, str | None, str]:
    return (
        exclusion.source,
        exclusion.account_id,
        exclusion.model,
        exclusion.deployment_id,
        exclusion.billing_route_id,
        exclusion.reason_code,
    )
