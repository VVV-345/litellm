"""定义 scope 资格记录的 Redis 键、候选主体和安全编解码。"""

from __future__ import annotations

from typing import Final

from account_pool.eligibility.models import (
    EligibilityExclusion,
    EligibilityScope,
    EligibilitySource,
    EligibilitySubject,
)
from account_pool.eligibility.state import activate_exclusion, configured_subjects
from account_pool.models import AccountConfig


def eligibility_subjects(accounts: tuple[AccountConfig, ...]) -> tuple[EligibilitySubject, ...]:
    return configured_subjects(accounts)


def channel_eligibility_key(account_id: str) -> str:
    return f"pool:eligibility:channel:{account_id}"


def model_eligibility_key(account_id: str, model: str) -> str:
    return f"pool:eligibility:model:{account_id}:{model}"


def deployment_eligibility_key(account_id: str, deployment_id: str) -> str:
    return f"pool:eligibility:deployment:{account_id}:{deployment_id}"


def billing_route_eligibility_key(account_id: str, billing_route_id: str | None) -> str:
    return (
        f"pool:eligibility:billing_route:{account_id}:{billing_route_id}"
        if billing_route_id is not None
        else "pool:eligibility:none"
    )


def eligibility_key(subject: EligibilitySubject) -> str:
    if subject.scope == EligibilityScope.CHANNEL:
        return channel_eligibility_key(subject.account_id)
    if subject.scope == EligibilityScope.MODEL:
        return model_eligibility_key(subject.account_id, subject.model or "")
    if subject.scope == EligibilityScope.DEPLOYMENT:
        return deployment_eligibility_key(subject.account_id, subject.deployment_id or "")
    return billing_route_eligibility_key(subject.account_id, subject.billing_route_id)


def decode_exclusions(
    subject: EligibilitySubject,
    entries: dict[str, str],
) -> tuple[EligibilityExclusion, ...]:
    return tuple(_decode_exclusion(subject, field, value) for field, value in entries.items())


def _decode_exclusion(subject: EligibilitySubject, field: str, value: str) -> EligibilityExclusion:
    source_value, source_separator, reason_code = field.partition("|")
    starts_at_value, time_separator, retry_at_value = value.partition("|")
    if not source_separator or not time_separator:
        raise ValueError("invalid Redis eligibility record")
    retry_at: Final = float(retry_at_value)
    return activate_exclusion(
        scope=subject.scope,
        source=EligibilitySource(source_value),
        account_id=subject.account_id,
        model=subject.model,
        deployment_id=subject.deployment_id,
        billing_route_id=subject.billing_route_id,
        reason_code=reason_code,
        starts_at=float(starts_at_value),
        retry_at=retry_at if retry_at > 0 else None,
    )
