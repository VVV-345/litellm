"""验证解析运行 PostgreSQL 仓储的规范化往返、幂等和事务回滚。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.models import CatalogImport
from account_pool.catalog.postgres import PostgresCatalogRepository
from account_pool.domain.provider_source import ProviderCapability
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
    ParserFailureCategory,
    ParserIssue,
    ParserRun,
    ParserRunStatus,
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
    ParserExportAttempt,
    ParserExportStatus,
    ParserPersistenceFailure,
    ParserPersistenceFailureCode,
    ParserRunsLoadSuccess,
    ParserRunWriteSuccess,
)
from account_pool.parsing.postgres import PostgresParserRunRepository
from account_pool.parsing.snapshots import SnapshotExportFailureCode
from psycopg import sql
from tests.catalog.test_importer import legacy_config

_PARSED_AT: Final = datetime(2026, 8, 19, 13, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ParserRepositoryFixture:
    database_url: str
    schema: str
    repository: PostgresParserRunRepository
    catalog: CatalogImport


def _migration_sql(pattern: str) -> bytes:
    repository_root: Final = Path(__file__).resolve().parents[3]
    migrations_root: Final = repository_root / "litellm-proxy-extras" / "litellm_proxy_extras" / "migrations"
    matches: Final = tuple(migrations_root.glob(f"*_{pattern}/migration.sql"))
    assert len(matches) == 1
    return matches[0].read_bytes()


@pytest_asyncio.fixture
async def parser_repository_fixture() -> AsyncIterator[ParserRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    schema: Final = f"account_pool_parser_test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await connection.execute("SELECT set_config('search_path', %s, false)", (schema,))
            await connection.execute(_migration_sql("add_account_pool_catalog"))
            await connection.execute(_migration_sql("add_account_pool_parser_runs"))
            catalog: Final = catalog_import_from_pool_config(legacy_config(), _PARSED_AT)
            catalog_repository: Final = PostgresCatalogRepository(database_url, schema=schema)
            imported: Final = await catalog_repository.import_once(catalog)
            assert imported.status == "created"
            yield ParserRepositoryFixture(
                database_url=database_url,
                schema=schema,
                repository=PostgresParserRunRepository(database_url, schema=schema),
                catalog=catalog,
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _rich_run(fixture: ParserRepositoryFixture, parser_run_id: UUID | None = None) -> ParserRun:
    channel: Final = fixture.catalog.channels[0]
    binding: Final = fixture.catalog.bindings[0]
    multiplier: Final = Decimal("1.25")
    input_price: Final = Decimal("2.50")
    output_price: Final = Decimal("10.00")
    return ParserRun(
        parser_run_id=parser_run_id or UUID("20000000-0000-0000-0000-000000000002"),
        channel_id=channel.channel_id,
        parser_id="fixture-parser",
        parser_version="1.2.3",
        parsed_at=_PARSED_AT,
        status=ParserRunStatus.SUCCESS,
        result=ParsedChannelData(
            subscription=SubscriptionData(
                plan_id="plan-pro",
                plan_name="Pro",
                status=SubscriptionStatus.ACTIVE,
                starts_at=_PARSED_AT - timedelta(days=1),
                expires_at=_PARSED_AT + timedelta(days=29),
                models=(ModelIdentity(provider_model_id="model-a", public_model_name="public-a"),),
                balance=Decimal("42.125"),
                currency="USD",
                channel_concurrency=8,
                model_concurrency=(ConcurrencyLimit(subject_id="model-a", limit=4),),
                limits=(
                    QuotaLimit(
                        scope=QuotaScope.MODEL,
                        subject_id="model-a",
                        kind=QuotaKind.TOKENS,
                        window_type=QuotaWindowType.ROLLING,
                        duration_seconds=18_000,
                        limit=Decimal("1000000"),
                        used=Decimal("125000"),
                        remaining=Decimal("875000"),
                        source="official-api",
                        observed_at=_PARSED_AT,
                    ),
                ),
            ),
            metered=MeteredData(
                groups=(
                    MeteredGroup(
                        group_id="premium",
                        group_name="Premium",
                        concurrency=12,
                        models=(
                            MeteredModelPrice(
                                provider_model_id="model-a",
                                public_model_name="public-a",
                                currency="USD",
                                unit="million_tokens",
                                input_price=input_price,
                                output_price=output_price,
                                group_multiplier=multiplier,
                                effective_prices=EffectivePrices(
                                    input_price=input_price * multiplier,
                                    output_price=output_price * multiplier,
                                ),
                                normalized_per_million_tokens=EffectivePrices(
                                    input_price=input_price * multiplier,
                                    output_price=output_price * multiplier,
                                ),
                                concurrency=6,
                            ),
                        ),
                    ),
                ),
            ),
            billing_routes=(
                BillingRoute(
                    route_id=UUID("30000000-0000-0000-0000-000000000003"),
                    deployment_binding_id=binding.binding_id,
                    mode=BillingMode.METERED,
                    provider_group_id="premium",
                ),
            ),
            capabilities=(ProviderCapability.MODEL_DISCOVERY, ProviderCapability.MODEL_PRICING),
            unresolved_fields=(UnresolvedField(path="subscription.monthly", reason="上游未提供月限制"),),
            evidence=(SafeEvidence(source="official-api", summary="已读取账户计费数据", observed_at=_PARSED_AT),),
            warnings=("月限制未知",),
        ),
        discovered_models=("model-a",),
        issues=(
            ParserIssue(
                parser_id="fixture-parser",
                parser_version="1.2.3",
                stage="quota_discovery",
                category=ParserFailureCategory.INCOMPLETE,
                field_paths=("subscription.monthly",),
                retryable=False,
                next_action="人工确认月限制",
                evidence_summary="上游响应没有月限制字段",
                first_seen_at=_PARSED_AT,
                latest_seen_at=_PARSED_AT,
            ),
        ),
    )


async def test_repository_round_trips_normalized_run_and_export_state(
    parser_repository_fixture: ParserRepositoryFixture,
) -> None:
    repository: Final = parser_repository_fixture.repository
    run: Final = _rich_run(parser_repository_fixture)

    created: Final = await repository.persist(run)
    unchanged: Final = await repository.persist(run)
    loaded: Final = await repository.load_exportable(10)

    assert isinstance(created, ParserRunWriteSuccess)
    assert created.status == "created"
    assert isinstance(unchanged, ParserRunWriteSuccess)
    assert unchanged.status == "unchanged"
    assert isinstance(loaded, ParserRunsLoadSuccess)
    assert tuple(record.run for record in loaded.records) == (run,)

    failed_export: Final = await repository.record_export_attempt(
        run.parser_run_id,
        ParserExportAttempt(
            attempted_at=_PARSED_AT + timedelta(minutes=1),
            failure_code=SnapshotExportFailureCode.LATEST_WRITE_FAILED,
            failure_retryable=True,
        ),
    )
    assert failed_export.status == "updated"
    assert failed_export.export.status == ParserExportStatus.RETRYABLE_FAILURE
    retryable_loaded: Final = await repository.load_exportable(10)
    assert isinstance(retryable_loaded, ParserRunsLoadSuccess)
    assert len(retryable_loaded.records) == 1

    successful_export: Final = await repository.record_export_attempt(
        run.parser_run_id,
        ParserExportAttempt(attempted_at=_PARSED_AT + timedelta(minutes=2)),
    )
    assert successful_export.status == "updated"
    assert successful_export.export.status == ParserExportStatus.SUCCEEDED
    assert successful_export.export.attempt_count == 2
    completed_loaded: Final = await repository.load_exportable(10)
    assert isinstance(completed_loaded, ParserRunsLoadSuccess)
    assert completed_loaded.records == ()


async def test_repository_persists_normalized_child_tables(
    parser_repository_fixture: ParserRepositoryFixture,
) -> None:
    run: Final = _rich_run(parser_repository_fixture)
    assert isinstance(await parser_repository_fixture.repository.persist(run), ParserRunWriteSuccess)

    async with await psycopg.AsyncConnection.connect(parser_repository_fixture.database_url) as connection:
        await connection.execute("SELECT set_config('search_path', %s, true)", (parser_repository_fixture.schema,))
        table_names: Final = (
            "LiteLLM_AccountPoolSubscriptionSnapshot",
            "LiteLLM_AccountPoolQuotaLimit",
            "LiteLLM_AccountPoolMeteredGroup",
            "LiteLLM_AccountPoolMeteredPrice",
            "LiteLLM_AccountPoolBillingRoute",
        )
        counts: Final = (
            await _table_count(connection=connection, table_name=table_names[0]),
            await _table_count(connection=connection, table_name=table_names[1]),
            await _table_count(connection=connection, table_name=table_names[2]),
            await _table_count(connection=connection, table_name=table_names[3]),
            await _table_count(connection=connection, table_name=table_names[4]),
        )

    assert counts == (1, 1, 1, 1, 1)


async def _table_count(connection: psycopg.AsyncConnection[tuple[object, ...]], table_name: str) -> int:
    cursor: Final = await connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table_name)))
    row: Final = await cursor.fetchone()
    assert row is not None
    count: Final = row[0]
    assert isinstance(count, int)
    return count


async def test_concurrent_run_id_is_idempotent(
    parser_repository_fixture: ParserRepositoryFixture,
) -> None:
    first: Final = parser_repository_fixture.repository
    second: Final = PostgresParserRunRepository(
        parser_repository_fixture.database_url,
        schema=parser_repository_fixture.schema,
    )
    run: Final = _rich_run(parser_repository_fixture)

    results: Final = await asyncio.gather(first.persist(run), second.persist(run))

    assert sorted(result.status for result in results) == ["created", "unchanged"]


async def test_same_run_id_with_different_content_is_rejected(
    parser_repository_fixture: ParserRepositoryFixture,
) -> None:
    run: Final = _rich_run(parser_repository_fixture)
    assert isinstance(await parser_repository_fixture.repository.persist(run), ParserRunWriteSuccess)

    conflict: Final = await parser_repository_fixture.repository.persist(
        run.model_copy(update={"parser_version": "different"})
    )

    assert isinstance(conflict, ParserPersistenceFailure)
    assert conflict.code == ParserPersistenceFailureCode.CONTENT_CONFLICT
    assert conflict.retryable is False


async def test_invalid_child_data_rolls_back_parent_transaction(
    parser_repository_fixture: ParserRepositoryFixture,
) -> None:
    run: Final = _rich_run(parser_repository_fixture)
    duplicate_route: Final = run.result.billing_routes[0].model_copy(update={"provider_group_id": "other"})
    invalid: Final = run.model_copy(
        update={
            "result": run.result.model_copy(
                update={"billing_routes": (run.result.billing_routes[0], duplicate_route)}
            )
        }
    )

    result: Final = await parser_repository_fixture.repository.persist(invalid)

    assert isinstance(result, ParserPersistenceFailure)
    assert result.code == ParserPersistenceFailureCode.INVALID_RESULT
    loaded: Final = await parser_repository_fixture.repository.load_exportable(10)
    assert isinstance(loaded, ParserRunsLoadSuccess)
    assert loaded.records == ()


async def test_missing_channel_is_a_permanent_failure(
    parser_repository_fixture: ParserRepositoryFixture,
) -> None:
    run: Final = _rich_run(parser_repository_fixture).model_copy(update={"channel_id": uuid4()})

    result: Final = await parser_repository_fixture.repository.persist(run)

    assert isinstance(result, ParserPersistenceFailure)
    assert result.code == ParserPersistenceFailureCode.CHANNEL_NOT_FOUND
    assert result.retryable is False


async def test_billing_route_must_reference_same_channel_binding(
    parser_repository_fixture: ParserRepositoryFixture,
) -> None:
    run: Final = _rich_run(parser_repository_fixture)
    other_channel_binding: Final = next(
        binding
        for binding in parser_repository_fixture.catalog.bindings
        if binding.channel_id != run.channel_id
    )
    invalid_route: Final = run.result.billing_routes[0].model_copy(
        update={"deployment_binding_id": other_channel_binding.binding_id}
    )
    invalid: Final = run.model_copy(
        update={"result": run.result.model_copy(update={"billing_routes": (invalid_route,)})}
    )

    result: Final = await parser_repository_fixture.repository.persist(invalid)

    assert isinstance(result, ParserPersistenceFailure)
    assert result.code == ParserPersistenceFailureCode.INVALID_RESULT
