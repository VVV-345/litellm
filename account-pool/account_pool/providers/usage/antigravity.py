"""解析 Antigravity 套餐、项目和模型额度，不发送网络请求或参与路由。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from account_pool.domain import ModelQuotaSnapshot, QuotaBalance, QuotaSnapshot, QuotaWindow
from account_pool.providers.usage.common import parse_model
from account_pool.providers.usage.contracts import ProviderQuotaRefresh


@dataclass(frozen=True, slots=True)
class AntigravityAssist:
    project_id: str | None
    plan_type: str | None
    balances: tuple[QuotaBalance, ...] = ()
    uses_gcp_tos: bool | None = None
    onboard_tier_id: str | None = None


class _AntigravityProject(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str | None = None


class _AntigravityCredit(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    credit_type: str | None = Field(default=None, alias="creditType")
    credit_amount: float | int | str | None = Field(default=None, alias="creditAmount")
    minimum_credit_amount_for_usage: float | int | str | None = Field(
        default=None,
        alias="minimumCreditAmountForUsage",
    )


class _AntigravityTier(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str | None = None
    uses_gcp_tos: bool | None = Field(default=None, alias="usesGcpTos")
    available_credits: tuple[_AntigravityCredit, ...] = Field(default=(), alias="availableCredits")


class _AntigravityAllowedTier(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str | None = None
    is_default: bool | None = Field(default=None, alias="isDefault")
    uses_gcp_tos: bool | None = Field(default=None, alias="usesGcpTos")


class _AntigravityAssistPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    project: str | _AntigravityProject | None = Field(default=None, alias="cloudaicompanionProject")
    current_tier: _AntigravityTier | None = Field(default=None, alias="currentTier")
    paid_tier: _AntigravityTier | None = Field(default=None, alias="paidTier")
    allowed_tiers: tuple[_AntigravityAllowedTier, ...] = Field(default=(), alias="allowedTiers")


class _AntigravityQuotaInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    remaining_fraction: float | None = Field(default=None, alias="remainingFraction")
    reset_time: datetime | None = Field(default=None, alias="resetTime")


class _AntigravityModelInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    quota_info: _AntigravityQuotaInfo | None = Field(default=None, alias="quotaInfo")


class _AntigravityModelsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    models: Mapping[str, _AntigravityModelInfo] = Field(default_factory=dict)


class _AntigravitySummaryBucket(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    bucket_id: str | None = Field(default=None, alias="bucketId")
    display_name: str | None = Field(default=None, alias="displayName")
    remaining_fraction: float | None = Field(default=None, alias="remainingFraction")
    reset_time: datetime | None = Field(default=None, alias="resetTime")


class _AntigravitySummaryGroup(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    group_id: str | None = Field(default=None, alias="groupId")
    display_name: str | None = Field(default=None, alias="displayName")
    name: str | None = None
    buckets: tuple[_AntigravitySummaryBucket, ...] = ()


class _AntigravitySummaryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    groups: tuple[_AntigravitySummaryGroup, ...] = ()


class _AntigravityOnboardResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    project: str | _AntigravityProject | None = Field(default=None, alias="cloudaicompanionProject")


class _AntigravityOnboardPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str | None = None
    done: bool = False
    response: _AntigravityOnboardResponse | None = None


def parse_antigravity_assist(body: str | None) -> AntigravityAssist | None:
    payload: Final = parse_model(_AntigravityAssistPayload, body)
    if payload is None:
        return None
    project_id: Final = (
        payload.project.strip()
        if isinstance(payload.project, str) and payload.project.strip()
        else payload.project.id.strip()
        if isinstance(payload.project, _AntigravityProject)
        and payload.project.id is not None
        and payload.project.id.strip()
        else None
    )
    paid_tier: Final = payload.paid_tier.id.strip() if payload.paid_tier and payload.paid_tier.id else None
    current_tier: Final = payload.current_tier.id.strip() if payload.current_tier and payload.current_tier.id else None
    selected_tier: Final = payload.paid_tier or payload.current_tier
    default_tier: Final = next((tier for tier in payload.allowed_tiers if tier.is_default is True), None)
    first_allowed_tier: Final = next((tier for tier in payload.allowed_tiers if tier.id), None)
    onboard_tier_id: Final = (
        default_tier.id.strip()
        if default_tier is not None and default_tier.id is not None and default_tier.id.strip()
        else first_allowed_tier.id.strip()
        if first_allowed_tier is not None and first_allowed_tier.id is not None and first_allowed_tier.id.strip()
        else "LEGACY"
        if payload.allowed_tiers
        else paid_tier or current_tier
    )
    current_tier_id: Final = None if payload.current_tier is None or current_tier is None else current_tier.casefold()
    paid_tier_id: Final = None if payload.paid_tier is None or paid_tier is None else paid_tier.casefold()
    uses_gcp_tos: Final = (
        payload.current_tier.uses_gcp_tos
        if payload.current_tier is not None and payload.current_tier.uses_gcp_tos is not None
        else default_tier.uses_gcp_tos
        if default_tier is not None and default_tier.uses_gcp_tos is not None
        else True
        if "standard-tier" in (current_tier_id, paid_tier_id)
        else None
    )
    balances: Final = (
        ()
        if selected_tier is None
        else tuple(
            balance
            for credit in selected_tier.available_credits
            if (balance := _antigravity_credit_balance(credit)) is not None
        )
    )
    return AntigravityAssist(
        project_id=project_id,
        plan_type=paid_tier or current_tier,
        balances=balances,
        uses_gcp_tos=uses_gcp_tos,
        onboard_tier_id=onboard_tier_id,
    )


def parse_antigravity_onboard_project(body: str | None) -> tuple[str | None, str | None, bool]:
    payload: Final = parse_model(_AntigravityOnboardPayload, body)
    if payload is None:
        return None, None, False
    project_value: Final = None if payload.response is None else payload.response.project
    project: Final = (
        project_value.strip()
        if isinstance(project_value, str) and project_value.strip()
        else project_value.id.strip()
        if isinstance(project_value, _AntigravityProject)
        and project_value.id is not None
        and project_value.id.strip()
        else None
    )
    operation: Final = payload.name.strip() if payload.name is not None and payload.name.strip() else None
    return project, operation, payload.done


def parse_antigravity_quota(
    models_body: str,
    assist: AntigravityAssist | None,
    observed_at: datetime,
    *,
    summary_body: str | None = None,
) -> ProviderQuotaRefresh | None:
    payload: Final = parse_model(_AntigravityModelsPayload, models_body)
    if payload is None:
        return None
    model_quotas: Final = tuple(
        model_quota
        for model, info in sorted(payload.models.items())
        if (model_quota := _antigravity_model_quota(model, info, observed_at)) is not None
    )
    summary_quotas: Final = _antigravity_summary_quotas(summary_body, observed_at)
    summary_names: Final = frozenset(item.model for item in summary_quotas)
    combined_model_quotas: Final = (
        *summary_quotas,
        *(item for item in model_quotas if item.model not in summary_names),
    )
    plan_type: Final = None if assist is None else assist.plan_type
    balances: Final = () if assist is None else assist.balances
    if not combined_model_quotas and plan_type is None and not balances:
        return None
    return ProviderQuotaRefresh(
        quota=QuotaSnapshot(observed_at=observed_at, plan_type=plan_type, balances=balances),
        model_quotas=combined_model_quotas,
    )


def _antigravity_credit_balance(credit: _AntigravityCredit) -> QuotaBalance | None:
    name: Final = (credit.credit_type or "").strip()
    available: Final = _nonnegative_float(credit.credit_amount)
    minimum: Final = _nonnegative_float(credit.minimum_credit_amount_for_usage)
    if not name or available is None:
        return None
    return QuotaBalance(name=name, available=available, minimum_required=minimum, unit="credits")


def _nonnegative_float(value: float | str | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed: Final = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _antigravity_summary_quotas(body: str | None, observed_at: datetime) -> tuple[ModelQuotaSnapshot, ...]:
    payload: Final = parse_model(_AntigravitySummaryPayload, body)
    if payload is None:
        return ()
    entries: Final = tuple(
        entry
        for group in payload.groups
        for bucket in group.buckets
        if (entry := _antigravity_summary_entry(group, bucket, observed_at)) is not None
    )
    models: Final = tuple(dict.fromkeys(model for model, _ in entries))
    return tuple(
        ModelQuotaSnapshot(
            model=model,
            quota=QuotaSnapshot(
                observed_at=observed_at,
                windows=tuple(window for candidate, window in entries if candidate == model),
            ),
        )
        for model in models
    )


def _antigravity_summary_entry(
    group: _AntigravitySummaryGroup,
    bucket: _AntigravitySummaryBucket,
    observed_at: datetime,
) -> tuple[str, QuotaWindow] | None:
    remaining_fraction: Final = bucket.remaining_fraction
    if remaining_fraction is None or remaining_fraction < 0 or remaining_fraction > 1:
        return None
    identifier: Final = (bucket.bucket_id or "").strip()
    display_name: Final = (bucket.display_name or "").strip()
    model: Final = identifier or display_name
    if not model:
        return None
    remaining: Final = remaining_fraction * 100
    resets_at: Final = bucket.reset_time
    group_label: Final = (group.display_name or group.name or group.group_id or "").strip()
    window_name, fallback_minutes = _antigravity_summary_window(group_label, display_name, identifier)
    return model, QuotaWindow(
        name=window_name,
        used_percent=100 - remaining,
        remaining_percent=remaining,
        window_minutes=fallback_minutes,
        resets_at=resets_at,
    )


def _antigravity_summary_window(group: str, display_name: str, identifier: str) -> tuple[str, int]:
    value: Final = " ".join((group, display_name, identifier)).casefold().replace("_", "-")
    if "5-hour" in value or "5 hour" in value or "5h" in value or "session" in value:
        return "5 hour", 300
    if "weekly" in value or "7-day" in value or "7 day" in value or "7d" in value or "week" in value:
        return "Weekly", 10080
    return "Quota bucket", 1


def _antigravity_model_quota(
    model: str,
    info: _AntigravityModelInfo,
    observed_at: datetime,
) -> ModelQuotaSnapshot | None:
    quota_info: Final = info.quota_info
    if quota_info is None or quota_info.remaining_fraction is None:
        return None
    remaining: Final = quota_info.remaining_fraction * 100
    if remaining < 0 or remaining > 100:
        return None
    resets_at: Final = quota_info.reset_time
    window_minutes: Final = (
        max(1, ceil((resets_at - observed_at).total_seconds() / 60))
        if resets_at is not None and resets_at > observed_at
        else 1
    )
    return ModelQuotaSnapshot(
        model=model,
        quota=QuotaSnapshot(
            observed_at=observed_at,
            windows=(
                QuotaWindow(
                    name="Model",
                    used_percent=100 - remaining,
                    remaining_percent=remaining,
                    window_minutes=window_minutes,
                    resets_at=resets_at,
                ),
            ),
        ),
    )
