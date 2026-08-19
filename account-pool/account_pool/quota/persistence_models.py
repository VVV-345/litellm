"""定义额度 usage 事件、运行快照和恢复代次的持久化领域模型。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from account_pool.models import (
    AccountConfig,
    AccountId,
    FrozenModel,
    Lease,
    QuotaWindowConfig,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
    SettleRequest,
)
from account_pool.quota.runtime import RuntimeQuotaWindow, matching_quota_windows

_USAGE_EVENT_NAMESPACE: Final = UUID("52d47880-2467-4b18-9cbf-b33271b96390")
_FINGERPRINT_PATTERN: Final = r"^[0-9a-f]{64}$"
_SAFE_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"


class QuotaGenerationStatus(StrEnum):
    INITIALIZING = "initializing"
    ACTIVE = "active"
    RETIRED = "retired"
    FAILED = "failed"


class QuotaRuntimeGeneration(FrozenModel):
    generation_id: UUID
    predecessor_generation_id: UUID | None = None
    status: QuotaGenerationStatus
    created_at: AwareDatetime
    activated_at: AwareDatetime | None = None
    closed_at: AwareDatetime | None = None
    failure_code: str | None = Field(default=None, pattern=_SAFE_CODE_PATTERN)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if (self.status == QuotaGenerationStatus.ACTIVE) != (self.activated_at is not None and self.closed_at is None):
            raise ValueError("only active quota generations require activated_at without closed_at")
        if self.status in (QuotaGenerationStatus.RETIRED, QuotaGenerationStatus.FAILED) and self.closed_at is None:
            raise ValueError("closed quota generations require closed_at")
        if (self.status == QuotaGenerationStatus.FAILED) != (self.failure_code is not None):
            raise ValueError("only failed quota generations require failure_code")
        return self


class QuotaUsageEvent(FrozenModel):
    event_id: UUID
    generation_id: UUID
    channel_id: UUID | None = None
    account_id: AccountId
    window_id: str = Field(min_length=1, max_length=200)
    lease_id: str = Field(min_length=1, max_length=255)
    request_id: str = Field(min_length=1, max_length=255)
    kind: RuntimeQuotaKind
    amount: Decimal = Field(gt=0)
    occurred_at: AwareDatetime
    source: Literal["settlement"] = "settlement"


class QuotaWindowRuntimeSnapshot(FrozenModel):
    generation_id: UUID
    channel_id: UUID | None = None
    account_id: AccountId
    window_id: str = Field(min_length=1, max_length=200)
    scope: RuntimeQuotaScope
    subject_id: str | None = Field(default=None, min_length=1)
    kind: RuntimeQuotaKind
    window_type: RuntimeQuotaWindowType | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    limit_value: Decimal | None = Field(default=None, ge=0)
    remaining_value: Decimal | None = Field(default=None, ge=0)
    reserved_value: Decimal = Field(default=Decimal("0"), ge=0)
    safety_reserve_value: Decimal = Field(default=Decimal("0"), ge=0)
    retry_at: AwareDatetime | None = None
    provider_reset_at: AwareDatetime | None = None
    provider_observed_at: AwareDatetime
    provider_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    source: str = Field(min_length=1, max_length=255)
    reason_code: str = Field(min_length=1, max_length=100)
    captured_at: AwareDatetime
    reservation_expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_scope_and_window(self) -> Self:
        if (self.scope == RuntimeQuotaScope.CHANNEL) != (self.subject_id is None):
            raise ValueError("only channel quota snapshots omit subject_id")
        if self.window_type == RuntimeQuotaWindowType.ROLLING and self.duration_seconds is None:
            raise ValueError("rolling quota snapshots require duration_seconds")
        if self.window_type == RuntimeQuotaWindowType.RESET_AT and self.provider_reset_at is None:
            raise ValueError("reset_at quota snapshots require provider_reset_at")
        if self.reserved_value > 0 and self.reservation_expires_at is None:
            raise ValueError("reserved quota snapshots require reservation_expires_at")
        return self


class QuotaRecoveryState(FrozenModel):
    generation: QuotaRuntimeGeneration
    windows: tuple[QuotaWindowRuntimeSnapshot, ...]
    usage_events: tuple[QuotaUsageEvent, ...]

    @model_validator(mode="after")
    def validate_generation_membership(self) -> Self:
        generation_id: Final = self.generation.generation_id
        if any(item.generation_id != generation_id for item in (*self.windows, *self.usage_events)):
            raise ValueError("quota recovery records must belong to one generation")
        return self


def quota_usage_event_id(generation_id: UUID, lease_id: str, window_id: str) -> UUID:
    # 同一代次内重复回调会生成同一 ID，数据库可据此阻止 usage 被重复累计。
    return uuid5(_USAGE_EVENT_NAMESPACE, f"{generation_id}:{lease_id}:{window_id}")


def build_quota_usage_events(
    *,
    generation_id: UUID,
    account: AccountConfig,
    lease: Lease,
    request: SettleRequest,
    windows: tuple[RuntimeQuotaWindow, ...],
    occurred_at: AwareDatetime,
) -> tuple[QuotaUsageEvent, ...]:
    matching: Final = matching_quota_windows(windows, lease.public_model, lease.billing_route_id)
    return tuple(
        QuotaUsageEvent(
            event_id=quota_usage_event_id(generation_id, lease.lease_id, window.config.window_id),
            generation_id=generation_id,
            channel_id=account.channel_id,
            account_id=account.id,
            window_id=window.config.window_id,
            lease_id=lease.lease_id,
            request_id=lease.request_id,
            kind=window.config.kind,
            amount=amount,
            occurred_at=occurred_at,
        )
        for window in matching
        for amount in (_settlement_amount(window.config.kind, request),)
        if amount is not None and amount > 0
    )


def build_quota_window_snapshot(
    *,
    generation_id: UUID,
    account: AccountConfig,
    window: RuntimeQuotaWindow,
    captured_at: AwareDatetime,
    reservation_expires_at: AwareDatetime | None = None,
) -> QuotaWindowRuntimeSnapshot:
    config: Final = window.config
    return QuotaWindowRuntimeSnapshot(
        generation_id=generation_id,
        channel_id=account.channel_id,
        account_id=account.id,
        window_id=config.window_id,
        scope=config.scope,
        subject_id=config.subject_id,
        kind=config.kind,
        window_type=config.window_type,
        duration_seconds=config.duration_seconds,
        limit_value=config.limit,
        remaining_value=window.remaining,
        reserved_value=window.reserved,
        safety_reserve_value=config.safety_reserve,
        retry_at=_datetime_from_timestamp(window.retry_at),
        provider_reset_at=_datetime_from_timestamp(config.reset_at),
        provider_observed_at=datetime.fromtimestamp(config.observed_at, tz=UTC),
        provider_fingerprint=quota_provider_fingerprint(config),
        source=config.source,
        reason_code=config.reason_code,
        captured_at=captured_at,
        reservation_expires_at=reservation_expires_at,
    )


def quota_provider_fingerprint(config: QuotaWindowConfig) -> str:
    payload: Final = json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _settlement_amount(kind: RuntimeQuotaKind, request: SettleRequest) -> Decimal | None:
    if kind == RuntimeQuotaKind.REQUESTS:
        return Decimal("1")
    if kind == RuntimeQuotaKind.TOKENS:
        return Decimal(request.input_tokens + request.output_tokens)
    if kind == RuntimeQuotaKind.CURRENCY and request.cost_usd is not None:
        return Decimal(str(request.cost_usd))
    return None


def _datetime_from_timestamp(value: float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(value, tz=UTC)
