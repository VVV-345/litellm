"""编排解析运行的 PostgreSQL 事务、幂等写入和导出状态更新。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Final, LiteralString, TypeVar, cast
from uuid import UUID, uuid5

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from pydantic import TypeAdapter, ValidationError

from account_pool.domain.provider_source import ProviderCapability
from account_pool.models import FrozenModel
from account_pool.parsing.models import (
    BillingRoute,
    ConcurrencyLimit,
    MeteredGroup,
    MeteredModelPrice,
    ModelIdentity,
    ParserIssue,
    ParserRun,
    SafeEvidence,
    UnresolvedField,
)
from account_pool.parsing.persistence import (
    ParserExportAttempt,
    ParserExportStatus,
    ParserExportUpdateResult,
    ParserExportUpdateSuccess,
    ParserPersistenceFailure,
    ParserPersistenceFailureCode,
    ParserRunsLoadResult,
    ParserRunsLoadSuccess,
    ParserRunWriteResult,
    ParserRunWriteSuccess,
    PersistedParserRun,
)
from account_pool.parsing.postgres.codec import decode_export_state, decode_record
from account_pool.parsing.postgres.statements import (
    INSERT_GROUP,
    INSERT_LIMIT,
    INSERT_PRICE,
    INSERT_ROUTE,
    INSERT_RUN,
    INSERT_SUBSCRIPTION,
    SELECT_EXPORTABLE,
    SELECT_GROUPS,
    SELECT_LIMITS,
    SELECT_PRICES,
    SELECT_ROUTES,
    SELECT_RUN,
    SELECT_SUBSCRIPTION,
    UPDATE_EXPORT,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODEL_IDENTITIES: Final = TypeAdapter(tuple[ModelIdentity, ...])
_CONCURRENCY_LIMITS: Final = TypeAdapter(tuple[ConcurrencyLimit, ...])
_CAPABILITIES: Final = TypeAdapter(tuple[ProviderCapability, ...])
_UNRESOLVED_FIELDS: Final = TypeAdapter(tuple[UnresolvedField, ...])
_EVIDENCE: Final = TypeAdapter(tuple[SafeEvidence, ...])
_STRINGS: Final = TypeAdapter(tuple[str, ...])
_ISSUES: Final = TypeAdapter(tuple[ParserIssue, ...])
_JsonValue = TypeVar("_JsonValue")


class _RunIdRow(FrozenModel):
    parser_run_id: UUID


class _BindingIdRow(FrozenModel):
    binding_id: UUID


class PostgresParserRunRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def persist(self, run: ParserRun) -> ParserRunWriteResult:
        content_hash: Final = _content_hash(run)
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                # 同一运行 ID 在多 Worker 间串行处理，避免并发重试产生半套子表数据。
                await connection.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_key(run.parser_run_id),))
                existing: Final = await _load_record(connection=connection, parser_run_id=run.parser_run_id)
                if existing is not None:
                    if existing.content_hash != content_hash:
                        return _failure(ParserPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
                    return ParserRunWriteSuccess(status="unchanged", record=existing)
                channel_cursor: Final = await connection.execute(
                    'SELECT 1 FROM "LiteLLM_AccountPoolChannel" WHERE channel_id = %s',
                    (str(run.channel_id),),
                )
                if await channel_cursor.fetchone() is None:
                    return _failure(ParserPersistenceFailureCode.CHANNEL_NOT_FOUND, retryable=False)
                routes_valid: Final = await _routes_are_valid(
                    connection=connection,
                    channel_id=run.channel_id,
                    routes=run.result.billing_routes,
                )
                if not routes_valid:
                    return _failure(ParserPersistenceFailureCode.INVALID_RESULT, retryable=False)
                await _insert_run(connection=connection, run=run, content_hash=content_hash)
                return ParserRunWriteSuccess(
                    status="created",
                    record=PersistedParserRun(run=run, content_hash=content_hash),
                )
        except (ValidationError, ValueError):
            return _failure(ParserPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except (psycopg.DataError, psycopg.IntegrityError):
            return _failure(ParserPersistenceFailureCode.INVALID_RESULT, retryable=False)
        except psycopg.Error:
            return _failure(ParserPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def load_exportable(self, limit: int) -> ParserRunsLoadResult:
        if limit < 1 or limit > 100:
            return _failure(ParserPersistenceFailureCode.INVALID_REQUEST, retryable=False)
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                rows: Final = await _fetch_all(connection, SELECT_EXPORTABLE, (limit,))
                run_ids: Final = tuple(_RunIdRow.model_validate(row).parser_run_id for row in rows)
                records: Final = await _load_records(connection=connection, parser_run_ids=run_ids)
                if records is None:
                    return _failure(ParserPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
                return ParserRunsLoadSuccess(records=records)
        except (ValidationError, ValueError):
            return _failure(ParserPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(ParserPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def record_export_attempt(
        self,
        parser_run_id: UUID,
        attempt: ParserExportAttempt,
    ) -> ParserExportUpdateResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    """
                    SELECT export_status, export_attempt_count, export_last_attempt_at,
                           exported_at, export_failure_code, export_failure_retryable
                    FROM "LiteLLM_AccountPoolParserRun"
                    WHERE parser_run_id = %s
                    FOR UPDATE
                    """,
                    (str(parser_run_id),),
                )
                raw_row: Final = await cursor.fetchone()
                if raw_row is None:
                    return _failure(ParserPersistenceFailureCode.RUN_NOT_FOUND, retryable=False)
                current: Final = decode_export_state(cast(object, raw_row))
                if current.status in (ParserExportStatus.SUCCEEDED, ParserExportStatus.PERMANENT_FAILURE):
                    return ParserExportUpdateSuccess(parser_run_id=parser_run_id, export=current)
                updated: Final = attempt.next_state(current.attempt_count)
                await connection.execute(
                    UPDATE_EXPORT,
                    (
                        updated.status,
                        updated.attempt_count,
                        updated.last_attempt_at,
                        updated.exported_at,
                        updated.failure_code,
                        updated.failure_retryable,
                        str(parser_run_id),
                    ),
                )
                return ParserExportUpdateSuccess(parser_run_id=parser_run_id, export=updated)
        except ValidationError:
            return _failure(ParserPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(ParserPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[Mapping[str, object]]:
        row_factory: Final = cast(AsyncRowFactory[Mapping[str, object]], dict_row)
        return await AsyncConnection[Mapping[str, object]].connect(
            self._database_url,
            row_factory=row_factory,
        )

    async def _set_search_path(self, connection: AsyncConnection[Mapping[str, object]]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))


def _failure(code: ParserPersistenceFailureCode, retryable: bool) -> ParserPersistenceFailure:
    return ParserPersistenceFailure(code=code, retryable=retryable)


def _content_hash(run: ParserRun) -> str:
    return sha256(run.model_dump_json().encode("utf-8")).hexdigest()


def _lock_key(parser_run_id: UUID) -> int:
    return int.from_bytes(parser_run_id.bytes[:8], byteorder="big", signed=True)


def _json(adapter: TypeAdapter[_JsonValue], value: _JsonValue) -> str:
    return adapter.dump_json(value).decode("utf-8")


async def _routes_are_valid(
    connection: AsyncConnection[Mapping[str, object]],
    channel_id: UUID,
    routes: tuple[BillingRoute, ...],
) -> bool:
    if not routes:
        return True
    cursor: Final = await connection.execute(
        """
        SELECT binding_id
        FROM "LiteLLM_AccountPoolBinding"
        WHERE channel_id = %s
        """,
        (str(channel_id),),
    )
    rows: Final = tuple(cast(object, row) for row in await cursor.fetchall())
    binding_ids: Final = frozenset(_BindingIdRow.model_validate(row).binding_id for row in rows)
    return all(route.deployment_binding_id in binding_ids for route in routes)


async def _insert_run(
    connection: AsyncConnection[Mapping[str, object]],
    run: ParserRun,
    content_hash: str,
) -> None:
    result: Final = run.result
    await connection.execute(
        INSERT_RUN,
        (
            str(run.parser_run_id),
            str(run.channel_id),
            run.parser_id,
            run.parser_version,
            run.parsed_at,
            run.status,
            content_hash,
            _json(_STRINGS, run.discovered_models),
            _json(_CAPABILITIES, result.capabilities),
            _json(_UNRESOLVED_FIELDS, result.unresolved_fields),
            _json(_EVIDENCE, result.evidence),
            _json(_STRINGS, result.warnings),
            _json(_ISSUES, run.issues),
            result.metered is not None,
        ),
    )
    await _insert_subscription(connection=connection, run=run)
    await _insert_metered(connection=connection, run=run)
    await _insert_routes(connection=connection, run=run)


async def _insert_subscription(
    connection: AsyncConnection[Mapping[str, object]],
    run: ParserRun,
) -> None:
    subscription: Final = run.result.subscription
    if subscription is None:
        return
    await connection.execute(
        INSERT_SUBSCRIPTION,
        (
            str(uuid5(run.parser_run_id, "subscription")),
            str(run.parser_run_id),
            subscription.plan_id,
            subscription.plan_name,
            subscription.status,
            subscription.starts_at,
            subscription.expires_at,
            _json(_MODEL_IDENTITIES, subscription.models),
            subscription.balance,
            subscription.currency,
            subscription.channel_concurrency,
            _json(_CONCURRENCY_LIMITS, subscription.model_concurrency),
        ),
    )
    for limit_order, limit in enumerate(subscription.limits):
        await connection.execute(
            INSERT_LIMIT,
            (
                str(uuid5(run.parser_run_id, f"quota:{limit_order}")),
                str(run.parser_run_id),
                limit_order,
                limit.scope,
                limit.subject_id,
                limit.kind,
                limit.window_type,
                limit.duration_seconds,
                limit.limit,
                limit.used,
                limit.remaining,
                limit.reset_at,
                limit.source,
                limit.observed_at,
            ),
        )


async def _insert_metered(
    connection: AsyncConnection[Mapping[str, object]],
    run: ParserRun,
) -> None:
    metered: Final = run.result.metered
    if metered is None:
        return
    for group_order, group in enumerate(metered.groups):
        await _insert_group(
            connection=connection,
            parser_run_id=run.parser_run_id,
            group_order=group_order,
            group=group,
        )


async def _insert_group(
    connection: AsyncConnection[Mapping[str, object]],
    parser_run_id: UUID,
    group_order: int,
    group: MeteredGroup,
) -> None:
    group_row_id: Final = uuid5(parser_run_id, f"metered-group:{group_order}")
    await connection.execute(
        INSERT_GROUP,
        (
            str(group_row_id),
            str(parser_run_id),
            group_order,
            group.group_id,
            group.group_name,
            group.concurrency,
        ),
    )
    for price_order, price in enumerate(group.models):
        await _insert_price(
            connection=connection,
            parser_run_id=parser_run_id,
            group_row_id=group_row_id,
            group_order=group_order,
            price_order=price_order,
            price=price,
        )


async def _insert_price(
    connection: AsyncConnection[Mapping[str, object]],
    parser_run_id: UUID,
    group_row_id: UUID,
    group_order: int,
    price_order: int,
    price: MeteredModelPrice,
) -> None:
    normalized: Final = price.normalized_per_million_tokens
    await connection.execute(
        INSERT_PRICE,
        (
            str(uuid5(parser_run_id, f"metered-price:{group_order}:{price_order}")),
            str(group_row_id),
            price_order,
            price.provider_model_id,
            price.litellm_model_name,
            price.public_model_name,
            price.currency,
            price.unit,
            price.input_price,
            price.output_price,
            price.cache_read_price,
            price.cache_write_price,
            price.group_multiplier,
            price.price_calculation,
            price.conversion_note,
            price.effective_prices.input_price,
            price.effective_prices.output_price,
            price.effective_prices.cache_read_price,
            price.effective_prices.cache_write_price,
            None if normalized is None else normalized.input_price,
            None if normalized is None else normalized.output_price,
            None if normalized is None else normalized.cache_read_price,
            None if normalized is None else normalized.cache_write_price,
            normalized is not None,
            price.concurrency,
        ),
    )


async def _insert_routes(
    connection: AsyncConnection[Mapping[str, object]],
    run: ParserRun,
) -> None:
    for route_order, route in enumerate(run.result.billing_routes):
        await connection.execute(
            INSERT_ROUTE,
            (
                str(uuid5(run.parser_run_id, f"billing-route:{route_order}")),
                str(run.parser_run_id),
                route_order,
                str(route.route_id),
                str(route.deployment_binding_id),
                route.mode,
                route.provider_group_id,
                route.request_parameter_ref,
            ),
        )


async def _load_record(
    connection: AsyncConnection[Mapping[str, object]],
    parser_run_id: UUID,
) -> PersistedParserRun | None:
    run_cursor: Final = await connection.execute(SELECT_RUN, (str(parser_run_id),))
    raw_run: Final = await run_cursor.fetchone()
    if raw_run is None:
        return None
    run_id_text: Final = str(parser_run_id)
    subscription_rows: Final = await _fetch_all(connection, SELECT_SUBSCRIPTION, (run_id_text,))
    quota_rows: Final = await _fetch_all(connection, SELECT_LIMITS, (run_id_text,))
    group_rows: Final = await _fetch_all(connection, SELECT_GROUPS, (run_id_text,))
    price_rows: Final = await _fetch_all(connection, SELECT_PRICES, (run_id_text,))
    route_rows: Final = await _fetch_all(connection, SELECT_ROUTES, (run_id_text,))
    return decode_record(
        raw_run=cast(object, raw_run),
        subscription_rows=subscription_rows,
        quota_rows=quota_rows,
        group_rows=group_rows,
        price_rows=price_rows,
        route_rows=route_rows,
    )


async def _load_records(
    connection: AsyncConnection[Mapping[str, object]],
    parser_run_ids: tuple[UUID, ...],
) -> tuple[PersistedParserRun, ...] | None:
    if not parser_run_ids:
        return ()
    first: Final = await _load_record(connection=connection, parser_run_id=parser_run_ids[0])
    if first is None:
        return None
    remaining: Final = await _load_records(connection=connection, parser_run_ids=parser_run_ids[1:])
    if remaining is None:
        return None
    return (first, *remaining)


async def _fetch_all(
    connection: AsyncConnection[Mapping[str, object]],
    query: str,
    params: tuple[object, ...],
) -> tuple[object, ...]:
    cursor: Final = await connection.execute(cast(LiteralString, query), params)
    return tuple(cast(object, row) for row in await cursor.fetchall())
