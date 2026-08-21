"""定义 Redis 额度窗口的精确编解码、稳定键和请求计划。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from types import MappingProxyType
from typing import Final, Literal

from account_pool.models import AccountConfig, QuotaWindowConfig, RuntimeQuotaKind, RuntimeQuotaScope, SettleRequest
from account_pool.quota.runtime import RuntimeQuotaWindow, reconcile_quota_windows

MAXIMUM_DECIMAL_SCALE: Final = 36
REDIS_QUOTA_DECIMAL_SCALE: Final = MAXIMUM_DECIMAL_SCALE
MAXIMUM_UNIT_DIGITS: Final = 256
REDIS_QUOTA_SCHEMA_VERSION: Final = "1"


@dataclass(frozen=True, slots=True)
class RedisQuotaCodecFailure:
    code: Literal["non_finite", "scale_too_large", "value_too_large", "invalid_units", "invalid_state"]
    detail: str


@dataclass(frozen=True, slots=True)
class EncodedQuotaAmount:
    units: str
    scale: int


@dataclass(frozen=True, slots=True)
class RedisQuotaWindowRecord:
    schema_version: str
    snapshot_fingerprint: str
    window_id: str
    account_id: str
    scale: int
    limit_units: str | None
    remaining_units: str | None
    safety_reserve_units: str
    reserved_units: str
    retry_at: float | None
    config: QuotaWindowConfig


@dataclass(frozen=True, slots=True)
class RedisQuotaConfiguration:
    account_id: str
    records: tuple[RedisQuotaWindowRecord, ...]


@dataclass(frozen=True, slots=True)
class RedisQuotaReservationSpec:
    window_key: str
    usage_key: str
    amount_units: str


@dataclass(frozen=True, slots=True)
class RedisQuotaReservationPlan:
    reservations: tuple[RedisQuotaReservationSpec, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(
            key for reservation in self.reservations for key in (reservation.window_key, reservation.usage_key)
        )

    @property
    def arguments(self) -> tuple[str, ...]:
        return tuple(reservation.amount_units for reservation in self.reservations)


@dataclass(frozen=True, slots=True)
class RedisQuotaSettlementAmounts:
    request_units: str
    token_units: str
    currency_units: str


RedisQuotaAmountResult = EncodedQuotaAmount | RedisQuotaCodecFailure
RedisQuotaWindowRecordResult = RedisQuotaWindowRecord | RedisQuotaCodecFailure
RedisQuotaConfigurationResult = RedisQuotaConfiguration | RedisQuotaCodecFailure
RedisQuotaRuntimeResult = RuntimeQuotaWindow | RedisQuotaCodecFailure
RedisQuotaReservationPlanResult = RedisQuotaReservationPlan | RedisQuotaCodecFailure
RedisQuotaSettlementAmountsResult = RedisQuotaSettlementAmounts | RedisQuotaCodecFailure


def quota_window_key(account_id: str, window_id: str) -> str:
    identity: Final = sha256(window_id.encode("utf-8")).hexdigest()
    return f"pool:account:{account_id}:quota:{identity}"


def quota_usage_key(account_id: str, window_id: str) -> str:
    return f"{quota_window_key(account_id, window_id)}:usage"


def quota_manifest_key(account_id: str) -> str:
    return f"pool:account:{account_id}:quota:manifest"


def encode_quota_amount(value: Decimal, scale: int) -> RedisQuotaAmountResult:
    if not value.is_finite():
        return RedisQuotaCodecFailure(code="non_finite", detail="quota amount must be finite")
    if scale < 0 or scale > MAXIMUM_DECIMAL_SCALE:
        return RedisQuotaCodecFailure(code="scale_too_large", detail=f"quota scale {scale} is unsupported")
    decimal_tuple: Final = value.as_tuple()
    if decimal_tuple.sign:
        return RedisQuotaCodecFailure(code="invalid_units", detail="quota amount must not be negative")
    exponent: Final = decimal_tuple.exponent
    if not isinstance(exponent, int):
        return RedisQuotaCodecFailure(code="non_finite", detail="quota amount must be finite")
    digits: Final = "".join(str(digit) for digit in decimal_tuple.digits)
    fractional_digits: Final = max(0, -exponent)
    discarded_digits: Final = max(0, fractional_digits - scale)
    if discarded_digits > 0 and any(digit != "0" for digit in digits[-discarded_digits:]):
        return RedisQuotaCodecFailure(
            code="scale_too_large", detail=f"quota amount requires more than {scale} decimals"
        )
    retained_digits: Final = digits[: len(digits) - discarded_digits] if discarded_digits > 0 else digits
    appended_zeros: Final = max(0, exponent + scale)
    unit_length: Final = len(retained_digits) + appended_zeros
    if unit_length > MAXIMUM_UNIT_DIGITS:
        return RedisQuotaCodecFailure(code="value_too_large", detail="quota amount exceeds Redis codec limit")
    normalized: Final = f"{retained_digits}{'0' * appended_zeros}".lstrip("0") or "0"
    return EncodedQuotaAmount(units=normalized, scale=scale)


def decode_quota_amount(units: str, scale: int) -> Decimal | RedisQuotaCodecFailure:
    if not units or not units.isascii() or not units.isdecimal():
        return RedisQuotaCodecFailure(code="invalid_units", detail="quota units must be unsigned decimal digits")
    if scale < 0 or scale > MAXIMUM_DECIMAL_SCALE:
        return RedisQuotaCodecFailure(code="scale_too_large", detail=f"quota scale {scale} is unsupported")
    if len(units.lstrip("0") or "0") > MAXIMUM_UNIT_DIGITS:
        return RedisQuotaCodecFailure(code="value_too_large", detail="quota units exceed Redis codec limit")
    return Decimal(units).scaleb(-scale)


def encode_quota_window(account_id: str, window: RuntimeQuotaWindow) -> RedisQuotaWindowRecordResult:
    scale_result: Final = _window_scale(window)
    if isinstance(scale_result, RedisQuotaCodecFailure):
        return scale_result
    limit_result: Final = _encode_optional(window.config.limit, scale_result)
    if isinstance(limit_result, RedisQuotaCodecFailure):
        return limit_result
    remaining_result: Final = _encode_optional(window.remaining, scale_result)
    if isinstance(remaining_result, RedisQuotaCodecFailure):
        return remaining_result
    safety_result: Final = encode_quota_amount(window.config.safety_reserve, scale_result)
    if isinstance(safety_result, RedisQuotaCodecFailure):
        return safety_result
    reserved_result: Final = encode_quota_amount(window.reserved, scale_result)
    if isinstance(reserved_result, RedisQuotaCodecFailure):
        return reserved_result
    return RedisQuotaWindowRecord(
        schema_version=REDIS_QUOTA_SCHEMA_VERSION,
        snapshot_fingerprint=_snapshot_fingerprint(
            config=window.config,
            scale=scale_result,
            limit_units=None if limit_result is None else limit_result.units,
            remaining_units=None if remaining_result is None else remaining_result.units,
            safety_reserve_units=safety_result.units,
        ),
        window_id=window.config.window_id,
        account_id=account_id,
        scale=scale_result,
        limit_units=None if limit_result is None else limit_result.units,
        remaining_units=None if remaining_result is None else remaining_result.units,
        safety_reserve_units=safety_result.units,
        reserved_units=reserved_result.units,
        retry_at=window.retry_at,
        config=window.config,
    )


def encode_account_quota_windows(account: AccountConfig) -> RedisQuotaConfigurationResult:
    runtime_windows: Final = reconcile_quota_windows(previous=(), configured=account.quota_windows)
    encoded: Final = tuple(encode_quota_window(account.id, window) for window in runtime_windows)
    failure: Final = next((result for result in encoded if isinstance(result, RedisQuotaCodecFailure)), None)
    if failure is not None:
        return failure
    return RedisQuotaConfiguration(
        account_id=account.id,
        records=tuple(result for result in encoded if isinstance(result, RedisQuotaWindowRecord)),
    )


def prepare_quota_reservation_plan(
    account: AccountConfig,
    public_model: str,
    billing_route_id: str | None,
    estimated_tokens: int,
) -> RedisQuotaReservationPlanResult:
    configuration: Final = encode_account_quota_windows(account)
    if isinstance(configuration, RedisQuotaCodecFailure):
        return configuration
    matching: Final = tuple(
        record for record in configuration.records if _matches_request(record.config, public_model, billing_route_id)
    )
    encoded_amounts: Final = tuple(
        encode_quota_amount(_reservation_amount(record.config.kind, estimated_tokens), REDIS_QUOTA_DECIMAL_SCALE)
        for record in matching
    )
    failure: Final = next(
        (amount for amount in encoded_amounts if isinstance(amount, RedisQuotaCodecFailure)),
        None,
    )
    if failure is not None:
        return failure
    amounts: Final = tuple(amount for amount in encoded_amounts if isinstance(amount, EncodedQuotaAmount))
    return RedisQuotaReservationPlan(
        reservations=tuple(
            RedisQuotaReservationSpec(
                window_key=quota_window_key(account.id, record.window_id),
                usage_key=quota_usage_key(account.id, record.window_id),
                amount_units=amount.units,
            )
            for record, amount in zip(matching, amounts, strict=True)
        )
    )


def prepare_quota_settlement_amounts(request: SettleRequest) -> RedisQuotaSettlementAmountsResult:
    request_amount: Final = encode_quota_amount(Decimal("1"), REDIS_QUOTA_DECIMAL_SCALE)
    token_amount: Final = encode_quota_amount(
        Decimal(request.input_tokens + request.output_tokens),
        REDIS_QUOTA_DECIMAL_SCALE,
    )
    currency_amount: Final = (
        None
        if request.cost_usd is None
        else encode_quota_amount(Decimal(str(request.cost_usd)), REDIS_QUOTA_DECIMAL_SCALE)
    )
    failure: Final = next(
        (
            amount
            for amount in (request_amount, token_amount, currency_amount)
            if isinstance(amount, RedisQuotaCodecFailure)
        ),
        None,
    )
    if failure is not None:
        return failure
    if not isinstance(request_amount, EncodedQuotaAmount) or not isinstance(token_amount, EncodedQuotaAmount):
        return RedisQuotaCodecFailure(code="invalid_state", detail="quota settlement encoding failed")
    return RedisQuotaSettlementAmounts(
        request_units=request_amount.units,
        token_units=token_amount.units,
        currency_units=currency_amount.units if isinstance(currency_amount, EncodedQuotaAmount) else "",
    )


def quota_window_hash_fields(record: RedisQuotaWindowRecord) -> Mapping[str, str]:
    config: Final = record.config
    return MappingProxyType(
        {
            "snapshot_fingerprint": record.snapshot_fingerprint,
            "schema_version": record.schema_version,
            "window_id": record.window_id,
            "account_id": record.account_id,
            "scale": str(record.scale),
            "scope": config.scope,
            "subject_id": config.subject_id or "",
            "kind": config.kind,
            "window_type": "" if config.window_type is None else config.window_type,
            "duration_seconds": "" if config.duration_seconds is None else str(config.duration_seconds),
            "limit_units": record.limit_units or "",
            "snapshot_remaining_units": record.remaining_units or "",
            "remaining_units": record.remaining_units or "",
            "safety_reserve_units": record.safety_reserve_units,
            "reserved_units": record.reserved_units,
            "retry_at": "" if record.retry_at is None else str(record.retry_at),
            "reset_at": "" if config.reset_at is None else str(config.reset_at),
            "observed_at": str(config.observed_at),
            "source": config.source,
            "reason_code": config.reason_code,
        }
    )


def configure_quota_script_args(record: RedisQuotaWindowRecord) -> tuple[str, ...]:
    fields: Final = quota_window_hash_fields(record)
    return (
        fields["snapshot_fingerprint"],
        fields["schema_version"],
        fields["window_id"],
        fields["account_id"],
        fields["scale"],
        fields["scope"],
        fields["subject_id"],
        fields["kind"],
        fields["window_type"],
        fields["duration_seconds"],
        fields["limit_units"],
        fields["snapshot_remaining_units"],
        fields["safety_reserve_units"],
        fields["retry_at"],
        fields["reset_at"],
        fields["observed_at"],
        fields["source"],
        fields["reason_code"],
    )


def decode_quota_window(fields: Mapping[str, str]) -> RedisQuotaRuntimeResult:
    try:
        scale: Final = int(fields["scale"])
        if scale != REDIS_QUOTA_DECIMAL_SCALE:
            return RedisQuotaCodecFailure(code="invalid_state", detail=f"unexpected Redis quota scale {scale}")
        limit: Final = _decode_optional(fields["limit_units"], scale)
        snapshot_remaining: Final = _decode_optional(fields["snapshot_remaining_units"], scale)
        remaining: Final = _decode_optional(fields["remaining_units"], scale)
        safety_reserve: Final = decode_quota_amount(fields["safety_reserve_units"], scale)
        reserved: Final = decode_quota_amount(fields["reserved_units"], scale)
        if isinstance(limit, RedisQuotaCodecFailure):
            return limit
        if isinstance(snapshot_remaining, RedisQuotaCodecFailure):
            return snapshot_remaining
        if isinstance(remaining, RedisQuotaCodecFailure):
            return remaining
        if isinstance(safety_reserve, RedisQuotaCodecFailure):
            return safety_reserve
        if isinstance(reserved, RedisQuotaCodecFailure):
            return reserved
        config: Final = QuotaWindowConfig.model_validate(
            {
                "window_id": fields["window_id"],
                "scope": fields["scope"],
                "subject_id": fields["subject_id"] or None,
                "kind": fields["kind"],
                "window_type": fields["window_type"] or None,
                "duration_seconds": None if not fields["duration_seconds"] else int(fields["duration_seconds"]),
                "limit": limit,
                "remaining": snapshot_remaining,
                "safety_reserve": safety_reserve,
                "reset_at": None if not fields["reset_at"] else float(fields["reset_at"]),
                "observed_at": float(fields["observed_at"]),
                "source": fields["source"],
                "reason_code": fields["reason_code"],
            }
        )
        return RuntimeQuotaWindow(
            config=config,
            remaining=remaining,
            retry_at=None if not fields["retry_at"] else float(fields["retry_at"]),
            reserved=reserved,
        )
    except (KeyError, ValueError) as error:
        return RedisQuotaCodecFailure(code="invalid_state", detail=f"invalid Redis quota state: {error}")


def _window_scale(window: RuntimeQuotaWindow) -> int | RedisQuotaCodecFailure:
    values: Final = tuple(
        value
        for value in (window.config.limit, window.remaining, window.config.safety_reserve, window.reserved)
        if value is not None
    )
    if any(not value.is_finite() for value in values):
        return RedisQuotaCodecFailure(code="non_finite", detail="quota window contains a non-finite amount")
    required_scale: Final = max((_decimal_scale(value) for value in values), default=0)
    if required_scale > REDIS_QUOTA_DECIMAL_SCALE:
        return RedisQuotaCodecFailure(code="scale_too_large", detail=f"quota window requires scale {required_scale}")
    return REDIS_QUOTA_DECIMAL_SCALE


def _decimal_scale(value: Decimal) -> int:
    decimal_tuple: Final = value.as_tuple()
    exponent: Final = decimal_tuple.exponent
    if not isinstance(exponent, int):
        return MAXIMUM_DECIMAL_SCALE + 1
    if not any(decimal_tuple.digits):
        return 0
    trailing_zeros: Final = next(
        (index for index, digit in enumerate(reversed(decimal_tuple.digits)) if digit != 0),
        len(decimal_tuple.digits),
    )
    return max(0, -exponent - trailing_zeros)


def _encode_optional(value: Decimal | None, scale: int) -> EncodedQuotaAmount | RedisQuotaCodecFailure | None:
    return None if value is None else encode_quota_amount(value, scale)


def _decode_optional(value: str, scale: int) -> Decimal | RedisQuotaCodecFailure | None:
    return None if not value else decode_quota_amount(value, scale)


def _matches_request(config: QuotaWindowConfig, public_model: str, billing_route_id: str | None) -> bool:
    if config.scope == RuntimeQuotaScope.CHANNEL:
        return True
    if config.scope == RuntimeQuotaScope.MODEL:
        return config.subject_id == public_model
    return billing_route_id is not None and config.subject_id == billing_route_id


def _reservation_amount(kind: RuntimeQuotaKind, estimated_tokens: int) -> Decimal:
    if kind == RuntimeQuotaKind.REQUESTS:
        return Decimal("1")
    if kind == RuntimeQuotaKind.TOKENS:
        return Decimal(estimated_tokens)
    return Decimal("0")


def _snapshot_fingerprint(
    config: QuotaWindowConfig,
    scale: int,
    limit_units: str | None,
    remaining_units: str | None,
    safety_reserve_units: str,
) -> str:
    serialized: Final = json.dumps(
        (
            REDIS_QUOTA_SCHEMA_VERSION,
            config.window_id,
            config.scope,
            config.subject_id,
            config.kind,
            config.window_type,
            config.duration_seconds,
            scale,
            limit_units,
            remaining_units,
            safety_reserve_units,
            config.reset_at,
            config.observed_at,
            config.source,
            config.reason_code,
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()
