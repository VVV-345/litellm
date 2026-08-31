"""将通用模型发现与价格结果转换为统一解析输出。"""

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

GENERIC_PARSER_ID: Final = "generic"
GENERIC_PARSER_VERSION: Final = "1.0.0"
GENERIC_PARSER_REGISTRATION: Final = ParserRegistration(
    parser_id=GENERIC_PARSER_ID,
    provider_ids=(GENERIC_PARSER_ID,),
    match_provider_only=True,
)
_PRICED_SPEC: Final = ModelDiscoveryParserSpec(
    parser_id=GENERIC_PARSER_ID,
    parser_version=GENERIC_PARSER_VERSION,
    unresolved_reason="通用解析器未返回套餐信息",
    warning="已完成模型发现与可用价格解析，套餐信息需要人工补充",
    next_action="在管理界面补充套餐信息，或使用人工价格覆盖已发现模型",
    evidence_summary="上游模型列表和兼容价格接口已完成校验",
)
_UNPRICED_SPEC: Final = ModelDiscoveryParserSpec(
    parser_id=GENERIC_PARSER_ID,
    parser_version=GENERIC_PARSER_VERSION,
    unresolved_reason="通用解析器未获取到可用价格",
    warning="已完成模型发现，价格需要人工补充",
    next_action="在管理界面为已发现模型填写价格，或提供管理员凭证后重新解析",
    evidence_summary="上游模型列表获取成功，兼容价格接口不可用",
)


def parse_generic_result(
    channel_id: UUID,
    parser_run_id: UUID,
    parsed_at: AwareDatetime,
    validation: ProviderValidationResult,
) -> ParserRun:
    metered: Final = _metered_data(validation)
    spec: Final = _PRICED_SPEC if metered is not None else _UNPRICED_SPEC
    return parse_model_discovery_result(
        channel_id=channel_id,
        parser_run_id=parser_run_id,
        parsed_at=parsed_at,
        validation=validation,
        spec=spec,
        metered=metered,
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
