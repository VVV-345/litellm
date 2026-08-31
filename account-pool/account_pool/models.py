"""定义号池配置、运行状态和管理 API 的数据模型。"""

from __future__ import annotations

from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

AccountId = Annotated[str, Field(min_length=1, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")]
ModelName = Annotated[str, Field(min_length=1)]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Strategy(StrEnum):
    PRIORITY = "priority"
    RANDOM = "random"
    LOWEST_LATENCY = "lowest_latency"
    HIGHEST_REMAINING_QUOTA = "highest_remaining_quota"
    LOWEST_EFFECTIVE_COST = "lowest_effective_cost"
    LEAST_INFLIGHT = "least_inflight"
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"
    QUOTA_AWARE_LEAST_INFLIGHT = "quota_aware_least_inflight"


class ChannelPriority(IntEnum):
    LOW = 100
    MEDIUM = 200
    HIGH = 300
    HIGHEST = 400


def normalize_channel_priority(value: int) -> ChannelPriority:
    if value >= ChannelPriority.HIGHEST:
        return ChannelPriority.HIGHEST
    if value >= ChannelPriority.HIGH:
        return ChannelPriority.HIGH
    if value >= ChannelPriority.MEDIUM:
        return ChannelPriority.MEDIUM
    return ChannelPriority.LOW


class Health(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    HALF_OPEN = "half_open"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"


class QuotaUnit(StrEnum):
    TOKENS = "tokens"
    USD = "usd"


class RuntimeQuotaScope(StrEnum):
    CHANNEL = "channel"
    MODEL = "model"
    BILLING_ROUTE = "billing_route"


class RuntimeQuotaKind(StrEnum):
    REQUESTS = "requests"
    TOKENS = "tokens"
    CREDITS = "credits"
    CURRENCY = "currency"
    PROVIDER_UNITS = "provider_units"


class RuntimeQuotaWindowType(StrEnum):
    ROLLING = "rolling"
    FIXED = "fixed"
    RESET_AT = "reset_at"
    LIFETIME = "lifetime"


class CostEvidenceKind(StrEnum):
    NORMALIZED_PER_MILLION_TOKENS = "normalized_per_million_tokens"
    EFFECTIVE_PRICES = "effective_prices"
    SUBSCRIPTION_INCLUDED = "subscription_included"


class RuntimeBillingMode(StrEnum):
    SUBSCRIPTION = "subscription"
    METERED = "metered"
    PROVIDER_DECIDED = "provider_decided"


class DeploymentCostEvidence(FrozenModel):
    kind: CostEvidenceKind
    currency: str | None = Field(default=None, min_length=1)
    unit: str | None = Field(default=None, min_length=1)
    input_price: Decimal | None = Field(default=None, ge=0)
    output_price: Decimal | None = Field(default=None, ge=0)
    cache_read_price: Decimal | None = Field(default=None, ge=0)
    cache_write_price: Decimal | None = Field(default=None, ge=0)
    effective_cost: Decimal = Field(ge=0)
    partial: bool = False
    provider_group_id: str | None = Field(default=None, min_length=1)
    billing_mode: RuntimeBillingMode = RuntimeBillingMode.PROVIDER_DECIDED

    @model_validator(mode="after")
    def validate_cost(self) -> DeploymentCostEvidence:
        if self.kind == CostEvidenceKind.SUBSCRIPTION_INCLUDED:
            if self.effective_cost != 0 or self.currency is not None or self.unit is not None:
                raise ValueError("included subscription cost must be zero without a currency basis")
            return self
        expected: Final = tuple(value for value in (self.input_price, self.output_price) if value is not None)
        if self.currency is None or self.unit is None or not expected:
            raise ValueError("metered cost requires a currency, unit, and token price")
        if self.effective_cost != sum(expected, Decimal("0")):
            raise ValueError("effective cost must equal the available input and output prices")
        if self.partial != ((self.input_price is None) != (self.output_price is None)):
            raise ValueError("partial must identify one-sided token price evidence")
        return self


class QuotaWindowConfig(FrozenModel):
    window_id: str = Field(min_length=1, max_length=200)
    scope: RuntimeQuotaScope
    subject_id: str | None = Field(default=None, min_length=1)
    kind: RuntimeQuotaKind
    window_type: RuntimeQuotaWindowType | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    limit: Decimal | None = Field(default=None, ge=0)
    remaining: Decimal | None = Field(default=None, ge=0)
    safety_reserve: Decimal = Field(default=Decimal("0"), ge=0)
    reset_at: float | None = Field(default=None, ge=0)
    observed_at: float = Field(ge=0)
    source: str = Field(min_length=1)
    reason_code: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_window(self) -> QuotaWindowConfig:
        if (self.scope == RuntimeQuotaScope.CHANNEL) != (self.subject_id is None):
            raise ValueError("only channel quota windows omit subject_id")
        if self.window_type == RuntimeQuotaWindowType.ROLLING and self.duration_seconds is None:
            raise ValueError("rolling quota windows require duration_seconds")
        if self.window_type == RuntimeQuotaWindowType.RESET_AT and self.reset_at is None:
            raise ValueError("reset_at quota windows require reset_at")
        if self.limit is not None and self.safety_reserve > self.limit:
            raise ValueError("quota safety_reserve cannot exceed limit")
        return self


class DeploymentConfig(FrozenModel):
    public_model: ModelName
    litellm_model_id: str = Field(min_length=1)
    binding_id: UUID | None = None
    provider_model: str | None = None
    billing_route_id: str | None = Field(default=None, min_length=1)
    billing_mode: RuntimeBillingMode = RuntimeBillingMode.PROVIDER_DECIDED
    cost_evidence: DeploymentCostEvidence | None = None
    manual_order: int | None = Field(default=None, ge=0)
    routing_weight: int | None = Field(default=None, ge=1, le=100)
    routing_paused: bool = False
    managed_by_pool: bool = False
    enabled: bool = True


class QuotaConfig(FrozenModel):
    unit: QuotaUnit = QuotaUnit.TOKENS
    total: float | None = Field(default=None, ge=0)
    five_hour: float | None = Field(default=None, ge=0)
    weekly: float | None = Field(default=None, ge=0)


class AccountConfig(FrozenModel):
    id: AccountId
    channel_id: UUID | None = None
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    group: str | None = None
    base_url_display: str = Field(min_length=1)
    enabled: bool = True
    max_concurrency: int = Field(ge=1)
    priority: int = 0
    weight: int = Field(default=1, ge=1, le=100)
    quotas: QuotaConfig = QuotaConfig()
    quota_windows: tuple[QuotaWindowConfig, ...] = ()
    deployments: tuple[DeploymentConfig, ...] = Field(min_length=1)


class ModelPolicy(FrozenModel):
    model: ModelName
    strategy: Strategy = Strategy.QUOTA_AWARE_LEAST_INFLIGHT
    version: int = Field(default=0, ge=0)


class PoolConfig(FrozenModel):
    accounts: tuple[AccountConfig, ...] = ()
    policies: tuple[ModelPolicy, ...] = ()

    @model_validator(mode="after")
    def validate_unique_ids(self) -> PoolConfig:
        account_ids = tuple(account.id for account in self.accounts)
        deployment_ids = tuple(
            deployment.litellm_model_id for account in self.accounts for deployment in account.deployments
        )
        policy_models = tuple(policy.model for policy in self.policies)
        quota_window_ids = tuple(window.window_id for account in self.accounts for window in account.quota_windows)
        if len(account_ids) != len(set(account_ids)):
            raise ValueError("account ids must be unique")
        if len(deployment_ids) != len(set(deployment_ids)):
            raise ValueError("LiteLLM deployment ids must be unique")
        if len(policy_models) != len(set(policy_models)):
            raise ValueError("model policies must be unique")
        if len(quota_window_ids) != len(set(quota_window_ids)):
            raise ValueError("quota window ids must be unique")
        return self


class QuotaSnapshot(FrozenModel):
    unit: QuotaUnit
    total: float | None
    five_hour: float | None
    weekly: float | None


class AccountSnapshot(FrozenModel):
    account_id: AccountId
    enabled: bool
    health: Health
    inflight: int = Field(ge=0)
    max_concurrency: int = Field(ge=1)
    cooldown_until: float | None
    consecutive_failures: int = Field(ge=0)
    reason_code: str | None = None
    quota: QuotaSnapshot


class Lease(FrozenModel):
    lease_id: str
    request_id: str
    account_id: AccountId
    deployment_id: str
    public_model: ModelName
    billing_route_id: str | None = None
    probe: bool = False
    generation_id: UUID | None = None
    expires_at: float
    absolute_expires_at: float
    settled: bool = False
    released: bool = False

    @model_validator(mode="after")
    def validate_expiry(self) -> Lease:
        if self.expires_at > self.absolute_expires_at:
            raise ValueError("lease expiry cannot exceed its absolute expiry")
        return self


class AcquireRequest(FrozenModel):
    request_id: str = Field(min_length=1)
    model: ModelName
    estimated_tokens: int = Field(default=0, ge=0)


class AcquireSuccess(FrozenModel):
    status: Literal["acquired"] = "acquired"
    lease: Lease


class AcquireRejectionStage(StrEnum):
    CONFIGURATION = "configuration"
    ELIGIBILITY = "eligibility"
    RESERVATION = "reservation"


class AcquireRejectionScope(StrEnum):
    CHANNEL = "channel"
    MODEL = "model"
    DEPLOYMENT = "deployment"
    BILLING_ROUTE = "billing_route"


class AcquireRejectionSource(StrEnum):
    ADMINISTRATIVE = "administrative"
    HEALTH = "health"
    RESTRICTION = "restriction"
    CAPACITY = "capacity"
    QUOTA = "quota"
    RUNTIME = "runtime"


class AcquireRejectionState(StrEnum):
    ACTIVE = "active"
    HALF_OPEN = "half_open"


class AcquireCandidateRejection(FrozenModel):
    account_id: AccountId
    deployment_id: str = Field(min_length=1)
    binding_id: UUID | None = None
    billing_route_id: str | None = Field(default=None, min_length=1)
    stage: AcquireRejectionStage
    reason_code: str = Field(min_length=1, max_length=100)
    scope: AcquireRejectionScope
    source: AcquireRejectionSource
    state: AcquireRejectionState = AcquireRejectionState.ACTIVE
    retry_at: float | None = Field(default=None, ge=0)


class AcquireUnavailable(FrozenModel):
    status: Literal["unavailable"] = "unavailable"
    code: Literal["no_available_route"] = "no_available_route"
    model: ModelName
    reason_codes: tuple[str, ...] = Field(min_length=1)
    candidates: tuple[AcquireCandidateRejection, ...] = ()
    retry_at: float | None = Field(default=None, ge=0)

    @property
    def reasons(self) -> tuple[str, ...]:
        if not self.candidates:
            return self.reason_codes
        return tuple(f"{candidate.account_id}:{candidate.reason_code}" for candidate in self.candidates)


AcquireResult = AcquireSuccess | AcquireUnavailable


class ReserveSuccess(FrozenModel):
    status: Literal["reserved"] = "reserved"
    lease: Lease


class ReserveRejected(FrozenModel):
    status: Literal["rejected"] = "rejected"
    reason: str


ReserveResult = ReserveSuccess | ReserveRejected


class SettleRequest(FrozenModel):
    lease_id: str = Field(min_length=1)
    success: bool
    status_code: int | None = Field(default=None, ge=100, le=599)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    error_type: str | None = None
    provider_error_code: str | None = Field(default=None, min_length=1, max_length=128)
    retry_after_seconds: float | None = Field(default=None, ge=0, le=86_400)


class SettlementEventRequest(FrozenModel):
    lease: Lease
    settlement: SettleRequest


class ReleaseRequest(FrozenModel):
    lease_id: str = Field(min_length=1)


class HeartbeatRequest(FrozenModel):
    lease_id: str = Field(min_length=1)


class RouteEntry(FrozenModel):
    account_id: AccountId
    display_name: str
    provider: str
    base_url_display: str
    deployment_id: str
    billing_route_id: str | None = None
    billing_mode: RuntimeBillingMode = RuntimeBillingMode.PROVIDER_DECIDED
    public_model: ModelName
    enabled: bool
    health: Health
    inflight: int
    max_concurrency: int
    cooldown_until: float | None
    reason_code: str | None
    exclusion_scope: str | None = None
    exclusion_source: str | None = None
    exclusion_state: str | None = None
    retry_at: float | None = None
    quota: QuotaSnapshot
    priority: int
    weight: int
    available: bool
    unavailable_reason: str | None
    binding_id: UUID | None = None
    position: int | None = Field(default=None, ge=1)
    strategy: Strategy | None = None
    dynamic_order: bool = False
    sort_reason_codes: tuple[str, ...] = ()
    remaining_quota_ratio: float | None = Field(default=None, ge=0)
    remaining_quota: Decimal | None = Field(default=None, ge=0)
    remaining_quota_unit: str | None = Field(default=None, min_length=1)
    latency_ewma_ms: float | None = Field(default=None, ge=0)
    effective_cost: Decimal | None = Field(default=None, ge=0)
    cost_evidence: DeploymentCostEvidence | None = None
    manual_order: int | None = Field(default=None, ge=0)
    effective_weight: int = Field(default=1, ge=1, le=100)
    routing_paused: bool = False


class AccountView(FrozenModel):
    id: AccountId
    display_name: str
    provider: str
    group: str | None
    base_url_display: str
    models: tuple[str, ...]
    priority: int
    weight: int
    quotas: QuotaConfig
    deployments: tuple[DeploymentConfig, ...]
    runtime: AccountSnapshot


class ModelSummary(FrozenModel):
    model: str
    strategy: Strategy
    accounts: int
    available_accounts: int
    inflight: int
    max_concurrency: int
    version: int = Field(default=0, ge=0)


class StatsView(FrozenModel):
    models: int
    accounts: int
    available_accounts: int
    inflight: int
    max_concurrency: int


class OperationResult(FrozenModel):
    ok: bool


class DeploymentInput(FrozenModel):
    public_model: ModelName
    provider_model: str | None = None
    litellm_model_id: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def validate_source(self) -> DeploymentInput:
        if self.litellm_model_id is None and self.provider_model is None:
            raise ValueError("provider_model is required when creating a LiteLLM deployment")
        return self


class AccountMutation(FrozenModel):
    id: AccountId
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    group: str | None = None
    base_url_display: str = Field(min_length=1)
    enabled: bool = True
    max_concurrency: int = Field(ge=1)
    priority: ChannelPriority = ChannelPriority.MEDIUM
    weight: int = Field(default=1, ge=1, le=100)
    quotas: QuotaConfig = QuotaConfig()
    deployments: tuple[DeploymentInput, ...] = Field(min_length=1)
    api_key: SecretStr | None = None


class PolicyUpdate(FrozenModel):
    strategy: Strategy


class ManagementResult(FrozenModel):
    ok: bool
    message: str


class LiteLLMStatus(FrozenModel):
    connected: bool
    authenticated: bool
    manageable: bool
    deployment_count: int | None = None
    message: str
