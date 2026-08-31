"""验证 PostgreSQL 渠道目录仓储的导入幂等性、冲突和并发行为。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.lifecycle import ApplyChannelDelete, CatalogApplySuccess, DeleteMode
from account_pool.catalog.models import CatalogImport, CatalogSnapshot, ImportResult
from account_pool.catalog.postgres import PostgresCatalogRepository
from account_pool.models import Strategy
from psycopg import sql
from psycopg.types.json import Jsonb
from tests.catalog.test_importer import legacy_config


@dataclass(frozen=True, slots=True)
class RepositoryFixture:
    database_url: str
    schema: str
    repository: PostgresCatalogRepository


def _migration_sql(pattern: str) -> str:
    repository_root: Final = Path(__file__).resolve().parents[3]
    migrations_root: Final = repository_root / "litellm-proxy-extras" / "litellm_proxy_extras" / "migrations"
    matches: Final = tuple(migrations_root.glob(f"*_{pattern}/migration.sql"))
    assert len(matches) == 1
    return matches[0].read_text(encoding="utf-8")


@pytest_asyncio.fixture
async def repository_fixture() -> AsyncIterator[RepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    schema: Final = f"account_pool_test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await connection.execute("SELECT set_config('search_path', %s, false)", (schema,))
            await connection.execute(_migration_sql("add_account_pool_catalog").encode("utf-8"))
            await connection.execute(_migration_sql("add_account_pool_parser_tasks").encode("utf-8"))
            await connection.execute(_migration_sql("add_account_pool_sync_and_audit").encode("utf-8"))
            await connection.execute(
                _migration_sql("decouple_account_pool_upstream_and_parser_provider").encode("utf-8")
            )
            await connection.execute(_migration_sql("persist_account_pool_model_discovery_provider").encode("utf-8"))
            await connection.execute(_migration_sql("add_account_pool_routing_policy").encode("utf-8"))
            await connection.execute(_migration_sql("cascade_account_pool_parser_task_delete").encode("utf-8"))
            yield RepositoryFixture(
                database_url=database_url,
                schema=schema,
                repository=PostgresCatalogRepository(database_url, schema=schema),
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _command(imported_at: datetime | None = None) -> CatalogImport:
    timestamp: Final = imported_at or datetime(2026, 8, 19, 4, 0, tzinfo=UTC)
    return catalog_import_from_pool_config(legacy_config(), timestamp)


async def test_load_snapshot_is_empty_after_migration(repository_fixture: RepositoryFixture) -> None:
    assert await repository_fixture.repository.load_snapshot() == CatalogSnapshot()


def test_priority_migration_normalizes_history_and_adds_constraint() -> None:
    migration: Final = _migration_sql("constrain_account_pool_channel_priority")

    assert 'UPDATE "LiteLLM_AccountPoolChannel"' in migration
    assert 'UPDATE "LiteLLM_AccountPoolSyncOperation"' in migration
    assert '"desired_payload"' in migration
    assert "~ '^-?[0-9]+([.][0-9]+)?$'" in migration
    assert "::INTEGER" not in migration
    assert 'CHECK ("priority" IN (100, 200, 300, 400))' in migration


def test_parser_provider_migration_adds_an_independent_nullable_column() -> None:
    migration: Final = _migration_sql("decouple_account_pool_upstream_and_parser_provider")

    assert 'ADD COLUMN "parser_provider_id" TEXT' in migration


def test_model_discovery_provider_migration_adds_an_independent_nullable_column() -> None:
    migration: Final = _migration_sql("persist_account_pool_model_discovery_provider")

    assert 'ADD COLUMN "model_discovery_provider_id" TEXT' in migration


async def test_priority_migration_normalizes_rows_and_rejects_new_arbitrary_values(
    repository_fixture: RepositoryFixture,
) -> None:
    async with await psycopg.AsyncConnection.connect(repository_fixture.database_url, autocommit=True) as connection:
        await connection.execute("SELECT set_config('search_path', %s, false)", (repository_fixture.schema,))
        for index, priority in enumerate((450, 350, 250, 150)):
            await connection.execute(
                """
                INSERT INTO "LiteLLM_AccountPoolChannel" (
                    channel_id, account_order, display_name, provider, base_url_display,
                    administrative_state, max_concurrency, priority, weight, quota_unit, updated_at
                ) VALUES (%s, %s, %s, 'openai', 'https://example.test/v1', 'enabled', 1, %s, 1, 'tokens', NOW())
                """,
                (f"channel-{index}", index, f"Channel {index}", priority),
            )
        for index, priority in enumerate((450, "350", None, "invalid")):
            await connection.execute(
                """
                INSERT INTO "LiteLLM_AccountPoolSyncOperation" (
                    operation_id, idempotency_key, channel_id, action, status,
                    desired_payload, updated_at, applied_at
                ) VALUES (%s, %s, %s, 'create_channel', 'applied', %s, NOW(), NOW())
                """,
                (
                    f"operation-{index}",
                    f"key-{index}",
                    f"channel-{index}",
                    Jsonb({"priority": priority}),
                ),
            )

        await connection.execute(_migration_sql("constrain_account_pool_channel_priority").encode("utf-8"))
        channel_rows: Final = await (
            await connection.execute('SELECT "priority" FROM "LiteLLM_AccountPoolChannel" ORDER BY "account_order"')
        ).fetchall()
        operation_rows: Final = await (
            await connection.execute(
                'SELECT "desired_payload"->>\'priority\' FROM "LiteLLM_AccountPoolSyncOperation" ORDER BY "operation_id"'
            )
        ).fetchall()

        assert tuple(row[0] for row in channel_rows) == (400, 300, 200, 100)
        assert tuple(row[0] for row in operation_rows) == ("400", "300", "100", "100")
        with pytest.raises(psycopg.errors.CheckViolation):
            await connection.execute(
                """
                INSERT INTO "LiteLLM_AccountPoolChannel" (
                    channel_id, account_order, display_name, provider, base_url_display,
                    administrative_state, max_concurrency, priority, weight, quota_unit, updated_at
                ) VALUES ('invalid-priority', 10, 'Invalid', 'openai', 'https://example.test/v1',
                    'enabled', 1, 250, 1, 'tokens', NOW())
                """
            )


async def test_first_import_is_created_and_timestamp_only_rerun_is_unchanged(
    repository_fixture: RepositoryFixture,
) -> None:
    command: Final = _command()

    created: Final = await repository_fixture.repository.import_once(command)
    unchanged: Final = await repository_fixture.repository.import_once(
        _command(command.channels[0].created_at + timedelta(hours=1))
    )

    assert created.status == "created"
    assert (created.created_channels, created.created_bindings, created.created_policies) == (2, 3, 2)
    assert unchanged.status == "unchanged"
    assert (unchanged.created_channels, unchanged.created_bindings, unchanged.created_policies) == (0, 0, 0)
    assert await repository_fixture.repository.load_snapshot() == CatalogSnapshot(
        channels=command.channels,
        bindings=command.bindings,
        policies=command.policies,
    )


async def test_model_discovery_provider_id_round_trips_through_postgres(
    repository_fixture: RepositoryFixture,
) -> None:
    original: Final = _command().channels[0]
    channel: Final = original.model_copy(update={"model_discovery_provider_id": "openai_compatible"})
    command: Final = CatalogImport(channels=(channel,), bindings=(), policies=())

    result: Final = await repository_fixture.repository.import_once(command)

    assert result.status == "created"
    assert (await repository_fixture.repository.load_snapshot()).channels == (channel,)


async def test_channel_delete_cascades_parser_task(repository_fixture: RepositoryFixture) -> None:
    imported: Final = _command()
    channel: Final = imported.channels[0]
    bindings: Final = tuple(binding for binding in imported.bindings if binding.channel_id == channel.channel_id)
    operation_id: Final = uuid4()
    await repository_fixture.repository.import_once(CatalogImport(channels=(channel,), bindings=bindings, policies=()))
    async with await psycopg.AsyncConnection.connect(repository_fixture.database_url, autocommit=True) as connection:
        await connection.execute("SELECT set_config('search_path', %s, false)", (repository_fixture.schema,))
        await connection.execute(
            """
            INSERT INTO "LiteLLM_AccountPoolSyncOperation" (
                operation_id, idempotency_key, channel_id, action, status, delete_mode,
                desired_payload, updated_at
            ) VALUES (%s, %s, %s, 'delete_channel', 'pending_delete', 'delete_managed_deployment', %s, NOW())
            """,
            (str(operation_id), f"delete-{operation_id}", str(channel.channel_id), Jsonb({})),
        )
        await connection.execute(
            """
            INSERT INTO "LiteLLM_AccountPoolParserTask" (
                task_id, channel_id, parser_run_id, provider_id, status, owner_instance_id,
                actor_id, actor_role, request_id, created_at, heartbeat_at
            ) VALUES (%s, %s, %s, 'openai', 'pending', 'instance', 'actor', 'admin', 'request', NOW(), NOW())
            """,
            ("parser-task", str(channel.channel_id), "parser-run"),
        )

    result: Final = await repository_fixture.repository.apply_lifecycle(
        ApplyChannelDelete(
            operation_id=operation_id,
            channel_id=channel.channel_id,
            mode=DeleteMode.DELETE_MANAGED_DEPLOYMENT,
            bindings=bindings,
        )
    )

    async with await psycopg.AsyncConnection.connect(repository_fixture.database_url, autocommit=True) as connection:
        await connection.execute("SELECT set_config('search_path', %s, false)", (repository_fixture.schema,))
        parser_task_count: Final = await (
            await connection.execute('SELECT COUNT(*) FROM "LiteLLM_AccountPoolParserTask"')
        ).fetchone()

    assert result == CatalogApplySuccess(operation_id=operation_id)
    assert parser_task_count == (0,)
    assert (await repository_fixture.repository.load_snapshot()).channels == ()


@pytest.mark.parametrize("alternate_identity", ["channel_id", "legacy_account_id"])
async def test_channel_identity_conflict_leaves_catalog_unchanged(
    repository_fixture: RepositoryFixture,
    alternate_identity: str,
) -> None:
    original: Final = _command()
    await repository_fixture.repository.import_once(original)
    first: Final = original.channels[0]
    conflicting_channel: Final = first.model_copy(
        update={
            "channel_id": uuid4() if alternate_identity == "legacy_account_id" else first.channel_id,
            "legacy_account_id": (
                "different-legacy-id" if alternate_identity == "channel_id" else first.legacy_account_id
            ),
            "display_name": "Conflicting channel",
        }
    )
    conflict_command: Final = CatalogImport(channels=(conflicting_channel,), bindings=(), policies=())

    result: Final = await repository_fixture.repository.import_once(conflict_command)

    assert result.status == "conflict"
    assert tuple(conflict.entity for conflict in result.conflicts) == ("channel",)
    assert await repository_fixture.repository.load_snapshot() == CatalogSnapshot(
        channels=original.channels,
        bindings=original.bindings,
        policies=original.policies,
    )


@pytest.mark.parametrize("alternate_identity", ["binding_id", "litellm_deployment_id"])
async def test_binding_identity_conflict_leaves_catalog_unchanged(
    repository_fixture: RepositoryFixture,
    alternate_identity: str,
) -> None:
    original: Final = _command()
    await repository_fixture.repository.import_once(original)
    first: Final = original.bindings[0]
    conflicting_binding: Final = first.model_copy(
        update={
            "binding_id": uuid4() if alternate_identity == "litellm_deployment_id" else first.binding_id,
            "litellm_deployment_id": (
                "different-deployment-id" if alternate_identity == "binding_id" else first.litellm_deployment_id
            ),
            "provider_model": "conflicting/provider-model",
        }
    )
    conflict_command: Final = CatalogImport(channels=(), bindings=(conflicting_binding,), policies=())

    result: Final = await repository_fixture.repository.import_once(conflict_command)

    assert result.status == "conflict"
    assert tuple(conflict.entity for conflict in result.conflicts) == ("binding",)
    assert await repository_fixture.repository.load_snapshot() == CatalogSnapshot(
        channels=original.channels,
        bindings=original.bindings,
        policies=original.policies,
    )


async def test_policy_conflict_rolls_back_channels_and_bindings(
    repository_fixture: RepositoryFixture,
) -> None:
    original: Final = _command()
    initial_policy: Final = original.policies[0]
    initial: Final = CatalogImport(channels=(), bindings=(), policies=(initial_policy,))
    await repository_fixture.repository.import_once(initial)
    conflicting_policy: Final = initial_policy.model_copy(update={"strategy": Strategy.WEIGHTED_ROUND_ROBIN})
    conflict_command: Final = CatalogImport(
        channels=original.channels,
        bindings=original.bindings,
        policies=(conflicting_policy,),
    )

    result: Final = await repository_fixture.repository.import_once(conflict_command)

    assert result.status == "conflict"
    assert tuple(conflict.entity for conflict in result.conflicts) == ("policy",)
    assert await repository_fixture.repository.load_snapshot() == CatalogSnapshot(policies=(initial_policy,))


async def test_snapshot_uses_persisted_order_columns(repository_fixture: RepositoryFixture) -> None:
    ordered: Final = _command()
    shuffled: Final = CatalogImport(
        channels=tuple(reversed(ordered.channels)),
        bindings=tuple(reversed(ordered.bindings)),
        policies=tuple(reversed(ordered.policies)),
    )
    await repository_fixture.repository.import_once(shuffled)

    snapshot: Final = await repository_fixture.repository.load_snapshot()

    assert snapshot.channels == ordered.channels
    assert snapshot.bindings == ordered.bindings
    assert snapshot.policies == ordered.policies


async def _release_together(
    repository: PostgresCatalogRepository,
    command: CatalogImport,
    ready: asyncio.Event,
    start: asyncio.Event,
) -> ImportResult:
    ready.set()
    await start.wait()
    return await repository.import_once(command)


async def test_concurrent_identical_imports_create_one_catalog(
    repository_fixture: RepositoryFixture,
) -> None:
    first_repository: Final = repository_fixture.repository
    second_repository: Final = PostgresCatalogRepository(
        repository_fixture.database_url,
        schema=repository_fixture.schema,
    )
    command: Final = _command()
    first_ready: Final = asyncio.Event()
    second_ready: Final = asyncio.Event()
    start: Final = asyncio.Event()
    first_task: Final = asyncio.create_task(_release_together(first_repository, command, first_ready, start))
    second_task: Final = asyncio.create_task(_release_together(second_repository, command, second_ready, start))
    await first_ready.wait()
    await second_ready.wait()
    start.set()

    results: Final = await asyncio.gather(first_task, second_task)

    assert sorted(result.status for result in results) == ["created", "unchanged"]
    assert await first_repository.load_snapshot() == CatalogSnapshot(
        channels=command.channels,
        bindings=command.bindings,
        policies=command.policies,
    )


async def test_concurrent_conflicting_imports_return_created_and_conflict(
    repository_fixture: RepositoryFixture,
) -> None:
    first_repository: Final = repository_fixture.repository
    second_repository: Final = PostgresCatalogRepository(
        repository_fixture.database_url,
        schema=repository_fixture.schema,
    )
    first_command: Final = _command()
    second_command: Final = first_command.model_copy(
        update={
            "channels": (
                first_command.channels[0].model_copy(update={"display_name": "Concurrent conflict"}),
                *first_command.channels[1:],
            )
        }
    )
    first_ready: Final = asyncio.Event()
    second_ready: Final = asyncio.Event()
    start: Final = asyncio.Event()
    first_task: Final = asyncio.create_task(_release_together(first_repository, first_command, first_ready, start))
    second_task: Final = asyncio.create_task(_release_together(second_repository, second_command, second_ready, start))
    await first_ready.wait()
    await second_ready.wait()
    start.set()

    results: Final = await asyncio.gather(first_task, second_task)
    snapshot: Final = await first_repository.load_snapshot()

    assert sorted(result.status for result in results) == ["conflict", "created"]
    assert snapshot in (
        CatalogSnapshot(
            channels=first_command.channels,
            bindings=first_command.bindings,
            policies=first_command.policies,
        ),
        CatalogSnapshot(
            channels=second_command.channels,
            bindings=second_command.bindings,
            policies=second_command.policies,
        ),
    )


@pytest.mark.parametrize("schema", ["", "public; DROP SCHEMA public", "has-dash", "1starts_with_digit"])
def test_repository_rejects_unsafe_schema(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        PostgresCatalogRepository("postgresql://localhost/test", schema=schema)
