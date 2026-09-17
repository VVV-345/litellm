"""本模块定义网关鉴权、候选快照和并发租约的内部协议，不向管理页面暴露内部凭据。"""

from __future__ import annotations

from typing import Literal, TypeAlias
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPolicy,
    ChannelKind,
    RoutingReason,
    SupplierKind,
)

AcquireRejectionReason: TypeAlias = Literal["concurrency", "configuration", "cooldown", "session", "token_budget"]


class ResolveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    card_key: str = Field(default="", max_length=256, repr=False)
    trusted_card_id: UUID | None = None
    trusted_key_id: UUID | None = None
    binding_id: UUID | None = None
    session_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> ResolveRequest:
        if self.card_key:
            if self.trusted_card_id is not None or self.trusted_key_id is not None or self.binding_id is not None:
                raise ValueError("Raw and trusted identities cannot be combined")
        elif self.trusted_card_id is None or self.trusted_key_id is None:
            raise ValueError("Trusted card and key identity are required")
        if self.binding_id is not None and self.binding_id != self.trusted_key_id:
            raise ValueError("Card binding must match the key identity")
        return self


class GatewayCredential(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_key: str = Field(repr=False)
    proxy_url: str | None = None
    weight: int = Field(default=1, ge=1, le=10000)


class CandidateModelQuota(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    remaining_percent: float = Field(ge=0, le=100)
    observed_at: AwareDatetime | None = None


class CandidateModelCooldown(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    retry_at: AwareDatetime
    reason: str = "upstream_error"


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    channel: ChannelKind
    supplier: SupplierKind
    environment_version: int
    policy_version: int
    enabled_models: tuple[str, ...]
    api_base: str
    api_key: str = Field(repr=False)
    credentials: tuple[GatewayCredential, ...] = Field(default=(), repr=False)
    headers: tuple[tuple[str, str], ...] = Field(default=(), repr=False)
    proxy_endpoint: str | None = None
    model_prefix: str = ""
    concurrency_limit: int = Field(ge=0, le=1000)
    policy: AccountPolicy
    remaining_percent: float | None = None
    quota_observed_at: AwareDatetime | None = None
    model_quotas: tuple[CandidateModelQuota, ...] = ()
    plan_type: str | None = None
    auth_file_plan_type: str | None = None
    subscription_active_until: AwareDatetime | None = None
    websocket_enabled: bool = False
    model_cooldowns: tuple[CandidateModelCooldown, ...] = ()


class Resolution(BaseModel):
    model_config = ConfigDict(frozen=True)
    card_id: UUID
    key_id: UUID
    card_version: int
    policy_version: int
    policy: AccountPolicy
    candidates: tuple[Candidate, ...]
    full_logging_enabled: bool = False
    full_log_skip_failed: bool = False
    full_log_retention_days: int = 30
    sticky_account_id: UUID | None = None
    streaming_mode: Literal["inherit", "enabled", "disabled"] = "inherit"
    websocket_enabled: bool = False
    enabled_models: tuple[str, ...] = ()


class AcquireRequest(ResolveRequest):
    account_id: UUID
    request_id: UUID
    model: str = Field(min_length=1, max_length=256)
    card_version: int
    policy_version: int
    account_version: int
    account_policy_version: int
    timeout_seconds: int = Field(ge=1, le=3600)
    estimated_tokens: int = Field(default=0, ge=0, le=1000000000)
    attempt: int = Field(ge=1, le=10)
    routing_reason: RoutingReason = "automatic"
    allow_session_rebind: bool = False


class Lease(BaseModel):
    model_config = ConfigDict(frozen=True)
    lease_id: UUID
    card_id: UUID
    key_id: UUID
    account_id: UUID
    request_id: UUID
    channel: ChannelKind
    supplier: SupplierKind
    model: str
    started_at: AwareDatetime
    reserved_tokens: int = Field(default=0, ge=0)
    budget_enabled: bool = False
    budget_window_seconds: int | None = Field(default=None, ge=60, le=2592000)
    budget_window_started_at: AwareDatetime | None = None
    attempt: int = Field(default=1, ge=1, le=10)
    routing_reason: RoutingReason = "automatic"


class FinishRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    lease_id: UUID
    http_status: int = Field(ge=100, le=599)
    stage: Literal["connection", "upstream", "response"] = "upstream"
    message: str = Field(max_length=500)
    upstream_code: str | None = Field(default=None, max_length=120)
    retry_after_seconds: int = Field(default=0, ge=0, le=86400)
    model_cooldown_seconds: int = Field(default=0, ge=0, le=86400)
    retryable: bool = False
    switched_account: bool = False
    next_account_id: UUID | None = None
    endpoint: str = Field(max_length=256)
    method: Literal["GET", "POST"] = "POST"
    detail: str | None = Field(default=None, max_length=2000)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_input_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    session_id: str | None = Field(default=None, max_length=128)
    proxy_endpoint: str | None = Field(default=None, max_length=256)
    cost_source: str = "unknown"
    cost_details: dict[str, JsonValue] = Field(default_factory=dict)
    full_log_state: Literal["disabled", "stored", "truncated", "failed"] = "disabled"
    spend_sync_state: Literal["pending", "synced", "failed", "unavailable", "standard"] = "pending"


class AcquireRejected(BaseModel):
    model_config = ConfigDict(frozen=True)
    reason: AcquireRejectionReason
    retry_after_seconds: int = Field(default=0, ge=0, le=86400)
