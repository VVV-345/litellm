"""定义解析器统一输出、计费数据与安全问题报告。"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from account_pool.domain.provider_source import ProviderCapability
from account_pool.models import FrozenModel


class ParserRunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    AUTHENTICATION_FAILED = "authentication_failed"
    TRANSPORT_FAILED = "transport_failed"
    INVALID_RESPONSE = "invalid_response"
    MANUAL_REQUIRED = "manual_required"


class ParserFailureCategory(StrEnum):
    AUTHENTICATION = "authentication"
    TRANSPORT = "transport"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED = "unsupported"
    INCOMPLETE = "incomplete"
    MANUAL_REQUIRED = "manual_required"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    TRIAL = "trial"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


class QuotaScope(StrEnum):
    CHANNEL = "channel"
    MODEL = "model"
    GROUP = "group"


class QuotaKind(StrEnum):
    REQUESTS = "requests"
    TOKENS = "tokens"
    CREDITS = "credits"
    CURRENCY = "currency"
    PROVIDER_UNITS = "provider_units"


class QuotaWindowType(StrEnum):
    ROLLING = "rolling"
    FIXED = "fixed"
    RESET_AT = "reset_at"
    LIFETIME = "lifetime"


class BillingMode(StrEnum):
    SUBSCRIPTION = "subscription"
    METERED = "metered"


class PriceCalculation(StrEnum):
    MULTIPLIER = "multiplier"
    PROVIDER_NORMALIZED = "provider_normalized"


class UnresolvedField(FrozenModel):
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    retryable: bool = False


class SafeEvidence(FrozenModel):
    source: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    observed_at: AwareDatetime


class ModelIdentity(FrozenModel):
    provider_model_id: str = Field(min_length=1)
    litellm_model_name: str | None = None
    public_model_name: str | None = None


class ConcurrencyLimit(FrozenModel):
    subject_id: str | None = None
    limit: int = Field(ge=1)


class QuotaLimit(FrozenModel):
    scope: QuotaScope
    subject_id: str | None = None
    kind: QuotaKind
    window_type: QuotaWindowType | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    limit: Decimal | None = Field(default=None, ge=0)
    used: Decimal | None = Field(default=None, ge=0)
    remaining: Decimal | None = Field(default=None, ge=0)
    reset_at: AwareDatetime | None = None
    source: str = Field(min_length=1)
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.window_type == QuotaWindowType.ROLLING and self.duration_seconds is None:
            raise ValueError("rolling quota window requires duration_seconds")
        if self.window_type == QuotaWindowType.RESET_AT and self.reset_at is None:
            raise ValueError("reset_at quota window requires reset_at")
        return self


class SubscriptionData(FrozenModel):
    plan_id: str | None = None
    plan_name: str | None = None
    status: SubscriptionStatus = SubscriptionStatus.UNKNOWN
    starts_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    models: tuple[ModelIdentity, ...] = ()
    balance: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    channel_concurrency: int | None = Field(default=None, ge=1)
    model_concurrency: tuple[ConcurrencyLimit, ...] = ()
    limits: tuple[QuotaLimit, ...] = ()


class EffectivePrices(FrozenModel):
    input_price: Decimal | None = Field(default=None, ge=0)
    output_price: Decimal | None = Field(default=None, ge=0)
    cache_read_price: Decimal | None = Field(default=None, ge=0)
    cache_write_price: Decimal | None = Field(default=None, ge=0)


class MeteredModelPrice(FrozenModel):
    provider_model_id: str = Field(min_length=1)
    litellm_model_name: str | None = None
    public_model_name: str | None = None
    currency: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    input_price: Decimal | None = Field(default=None, ge=0)
    output_price: Decimal | None = Field(default=None, ge=0)
    cache_read_price: Decimal | None = Field(default=None, ge=0)
    cache_write_price: Decimal | None = Field(default=None, ge=0)
    group_multiplier: Decimal = Field(default=Decimal("1"), gt=0)
    price_calculation: PriceCalculation = PriceCalculation.MULTIPLIER
    conversion_note: str | None = None
    effective_prices: EffectivePrices
    normalized_per_million_tokens: EffectivePrices | None = None
    concurrency: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_effective_prices(self) -> Self:
        if self.price_calculation == PriceCalculation.PROVIDER_NORMALIZED:
            if self.conversion_note is None:
                raise ValueError("provider-normalized prices require a conversion note")
            return self
        pairs: Final = (
            ("input", self.input_price, self.effective_prices.input_price),
            ("output", self.output_price, self.effective_prices.output_price),
            ("cache read", self.cache_read_price, self.effective_prices.cache_read_price),
            ("cache write", self.cache_write_price, self.effective_prices.cache_write_price),
        )
        for label, source, effective in pairs:
            if source is None and effective is None:
                continue
            if source is None or effective != source * self.group_multiplier:
                raise ValueError(f"effective {label} price must equal source price multiplied by group multiplier")
        return self


class MeteredGroup(FrozenModel):
    group_id: str | None = None
    group_name: str | None = None
    models: tuple[MeteredModelPrice, ...] = ()
    concurrency: int | None = Field(default=None, ge=1)


class MeteredData(FrozenModel):
    groups: tuple[MeteredGroup, ...] = ()


class BillingRoute(FrozenModel):
    route_id: UUID
    deployment_binding_id: UUID
    mode: BillingMode
    provider_group_id: str | None = None
    request_parameter_ref: str | None = None


class ParsedChannelData(FrozenModel):
    subscription: SubscriptionData | None = None
    metered: MeteredData | None = None
    billing_routes: tuple[BillingRoute, ...] = ()
    capabilities: tuple[ProviderCapability, ...] = ()
    unresolved_fields: tuple[UnresolvedField, ...] = ()
    evidence: tuple[SafeEvidence, ...] = ()
    warnings: tuple[str, ...] = ()

    def has_data(self) -> bool:
        return any(
            (
                self.subscription is not None,
                self.metered is not None,
                bool(self.billing_routes),
                bool(self.capabilities),
                bool(self.evidence),
            )
        )


class ParserIssue(FrozenModel):
    parser_id: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    category: ParserFailureCategory
    field_paths: tuple[str, ...] = ()
    retryable: bool
    next_action: str = Field(min_length=1)
    evidence_summary: str = Field(min_length=1)
    first_seen_at: AwareDatetime
    latest_seen_at: AwareDatetime

    @model_validator(mode="after")
    def validate_occurrence_order(self) -> Self:
        if self.latest_seen_at < self.first_seen_at:
            raise ValueError("latest issue occurrence cannot precede first occurrence")
        return self


class ParserRun(FrozenModel):
    parser_run_id: UUID
    channel_id: UUID
    parser_id: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    parsed_at: AwareDatetime
    status: ParserRunStatus
    result: ParsedChannelData = ParsedChannelData()
    discovered_models: tuple[str, ...] = ()
    issues: tuple[ParserIssue, ...] = ()

    @model_validator(mode="after")
    def validate_status_result(self) -> Self:
        if self.status == ParserRunStatus.SUCCESS and not self.result.has_data():
            raise ValueError("successful parser run requires a non-empty result")
        if self.status not in (ParserRunStatus.SUCCESS, ParserRunStatus.PARTIAL) and not self.issues:
            raise ValueError("failed parser run requires at least one issue")
        return self
