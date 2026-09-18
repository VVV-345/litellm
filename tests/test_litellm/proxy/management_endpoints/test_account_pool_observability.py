"""验证标准统计的账号归属、缓存用量和数据库故障边界。"""

from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from typing import Final
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from litellm.proxy.db.prisma_client import PrismaWrapper
from litellm.proxy.db.routing_prisma_wrapper import RoutingPrismaWrapper
from litellm.proxy.management_endpoints.account_pool_observability import standard_dashboard


@pytest.mark.asyncio
async def test_standard_dashboard_preserves_final_results_and_cache_usage() -> None:
    account: Final = uuid4()
    now: Final = datetime(2026, 9, 18, tzinfo=timezone.utc)
    totals: Final = {
        "total_requests": 4,
        "succeeded_requests": 3,
        "failed_requests": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 80,
        "cache_creation_input_tokens": 10,
        "total_cost_usd": 0.125,
        "known_cost_requests": 3,
        "average_duration_ms": 1500,
    }

    async def query(sql: str, since: datetime) -> object:
        assert since == now - timedelta(days=30)
        return [{"account_id": str(account), **totals}, {"account_id": None, **totals}]

    dashboard: Final = await standard_dashboard(query, now=now)

    assert dashboard.summary.total_requests == 4
    assert dashboard.summary.failed_requests == 1
    assert dashboard.cards[0].account_id == dashboard.cards[0].card_id == account
    assert dashboard.cards[0].cache_rate == 0.8
    assert dashboard.cards[0].cache_creation_input_tokens == 10
    assert dashboard.cards[0].statistics_source == "litellm"
    assert dashboard.occurred_from == now - timedelta(days=30)


@pytest.mark.asyncio
async def test_standard_dashboard_does_not_hide_database_failure_as_zero_requests() -> None:
    async def unavailable(sql: str, since: datetime) -> object:
        raise HTTPException(503, "LiteLLM statistics database is unavailable")

    with pytest.raises(HTTPException) as failure:
        await standard_dashboard(unavailable)
    assert failure.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("replica", [False, True])
async def test_statistics_use_dynamic_prisma_query_and_read_replica(replica: bool) -> None:
    query: Final = AsyncMock(return_value=[])
    writer_query: Final = AsyncMock(side_effect=AssertionError("read should use replica"))
    reader: Final = PrismaWrapper(SimpleNamespace(query_raw=query))
    database: Final = (
        RoutingPrismaWrapper(PrismaWrapper(SimpleNamespace(query_raw=writer_query)), reader) if replica else reader
    )
    with patch("litellm.proxy.proxy_server.prisma_client", SimpleNamespace(db=database)):
        report: Final = await standard_dashboard()
    assert report.summary.total_requests == 0
    query.assert_awaited_once()
    writer_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_statistics_query_against_postgres_prisma_binding() -> None:
    database_url: Final = os.getenv("ACCOUNT_POOL_STATS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("requires a LiteLLM PostgreSQL database")
    from prisma import Prisma

    client: Final = Prisma(datasource={"url": database_url})
    await client.connect()
    try:
        with patch("litellm.proxy.proxy_server.prisma_client", SimpleNamespace(db=PrismaWrapper(client))):
            report: Final = await standard_dashboard()
        assert report.statistics_source == "litellm"
        assert report.summary.total_requests >= 0
    finally:
        await client.disconnect()
