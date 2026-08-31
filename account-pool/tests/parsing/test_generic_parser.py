"""验证 通用解析器 倍率价格到统一按量分组的转换，及分组倍率的有效价格计算。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from uuid import uuid4

from account_pool.domain.provider_source import (
    MeteredPriceOffer,
    ModelOffer,
    PricingDiscoveryFailureCode,
    ProviderValidationResult,
)
from account_pool.parsing.models import ParserRunStatus
from account_pool.provider_services.generic.manifest import GENERIC_MANIFEST
from account_pool.provider_services.generic.parser import parse_generic_result


def _validation(
    models: tuple[ModelOffer, ...],
    pricing: tuple[MeteredPriceOffer, ...],
    pricing_failure_code: PricingDiscoveryFailureCode | None = None,
) -> ProviderValidationResult:
    return ProviderValidationResult(
        ok=True,
        provider_id="generic",
        normalized_api_base="https://gateway.example.com/v1",
        group="premium",
        key_fingerprint="fingerprint",
        message="不会复制到解析结果的渠道校验文案",
        pricing_failure_code=pricing_failure_code,
        capabilities=GENERIC_MANIFEST.capabilities,
        models=models,
        pricing=pricing,
    )


def test_generic_parser_applies_group_multiplier_to_effective_prices() -> None:
    run: Final = parse_generic_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 22, 8, 0, tzinfo=UTC),
        validation=_validation(
            models=(ModelOffer(model="gpt-4o"), ModelOffer(model="claude-3.5-sonnet")),
            pricing=(
                MeteredPriceOffer(
                    provider_model_id="gpt-4o",
                    group_name="premium",
                    currency="RATIO",
                    unit="multiplier",
                    input_price=Decimal("2.0"),
                    output_price=Decimal("4.0"),
                    group_multiplier=Decimal("1.5"),
                ),
            ),
        ),
    )

    assert run.parser_id == "generic"
    assert run.status == ParserRunStatus.PARTIAL
    assert run.discovered_models == ("claude-3.5-sonnet", "gpt-4o")
    assert run.result.metered is not None
    assert run.result.subscription is None

    group: Final = run.result.metered.groups[0]
    price: Final = group.models[0]
    assert group.group_name == "premium"
    assert price.input_price == Decimal("2.0")
    assert price.output_price == Decimal("4.0")
    assert price.group_multiplier == Decimal("1.5")
    assert price.effective_prices.input_price == Decimal("3.0")
    assert price.effective_prices.output_price == Decimal("6.0")
    assert price.normalized_per_million_tokens is None

    serialized: Final = run.model_dump_json()
    assert "gateway.example.com" not in serialized
    assert "fingerprint" not in serialized
    assert "渠道校验文案" not in serialized


def test_generic_parser_omits_unpriced_visible_models() -> None:
    run: Final = parse_generic_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 22, 8, 0, tzinfo=UTC),
        validation=_validation(
            models=(ModelOffer(model="priced-model"), ModelOffer(model="unpriced-model")),
            pricing=(
                MeteredPriceOffer(
                    provider_model_id="priced-model",
                    group_name="premium",
                    currency="RATIO",
                    unit="multiplier",
                    input_price=Decimal("2.0"),
                    output_price=Decimal("4.0"),
                    group_multiplier=Decimal("1.5"),
                ),
            ),
        ),
    )

    assert run.result.metered is not None
    assert tuple(price.provider_model_id for price in run.result.metered.groups[0].models) == ("priced-model",)
    assert run.discovered_models == ("priced-model", "unpriced-model")


def test_generic_parser_without_pricing_keeps_metered_unresolved() -> None:
    run: Final = parse_generic_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 22, 8, 0, tzinfo=UTC),
        validation=_validation(models=(ModelOffer(model="gpt-4o"),), pricing=()),
    )

    assert run.status == ParserRunStatus.PARTIAL
    assert run.result.metered is None
    assert tuple(field.path for field in run.result.unresolved_fields) == ("subscription", "metered")
    assert run.result.warnings == ("已完成模型发现，价格需要人工补充",)
    assert run.issues[0].next_action == "在管理界面为已发现模型填写价格，或提供管理员凭证后重新解析"


def test_generic_parser_marks_authentication_pricing_failure_with_targeted_remediation() -> None:
    run: Final = parse_generic_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 22, 8, 0, tzinfo=UTC),
        validation=_validation(
            models=(ModelOffer(model="gpt-4o"),),
            pricing=(),
            pricing_failure_code=PricingDiscoveryFailureCode.AUTHENTICATION,
        ),
    )

    assert run.result.warnings == ("已完成模型发现，价格需要人工补充",)
    assert not run.issues[0].retryable


def test_generic_parser_marks_invalid_pricing_response_with_targeted_remediation() -> None:
    run: Final = parse_generic_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 22, 8, 0, tzinfo=UTC),
        validation=_validation(
            models=(ModelOffer(model="gpt-4o"),),
            pricing=(),
            pricing_failure_code=PricingDiscoveryFailureCode.UPSTREAM_RESPONSE,
        ),
    )

    assert run.result.warnings == ("已完成模型发现，价格需要人工补充",)
    assert not run.issues[0].retryable


def test_generic_parser_marks_transient_pricing_failure_retryable() -> None:
    run: Final = parse_generic_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 22, 8, 0, tzinfo=UTC),
        validation=_validation(
            models=(ModelOffer(model="gpt-4o"),),
            pricing=(),
            pricing_failure_code=PricingDiscoveryFailureCode.TRANSPORT,
        ),
    )

    assert run.result.warnings == ("已完成模型发现，价格需要人工补充",)
    assert run.result.unresolved_fields[1].retryable
    assert run.issues[0].retryable
    assert run.issues[0].next_action == "在管理界面为已发现模型填写价格，或提供管理员凭证后重新解析"
