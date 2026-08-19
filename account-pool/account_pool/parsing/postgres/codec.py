"""校验 PostgreSQL 行并重建强类型解析运行和导出状态。"""

from decimal import Decimal
from typing import Final
from uuid import UUID

from pydantic import AwareDatetime, TypeAdapter

from account_pool.domain.provider_source import ProviderCapability
from account_pool.models import FrozenModel
from account_pool.parsing.models import (
    BillingMode,
    BillingRoute,
    ConcurrencyLimit,
    EffectivePrices,
    MeteredData,
    MeteredGroup,
    MeteredModelPrice,
    ModelIdentity,
    ParsedChannelData,
    ParserIssue,
    ParserRun,
    ParserRunStatus,
    PriceCalculation,
    QuotaKind,
    QuotaLimit,
    QuotaScope,
    QuotaWindowType,
    SafeEvidence,
    SubscriptionData,
    SubscriptionStatus,
    UnresolvedField,
)
from account_pool.parsing.persistence import (
    ParserExportState,
    ParserExportStatus,
    PersistedParserRun,
)
from account_pool.parsing.snapshots import SnapshotExportFailureCode

_MODEL_IDENTITIES: Final = TypeAdapter(tuple[ModelIdentity, ...])
_CONCURRENCY_LIMITS: Final = TypeAdapter(tuple[ConcurrencyLimit, ...])
_CAPABILITIES: Final = TypeAdapter(tuple[ProviderCapability, ...])
_UNRESOLVED_FIELDS: Final = TypeAdapter(tuple[UnresolvedField, ...])
_EVIDENCE: Final = TypeAdapter(tuple[SafeEvidence, ...])
_STRINGS: Final = TypeAdapter(tuple[str, ...])
_ISSUES: Final = TypeAdapter(tuple[ParserIssue, ...])


class _ParserRunRow(FrozenModel):
    parser_run_id: UUID
    channel_id: UUID
    parser_id: str
    parser_version: str
    parsed_at: AwareDatetime
    status: ParserRunStatus
    content_hash: str
    discovered_models: object
    capabilities: object
    unresolved_fields: object
    evidence: object
    warnings: object
    issues: object
    has_metered: bool
    export_status: ParserExportStatus
    export_attempt_count: int
    export_last_attempt_at: AwareDatetime | None
    exported_at: AwareDatetime | None
    export_failure_code: SnapshotExportFailureCode | None
    export_failure_retryable: bool | None


class _SubscriptionRow(FrozenModel):
    subscription_snapshot_id: UUID
    parser_run_id: UUID
    plan_id: str | None
    plan_name: str | None
    status: SubscriptionStatus
    starts_at: AwareDatetime | None
    expires_at: AwareDatetime | None
    models: object
    balance: Decimal | None
    currency: str | None
    channel_concurrency: int | None
    model_concurrency: object


class _QuotaRow(FrozenModel):
    quota_limit_id: UUID
    parser_run_id: UUID
    limit_order: int
    scope: QuotaScope
    subject_id: str | None
    kind: QuotaKind
    window_type: QuotaWindowType | None
    duration_seconds: int | None
    limit_value: Decimal | None
    used_value: Decimal | None
    remaining_value: Decimal | None
    reset_at: AwareDatetime | None
    source: str
    observed_at: AwareDatetime


class _GroupRow(FrozenModel):
    metered_group_row_id: UUID
    parser_run_id: UUID
    group_order: int
    group_id: str | None
    group_name: str | None
    concurrency: int | None


class _PriceRow(FrozenModel):
    metered_price_id: UUID
    metered_group_row_id: UUID
    price_order: int
    provider_model_id: str
    litellm_model_name: str | None
    public_model_name: str | None
    currency: str
    unit: str
    input_price: Decimal | None
    output_price: Decimal | None
    cache_read_price: Decimal | None
    cache_write_price: Decimal | None
    group_multiplier: Decimal
    price_calculation: PriceCalculation
    conversion_note: str | None
    effective_input_price: Decimal | None
    effective_output_price: Decimal | None
    effective_cache_read_price: Decimal | None
    effective_cache_write_price: Decimal | None
    normalized_input_price: Decimal | None
    normalized_output_price: Decimal | None
    normalized_cache_read_price: Decimal | None
    normalized_cache_write_price: Decimal | None
    has_normalized_prices: bool
    concurrency: int | None


class _RouteRow(FrozenModel):
    billing_route_row_id: UUID
    parser_run_id: UUID
    route_order: int
    route_id: UUID
    deployment_binding_id: UUID
    mode: BillingMode
    provider_group_id: str | None
    request_parameter_ref: str | None


class _ExportRow(FrozenModel):
    export_status: ParserExportStatus
    export_attempt_count: int
    export_last_attempt_at: AwareDatetime | None
    exported_at: AwareDatetime | None
    export_failure_code: SnapshotExportFailureCode | None
    export_failure_retryable: bool | None


def decode_record(
    raw_run: object,
    subscription_rows: tuple[object, ...],
    quota_rows: tuple[object, ...],
    group_rows: tuple[object, ...],
    price_rows: tuple[object, ...],
    route_rows: tuple[object, ...],
) -> PersistedParserRun:
    run_row: Final = _ParserRunRow.model_validate(raw_run)
    subscriptions: Final = tuple(_SubscriptionRow.model_validate(row) for row in subscription_rows)
    if len(subscriptions) > 1:
        raise ValueError("parser run has multiple subscription snapshots")
    limits: Final = tuple(_QuotaRow.model_validate(row) for row in quota_rows)
    groups: Final = tuple(_GroupRow.model_validate(row) for row in group_rows)
    prices: Final = tuple(_PriceRow.model_validate(row) for row in price_rows)
    routes: Final = tuple(_RouteRow.model_validate(row) for row in route_rows)
    if not subscriptions and limits:
        raise ValueError("parser run has quota limits without a subscription snapshot")
    if not run_row.has_metered and (groups or prices):
        raise ValueError("parser run has metered child rows without metered data")
    group_ids: Final = frozenset(group.metered_group_row_id for group in groups)
    if any(price.metered_group_row_id not in group_ids for price in prices):
        raise ValueError("parser run has a price without a metered group")
    subscription: Final = None if not subscriptions else _decode_subscription(subscriptions[0], limits)
    metered: Final = _decode_metered(groups, prices) if run_row.has_metered else None
    result: Final = ParsedChannelData(
        subscription=subscription,
        metered=metered,
        billing_routes=tuple(_decode_route(route) for route in routes),
        capabilities=_CAPABILITIES.validate_python(run_row.capabilities),
        unresolved_fields=_UNRESOLVED_FIELDS.validate_python(run_row.unresolved_fields),
        evidence=_EVIDENCE.validate_python(run_row.evidence),
        warnings=_STRINGS.validate_python(run_row.warnings),
    )
    run: Final = ParserRun(
        parser_run_id=run_row.parser_run_id,
        channel_id=run_row.channel_id,
        parser_id=run_row.parser_id,
        parser_version=run_row.parser_version,
        parsed_at=run_row.parsed_at,
        status=run_row.status,
        result=result,
        discovered_models=_STRINGS.validate_python(run_row.discovered_models),
        issues=_ISSUES.validate_python(run_row.issues),
    )
    return PersistedParserRun(
        run=run,
        content_hash=run_row.content_hash,
        export=_export_state(run_row),
    )


def decode_export_state(value: object) -> ParserExportState:
    return _export_state(_ExportRow.model_validate(value))


def _decode_subscription(row: _SubscriptionRow, limits: tuple[_QuotaRow, ...]) -> SubscriptionData:
    return SubscriptionData(
        plan_id=row.plan_id,
        plan_name=row.plan_name,
        status=row.status,
        starts_at=row.starts_at,
        expires_at=row.expires_at,
        models=_MODEL_IDENTITIES.validate_python(row.models),
        balance=row.balance,
        currency=row.currency,
        channel_concurrency=row.channel_concurrency,
        model_concurrency=_CONCURRENCY_LIMITS.validate_python(row.model_concurrency),
        limits=tuple(
            QuotaLimit(
                scope=limit.scope,
                subject_id=limit.subject_id,
                kind=limit.kind,
                window_type=limit.window_type,
                duration_seconds=limit.duration_seconds,
                limit=limit.limit_value,
                used=limit.used_value,
                remaining=limit.remaining_value,
                reset_at=limit.reset_at,
                source=limit.source,
                observed_at=limit.observed_at,
            )
            for limit in limits
        ),
    )


def _decode_metered(groups: tuple[_GroupRow, ...], prices: tuple[_PriceRow, ...]) -> MeteredData:
    return MeteredData(
        groups=tuple(
            MeteredGroup(
                group_id=group.group_id,
                group_name=group.group_name,
                concurrency=group.concurrency,
                models=tuple(
                    _decode_price(price)
                    for price in prices
                    if price.metered_group_row_id == group.metered_group_row_id
                ),
            )
            for group in groups
        )
    )


def _decode_price(row: _PriceRow) -> MeteredModelPrice:
    normalized: Final = (
        None
        if not row.has_normalized_prices
        else EffectivePrices(
            input_price=row.normalized_input_price,
            output_price=row.normalized_output_price,
            cache_read_price=row.normalized_cache_read_price,
            cache_write_price=row.normalized_cache_write_price,
        )
    )
    return MeteredModelPrice(
        provider_model_id=row.provider_model_id,
        litellm_model_name=row.litellm_model_name,
        public_model_name=row.public_model_name,
        currency=row.currency,
        unit=row.unit,
        input_price=row.input_price,
        output_price=row.output_price,
        cache_read_price=row.cache_read_price,
        cache_write_price=row.cache_write_price,
        group_multiplier=row.group_multiplier,
        price_calculation=row.price_calculation,
        conversion_note=row.conversion_note,
        effective_prices=EffectivePrices(
            input_price=row.effective_input_price,
            output_price=row.effective_output_price,
            cache_read_price=row.effective_cache_read_price,
            cache_write_price=row.effective_cache_write_price,
        ),
        normalized_per_million_tokens=normalized,
        concurrency=row.concurrency,
    )


def _decode_route(row: _RouteRow) -> BillingRoute:
    return BillingRoute(
        route_id=row.route_id,
        deployment_binding_id=row.deployment_binding_id,
        mode=row.mode,
        provider_group_id=row.provider_group_id,
        request_parameter_ref=row.request_parameter_ref,
    )


def _export_state(row: _ParserRunRow | _ExportRow) -> ParserExportState:
    return ParserExportState(
        status=row.export_status,
        attempt_count=row.export_attempt_count,
        last_attempt_at=row.export_last_attempt_at,
        exported_at=row.exported_at,
        failure_code=row.export_failure_code,
        failure_retryable=row.export_failure_retryable,
    )
