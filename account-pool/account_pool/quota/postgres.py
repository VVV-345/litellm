"""使用 PostgreSQL 持久化额度运行代次、usage 增量和可恢复窗口快照。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Final, Literal, TypeAlias, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from pydantic import AwareDatetime, ValidationError

from account_pool.models import FrozenModel, RuntimeQuotaKind, RuntimeQuotaScope, RuntimeQuotaWindowType
from account_pool.quota.persistence_models import (
    QuotaGenerationStatus,
    QuotaRecoveryState,
    QuotaRuntimeGeneration,
    QuotaUsageEvent,
    QuotaWindowRuntimeSnapshot,
)
from account_pool.quota.repository import (
    QuotaGenerationWriteResult,
    QuotaGenerationWriteSuccess,
    QuotaPersistenceFailure,
    QuotaPersistenceFailureCode,
    QuotaRecoveryLoadResult,
    QuotaRecoveryLoadSuccess,
    QuotaSnapshotWriteResult,
    QuotaSnapshotWriteSuccess,
    QuotaUsageWriteResult,
    QuotaUsageWriteSuccess,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GENERATION_COLUMNS: Final = """
generation_id, predecessor_generation_id, status, created_at, activated_at, closed_at, failure_code
"""
_USAGE_COLUMNS: Final = """
event_id, generation_id, channel_id, account_id, window_id, lease_id, request_id,
kind, amount, occurred_at, source
"""
_QUALIFIED_USAGE_COLUMNS: Final = """
usage.event_id, usage.generation_id, usage.channel_id, usage.account_id, usage.window_id,
usage.lease_id, usage.request_id, usage.kind, usage.amount, usage.occurred_at, usage.source
"""
_SNAPSHOT_COLUMNS: Final = """
generation_id, channel_id, account_id, window_id, scope, subject_id, kind, window_type,
duration_seconds, limit_value, provider_remaining_value, remaining_value,
reserved_value, safety_reserve_value,
retry_at, provider_reset_at, provider_observed_at, provider_fingerprint, source,
reason_code, captured_at, reservation_expires_at
"""


class _GenerationRow(FrozenModel):
    generation_id: UUID
    predecessor_generation_id: UUID | None
    status: QuotaGenerationStatus
    created_at: AwareDatetime
    activated_at: AwareDatetime | None
    closed_at: AwareDatetime | None
    failure_code: str | None


class _UsageRow(FrozenModel):
    event_id: UUID
    generation_id: UUID
    channel_id: UUID | None
    account_id: str
    window_id: str
    lease_id: str
    request_id: str
    kind: RuntimeQuotaKind
    amount: Decimal
    occurred_at: AwareDatetime
    source: Literal["settlement"]


class _SnapshotRow(FrozenModel):
    generation_id: UUID
    channel_id: UUID | None
    account_id: str
    window_id: str
    scope: RuntimeQuotaScope
    subject_id: str | None
    kind: RuntimeQuotaKind
    window_type: RuntimeQuotaWindowType | None
    duration_seconds: int | None
    limit_value: Decimal | None
    provider_remaining_value: Decimal | None
    remaining_value: Decimal | None
    reserved_value: Decimal
    safety_reserve_value: Decimal
    retry_at: AwareDatetime | None
    provider_reset_at: AwareDatetime | None
    provider_observed_at: AwareDatetime
    provider_fingerprint: str
    source: str
    reason_code: str
    captured_at: AwareDatetime
    reservation_expires_at: AwareDatetime | None


DatabaseRow: TypeAlias = Mapping[str, object]


class _UsageContentConflict(Exception):
    pass


class _SnapshotStateConflict(Exception):
    pass


class PostgresQuotaRuntimeRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def begin_generation(self, generation: QuotaRuntimeGeneration) -> QuotaGenerationWriteResult:
        if generation.status != QuotaGenerationStatus.INITIALIZING:
            return _failure(QuotaPersistenceFailureCode.STATE_CONFLICT, retryable=False)
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    INSERT INTO "LiteLLM_AccountPoolQuotaGeneration" ({_GENERATION_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (generation_id) DO NOTHING
                    RETURNING {_GENERATION_COLUMNS}
                    """,
                    _generation_values(generation),
                )
                inserted: Final = await cursor.fetchone()
                if inserted is not None:
                    return QuotaGenerationWriteSuccess(status="created", generation=decode_generation_row(inserted))
                existing: Final = await _load_generation(connection, generation.generation_id)
                if existing == generation:
                    return QuotaGenerationWriteSuccess(status="unchanged", generation=generation)
                return _failure(QuotaPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(QuotaPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.IntegrityError:
            return _failure(QuotaPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(QuotaPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def activate_generation(self, generation_id: UUID, at: AwareDatetime) -> QuotaGenerationWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                current: Final = await _load_generation(connection, generation_id, lock=True)
                if current is None:
                    return _failure(QuotaPersistenceFailureCode.GENERATION_NOT_FOUND, retryable=False)
                if current.status == QuotaGenerationStatus.ACTIVE:
                    return QuotaGenerationWriteSuccess(status="unchanged", generation=current)
                if current.status != QuotaGenerationStatus.INITIALIZING:
                    return _failure(QuotaPersistenceFailureCode.STATE_CONFLICT, retryable=False)

                # 激活新代次和退役旧代次必须处于同一事务，数据库中始终最多只有一个 active 代次。
                await connection.execute(
                    """
                    UPDATE "LiteLLM_AccountPoolQuotaGeneration"
                    SET status = 'retired', closed_at = %s
                    WHERE status = 'active' AND generation_id <> %s
                    """,
                    (at, str(generation_id)),
                )
                cursor: Final = await connection.execute(
                    f"""
                    UPDATE "LiteLLM_AccountPoolQuotaGeneration"
                    SET status = 'active', activated_at = %s, closed_at = NULL, failure_code = NULL
                    WHERE generation_id = %s AND status = 'initializing'
                    RETURNING {_GENERATION_COLUMNS}
                    """,
                    (at, str(generation_id)),
                )
                updated: Final = await cursor.fetchone()
                if updated is None:
                    return _failure(QuotaPersistenceFailureCode.STATE_CONFLICT, retryable=False)
                return QuotaGenerationWriteSuccess(status="updated", generation=decode_generation_row(updated))
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(QuotaPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.IntegrityError:
            return _failure(QuotaPersistenceFailureCode.STATE_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(QuotaPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def fail_generation(
        self,
        generation_id: UUID,
        failure_code: str,
        at: AwareDatetime,
    ) -> QuotaGenerationWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    UPDATE "LiteLLM_AccountPoolQuotaGeneration"
                    SET status = 'failed', closed_at = %s, failure_code = %s
                    WHERE generation_id = %s AND status IN ('initializing', 'active', 'failed')
                    RETURNING {_GENERATION_COLUMNS}
                    """,
                    (at, failure_code, str(generation_id)),
                )
                row: Final = await cursor.fetchone()
                if row is None:
                    return _failure(QuotaPersistenceFailureCode.GENERATION_NOT_FOUND, retryable=False)
                return QuotaGenerationWriteSuccess(status="updated", generation=decode_generation_row(row))
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(QuotaPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.IntegrityError:
            return _failure(QuotaPersistenceFailureCode.STATE_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(QuotaPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def append_usage(self, events: tuple[QuotaUsageEvent, ...]) -> QuotaUsageWriteResult:
        if not events:
            return QuotaUsageWriteSuccess(events=())
        if len({event.event_id for event in events}) != len(events):
            return _failure(QuotaPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = connection.cursor()
                await cursor.executemany(
                    f"""
                    INSERT INTO "LiteLLM_AccountPoolQuotaUsageEvent" ({_USAGE_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    tuple(_usage_values(event) for event in events),
                )
                # ON CONFLICT 只负责幂等；回读完整内容可区分合法重试和同 ID 的内容冲突。
                loaded: Final = await _load_usage_events(connection, tuple(event.event_id for event in events))
                loaded_by_id: Final = {event.event_id: event for event in loaded}
                if any(loaded_by_id.get(event.event_id) != event for event in events):
                    # 同批次可能已插入其他事件，冲突时必须回滚，不能留下部分 usage。
                    raise _UsageContentConflict
                return QuotaUsageWriteSuccess(events=events)
        except _UsageContentConflict:
            return _failure(QuotaPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(QuotaPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.IntegrityError:
            return _failure(QuotaPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(QuotaPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def save_snapshots(
        self,
        snapshots: tuple[QuotaWindowRuntimeSnapshot, ...],
    ) -> QuotaSnapshotWriteResult:
        if not snapshots:
            return QuotaSnapshotWriteSuccess(snapshots=())
        identities: Final = tuple((item.generation_id, item.account_id, item.window_id) for item in snapshots)
        if len(set(identities)) != len(identities) or len({item.generation_id for item in snapshots}) != 1:
            return _failure(QuotaPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = connection.cursor()
                await cursor.executemany(
                    f"""
                    INSERT INTO "LiteLLM_AccountPoolQuotaRuntimeSnapshot" ({_SNAPSHOT_COLUMNS})
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (generation_id, account_id, window_id) DO UPDATE SET
                        channel_id = EXCLUDED.channel_id,
                        scope = EXCLUDED.scope,
                        subject_id = EXCLUDED.subject_id,
                        kind = EXCLUDED.kind,
                        window_type = EXCLUDED.window_type,
                        duration_seconds = EXCLUDED.duration_seconds,
                        limit_value = EXCLUDED.limit_value,
                        provider_remaining_value = EXCLUDED.provider_remaining_value,
                        remaining_value = EXCLUDED.remaining_value,
                        reserved_value = EXCLUDED.reserved_value,
                        safety_reserve_value = EXCLUDED.safety_reserve_value,
                        retry_at = EXCLUDED.retry_at,
                        provider_reset_at = EXCLUDED.provider_reset_at,
                        provider_observed_at = EXCLUDED.provider_observed_at,
                        provider_fingerprint = EXCLUDED.provider_fingerprint,
                        source = EXCLUDED.source,
                        reason_code = EXCLUDED.reason_code,
                        captured_at = EXCLUDED.captured_at,
                        reservation_expires_at = EXCLUDED.reservation_expires_at
                    WHERE EXCLUDED.captured_at >= "LiteLLM_AccountPoolQuotaRuntimeSnapshot".captured_at
                    """,
                    tuple(_snapshot_values(snapshot) for snapshot in snapshots),
                )
                loaded: Final = await _load_snapshots(connection, snapshots[0].generation_id)
                loaded_by_identity: Final = {
                    (item.generation_id, item.account_id, item.window_id): item for item in loaded
                }
                if any(
                    loaded_by_identity.get(identity) != snapshot for identity, snapshot in zip(identities, snapshots)
                ):
                    # 快照批次必须原子更新，旧快照或内容冲突不能提交同批次的其他窗口。
                    raise _SnapshotStateConflict
                return QuotaSnapshotWriteSuccess(snapshots=snapshots)
        except _SnapshotStateConflict:
            return _failure(QuotaPersistenceFailureCode.STATE_CONFLICT, retryable=False)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(QuotaPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.IntegrityError:
            return _failure(QuotaPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(QuotaPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def load_active_recovery_state(self) -> QuotaRecoveryLoadResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    SELECT {_GENERATION_COLUMNS}
                    FROM "LiteLLM_AccountPoolQuotaGeneration"
                    WHERE status = 'active'
                    """
                )
                row: Final = await cursor.fetchone()
                if row is None:
                    return _failure(QuotaPersistenceFailureCode.ACTIVE_GENERATION_NOT_FOUND, retryable=False)
                generation: Final = decode_generation_row(row)
                windows: Final = await _load_snapshots(connection, generation.generation_id)
                usage_events: Final = await _load_generation_usage(connection, generation.generation_id)
                return QuotaRecoveryLoadSuccess(
                    state=QuotaRecoveryState(
                        generation=generation,
                        windows=windows,
                        usage_events=usage_events,
                    )
                )
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(QuotaPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(QuotaPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[DatabaseRow]:
        row_factory: Final = cast(AsyncRowFactory[DatabaseRow], dict_row)
        return await AsyncConnection[DatabaseRow].connect(self._database_url, row_factory=row_factory)

    async def _set_search_path(self, connection: AsyncConnection[DatabaseRow]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))


def decode_generation_row(value: object) -> QuotaRuntimeGeneration:
    row: Final = _GenerationRow.model_validate(value)
    return QuotaRuntimeGeneration(
        generation_id=row.generation_id,
        predecessor_generation_id=row.predecessor_generation_id,
        status=row.status,
        created_at=row.created_at,
        activated_at=row.activated_at,
        closed_at=row.closed_at,
        failure_code=row.failure_code,
    )


def decode_usage_row(value: object) -> QuotaUsageEvent:
    row: Final = _UsageRow.model_validate(value)
    return QuotaUsageEvent(
        event_id=row.event_id,
        generation_id=row.generation_id,
        channel_id=row.channel_id,
        account_id=row.account_id,
        window_id=row.window_id,
        lease_id=row.lease_id,
        request_id=row.request_id,
        kind=row.kind,
        amount=row.amount,
        occurred_at=row.occurred_at,
        source=row.source,
    )


def decode_snapshot_row(value: object) -> QuotaWindowRuntimeSnapshot:
    row: Final = _SnapshotRow.model_validate(value)
    return QuotaWindowRuntimeSnapshot(
        generation_id=row.generation_id,
        channel_id=row.channel_id,
        account_id=row.account_id,
        window_id=row.window_id,
        scope=row.scope,
        subject_id=row.subject_id,
        kind=row.kind,
        window_type=row.window_type,
        duration_seconds=row.duration_seconds,
        limit_value=row.limit_value,
        provider_remaining_value=row.provider_remaining_value,
        remaining_value=row.remaining_value,
        reserved_value=row.reserved_value,
        safety_reserve_value=row.safety_reserve_value,
        retry_at=row.retry_at,
        provider_reset_at=row.provider_reset_at,
        provider_observed_at=row.provider_observed_at,
        provider_fingerprint=row.provider_fingerprint,
        source=row.source,
        reason_code=row.reason_code,
        captured_at=row.captured_at,
        reservation_expires_at=row.reservation_expires_at,
    )


async def _load_generation(
    connection: AsyncConnection[DatabaseRow],
    generation_id: UUID,
    lock: bool = False,
) -> QuotaRuntimeGeneration | None:
    cursor: Final = await connection.execute(
        f"""
        SELECT {_GENERATION_COLUMNS}
        FROM "LiteLLM_AccountPoolQuotaGeneration"
        WHERE generation_id = %s
        {"FOR UPDATE" if lock else ""}
        """,
        (str(generation_id),),
    )
    row: Final = await cursor.fetchone()
    return None if row is None else decode_generation_row(row)


async def _load_usage_events(
    connection: AsyncConnection[DatabaseRow],
    event_ids: tuple[UUID, ...],
) -> tuple[QuotaUsageEvent, ...]:
    cursor: Final = await connection.execute(
        f"""
        SELECT {_USAGE_COLUMNS}
        FROM "LiteLLM_AccountPoolQuotaUsageEvent"
        WHERE event_id = ANY(%s)
        ORDER BY occurred_at, event_id
        """,
        (tuple(str(event_id) for event_id in event_ids),),
    )
    return tuple(decode_usage_row(row) for row in await cursor.fetchall())


async def _load_generation_usage(
    connection: AsyncConnection[DatabaseRow],
    generation_id: UUID,
) -> tuple[QuotaUsageEvent, ...]:
    cursor: Final = await connection.execute(
        f"""
        WITH RECURSIVE quota_lineage AS (
            SELECT generation_id, predecessor_generation_id
            FROM "LiteLLM_AccountPoolQuotaGeneration"
            WHERE generation_id = %s
            UNION ALL
            SELECT predecessor.generation_id, predecessor.predecessor_generation_id
            FROM "LiteLLM_AccountPoolQuotaGeneration" AS predecessor
            JOIN quota_lineage AS current
                ON predecessor.generation_id = current.predecessor_generation_id
        )
        SELECT {_QUALIFIED_USAGE_COLUMNS}
        FROM "LiteLLM_AccountPoolQuotaUsageEvent" AS usage
        JOIN quota_lineage ON quota_lineage.generation_id = usage.generation_id
        ORDER BY usage.occurred_at, usage.event_id
        """,
        (str(generation_id),),
    )
    return tuple(decode_usage_row(row) for row in await cursor.fetchall())


async def _load_snapshots(
    connection: AsyncConnection[DatabaseRow],
    generation_id: UUID,
) -> tuple[QuotaWindowRuntimeSnapshot, ...]:
    cursor: Final = await connection.execute(
        f"""
        SELECT {_SNAPSHOT_COLUMNS}
        FROM "LiteLLM_AccountPoolQuotaRuntimeSnapshot"
        WHERE generation_id = %s
        ORDER BY account_id, window_id
        """,
        (str(generation_id),),
    )
    return tuple(decode_snapshot_row(row) for row in await cursor.fetchall())


def _generation_values(generation: QuotaRuntimeGeneration) -> tuple[object, ...]:
    return (
        str(generation.generation_id),
        None if generation.predecessor_generation_id is None else str(generation.predecessor_generation_id),
        generation.status.value,
        generation.created_at,
        generation.activated_at,
        generation.closed_at,
        generation.failure_code,
    )


def _usage_values(event: QuotaUsageEvent) -> tuple[object, ...]:
    return (
        str(event.event_id),
        str(event.generation_id),
        None if event.channel_id is None else str(event.channel_id),
        event.account_id,
        event.window_id,
        event.lease_id,
        event.request_id,
        event.kind.value,
        event.amount,
        event.occurred_at,
        event.source,
    )


def _snapshot_values(snapshot: QuotaWindowRuntimeSnapshot) -> tuple[object, ...]:
    return (
        str(snapshot.generation_id),
        None if snapshot.channel_id is None else str(snapshot.channel_id),
        snapshot.account_id,
        snapshot.window_id,
        snapshot.scope.value,
        snapshot.subject_id,
        snapshot.kind.value,
        None if snapshot.window_type is None else snapshot.window_type.value,
        snapshot.duration_seconds,
        snapshot.limit_value,
        snapshot.provider_remaining_value,
        snapshot.remaining_value,
        snapshot.reserved_value,
        snapshot.safety_reserve_value,
        snapshot.retry_at,
        snapshot.provider_reset_at,
        snapshot.provider_observed_at,
        snapshot.provider_fingerprint,
        snapshot.source,
        snapshot.reason_code,
        snapshot.captured_at,
        snapshot.reservation_expires_at,
    )


def _failure(code: QuotaPersistenceFailureCode, retryable: bool) -> QuotaPersistenceFailure:
    return QuotaPersistenceFailure(code=code, retryable=retryable)
