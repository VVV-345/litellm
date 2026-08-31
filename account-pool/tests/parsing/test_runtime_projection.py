"""验证最新解析结果只读取一次并同时投影额度窗口与调度价格。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, Never
from uuid import UUID

import pytest
from account_pool.models import AccountConfig, DeploymentConfig, PoolConfig
from account_pool.parsing.models import (
    EffectivePrices,
    MeteredData,
    MeteredGroup,
    MeteredModelPrice,
    ParsedChannelData,
    ParserRunStatus,
    QuotaKind,
    QuotaLimit,
    QuotaScope,
    QuotaWindowType,
    SubscriptionData,
)
from account_pool.parsing.projection import ParserRuntimeConfigEnricher
from account_pool.parsing.service import EffectiveParserData, EffectiveParserDataResult

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_BINDING_ID: Final = UUID("20000000-0000-0000-0000-000000000001")
_RUN_ID: Final = UUID("30000000-0000-0000-0000-000000000001")
_OBSERVED_AT: Final = datetime(2026, 8, 21, tzinfo=UTC)


class CountingParserData:
    def __init__(self, result: EffectiveParserDataResult) -> None:
        self._result: Final = result
        self.calls = 0

    async def effective_data(self, channel_id: UUID) -> EffectiveParserDataResult:
        assert channel_id == _CHANNEL_ID
        self.calls += 1
        return self._result

    async def history(self, channel_id: UUID, limit: int) -> Never:
        raise AssertionError((channel_id, limit))

    async def snapshot(self, channel_id: UUID) -> Never:
        raise AssertionError(channel_id)


@pytest.mark.asyncio
async def test_combined_projection_reads_once_and_enriches_quota_and_cost() -> None:
    parsed: Final = ParsedChannelData(
        subscription=SubscriptionData(
            channel_concurrency=10,
            limits=(
                QuotaLimit(
                    scope=QuotaScope.CHANNEL,
                    kind=QuotaKind.TOKENS,
                    window_type=QuotaWindowType.ROLLING,
                    duration_seconds=18_000,
                    limit=Decimal("100"),
                    remaining=Decimal("80"),
                    source="official-api",
                    observed_at=_OBSERVED_AT,
                ),
            )
        ),
        metered=MeteredData(
            groups=(
                MeteredGroup(
                    group_id="standard",
                    models=(
                        MeteredModelPrice(
                            provider_model_id="provider-a",
                            public_model_name="public-a",
                            currency="USD",
                            unit="million_tokens",
                            input_price=Decimal("2"),
                            output_price=Decimal("8"),
                            effective_prices=EffectivePrices(
                                input_price=Decimal("2"),
                                output_price=Decimal("8"),
                            ),
                            normalized_per_million_tokens=EffectivePrices(
                                input_price=Decimal("2"),
                                output_price=Decimal("8"),
                            ),
                        ),
                    ),
                ),
            )
        ),
    )
    effective: Final = EffectiveParserData(
        channel_id=_CHANNEL_ID,
        parser_run_id=_RUN_ID,
        parser_id="fixture-parser",
        parser_version="1.0.0",
        parsed_at=_OBSERVED_AT,
        parser_status=ParserRunStatus.SUCCESS,
        raw_result=parsed,
        effective_result=parsed,
    )
    parser_data: Final = CountingParserData(effective)
    account: Final = AccountConfig(
        id="channel-a",
        channel_id=_CHANNEL_ID,
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=2,
        deployments=(
            DeploymentConfig(
                public_model="public-a",
                provider_model="provider-a",
                litellm_model_id="deployment-a",
                binding_id=_BINDING_ID,
            ),
        ),
    )

    enriched: Final = await ParserRuntimeConfigEnricher(parser_data).enrich(PoolConfig(accounts=(account,)))

    assert parser_data.calls == 1
    assert enriched.accounts[0].max_concurrency == 10
    assert enriched.accounts[0].quota_windows[0].remaining == Decimal("80")
    assert enriched.accounts[0].deployments[0].cost_evidence is not None
    assert enriched.accounts[0].deployments[0].cost_evidence.effective_cost == Decimal("10")
