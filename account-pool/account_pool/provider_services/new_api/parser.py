"""将 New API 网关的模型发现与倍率价格转换为统一解析器输出。"""

from decimal import Decimal
from typing import Final
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.domain.provider_source import MeteredPriceOffer, ProviderValidationResult
from account_pool.parsing.model_discovery import ModelDiscoveryParserSpec, parse_model_discovery_result
from account_pool.parsing.models import (
    EffectivePrices,
    MeteredData,
    MeteredGroup,
    MeteredModelPrice,
    ParserRun,
    PriceCalculation,
)
from account_pool.parsing.registry import ParserRegistration

PARSER_ID: Final = "new-api"
PARSER_VERSION: Final = "1.0.0"
NEW_API_PARSER_SPEC: Final = ModelDiscoveryParserSpec(
    parser_id=PARSER_ID,
    parser_version=PARSER_VERSION,
    unresolved_reason="New API 倍率为相对价格，未返回渠道基础价格与币种",
    warning="模型发现与分组倍率已完成，绝对金额需要人工补充渠道基础价格",
    next_action="在管理界面补充渠道基础价格，或将倍率换算为每百万 token 绝对价格",
    evidence_summary="上游返回模型倍率与分组倍率，未提供可换算为货币的基础价格",
)
NEW_API_PARSER_REGISTRATION: Final = ParserRegistration(
    parser_id=PARSER_ID,
    provider_ids=("new_api",),
    match_provider_only=True,
)


def parse_new_api_result(
    channel_id: UUID,
    parser_run_id: UUID,
    parsed_at: AwareDatetime,
    validation: ProviderValidationResult,
) -> ParserRun:
    return parse_model_discovery_result(
        channel_id=channel_id,
        parser_run_id=parser_run_id,
        parsed_at=parsed_at,
        validation=validation,
        spec=NEW_API_PARSER_SPEC,
        metered=_metered_data(validation),
    )


def _metered_data(validation: ProviderValidationResult) -> MeteredData | None:
    if not validation.pricing:
        return None
    prices: Final = tuple(_metered_price(offer) for offer in validation.pricing)
    return MeteredData(groups=(MeteredGroup(group_name=validation.group, models=prices),))


def _metered_price(offer: MeteredPriceOffer) -> MeteredModelPrice:
    return MeteredModelPrice(
        provider_model_id=offer.provider_model_id,
        currency=offer.currency or "RATIO",
        unit=offer.unit or "multiplier",
        input_price=offer.input_price,
        output_price=offer.output_price,
        cache_read_price=offer.cache_read_price,
        cache_write_price=offer.cache_write_price,
        group_multiplier=offer.group_multiplier,
        price_calculation=PriceCalculation.MULTIPLIER,
        effective_prices=EffectivePrices(
            input_price=_multiply(offer.input_price, offer.group_multiplier),
            output_price=_multiply(offer.output_price, offer.group_multiplier),
            cache_read_price=_multiply(offer.cache_read_price, offer.group_multiplier),
            cache_write_price=_multiply(offer.cache_write_price, offer.group_multiplier),
        ),
    )


def _multiply(source: Decimal | None, multiplier: Decimal) -> Decimal | None:
    return None if source is None else source * multiplier
