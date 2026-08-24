"""验证人工覆盖的稳定定位、撤销、重新解析合成和安全边界。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID

import pytest
from account_pool.parsing.models import (
    EffectivePrices,
    MeteredData,
    MeteredGroup,
    MeteredModelPrice,
    ModelIdentity,
    ParsedChannelData,
    ParserRun,
    ParserRunStatus,
    PriceCalculation,
    SubscriptionData,
)
from account_pool.parsing.overrides.composer import (
    OverrideApplyFailureCode,
    active_override_events,
    compose_effective_result,
)
from account_pool.parsing.overrides.models import (
    FieldOverrideEvent,
    MeteredPriceTarget,
    OverrideAction,
    OverrideTarget,
    RootField,
    RootFieldTarget,
    SubscriptionField,
    SubscriptionFieldTarget,
)
from pydantic import JsonValue, TypeAdapter, ValidationError

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_NOW: Final = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
_JSON: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)


def test_metered_override_requires_effective_prices_to_match_group_multiplier() -> None:
    with pytest.raises(ValidationError):
        MeteredModelPrice(
            provider_model_id="model-a",
            currency="RATIO",
            unit="multiplier",
            input_price=Decimal("2"),
            output_price=Decimal("3"),
            group_multiplier=Decimal("1.5"),
            price_calculation=PriceCalculation.MULTIPLIER,
            effective_prices=EffectivePrices(input_price=Decimal("2"), output_price=Decimal("3")),
        )


def _price(model: str, input_price: str) -> MeteredModelPrice:
    source: Final = Decimal(input_price)
    return MeteredModelPrice(
        provider_model_id=model,
        currency="USD",
        unit="million_tokens",
        input_price=source,
        group_multiplier=Decimal("1"),
        effective_prices=EffectivePrices(input_price=source),
    )


def _run(
    parser_run_id: UUID = _RUN_ID,
    balance: str = "10",
    groups: tuple[MeteredGroup, ...] | None = None,
) -> ParserRun:
    resolved_groups: Final = groups or (
        MeteredGroup(
            group_id="standard",
            models=(_price("model-a", "1"), _price("model-b", "2")),
        ),
    )
    return ParserRun(
        parser_run_id=parser_run_id,
        channel_id=_CHANNEL_ID,
        parser_id="fixture-parser",
        parser_version="1.0.0",
        parsed_at=_NOW,
        status=ParserRunStatus.SUCCESS,
        result=ParsedChannelData(
            subscription=SubscriptionData(
                plan_name="Pro",
                balance=Decimal(balance),
                models=(ModelIdentity(provider_model_id="model-a"),),
            ),
            metered=MeteredData(groups=resolved_groups),
        ),
    )


def _event(
    override_id: str,
    target: OverrideTarget,
    value: JsonValue | None,
    occurred_at: datetime = _NOW,
    action: OverrideAction = OverrideAction.SET,
    supersedes: UUID | None = None,
    channel_id: UUID = _CHANNEL_ID,
) -> FieldOverrideEvent:
    return FieldOverrideEvent(
        override_id=UUID(override_id),
        channel_id=channel_id,
        source_parser_run_id=_RUN_ID,
        target=target,
        action=action,
        value=value,
        had_previous_override=supersedes is not None,
        previous_value=None,
        supersedes_override_id=supersedes,
        actor_id="admin-user",
        reason="人工确认账户数据",
        occurred_at=occurred_at,
    )


def _json_value(model: MeteredModelPrice) -> JsonValue:
    return _JSON.validate_json(model.model_dump_json())


def test_latest_event_per_channel_and_path_controls_active_override() -> None:
    target: Final = SubscriptionFieldTarget(field=SubscriptionField.BALANCE)
    first: Final = _event("30000000-0000-0000-0000-000000000003", target, "20")
    second: Final = _event(
        "30000000-0000-0000-0000-000000000004",
        target,
        "30",
        occurred_at=_NOW + timedelta(minutes=1),
        supersedes=first.override_id,
    )
    revoked: Final = _event(
        "30000000-0000-0000-0000-000000000005",
        target,
        None,
        occurred_at=_NOW + timedelta(minutes=2),
        action=OverrideAction.REVOKE,
        supersedes=second.override_id,
    )
    other_channel: Final = _event(
        "30000000-0000-0000-0000-000000000006",
        target,
        "99",
        channel_id=UUID("10000000-0000-0000-0000-000000000009"),
    )

    active: Final = active_override_events((second, other_channel, first, revoked))

    assert active == (other_channel,)


def test_override_survives_reparse_without_replacing_raw_result() -> None:
    event: Final = _event(
        "30000000-0000-0000-0000-000000000003",
        SubscriptionFieldTarget(field=SubscriptionField.BALANCE),
        "42.5",
    )
    reparsed: Final = _run(
        parser_run_id=UUID("20000000-0000-0000-0000-000000000007"),
        balance="11",
    )

    composition: Final = compose_effective_result(reparsed, (event,))

    assert composition.raw_result.subscription is not None
    assert composition.raw_result.subscription.balance == Decimal("11")
    assert composition.effective_result.subscription is not None
    assert composition.effective_result.subscription.balance == Decimal("42.5")
    assert composition.applied_override_ids == (event.override_id,)
    assert composition.failures == ()


def test_metered_price_target_is_stable_when_groups_and_models_reorder() -> None:
    replacement: Final = _price("model-a", "0.5")
    event: Final = _event(
        "30000000-0000-0000-0000-000000000003",
        MeteredPriceTarget(group_id="standard", provider_model_id="model-a"),
        _json_value(replacement),
    )
    reparsed: Final = _run(
        groups=(
            MeteredGroup(group_id="other", models=(_price("other-model", "9"),)),
            MeteredGroup(
                group_id="standard",
                models=(_price("model-b", "2"), _price("model-a", "1")),
            ),
        )
    )

    composition: Final = compose_effective_result(reparsed, (event,))

    assert composition.effective_result.metered is not None
    standard: Final = next(
        group for group in composition.effective_result.metered.groups if group.group_id == "standard"
    )
    prices: Final = {price.provider_model_id: price.input_price for price in standard.models}
    assert prices == {"model-b": Decimal("2"), "model-a": Decimal("0.5")}


def test_invalid_override_is_reported_and_does_not_block_later_valid_override() -> None:
    invalid: Final = _event(
        "30000000-0000-0000-0000-000000000003",
        SubscriptionFieldTarget(field=SubscriptionField.CHANNEL_CONCURRENCY),
        0,
    )
    valid: Final = _event(
        "30000000-0000-0000-0000-000000000004",
        RootFieldTarget(field=RootField.WARNINGS),
        ["管理员已复核"],
        occurred_at=_NOW + timedelta(minutes=1),
    )

    composition: Final = compose_effective_result(_run(), (valid, invalid))

    assert composition.effective_result.warnings == ("管理员已复核",)
    assert composition.applied_override_ids == (valid.override_id,)
    assert len(composition.failures) == 1
    assert composition.failures[0].override_id == invalid.override_id
    assert composition.failures[0].code == OverrideApplyFailureCode.INVALID_VALUE


def test_price_identity_change_and_missing_target_are_reported() -> None:
    identity_change: Final = _event(
        "30000000-0000-0000-0000-000000000003",
        MeteredPriceTarget(group_id="standard", provider_model_id="model-a"),
        _json_value(_price("different-model", "1")),
    )
    missing: Final = _event(
        "30000000-0000-0000-0000-000000000004",
        MeteredPriceTarget(group_id="missing", provider_model_id="model-a"),
        _json_value(_price("model-a", "1")),
        occurred_at=_NOW + timedelta(minutes=1),
    )

    composition: Final = compose_effective_result(_run(), (identity_change, missing))

    assert tuple(failure.code for failure in composition.failures) == (
        OverrideApplyFailureCode.IDENTITY_CHANGE,
        OverrideApplyFailureCode.TARGET_MISSING,
    )


def test_override_rejects_sensitive_value_and_reason() -> None:
    target: Final = RootFieldTarget(field=RootField.WARNINGS)
    with pytest.raises(ValidationError, match="cannot be persisted"):
        _event(
            "30000000-0000-0000-0000-000000000003",
            target,
            ["authorization: bearer test-placeholder"],
        )
    with pytest.raises(ValidationError, match="cannot be persisted"):
        FieldOverrideEvent(
            override_id=UUID("30000000-0000-0000-0000-000000000004"),
            channel_id=_CHANNEL_ID,
            source_parser_run_id=_RUN_ID,
            target=target,
            action=OverrideAction.SET,
            value=["safe"],
            actor_id="admin-user",
            reason="see https://private.example",
            occurred_at=_NOW,
        )
