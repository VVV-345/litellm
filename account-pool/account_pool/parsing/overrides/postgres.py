"""使用 PostgreSQL 追加和读取不可变人工覆盖事件链。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Final, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from pydantic import AwareDatetime, JsonValue, TypeAdapter, ValidationError

from account_pool.models import FrozenModel
from account_pool.parsing.overrides.models import (
    FieldOverrideEvent,
    OverrideAction,
    OverrideTarget,
)
from account_pool.parsing.overrides.repository import (
    OverrideBatchWriteResult,
    OverrideBatchWriteSuccess,
    OverrideEventsLoadResult,
    OverrideEventsLoadSuccess,
    OverridePersistenceFailure,
    OverridePersistenceFailureCode,
    OverrideWriteResult,
    OverrideWriteSuccess,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TARGET: Final[TypeAdapter[OverrideTarget]] = TypeAdapter(OverrideTarget)
_JSON_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_EVENT_JSON: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_SELECT_COLUMNS: Final = """
override_id, channel_id, source_parser_run_id, field_path, target_kind, target, action, value,
had_previous_override, previous_value, supersedes_override_id, actor_id,
actor_role, request_id, reason, occurred_at, content_hash
"""
_SELECT_BY_ID: Final = f"""
SELECT {_SELECT_COLUMNS}
FROM "LiteLLM_AccountPoolFieldOverride"
WHERE override_id = %s
"""
_SELECT_FOR_CHANNEL: Final = f"""
SELECT {_SELECT_COLUMNS}
FROM "LiteLLM_AccountPoolFieldOverride"
WHERE channel_id = %s
ORDER BY occurred_at, override_id
"""
_SELECT_HEADS: Final = f"""
SELECT {_SELECT_COLUMNS}
FROM "LiteLLM_AccountPoolFieldOverride" AS candidate
WHERE candidate.channel_id = %s
  AND candidate.field_path = %s
  AND NOT EXISTS (
      SELECT 1
      FROM "LiteLLM_AccountPoolFieldOverride" AS child
      WHERE child.supersedes_override_id = candidate.override_id
  )
FOR UPDATE
"""
_INSERT_EVENT: Final = """
INSERT INTO "LiteLLM_AccountPoolFieldOverride" (
    override_id, channel_id, source_parser_run_id, field_path, target_kind,
    target, action, value, had_previous_override, previous_value,
    supersedes_override_id, actor_id, actor_role, request_id, reason, occurred_at, content_hash
) VALUES (
    %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s::jsonb,
    %s, %s, %s, %s, %s, %s, %s
)
"""


class _OverrideRow(FrozenModel):
    override_id: UUID
    channel_id: UUID
    source_parser_run_id: UUID
    field_path: str
    target_kind: str
    target: object
    action: OverrideAction
    value: object
    had_previous_override: bool
    previous_value: object
    supersedes_override_id: UUID | None
    actor_id: str
    actor_role: str | None
    request_id: str | None
    reason: str
    occurred_at: AwareDatetime
    content_hash: str


class PostgresOverrideEventRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def append(self, event: FieldOverrideEvent) -> OverrideWriteResult:
        content_hash: Final = _content_hash(event)
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                # 同一渠道同一字段串行追加，避免并发请求同时生成两个事件链头。
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (_lock_key(channel_id=event.channel_id, field_path=event.field_path()),),
                )
                existing: Final = await _load_by_id(connection=connection, override_id=event.override_id)
                if existing is not None:
                    if _content_hash(existing) != content_hash:
                        return _failure(OverridePersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
                    return OverrideWriteSuccess(status="unchanged", event=existing)
                channel_cursor: Final = await connection.execute(
                    'SELECT 1 FROM "LiteLLM_AccountPoolChannel" WHERE channel_id = %s',
                    (str(event.channel_id),),
                )
                if await channel_cursor.fetchone() is None:
                    return _failure(OverridePersistenceFailureCode.CHANNEL_NOT_FOUND, retryable=False)
                source_cursor: Final = await connection.execute(
                    """
                    SELECT 1
                    FROM "LiteLLM_AccountPoolParserRun"
                    WHERE parser_run_id = %s AND channel_id = %s
                    """,
                    (str(event.source_parser_run_id), str(event.channel_id)),
                )
                if await source_cursor.fetchone() is None:
                    return _failure(OverridePersistenceFailureCode.SOURCE_RUN_NOT_FOUND, retryable=False)
                heads: Final = await _load_heads(
                    connection=connection,
                    channel_id=event.channel_id,
                    field_path=event.field_path(),
                )
                if len(heads) > 1:
                    return _failure(OverridePersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
                current: Final = None if not heads else heads[0]
                if not _matches_predecessor(event=event, current=current):
                    return _failure(OverridePersistenceFailureCode.PREDECESSOR_CONFLICT, retryable=False)
                await connection.execute(
                    _INSERT_EVENT,
                    _insert_values(event),
                )
                return OverrideWriteSuccess(status="created", event=event)
        except (ValidationError, ValueError):
            return _failure(OverridePersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.IntegrityError:
            return _failure(OverridePersistenceFailureCode.PREDECESSOR_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(OverridePersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def append_batch(self, events: tuple[FieldOverrideEvent, ...]) -> OverrideBatchWriteResult:
        if not events:
            return OverrideBatchWriteSuccess(status="unchanged", events=())
        channel_ids: Final = frozenset(event.channel_id for event in events)
        source_run_ids: Final = frozenset(event.source_parser_run_id for event in events)
        override_ids: Final = tuple(event.override_id for event in events)
        field_paths: Final = tuple(event.field_path() for event in events)
        if (
            len(channel_ids) != 1
            or len(source_run_ids) != 1
            or len(override_ids) != len(frozenset(override_ids))
            or len(field_paths) != len(frozenset(field_paths))
        ):
            return _failure(OverridePersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        channel_id: Final = next(iter(channel_ids))
        source_run_id: Final = next(iter(source_run_ids))
        ordered: Final = tuple(sorted(events, key=lambda event: event.field_path()))
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                for field_path in sorted(field_paths):
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock(%s)",
                        (_lock_key(channel_id=channel_id, field_path=field_path),),
                    )
                channel_cursor: Final = await connection.execute(
                    'SELECT 1 FROM "LiteLLM_AccountPoolChannel" WHERE channel_id = %s',
                    (str(channel_id),),
                )
                if await channel_cursor.fetchone() is None:
                    return _failure(OverridePersistenceFailureCode.CHANNEL_NOT_FOUND, retryable=False)
                source_cursor: Final = await connection.execute(
                    """
                    SELECT 1 FROM "LiteLLM_AccountPoolParserRun"
                    WHERE parser_run_id = %s AND channel_id = %s
                    """,
                    (str(source_run_id), str(channel_id)),
                )
                if await source_cursor.fetchone() is None:
                    return _failure(OverridePersistenceFailureCode.SOURCE_RUN_NOT_FOUND, retryable=False)
                validated: Final = await _validate_batch(connection, ordered)
                if isinstance(validated, OverridePersistenceFailure):
                    return validated
                for event in validated:
                    await connection.execute(_INSERT_EVENT, _insert_values(event))
                return OverrideBatchWriteSuccess(
                    status="created" if validated else "unchanged",
                    events=ordered,
                )
        except (ValidationError, ValueError):
            return _failure(OverridePersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.IntegrityError:
            return _failure(OverridePersistenceFailureCode.PREDECESSOR_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(OverridePersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def load_for_channel(self, channel_id: UUID) -> OverrideEventsLoadResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                channel_cursor: Final = await connection.execute(
                    'SELECT 1 FROM "LiteLLM_AccountPoolChannel" WHERE channel_id = %s',
                    (str(channel_id),),
                )
                if await channel_cursor.fetchone() is None:
                    return _failure(OverridePersistenceFailureCode.CHANNEL_NOT_FOUND, retryable=False)
                cursor: Final = await connection.execute(_SELECT_FOR_CHANNEL, (str(channel_id),))
                rows: Final = tuple(cast(object, row) for row in await cursor.fetchall())
                events: Final = tuple(_decode_event(row) for row in rows)
                return OverrideEventsLoadSuccess(events=events)
        except (ValidationError, ValueError):
            return _failure(OverridePersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(OverridePersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[Mapping[str, object]]:
        row_factory: Final = cast(AsyncRowFactory[Mapping[str, object]], dict_row)
        return await AsyncConnection[Mapping[str, object]].connect(
            self._database_url,
            row_factory=row_factory,
        )

    async def _set_search_path(self, connection: AsyncConnection[Mapping[str, object]]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))


def _failure(code: OverridePersistenceFailureCode, retryable: bool) -> OverridePersistenceFailure:
    return OverridePersistenceFailure(code=code, retryable=retryable)


async def _validate_batch(
    connection: AsyncConnection[Mapping[str, object]],
    events: tuple[FieldOverrideEvent, ...],
) -> tuple[FieldOverrideEvent, ...] | OverridePersistenceFailure:
    if not events:
        return ()
    event, *remaining_values = events
    remaining: Final = tuple(remaining_values)
    existing: Final = await _load_by_id(connection=connection, override_id=event.override_id)
    if existing is not None:
        if _content_hash(existing) != _content_hash(event):
            return _failure(OverridePersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        return await _validate_batch(connection, remaining)
    heads: Final = await _load_heads(
        connection=connection,
        channel_id=event.channel_id,
        field_path=event.field_path(),
    )
    if len(heads) > 1:
        return _failure(OverridePersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
    current: Final = None if not heads else heads[0]
    if not _matches_predecessor(event=event, current=current):
        return _failure(OverridePersistenceFailureCode.PREDECESSOR_CONFLICT, retryable=False)
    validated_remaining: Final = await _validate_batch(connection, remaining)
    if isinstance(validated_remaining, OverridePersistenceFailure):
        return validated_remaining
    return (event, *validated_remaining)


def _insert_values(event: FieldOverrideEvent) -> tuple[object, ...]:
    return (
        str(event.override_id),
        str(event.channel_id),
        str(event.source_parser_run_id),
        event.field_path(),
        event.target.kind,
        _TARGET.dump_json(event.target).decode("utf-8"),
        event.action,
        _JSON_VALUE.dump_json(event.value).decode("utf-8"),
        event.had_previous_override,
        _JSON_VALUE.dump_json(event.previous_value).decode("utf-8"),
        None if event.supersedes_override_id is None else str(event.supersedes_override_id),
        event.actor_id,
        event.actor_role,
        event.request_id,
        event.reason,
        event.occurred_at,
        _content_hash(event),
    )


def _content_hash(event: FieldOverrideEvent) -> str:
    payload: Final = _EVENT_JSON.validate_json(event.model_dump_json())
    if not isinstance(payload, dict):
        raise ValueError("override event must serialize to a JSON object")
    compatible_payload: Final = {
        key: value
        for key, value in payload.items()
        if not (key in {"actor_role", "request_id"} and value is None)
    }
    return sha256(_canonical_json(compatible_payload).encode()).hexdigest()


def _lock_key(channel_id: UUID, field_path: str) -> int:
    digest: Final = sha256(f"{channel_id}:{field_path}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _canonical_json(value: JsonValue) -> str:
    # PostgreSQL JSONB 不保留对象键顺序，哈希前必须递归规范化以保证幂等比较。
    if value is None or isinstance(value, str | bool | int | float):
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if isinstance(value, list):
        return f"[{','.join(_canonical_json(item) for item in value)}]"
    entries: Final = tuple(
        f"{json.dumps(key, ensure_ascii=True)}:{_canonical_json(item)}"
        for key, item in sorted(value.items())
    )
    return f"{{{','.join(entries)}}}"


def _matches_predecessor(event: FieldOverrideEvent, current: FieldOverrideEvent | None) -> bool:
    if current is None:
        return (
            event.supersedes_override_id is None
            and not event.had_previous_override
            and event.previous_value is None
        )
    current_is_active: Final = current.action == OverrideAction.SET
    expected_previous: Final = current.value if current_is_active else None
    return (
        event.target == current.target
        and event.supersedes_override_id == current.override_id
        and event.had_previous_override == current_is_active
        and event.previous_value == expected_previous
    )


async def _load_by_id(
    connection: AsyncConnection[Mapping[str, object]],
    override_id: UUID,
) -> FieldOverrideEvent | None:
    cursor: Final = await connection.execute(_SELECT_BY_ID, (str(override_id),))
    row: Final = await cursor.fetchone()
    return None if row is None else _decode_event(cast(object, row))


async def _load_heads(
    connection: AsyncConnection[Mapping[str, object]],
    channel_id: UUID,
    field_path: str,
) -> tuple[FieldOverrideEvent, ...]:
    cursor: Final = await connection.execute(_SELECT_HEADS, (str(channel_id), field_path))
    return tuple(_decode_event(cast(object, row)) for row in await cursor.fetchall())


def _decode_event(value: object) -> FieldOverrideEvent:
    row: Final = _OverrideRow.model_validate(value)
    target: Final = _TARGET.validate_python(row.target)
    if target.kind != row.target_kind or target.field_path() != row.field_path:
        raise ValueError("stored override target metadata does not match target")
    event: Final = FieldOverrideEvent(
        override_id=row.override_id,
        channel_id=row.channel_id,
        source_parser_run_id=row.source_parser_run_id,
        target=target,
        action=row.action,
        value=_JSON_VALUE.validate_python(row.value),
        had_previous_override=row.had_previous_override,
        previous_value=_JSON_VALUE.validate_python(row.previous_value),
        supersedes_override_id=row.supersedes_override_id,
        actor_id=row.actor_id,
        actor_role=row.actor_role,
        request_id=row.request_id,
        reason=row.reason,
        occurred_at=row.occurred_at,
    )
    if _content_hash(event) != row.content_hash:
        raise ValueError("stored override content hash does not match decoded event")
    return event
