"""定义渠道目录、解析数据和运行状态合并后的脱敏总览契约。"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from account_pool.catalog.models import AdministrativeState
from account_pool.models import ChannelPriority, FrozenModel, Health, ModelName, QuotaSnapshot
from account_pool.parsing.models import ParserRunStatus, SubscriptionStatus


class ParserOverviewState(StrEnum):
    LOADED = "loaded"
    NOT_RUN = "not_run"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class OverviewFailureCode(StrEnum):
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"


class AccountPoolOverviewFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: OverviewFailureCode
    retryable: bool


class SubscriptionOverview(FrozenModel):
    plan_name: str | None = None
    status: SubscriptionStatus
    expires_at: AwareDatetime | None = None
    balance: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    model_count: int = Field(ge=0)
    limit_count: int = Field(ge=0)


class MeteredOverview(FrozenModel):
    group_count: int = Field(ge=0)
    model_count: int = Field(ge=0)


class ParserOverview(FrozenModel):
    state: ParserOverviewState
    parser_id: str | None = None
    parser_version: str | None = None
    status: ParserRunStatus | None = None
    parsed_at: AwareDatetime | None = None
    subscription: SubscriptionOverview | None = None
    metered: MeteredOverview | None = None
    unresolved_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    active_override_count: int = Field(default=0, ge=0)
    failure_code: str | None = None


class RuntimeOverview(FrozenModel):
    health: Health
    reason_code: str | None = None
    inflight: int = Field(ge=0)
    max_concurrency: int = Field(ge=1)
    cooldown_until: float | None = None
    quota: QuotaSnapshot


class ChannelActivityOverview(FrozenModel):
    persistence_available: bool
    last_request_at: AwareDatetime | None = None
    last_success_at: AwareDatetime | None = None
    last_failure_at: AwareDatetime | None = None
    last_probe_at: AwareDatetime | None = None


class ChannelOverview(FrozenModel):
    channel_id: UUID
    account_id: str | None = None
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    group: str | None = None
    base_url_display: str = Field(min_length=1)
    key_mask: str | None = None
    administrative_state: AdministrativeState
    priority: ChannelPriority
    configured_models: tuple[ModelName, ...] = ()
    schedulable_models: tuple[ModelName, ...] = ()
    unavailable_reason_codes: tuple[str, ...] = ()
    binding_count: int = Field(ge=0)
    enabled_binding_count: int = Field(ge=0)
    runtime: RuntimeOverview | None = None
    parser: ParserOverview
    activity: ChannelActivityOverview


class AccountPoolOverview(FrozenModel):
    status: Literal["loaded"] = "loaded"
    channels: tuple[ChannelOverview, ...] = ()
    channel_count: int = Field(ge=0)
    administratively_enabled_count: int = Field(ge=0)
    healthy_count: int = Field(ge=0)
    schedulable_count: int = Field(ge=0)
    configured_model_count: int = Field(ge=0)
    schedulable_model_count: int = Field(ge=0)
    inflight: int = Field(ge=0)
    max_concurrency: int = Field(ge=0)


AccountPoolOverviewResult = AccountPoolOverview | AccountPoolOverviewFailure
