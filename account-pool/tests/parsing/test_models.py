"""验证统一解析器模型的字段约束、计费关系和脱敏要求。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from uuid import uuid4

import pytest
from account_pool.parsing.models import (
    EffectivePrices,
    MeteredModelPrice,
    ParsedChannelData,
    ParserRun,
    ParserRunStatus,
    PriceCalculation,
)
from pydantic import ValidationError


def test_metered_price_uses_decimal_and_validates_effective_prices() -> None:
    price: Final = MeteredModelPrice(
        provider_model_id="provider-model",
        litellm_model_name="openai/provider-model",
        public_model_name="public-model",
        currency="USD",
        unit="million_tokens",
        input_price=Decimal("2.50"),
        output_price=Decimal("10.00"),
        cache_read_price=None,
        cache_write_price=None,
        group_multiplier=Decimal("1.20"),
        effective_prices=EffectivePrices(
            input_price=Decimal("3.000"),
            output_price=Decimal("12.000"),
            cache_read_price=None,
            cache_write_price=None,
        ),
    )

    assert price.input_price == Decimal("2.50")
    assert price.effective_prices.input_price == Decimal("3.000")

    with pytest.raises(ValidationError, match="effective input price"):
        price.model_copy(
            update={"effective_prices": price.effective_prices.model_copy(update={"input_price": Decimal("999")})}
        ).__class__.model_validate(
            {
                **price.model_dump(),
                "effective_prices": {
                    **price.effective_prices.model_dump(),
                    "input_price": Decimal("999"),
                },
            }
        )


def test_provider_normalized_price_requires_conversion_note() -> None:
    payload: Final = {
        "provider_model_id": "provider-model",
        "currency": "CREDITS",
        "unit": "provider_unit",
        "input_price": Decimal("2"),
        "group_multiplier": Decimal("1.5"),
        "price_calculation": PriceCalculation.PROVIDER_NORMALIZED,
        "effective_prices": {"input_price": Decimal("4")},
    }

    with pytest.raises(ValidationError, match="conversion note"):
        MeteredModelPrice.model_validate(payload)

    price: Final = MeteredModelPrice.model_validate(
        {**payload, "conversion_note": "厂商倍率不是乘法，已按厂商规则标准化"}
    )
    assert price.effective_prices.input_price == Decimal("4")


def test_parser_run_rejects_naive_time_and_status_without_result() -> None:
    empty: Final = ParsedChannelData()

    with pytest.raises(ValidationError):
        ParserRun(
            parser_run_id=uuid4(),
            channel_id=uuid4(),
            parser_id="test-parser",
            parser_version="1.0.0",
            parsed_at=datetime(2026, 8, 19, 8, 0),
            status=ParserRunStatus.PARTIAL,
            result=empty,
        )

    with pytest.raises(ValidationError, match="successful parser run"):
        ParserRun(
            parser_run_id=uuid4(),
            channel_id=uuid4(),
            parser_id="test-parser",
            parser_version="1.0.0",
            parsed_at=datetime(2026, 8, 19, 8, 0, tzinfo=UTC),
            status=ParserRunStatus.SUCCESS,
            result=empty,
        )


def test_parser_models_reject_secret_shaped_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ParsedChannelData.model_validate(
            {
                "subscription": None,
                "metered": None,
                "billing_routes": [],
                "capabilities": [],
                "unresolved_fields": [],
                "evidence": [],
                "warnings": [],
                "api_key": "must-not-be-accepted",
            }
        )
