"""使用 PostgreSQL 持久化带版本控制的模型策略和候选人工覆盖。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row

from account_pool.models import FrozenModel, Strategy
from account_pool.routing.models import (
    RoutingCandidateMutation,
    RoutingCandidateOverride,
    RoutingFailure,
    RoutingFailureCode,
    RoutingOrderMutation,
    RoutingPolicyResult,
    RoutingPolicyState,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ROUTING_POLICY_LOCK_KEY: Final = 4_186_343_189_064_001_904

_SELECT_POLICY: Final = """
SELECT strategy, version
FROM "LiteLLM_AccountPoolModelPolicy"
WHERE model = %s
"""
_SELECT_POLICY_FOR_UPDATE: Final = f"{_SELECT_POLICY} FOR UPDATE"
_SELECT_OVERRIDES: Final = """
SELECT binding_id, manual_order, weight, paused
FROM "LiteLLM_AccountPoolModelCandidateOverride"
WHERE model = %s
ORDER BY manual_order NULLS LAST, binding_id
"""
_SELECT_BINDINGS_FOR_UPDATE: Final = """
SELECT binding_id
FROM "LiteLLM_AccountPoolBinding"
WHERE public_model = %s
ORDER BY binding_id
FOR UPDATE
"""
_UPSERT_OVERRIDE: Final = """
INSERT INTO "LiteLLM_AccountPoolModelCandidateOverride" (
    model, binding_id, manual_order, weight, paused, created_at, updated_at
) VALUES (%s, %s, NULL, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT (model, binding_id) DO UPDATE SET
    weight = EXCLUDED.weight,
    paused = EXCLUDED.paused,
    updated_at = CURRENT_TIMESTAMP
"""
_CLEAR_MANUAL_ORDER: Final = """
UPDATE "LiteLLM_AccountPoolModelCandidateOverride"
SET manual_order = NULL, updated_at = CURRENT_TIMESTAMP
WHERE model = %s
"""
_UPSERT_ORDER: Final = """
INSERT INTO "LiteLLM_AccountPoolModelCandidateOverride" (
    model, binding_id, manual_order, weight, paused, created_at, updated_at
) VALUES (%s, %s, %s, NULL, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT (model, binding_id) DO UPDATE SET
    manual_order = EXCLUDED.manual_order,
    updated_at = CURRENT_TIMESTAMP
"""


class _PolicyRow(FrozenModel):
    strategy: Strategy
    version: int


class _OverrideRow(FrozenModel):
    binding_id: UUID
    manual_order: int | None
    weight: int | None
    paused: bool


class PostgresRoutingPolicyRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def load(self, model: str) -> RoutingPolicyResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                if not await _model_exists(connection, model):
                    return _failure(RoutingFailureCode.MODEL_NOT_FOUND, retryable=False)
                return await _load_state(connection, model)
        except psycopg.Error:
            return _failure(RoutingFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def update_policy(
        self,
        model: str,
        strategy: Strategy,
        expected_version: int,
    ) -> RoutingPolicyResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                if not await _model_exists(connection, model):
                    return _failure(RoutingFailureCode.MODEL_NOT_FOUND, retryable=False)
                current: Final = await _locked_policy(connection, model)
                conflict: Final = _version_conflict(current=current, expected=expected_version)
                if conflict is not None:
                    return conflict
                next_version: Final = expected_version + 1
                if current is None:
                    await _insert_policy(connection, model, strategy, next_version)
                else:
                    await connection.execute(
                        """
                        UPDATE "LiteLLM_AccountPoolModelPolicy"
                        SET strategy = %s, version = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE model = %s
                        """,
                        (strategy.value, next_version, model),
                    )
                return await _load_state(connection, model)
        except psycopg.Error:
            return _failure(RoutingFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def update_candidate(
        self,
        model: str,
        binding_id: UUID,
        mutation: RoutingCandidateMutation,
    ) -> RoutingPolicyResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                if not await _binding_exists(connection, model, binding_id):
                    return _failure(RoutingFailureCode.BINDING_NOT_FOUND, retryable=False)
                current: Final = await _locked_policy(connection, model)
                conflict: Final = _version_conflict(current=current, expected=mutation.expected_version)
                if conflict is not None:
                    return conflict
                next_version: Final = mutation.expected_version + 1
                if current is None:
                    await _insert_policy(
                        connection,
                        model,
                        Strategy.QUOTA_AWARE_LEAST_INFLIGHT,
                        next_version,
                    )
                await connection.execute(
                    _UPSERT_OVERRIDE,
                    (
                        model,
                        str(binding_id),
                        mutation.weight,
                        mutation.paused,
                    ),
                )
                if current is not None:
                    await _update_version(connection, model, next_version)
                return await _load_state(connection, model)
        except (psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation):
            return _failure(RoutingFailureCode.CANDIDATE_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(RoutingFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def update_order(
        self,
        model: str,
        mutation: RoutingOrderMutation,
    ) -> RoutingPolicyResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                current: Final = await _locked_policy(connection, model)
                binding_ids: Final = await _binding_ids(connection, model)
                if not binding_ids:
                    return _failure(RoutingFailureCode.MODEL_NOT_FOUND, retryable=False)
                if frozenset(binding_ids) != frozenset(mutation.binding_ids):
                    return _failure(RoutingFailureCode.CANDIDATE_CONFLICT, retryable=False)
                conflict: Final = _version_conflict(current=current, expected=mutation.expected_version)
                if conflict is not None:
                    return conflict
                next_version: Final = mutation.expected_version + 1
                if current is None:
                    await _insert_policy(
                        connection,
                        model,
                        Strategy.QUOTA_AWARE_LEAST_INFLIGHT,
                        next_version,
                    )
                else:
                    await _update_version(connection, model, next_version)
                await connection.execute(_CLEAR_MANUAL_ORDER, (model,))
                for manual_order, binding_id in enumerate(mutation.binding_ids):
                    await connection.execute(_UPSERT_ORDER, (model, str(binding_id), manual_order))
                return await _load_state(connection, model)
        except (psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation):
            return _failure(RoutingFailureCode.CANDIDATE_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(RoutingFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def delete_candidate(
        self,
        model: str,
        binding_id: UUID,
        expected_version: int,
    ) -> RoutingPolicyResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                if not await _model_exists(connection, model):
                    return _failure(RoutingFailureCode.MODEL_NOT_FOUND, retryable=False)
                current: Final = await _locked_policy(connection, model)
                conflict: Final = _version_conflict(current=current, expected=expected_version)
                if conflict is not None:
                    return conflict
                if current is None:
                    return await _load_state(connection, model)
                cursor: Final = await connection.execute(
                    """
                    DELETE FROM "LiteLLM_AccountPoolModelCandidateOverride"
                    WHERE model = %s AND binding_id = %s
                    """,
                    (model, str(binding_id)),
                )
                if cursor.rowcount > 0:
                    await _update_version(connection, model, expected_version + 1)
                return await _load_state(connection, model)
        except psycopg.Error:
            return _failure(RoutingFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[Mapping[str, object]]:
        row_factory: Final = cast(AsyncRowFactory[Mapping[str, object]], dict_row)
        return await AsyncConnection[Mapping[str, object]].connect(self._database_url, row_factory=row_factory)

    async def _set_search_path(self, connection: AsyncConnection[Mapping[str, object]]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))


async def _load_state(
    connection: AsyncConnection[Mapping[str, object]],
    model: str,
) -> RoutingPolicyState:
    policy_cursor: Final = await connection.execute(_SELECT_POLICY, (model,))
    policy_value: Final = await policy_cursor.fetchone()
    policy: Final = None if policy_value is None else _PolicyRow.model_validate(policy_value)
    override_cursor: Final = await connection.execute(_SELECT_OVERRIDES, (model,))
    overrides: Final = tuple(_OverrideRow.model_validate(row) for row in await override_cursor.fetchall())
    return RoutingPolicyState(
        model=model,
        strategy=Strategy.QUOTA_AWARE_LEAST_INFLIGHT if policy is None else policy.strategy,
        version=0 if policy is None else policy.version,
        overrides=tuple(
            RoutingCandidateOverride(
                binding_id=row.binding_id,
                manual_order=row.manual_order,
                weight=row.weight,
                paused=row.paused,
            )
            for row in overrides
        ),
    )


async def _locked_policy(
    connection: AsyncConnection[Mapping[str, object]],
    model: str,
) -> _PolicyRow | None:
    await connection.execute("SELECT pg_advisory_xact_lock(%s)", (_ROUTING_POLICY_LOCK_KEY,))
    cursor: Final = await connection.execute(_SELECT_POLICY_FOR_UPDATE, (model,))
    value: Final = await cursor.fetchone()
    return None if value is None else _PolicyRow.model_validate(value)


async def _model_exists(connection: AsyncConnection[Mapping[str, object]], model: str) -> bool:
    cursor: Final = await connection.execute(
        'SELECT 1 FROM "LiteLLM_AccountPoolBinding" WHERE public_model = %s AND enabled = TRUE LIMIT 1',
        (model,),
    )
    return await cursor.fetchone() is not None


async def _binding_exists(
    connection: AsyncConnection[Mapping[str, object]],
    model: str,
    binding_id: UUID,
) -> bool:
    cursor: Final = await connection.execute(
        """
        SELECT 1 FROM "LiteLLM_AccountPoolBinding"
        WHERE public_model = %s AND binding_id = %s AND enabled = TRUE
        LIMIT 1
        """,
        (model, str(binding_id)),
    )
    return await cursor.fetchone() is not None


async def _binding_ids(
    connection: AsyncConnection[Mapping[str, object]],
    model: str,
) -> tuple[UUID, ...]:
    cursor: Final = await connection.execute(_SELECT_BINDINGS_FOR_UPDATE, (model,))
    values: Final = await cursor.fetchall()
    return tuple(UUID(str(row["binding_id"])) for row in values)


async def _insert_policy(
    connection: AsyncConnection[Mapping[str, object]],
    model: str,
    strategy: Strategy,
    version: int,
) -> None:
    await connection.execute(
        """
        INSERT INTO "LiteLLM_AccountPoolModelPolicy" (
            model, policy_order, strategy, version, created_at, updated_at
        ) VALUES (
            %s,
            (SELECT COALESCE(MAX(policy_order), -1) + 1 FROM "LiteLLM_AccountPoolModelPolicy"),
            %s,
            %s,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        """,
        (model, strategy.value, version),
    )


async def _update_version(
    connection: AsyncConnection[Mapping[str, object]],
    model: str,
    version: int,
) -> None:
    await connection.execute(
        """
        UPDATE "LiteLLM_AccountPoolModelPolicy"
        SET version = %s, updated_at = CURRENT_TIMESTAMP
        WHERE model = %s
        """,
        (version, model),
    )


def _version_conflict(current: _PolicyRow | None, expected: int) -> RoutingFailure | None:
    version: Final = 0 if current is None else current.version
    if version == expected:
        return None
    return _failure(RoutingFailureCode.VERSION_CONFLICT, retryable=False, current_version=version)


def _failure(
    code: RoutingFailureCode,
    retryable: bool,
    current_version: int | None = None,
) -> RoutingFailure:
    return RoutingFailure(code=code, retryable=retryable, current_version=current_version)
