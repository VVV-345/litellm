"""定义号池配置、运行状态和管理 API 的数据模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

AccountId = Annotated[str, Field(min_length=1, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")]
ModelName = Annotated[str, Field(min_length=1)]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Strategy(StrEnum):
    PRIORITY = "priority"
    LEAST_INFLIGHT = "least_inflight"
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"
    QUOTA_AWARE_LEAST_INFLIGHT = "quota_aware_least_inflight"


class Health(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"


class QuotaUnit(StrEnum):
    TOKENS = "tokens"
    USD = "usd"


class DeploymentConfig(FrozenModel):
    public_model: ModelName
    litellm_model_id: str = Field(min_length=1)
    provider_model: str | None = None
    managed_by_pool: bool = False
    enabled: bool = True


class QuotaConfig(FrozenModel):
    unit: QuotaUnit = QuotaUnit.TOKENS
    total: float | None = Field(default=None, ge=0)
    five_hour: float | None = Field(default=None, ge=0)
    weekly: float | None = Field(default=None, ge=0)


class AccountConfig(FrozenModel):
    id: AccountId
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    group: str | None = None
    base_url_display: str = Field(min_length=1)
    enabled: bool = True
    max_concurrency: int = Field(ge=1)
    priority: int = 0
    weight: int = Field(default=1, ge=1, le=100)
    quotas: QuotaConfig = QuotaConfig()
    deployments: tuple[DeploymentConfig, ...] = Field(min_length=1)


class ModelPolicy(FrozenModel):
    model: ModelName
    strategy: Strategy = Strategy.QUOTA_AWARE_LEAST_INFLIGHT


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
        if len(account_ids) != len(set(account_ids)):
            raise ValueError("account ids must be unique")
        if len(deployment_ids) != len(set(deployment_ids)):
            raise ValueError("LiteLLM deployment ids must be unique")
        if len(policy_models) != len(set(policy_models)):
            raise ValueError("model policies must be unique")
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
    expires_at: float
    settled: bool = False
    released: bool = False


class AcquireRequest(FrozenModel):
    request_id: str = Field(min_length=1)
    model: ModelName
    estimated_tokens: int = Field(default=0, ge=0)


class AcquireSuccess(FrozenModel):
    status: Literal["acquired"] = "acquired"
    lease: Lease


class AcquireUnavailable(FrozenModel):
    status: Literal["unavailable"] = "unavailable"
    model: ModelName
    reasons: tuple[str, ...]


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
    public_model: ModelName
    enabled: bool
    health: Health
    inflight: int
    max_concurrency: int
    cooldown_until: float | None
    reason_code: str | None
    quota: QuotaSnapshot
    priority: int
    weight: int
    available: bool
    unavailable_reason: str | None


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
    priority: int = 0
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
