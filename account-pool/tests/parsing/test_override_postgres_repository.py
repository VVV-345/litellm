"""验证人工覆盖 PostgreSQL 仓储的迁移、幂等、事件链和并发约束。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.postgres import PostgresCatalogRepository
from account_pool.parsing.models import ParsedChannelData, ParserRun, ParserRunStatus
from account_pool.parsing.overrides.composer import active_override_events
from account_pool.parsing.overrides.models import (
    FieldOverrideEvent,
    MeteredPriceTarget,
    OverrideAction,
    OverrideTarget,
    SubscriptionField,
    SubscriptionFieldTarget,
)
from account_pool.parsing.overrides.postgres import PostgresOverrideEventRepository
from account_pool.parsing.overrides.repository import (
    OverrideEventsLoadSuccess,
    OverridePersistenceFailure,
    OverridePersistenceFailureCode,
    OverrideWriteSuccess,
)
from account_pool.parsing.persistence import ParserRunWriteSuccess
from account_pool.parsing.postgres import PostgresParserRunRepository
from psycopg import sql
from pydantic import JsonValue, TypeAdapter
from tests.catalog.test_importer import legacy_config

_NOW: Final = datetime(2026, 8, 19, 17, 0, tzinfo=UTC)
_FIRST_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000021")
_SECOND_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000022")
_JSON: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)


@dataclass(frozen=True, slots=True)
class OverrideRepositoryFixture:
    database_url: str
    schema: str
    repository: PostgresOverrideEventRepository
    first_channel_id: UUID
    second_channel_id: UUID


def _migration_sql(pattern: str) -> bytes:
    repository_root: Final = Path(__file__).resolve().parents[3]
    migrations_root: Final = repository_root / "litellm-proxy-extras" / "litellm_proxy_extras" / "migrations"
    matches: Final = tuple(migrations_root.glob(f"*_{pattern}/migration.sql"))
    assert len(matches) == 1
    return matches[0].read_bytes()


def _run(parser_run_id: UUID, channel_id: UUID) -> ParserRun:
    return ParserRun(
        parser_run_id=parser_run_id,
        channel_id=channel_id,
        parser_id="fixture-parser",
        parser_version="1.0.0",
        parsed_at=_NOW,
        status=ParserRunStatus.SUCCESS,
        result=ParsedChannelData(),
    )


@pytest_asyncio.fixture
async def override_repository_fixture() -> AsyncIterator[OverrideRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    schema: Final = f"account_pool_override_test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await connection.execute("SELECT set_config('search_path', %s, false)", (schema,))
            await connection.execute(_migration_sql("add_account_pool_catalog"))
            await connection.execute(_migration_sql("add_account_pool_parser_runs"))
            await connection.execute(_migration_sql("add_account_pool_field_overrides"))
            catalog: Final = catalog_import_from_pool_config(legacy_config(), _NOW)
            imported: Final = await PostgresCatalogRepository(database_url, schema=schema).import_once(catalog)
            assert imported.status == "created"
            parser_repository: Final = PostgresParserRunRepository(database_url, schema=schema)
            first_run: Final = await parser_repository.persist(
                _run(_FIRST_RUN_ID, catalog.channels[0].channel_id)
            )
            second_run: Final = await parser_repository.persist(
                _run(_SECOND_RUN_ID, catalog.channels[1].channel_id)
            )
            assert isinstance(first_run, ParserRunWriteSuccess)
            assert isinstance(second_run, ParserRunWriteSuccess)
            yield OverrideRepositoryFixture(
                database_url=database_url,
                schema=schema,
                repository=PostgresOverrideEventRepository(database_url, schema=schema),
                first_channel_id=catalog.channels[0].channel_id,
                second_channel_id=catalog.channels[1].channel_id,
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _event(
    fixture: OverrideRepositoryFixture,
    override_id: UUID,
    value: JsonValue | None,
    *,
    target: OverrideTarget | None = None,
    source_parser_run_id: UUID = _FIRST_RUN_ID,
    action: OverrideAction = OverrideAction.SET,
    had_previous_override: bool = False,
    previous_value: JsonValue | None = None,
    supersedes_override_id: UUID | None = None,
    occurred_at: datetime = _NOW,
) -> FieldOverrideEvent:
    resolved_target: Final = target or SubscriptionFieldTarget(field=SubscriptionField.BALANCE)
    return FieldOverrideEvent(
        override_id=override_id,
        channel_id=fixture.first_channel_id,
        source_parser_run_id=source_parser_run_id,
        target=resolved_target,
        action=action,
        value=value,
        had_previous_override=had_previous_override,
        previous_value=previous_value,
        supersedes_override_id=supersedes_override_id,
        actor_id="admin-user",
        reason="人工核对上游账户数据",
        occurred_at=occurred_at,
    )


async def test_repository_applies_migration_round_trips_and_is_idempotent(
    override_repository_fixture: OverrideRepositoryFixture,
) -> None:
    fixture: Final = override_repository_fixture
    target: Final = MeteredPriceTarget(group_id="standard", provider_model_id="model-a")
    price_value: Final = _JSON.validate_python(
        {
            "provider_model_id": "model-a",
            "currency": "USD",
            "unit": "million_tokens",
            "input_price": "1",
            "group_multiplier": "1",
            "effective_prices": {"input_price": "1"},
        }
    )
    event: Final = _event(fixture, uuid4(), price_value, target=target)

    created: Final = await fixture.repository.append(event)
    unchanged: Final = await fixture.repository.append(event)
    loaded: Final = await fixture.repository.load_for_channel(fixture.first_channel_id)

    assert isinstance(created, OverrideWriteSuccess)
    assert created.status == "created"
    assert isinstance(unchanged, OverrideWriteSuccess)
    assert unchanged.status == "unchanged"
    assert isinstance(loaded, OverrideEventsLoadSuccess)
    assert loaded.events == (event,)


async def test_same_override_id_with_different_content_is_rejected(
    override_repository_fixture: OverrideRepositoryFixture,
) -> None:
    fixture: Final = override_repository_fixture
    event: Final = _event(fixture, uuid4(), "20")
    assert isinstance(await fixture.repository.append(event), OverrideWriteSuccess)

    conflict: Final = await fixture.repository.append(event.model_copy(update={"reason": "不同的人工原因"}))

    assert isinstance(conflict, OverridePersistenceFailure)
    assert conflict.code == OverridePersistenceFailureCode.CONTENT_CONFLICT
    assert conflict.retryable is False


async def test_set_modify_and_revoke_form_an_auditable_chain(
    override_repository_fixture: OverrideRepositoryFixture,
) -> None:
    fixture: Final = override_repository_fixture
    first: Final = _event(fixture, uuid4(), "20")
    modified: Final = _event(
        fixture,
        uuid4(),
        "30",
        had_previous_override=True,
        previous_value="20",
        supersedes_override_id=first.override_id,
        occurred_at=_NOW + timedelta(minutes=1),
    )
    revoked: Final = _event(
        fixture,
        uuid4(),
        None,
        action=OverrideAction.REVOKE,
        had_previous_override=True,
        previous_value="30",
        supersedes_override_id=modified.override_id,
        occurred_at=_NOW + timedelta(minutes=2),
    )

    results: Final = (
        await fixture.repository.append(first),
        await fixture.repository.append(modified),
        await fixture.repository.append(revoked),
    )
    loaded: Final = await fixture.repository.load_for_channel(fixture.first_channel_id)

    assert all(isinstance(result, OverrideWriteSuccess) for result in results)
    assert isinstance(loaded, OverrideEventsLoadSuccess)
    assert loaded.events == (first, modified, revoked)
    assert active_override_events(loaded.events) == ()


async def test_stale_predecessor_is_rejected(
    override_repository_fixture: OverrideRepositoryFixture,
) -> None:
    fixture: Final = override_repository_fixture
    first: Final = _event(fixture, uuid4(), "20")
    assert isinstance(await fixture.repository.append(first), OverrideWriteSuccess)

    stale: Final = await fixture.repository.append(_event(fixture, uuid4(), "30"))

    assert isinstance(stale, OverridePersistenceFailure)
    assert stale.code == OverridePersistenceFailureCode.PREDECESSOR_CONFLICT


async def test_source_parser_run_must_belong_to_event_channel(
    override_repository_fixture: OverrideRepositoryFixture,
) -> None:
    fixture: Final = override_repository_fixture

    result: Final = await fixture.repository.append(
        _event(fixture, uuid4(), "20", source_parser_run_id=_SECOND_RUN_ID)
    )

    assert isinstance(result, OverridePersistenceFailure)
    assert result.code == OverridePersistenceFailureCode.SOURCE_RUN_NOT_FOUND


async def test_concurrent_children_cannot_create_two_chain_heads(
    override_repository_fixture: OverrideRepositoryFixture,
) -> None:
    fixture: Final = override_repository_fixture
    first: Final = _event(fixture, uuid4(), "20")
    assert isinstance(await fixture.repository.append(first), OverrideWriteSuccess)
    first_child: Final = _event(
        fixture,
        uuid4(),
        "30",
        had_previous_override=True,
        previous_value="20",
        supersedes_override_id=first.override_id,
        occurred_at=_NOW + timedelta(minutes=1),
    )
    second_child: Final = first_child.model_copy(
        update={"override_id": uuid4(), "value": "40", "occurred_at": _NOW + timedelta(minutes=2)}
    )
    other_repository: Final = PostgresOverrideEventRepository(
        fixture.database_url,
        schema=fixture.schema,
    )

    outcomes: Final = await asyncio.gather(
        fixture.repository.append(first_child),
        other_repository.append(second_child),
    )
    loaded: Final = await fixture.repository.load_for_channel(fixture.first_channel_id)

    assert sorted(outcome.status for outcome in outcomes) == ["created", "failed"]
    failure: Final = next(outcome for outcome in outcomes if isinstance(outcome, OverridePersistenceFailure))
    assert failure.code == OverridePersistenceFailureCode.PREDECESSOR_CONFLICT
    assert isinstance(loaded, OverrideEventsLoadSuccess)
    assert len(active_override_events(loaded.events)) == 1
