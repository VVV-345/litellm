"""验证 PostgreSQL 模型策略仓储的版本控制、候选归属和人工顺序约束。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.postgres import PostgresCatalogRepository
from account_pool.models import Strategy
from account_pool.routing.models import (
    RoutingCandidateMutation,
    RoutingFailure,
    RoutingFailureCode,
    RoutingOrderMutation,
    RoutingPolicyState,
)
from account_pool.routing.postgres import PostgresRoutingPolicyRepository
from psycopg import sql
from tests.catalog.test_importer import legacy_config


@dataclass(frozen=True, slots=True)
class RoutingRepositoryFixture:
    repository: PostgresRoutingPolicyRepository
    database_url: str
    schema: str
    model: str
    binding_ids: tuple[UUID, UUID]


def _migration_sql(pattern: str) -> bytes:
    repository_root: Final = Path(__file__).resolve().parents[3]
    migrations_root: Final = repository_root / "litellm-proxy-extras" / "litellm_proxy_extras" / "migrations"
    matches: Final = tuple(migrations_root.glob(f"*_{pattern}/migration.sql"))
    assert len(matches) == 1
    return matches[0].read_bytes()


@pytest_asyncio.fixture
async def routing_repository_fixture() -> AsyncIterator[RoutingRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    schema: Final = f"account_pool_routing_test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await connection.execute("SELECT set_config('search_path', %s, false)", (schema,))
            await connection.execute(_migration_sql("add_account_pool_catalog"))
            await connection.execute(_migration_sql("add_account_pool_routing_policy"))
            catalog: Final = catalog_import_from_pool_config(
                legacy_config(),
                datetime(2026, 8, 20, 5, 0, tzinfo=UTC),
            )
            imported: Final = await PostgresCatalogRepository(database_url, schema=schema).import_once(catalog)
            assert imported.status == "created"
            model: Final = catalog.bindings[0].public_model
            matching: Final = tuple(binding.binding_id for binding in catalog.bindings if binding.public_model == model)
            assert len(matching) >= 2
            yield RoutingRepositoryFixture(
                repository=PostgresRoutingPolicyRepository(database_url, schema=schema),
                database_url=database_url,
                schema=schema,
                model=model,
                binding_ids=(matching[0], matching[1]),
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_policy_and_candidate_updates_increment_version(
    routing_repository_fixture: RoutingRepositoryFixture,
) -> None:
    fixture: Final = routing_repository_fixture
    initial: Final = await fixture.repository.load(fixture.model)
    assert isinstance(initial, RoutingPolicyState)

    policy: Final = await fixture.repository.update_policy(fixture.model, Strategy.LOWEST_LATENCY, initial.version)
    assert isinstance(policy, RoutingPolicyState)
    candidate: Final = await fixture.repository.update_candidate(
        fixture.model,
        fixture.binding_ids[0],
        RoutingCandidateMutation(expected_version=policy.version, weight=9, paused=True),
    )

    assert isinstance(candidate, RoutingPolicyState)
    assert candidate.version == policy.version + 1
    assert candidate.overrides[0].binding_id == fixture.binding_ids[0]
    assert (candidate.overrides[0].manual_order, candidate.overrides[0].weight, candidate.overrides[0].paused) == (None, 9, True)


async def test_stale_version_and_incomplete_drag_order_are_rejected(
    routing_repository_fixture: RoutingRepositoryFixture,
) -> None:
    fixture: Final = routing_repository_fixture
    initial: Final = await fixture.repository.load(fixture.model)
    assert isinstance(initial, RoutingPolicyState)
    first: Final = await fixture.repository.update_order(
        fixture.model,
        RoutingOrderMutation(expected_version=initial.version, binding_ids=fixture.binding_ids),
    )
    assert isinstance(first, RoutingPolicyState)
    assert tuple(override.manual_order for override in first.overrides) == (0, 1)

    stale: Final = await fixture.repository.update_policy(fixture.model, Strategy.RANDOM, initial.version)
    incomplete: Final = await fixture.repository.update_order(
        fixture.model,
        RoutingOrderMutation(expected_version=first.version, binding_ids=(fixture.binding_ids[0],)),
    )

    assert isinstance(stale, RoutingFailure)
    assert (stale.code, stale.current_version) == (RoutingFailureCode.VERSION_CONFLICT, first.version)
    assert isinstance(incomplete, RoutingFailure)
    assert incomplete.code == RoutingFailureCode.CANDIDATE_CONFLICT


async def test_drag_order_accepts_disabled_bindings(
    routing_repository_fixture: RoutingRepositoryFixture,
) -> None:
    fixture: Final = routing_repository_fixture
    async with await psycopg.AsyncConnection.connect(fixture.database_url) as connection:
        await connection.execute("SELECT set_config('search_path', %s, false)", (fixture.schema,))
        await connection.execute(
            'UPDATE "LiteLLM_AccountPoolBinding" SET enabled = FALSE WHERE binding_id = %s',
            (str(fixture.binding_ids[1]),),
        )
    initial: Final = await fixture.repository.load(fixture.model)
    assert isinstance(initial, RoutingPolicyState)

    ordered: Final = await fixture.repository.update_order(
        fixture.model,
        RoutingOrderMutation(expected_version=initial.version, binding_ids=tuple(reversed(fixture.binding_ids))),
    )

    assert isinstance(ordered, RoutingPolicyState)
    assert tuple(override.binding_id for override in ordered.overrides) == tuple(reversed(fixture.binding_ids))


async def test_candidate_delete_and_wrong_binding_are_safe(
    routing_repository_fixture: RoutingRepositoryFixture,
) -> None:
    fixture: Final = routing_repository_fixture
    initial: Final = await fixture.repository.load(fixture.model)
    assert isinstance(initial, RoutingPolicyState)
    created: Final = await fixture.repository.update_candidate(
        fixture.model,
        fixture.binding_ids[0],
        RoutingCandidateMutation(expected_version=initial.version, weight=5),
    )
    assert isinstance(created, RoutingPolicyState)

    deleted: Final = await fixture.repository.delete_candidate(
        fixture.model,
        fixture.binding_ids[0],
        created.version,
    )
    wrong: Final = await fixture.repository.update_candidate(
        fixture.model,
        uuid4(),
        RoutingCandidateMutation(expected_version=deleted.version if isinstance(deleted, RoutingPolicyState) else 0),
    )

    assert isinstance(deleted, RoutingPolicyState)
    assert deleted.overrides == ()
    assert isinstance(wrong, RoutingFailure)
    assert wrong.code == RoutingFailureCode.BINDING_NOT_FOUND


@pytest.mark.parametrize("schema", ["", "public; DROP SCHEMA public", "has-dash", "1starts_with_digit"])
def test_repository_rejects_unsafe_schema(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        PostgresRoutingPolicyRepository("postgresql://localhost/test", schema=schema)
