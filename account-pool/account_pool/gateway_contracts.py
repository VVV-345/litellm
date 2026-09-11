"""本模块定义网关鉴权、候选快照和并发租约的内部协议，不向管理页面暴露内部凭据。"""

from __future__ import annotations

from typing import Literal, TypeAlias
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from account_pool.domain import ChannelKind, GatewayCredential, SupplierKind
from account_pool.policies import AccountPolicy

RoutingReason: TypeAlias = Literal[
    "automatic",
    "single_account",
    "session_affinity",
    "session_rebind",
    "preferred_account",
    "priority",
    "quota",
    "random_weighted",
    "custom_order",
    "backup_account",
    "concurrency_fallback",
    "token_budget_fallback",
    "retry_failover",
]

AcquireRejectionReason: TypeAlias = Literal[
    "concurrency", "configuration", "cooldown", "session", "token_budget"
]


class ResolveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    card_key: str = Field(min_length=16, max_length=256, repr=False)
    session_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


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
    model_prefix: str = ""
    concurrency_limit: int
    policy: AccountPolicy
    remaining_percent: float | None = None
    quota_observed_at: AwareDatetime | None = None


class Resolution(BaseModel):
    model_config = ConfigDict(frozen=True)
    card_id: UUID
    key_id: UUID
    card_version: int
    policy_version: int
    policy: AccountPolicy
    candidates: tuple[Candidate, ...]
    sticky_account_id: UUID | None = None


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
    attempt: int = Field(ge=1, le=5)
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
    attempt: int = Field(default=1, ge=1, le=5)
    routing_reason: RoutingReason = "automatic"


class FinishRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    lease_id: UUID
    http_status: int = Field(ge=100, le=599)
    stage: Literal["connection", "upstream", "response"] = "upstream"
    message: str = Field(max_length=500)
    upstream_code: str | None = Field(default=None, max_length=120)
    retryable: bool = False
    switched_account: bool = False
    next_account_id: UUID | None = None
    endpoint: str = Field(max_length=256)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class AcquireRejected(BaseModel):
    model_config = ConfigDict(frozen=True)
    reason: AcquireRejectionReason
