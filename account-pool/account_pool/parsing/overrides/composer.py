"""按稳定目标确定性合成原始解析结果与当前有效人工覆盖。"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final, Literal, assert_never
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter, ValidationError

from account_pool.domain.provider_source import ProviderCapability
from account_pool.models import FrozenModel
from account_pool.parsing.models import (
    BillingRoute,
    ConcurrencyLimit,
    MeteredData,
    MeteredGroup,
    MeteredModelPrice,
    ModelIdentity,
    ParsedChannelData,
    ParserRun,
    QuotaLimit,
    SafeEvidence,
    SubscriptionData,
    SubscriptionStatus,
    UnresolvedField,
)
from account_pool.parsing.overrides.models import (
    BillingRouteTarget,
    FieldOverrideEvent,
    MeteredGroupTarget,
    MeteredPriceTarget,
    OverrideAction,
    RootField,
    RootFieldTarget,
    SubscriptionField,
    SubscriptionFieldTarget,
    SubscriptionModelTarget,
)

_SUBSCRIPTION: Final[TypeAdapter[SubscriptionData | None]] = TypeAdapter(SubscriptionData | None)
_METERED: Final[TypeAdapter[MeteredData | None]] = TypeAdapter(MeteredData | None)
_BILLING_ROUTES: Final[TypeAdapter[tuple[BillingRoute, ...]]] = TypeAdapter(tuple[BillingRoute, ...])
_CAPABILITIES: Final[TypeAdapter[tuple[ProviderCapability, ...]]] = TypeAdapter(
    tuple[ProviderCapability, ...]
)
_UNRESOLVED_FIELDS: Final[TypeAdapter[tuple[UnresolvedField, ...]]] = TypeAdapter(
    tuple[UnresolvedField, ...]
)
_EVIDENCE: Final[TypeAdapter[tuple[SafeEvidence, ...]]] = TypeAdapter(tuple[SafeEvidence, ...])
_WARNINGS: Final[TypeAdapter[tuple[str, ...]]] = TypeAdapter(tuple[str, ...])
_OPTIONAL_STRING: Final[TypeAdapter[str | None]] = TypeAdapter(str | None)
_SUBSCRIPTION_STATUS: Final[TypeAdapter[SubscriptionStatus]] = TypeAdapter(SubscriptionStatus)
_OPTIONAL_DATETIME: Final[TypeAdapter[AwareDatetime | None]] = TypeAdapter(AwareDatetime | None)
_MODEL_IDENTITIES: Final[TypeAdapter[tuple[ModelIdentity, ...]]] = TypeAdapter(tuple[ModelIdentity, ...])
_OPTIONAL_NONNEGATIVE_DECIMAL: Final[TypeAdapter[Decimal | None]] = TypeAdapter(
    Annotated[Decimal | None, Field(ge=0)]
)
_OPTIONAL_POSITIVE_INT: Final[TypeAdapter[int | None]] = TypeAdapter(
    Annotated[int | None, Field(ge=1)]
)
_CONCURRENCY_LIMITS: Final[TypeAdapter[tuple[ConcurrencyLimit, ...]]] = TypeAdapter(
    tuple[ConcurrencyLimit, ...]
)
_QUOTA_LIMITS: Final[TypeAdapter[tuple[QuotaLimit, ...]]] = TypeAdapter(tuple[QuotaLimit, ...])
_MODEL_IDENTITY: Final[TypeAdapter[ModelIdentity]] = TypeAdapter(ModelIdentity)
_METERED_GROUP: Final[TypeAdapter[MeteredGroup]] = TypeAdapter(MeteredGroup)
_METERED_PRICE: Final[TypeAdapter[MeteredModelPrice]] = TypeAdapter(MeteredModelPrice)
_BILLING_ROUTE: Final[TypeAdapter[BillingRoute]] = TypeAdapter(BillingRoute)


class OverrideApplyFailureCode(StrEnum):
    CHANNEL_MISMATCH = "channel_mismatch"
    TARGET_MISSING = "target_missing"
    TARGET_AMBIGUOUS = "target_ambiguous"
    IDENTITY_CHANGE = "identity_change"
    INVALID_VALUE = "invalid_value"


class OverrideApplyFailure(FrozenModel):
    override_id: UUID
    field_path: str = Field(min_length=1)
    code: OverrideApplyFailureCode


class OverrideComposition(FrozenModel):
    raw_result: ParsedChannelData
    effective_result: ParsedChannelData
    applied_override_ids: tuple[UUID, ...] = ()
    failures: tuple[OverrideApplyFailure, ...] = ()


class _ApplySuccess(FrozenModel):
    status: Literal["applied"] = "applied"
    result: ParsedChannelData


def active_override_events(events: tuple[FieldOverrideEvent, ...]) -> tuple[FieldOverrideEvent, ...]:
    # 事件只追加不更新，因此每条字段链中未被后继引用的事件才是当前状态。
    superseded_ids: Final = frozenset(
        event.supersedes_override_id for event in events if event.supersedes_override_id is not None
    )
    chain_heads: Final = tuple(event for event in events if event.override_id not in superseded_ids)
    newest_first: Final = tuple(
        sorted(chain_heads, key=lambda event: (event.occurred_at, str(event.override_id)), reverse=True)
    )
    latest_per_path: Final = tuple(
        event
        for index, event in enumerate(newest_first)
        if all(
            previous.channel_id != event.channel_id or previous.field_path() != event.field_path()
            for previous in newest_first[:index]
        )
    )
    return tuple(
        sorted(
            (event for event in latest_per_path if event.action == OverrideAction.SET),
            key=lambda event: (event.occurred_at, str(event.override_id)),
        )
    )


def compose_effective_result(
    run: ParserRun,
    events: tuple[FieldOverrideEvent, ...],
) -> OverrideComposition:
    active: Final = active_override_events(events)
    return _compose(run=run, current=run.result, remaining=active)


def _compose(
    run: ParserRun,
    current: ParsedChannelData,
    remaining: tuple[FieldOverrideEvent, ...],
) -> OverrideComposition:
    if not remaining:
        return OverrideComposition(raw_result=run.result, effective_result=current)
    event: Final = remaining[0]
    if event.channel_id != run.channel_id:
        mismatch_tail: Final = _compose(run=run, current=current, remaining=remaining[1:])
        return mismatch_tail.model_copy(
            update={
                "failures": (
                    OverrideApplyFailure(
                        override_id=event.override_id,
                        field_path=event.field_path(),
                        code=OverrideApplyFailureCode.CHANNEL_MISMATCH,
                    ),
                    *mismatch_tail.failures,
                )
            }
        )
    applied: Final = _apply(current=current, event=event)
    if isinstance(applied, OverrideApplyFailure):
        resolved_failure: Final = applied.model_copy(update={"override_id": event.override_id})
        failure_tail: Final = _compose(run=run, current=current, remaining=remaining[1:])
        return failure_tail.model_copy(update={"failures": (resolved_failure, *failure_tail.failures)})
    success_tail: Final = _compose(run=run, current=applied.result, remaining=remaining[1:])
    return success_tail.model_copy(
        update={"applied_override_ids": (event.override_id, *success_tail.applied_override_ids)}
    )


def _apply(
    current: ParsedChannelData,
    event: FieldOverrideEvent,
) -> _ApplySuccess | OverrideApplyFailure:
    try:
        target: Final = event.target
        if isinstance(target, RootFieldTarget):
            return _apply_root(current=current, target=target, value=event.value)
        if isinstance(target, SubscriptionFieldTarget):
            return _apply_subscription_field(current=current, target=target, value=event.value)
        if isinstance(target, SubscriptionModelTarget):
            return _apply_subscription_model(current=current, target=target, value=event.value)
        if isinstance(target, MeteredGroupTarget):
            return _apply_metered_group(current=current, target=target, value=event.value)
        if isinstance(target, MeteredPriceTarget):
            return _apply_metered_price(current=current, target=target, value=event.value)
        return _apply_billing_route(current=current, target=target, value=event.value)
    except ValidationError:
        return _failure(event=event, code=OverrideApplyFailureCode.INVALID_VALUE)


def _apply_root(current: ParsedChannelData, target: RootFieldTarget, value: object) -> _ApplySuccess:
    match target.field:
        case RootField.SUBSCRIPTION:
            return _ApplySuccess(
                result=current.model_copy(update={"subscription": _SUBSCRIPTION.validate_python(value)})
            )
        case RootField.METERED:
            return _ApplySuccess(result=current.model_copy(update={"metered": _METERED.validate_python(value)}))
        case RootField.BILLING_ROUTES:
            return _ApplySuccess(
                result=current.model_copy(update={"billing_routes": _BILLING_ROUTES.validate_python(value)})
            )
        case RootField.CAPABILITIES:
            return _ApplySuccess(
                result=current.model_copy(update={"capabilities": _CAPABILITIES.validate_python(value)})
            )
        case RootField.UNRESOLVED_FIELDS:
            return _ApplySuccess(
                result=current.model_copy(
                    update={"unresolved_fields": _UNRESOLVED_FIELDS.validate_python(value)}
                )
            )
        case RootField.EVIDENCE:
            return _ApplySuccess(
                result=current.model_copy(update={"evidence": _EVIDENCE.validate_python(value)})
            )
        case RootField.WARNINGS:
            return _ApplySuccess(
                result=current.model_copy(update={"warnings": _WARNINGS.validate_python(value)})
            )
    assert_never(target.field)


def _apply_subscription_field(
    current: ParsedChannelData,
    target: SubscriptionFieldTarget,
    value: object,
) -> _ApplySuccess | OverrideApplyFailure:
    subscription: Final = current.subscription
    if subscription is None:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_MISSING)
    match target.field:
        case SubscriptionField.PLAN_ID:
            updated: Final = subscription.model_copy(
                update={"plan_id": _OPTIONAL_STRING.validate_python(value)}
            )
            return _ApplySuccess(result=current.model_copy(update={"subscription": updated}))
        case SubscriptionField.PLAN_NAME:
            updated_plan_name: Final = subscription.model_copy(
                update={"plan_name": _OPTIONAL_STRING.validate_python(value)}
            )
            return _ApplySuccess(result=current.model_copy(update={"subscription": updated_plan_name}))
        case SubscriptionField.STATUS:
            updated_status: Final = subscription.model_copy(
                update={"status": _SUBSCRIPTION_STATUS.validate_python(value)}
            )
            return _ApplySuccess(result=current.model_copy(update={"subscription": updated_status}))
        case SubscriptionField.STARTS_AT:
            updated_starts_at: Final = subscription.model_copy(
                update={"starts_at": _OPTIONAL_DATETIME.validate_python(value)}
            )
            return _ApplySuccess(result=current.model_copy(update={"subscription": updated_starts_at}))
        case SubscriptionField.EXPIRES_AT:
            updated_expires_at: Final = subscription.model_copy(
                update={"expires_at": _OPTIONAL_DATETIME.validate_python(value)}
            )
            return _ApplySuccess(result=current.model_copy(update={"subscription": updated_expires_at}))
        case SubscriptionField.MODELS:
            updated_models: Final = subscription.model_copy(
                update={"models": _MODEL_IDENTITIES.validate_python(value)}
            )
            return _ApplySuccess(result=current.model_copy(update={"subscription": updated_models}))
        case SubscriptionField.BALANCE:
            updated_balance: Final = subscription.model_copy(
                update={"balance": _OPTIONAL_NONNEGATIVE_DECIMAL.validate_python(value)}
            )
            return _ApplySuccess(result=current.model_copy(update={"subscription": updated_balance}))
        case SubscriptionField.CURRENCY:
            updated_currency: Final = subscription.model_copy(
                update={"currency": _OPTIONAL_STRING.validate_python(value)}
            )
            return _ApplySuccess(result=current.model_copy(update={"subscription": updated_currency}))
        case SubscriptionField.CHANNEL_CONCURRENCY:
            updated_channel_concurrency: Final = subscription.model_copy(
                update={"channel_concurrency": _OPTIONAL_POSITIVE_INT.validate_python(value)}
            )
            return _ApplySuccess(
                result=current.model_copy(update={"subscription": updated_channel_concurrency})
            )
        case SubscriptionField.MODEL_CONCURRENCY:
            updated_model_concurrency: Final = subscription.model_copy(
                update={"model_concurrency": _CONCURRENCY_LIMITS.validate_python(value)}
            )
            return _ApplySuccess(
                result=current.model_copy(update={"subscription": updated_model_concurrency})
            )
        case SubscriptionField.LIMITS:
            updated_limits: Final = subscription.model_copy(
                update={"limits": _QUOTA_LIMITS.validate_python(value)}
            )
            return _ApplySuccess(result=current.model_copy(update={"subscription": updated_limits}))
    assert_never(target.field)


def _apply_subscription_model(
    current: ParsedChannelData,
    target: SubscriptionModelTarget,
    value: object,
) -> _ApplySuccess | OverrideApplyFailure:
    subscription: Final = current.subscription
    if subscription is None:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_MISSING)
    matches: Final = tuple(
        model for model in subscription.models if model.provider_model_id == target.provider_model_id
    )
    if not matches:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_MISSING)
    if len(matches) > 1:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_AMBIGUOUS)
    replacement: Final = _MODEL_IDENTITY.validate_python(value)
    if replacement.provider_model_id != target.provider_model_id:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.IDENTITY_CHANGE)
    models: Final = tuple(
        replacement if model.provider_model_id == target.provider_model_id else model
        for model in subscription.models
    )
    updated: Final = subscription.model_copy(update={"models": models})
    return _ApplySuccess(result=current.model_copy(update={"subscription": updated}))


def _apply_metered_group(
    current: ParsedChannelData,
    target: MeteredGroupTarget,
    value: object,
) -> _ApplySuccess | OverrideApplyFailure:
    metered: Final = current.metered
    if metered is None:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_MISSING)
    matches: Final = tuple(group for group in metered.groups if group.group_id == target.group_id)
    if not matches:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_MISSING)
    if len(matches) > 1:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_AMBIGUOUS)
    replacement: Final = _METERED_GROUP.validate_python(value)
    if replacement.group_id != target.group_id:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.IDENTITY_CHANGE)
    groups: Final = tuple(
        replacement if group.group_id == target.group_id else group for group in metered.groups
    )
    return _ApplySuccess(result=current.model_copy(update={"metered": MeteredData(groups=groups)}))


def _apply_metered_price(
    current: ParsedChannelData,
    target: MeteredPriceTarget,
    value: object,
) -> _ApplySuccess | OverrideApplyFailure:
    metered: Final = current.metered
    if metered is None:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_MISSING)
    groups: Final = tuple(group for group in metered.groups if group.group_id == target.group_id)
    if not groups:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_MISSING)
    if len(groups) > 1:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_AMBIGUOUS)
    group: Final = next(iter(groups))
    prices: Final = tuple(
        price for price in group.models if price.provider_model_id == target.provider_model_id
    )
    if not prices:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_MISSING)
    if len(prices) > 1:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_AMBIGUOUS)
    # 价格包含多个相互关联字段，整对象校验可避免只改局部字段后产生不一致数据。
    replacement: Final = _METERED_PRICE.validate_python(value)
    if replacement.provider_model_id != target.provider_model_id:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.IDENTITY_CHANGE)
    updated_prices: Final = tuple(
        replacement if price.provider_model_id == target.provider_model_id else price
        for price in group.models
    )
    updated_group: Final = group.model_copy(update={"models": updated_prices})
    updated_groups: Final = tuple(
        updated_group if candidate.group_id == target.group_id else candidate
        for candidate in metered.groups
    )
    return _ApplySuccess(result=current.model_copy(update={"metered": MeteredData(groups=updated_groups)}))


def _apply_billing_route(
    current: ParsedChannelData,
    target: BillingRouteTarget,
    value: object,
) -> _ApplySuccess | OverrideApplyFailure:
    matches: Final = tuple(route for route in current.billing_routes if route.route_id == target.route_id)
    if not matches:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_MISSING)
    if len(matches) > 1:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.TARGET_AMBIGUOUS)
    replacement: Final = _BILLING_ROUTE.validate_python(value)
    if replacement.route_id != target.route_id:
        return _target_failure(target.field_path(), OverrideApplyFailureCode.IDENTITY_CHANGE)
    routes: Final = tuple(
        replacement if route.route_id == target.route_id else route for route in current.billing_routes
    )
    return _ApplySuccess(result=current.model_copy(update={"billing_routes": routes}))


def _failure(event: FieldOverrideEvent, code: OverrideApplyFailureCode) -> OverrideApplyFailure:
    return OverrideApplyFailure(override_id=event.override_id, field_path=event.field_path(), code=code)


def _target_failure(field_path: str, code: OverrideApplyFailureCode) -> OverrideApplyFailure:
    return OverrideApplyFailure(override_id=UUID(int=0), field_path=field_path, code=code)
