"""本模块持久化卡片凭据和事件日志，使用数据库条件更新处理跨进程竞争。"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from account_pool.card_keys import CardKeyRecord
from account_pool.error_logs import ErrorLogDetail, ErrorLogPage, ErrorLogQuery, ErrorLogRecord
from account_pool.repository import _connection

_KEY: Final = TypeAdapter(CardKeyRecord)
_SCHEMA: Final = (
    """
    CREATE TABLE IF NOT EXISTS account_pool_card_keys (
        card_id uuid PRIMARY KEY REFERENCES account_pool_environments(id) ON DELETE CASCADE,
        key_id uuid UNIQUE NOT NULL,
        key_hash text UNIQUE NOT NULL,
        created_at timestamptz NOT NULL,
        revoked_at timestamptz,
        last_used_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_pool_error_log (
        event_id uuid PRIMARY KEY,
        occurred_at timestamptz NOT NULL,
        card_id uuid NOT NULL,
        request_id uuid NOT NULL,
        payload jsonb NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS account_pool_log_time_idx ON account_pool_error_log (occurred_at DESC, event_id)",
    "CREATE INDEX IF NOT EXISTS account_pool_log_card_idx ON account_pool_error_log (card_id, occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS account_pool_log_request_idx ON account_pool_error_log (request_id, card_id)",
    "CREATE INDEX IF NOT EXISTS account_pool_log_filter_idx ON account_pool_error_log USING gin (payload jsonb_path_ops)",
)


async def initialize_management_schema(database_url: str) -> None:
    async with _connection(database_url) as connection:
        for statement in _SCHEMA:
            await connection.execute(statement)


class PostgresCardKeyRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def get(self, card_id: UUID) -> CardKeyRecord | None:
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT * FROM account_pool_card_keys WHERE card_id = %s", (card_id,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _KEY.validate_python(row)

    async def save(self, record: CardKeyRecord, expected_key_id: UUID | None) -> bool:
        async with _connection(self._database_url) as connection:
            # 锁定卡片，避免删除与新凭据创建交错；轮换同时比较页面看到的 Key ID。
            card_cursor: Final = await connection.execute(
                "SELECT id FROM account_pool_environments WHERE id = %s "
                "AND payload->>'status' <> 'deleting' FOR UPDATE", (record.card_id,),
            )
            if await card_cursor.fetchone() is None:
                return False
            if expected_key_id is None:
                created: Final = await connection.execute(
                    """
                    INSERT INTO account_pool_card_keys (card_id, key_id, key_hash, created_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (card_id) DO UPDATE
                    SET key_id = EXCLUDED.key_id, key_hash = EXCLUDED.key_hash,
                        created_at = EXCLUDED.created_at, revoked_at = NULL, last_used_at = NULL
                    WHERE account_pool_card_keys.revoked_at IS NOT NULL
                    """, (record.card_id, record.key_id, record.key_hash, record.created_at),
                )
                return created.rowcount == 1
            rotated: Final = await connection.execute(
                """
                UPDATE account_pool_card_keys SET key_id = %s, key_hash = %s, created_at = %s,
                    revoked_at = NULL, last_used_at = NULL
                WHERE card_id = %s AND key_id = %s AND revoked_at IS NULL
                """, (record.key_id, record.key_hash, record.created_at, record.card_id, expected_key_id),
            )
            return rotated.rowcount == 1

    async def revoke(self, card_id: UUID, expected_key_id: UUID, revoked_at: datetime) -> bool:
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "UPDATE account_pool_card_keys SET revoked_at = COALESCE(revoked_at, %s) "
                "WHERE card_id = %s AND key_id = %s", (revoked_at, card_id, expected_key_id),
            )
            return cursor.rowcount == 1


class PostgresErrorLogRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def append(self, event: ErrorLogRecord) -> None:
        async with _connection(self._database_url) as connection:
            # 每个请求按顺序编号；事务锁保证后台重试与前台操作不会产生重复序号。
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(event.request_id),),
            )
            attempts: Final = await connection.execute(
                "SELECT COALESCE(MAX((payload->>'attempt')::integer), 0) + 1 AS attempt "
                "FROM account_pool_error_log WHERE request_id = %s AND card_id = %s",
                (event.request_id, event.card_id),
            )
            row: Final = await attempts.fetchone()
            attempt: Final = TypeAdapter(int).validate_python(None if row is None else row["attempt"])
            saved: Final = event.model_copy(update={"attempt": attempt, "retry_count": attempt - 1})
            await connection.execute(
                "INSERT INTO account_pool_error_log (event_id, occurred_at, card_id, request_id, payload) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
                (saved.event_id, saved.occurred_at, saved.card_id, saved.request_id, Jsonb(saved.model_dump(mode="json"))),
            )

    async def query(self, query: ErrorLogQuery) -> ErrorLogPage:
        filters: Final = query.model_dump(mode="json", exclude_none=True,
                                         exclude={"occurred_from", "occurred_to", "limit", "offset"})
        conditions: Final = tuple(
            (clause, value) for clause, value in (
                ("occurred_at >= %s", query.occurred_from),
                ("occurred_at <= %s", query.occurred_to),
                ("card_id = %s", query.card_id),
                ("request_id = %s", query.request_id),
            ) if value is not None
        )
        where: Final = sql.SQL(" AND ").join(
            (sql.SQL("payload @> %s"), *(sql.SQL(clause) for clause, _ in conditions))
        )
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                sql.SQL("SELECT payload FROM account_pool_error_log WHERE {} "
                        "ORDER BY occurred_at DESC, event_id DESC LIMIT %s OFFSET %s").format(where),
                (Jsonb(filters), *(value for _, value in conditions), query.limit + 1, query.offset),
            )
            rows: Final = await cursor.fetchall()
        return ErrorLogPage(
            items=tuple(ErrorLogRecord.model_validate(row["payload"]) for row in rows[:query.limit]),
            has_more=len(rows) > query.limit,
        )

    async def detail(self, event_id: UUID) -> ErrorLogDetail | None:
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_error_log WHERE event_id = %s", (event_id,),
            )
            row: Final = await cursor.fetchone()
            if row is None:
                return None
            event: Final = ErrorLogRecord.model_validate(row["payload"])
            chain: Final = await connection.execute(
                "SELECT payload FROM account_pool_error_log WHERE request_id = %s AND card_id = %s "
                "ORDER BY (payload->>'attempt')::integer, occurred_at, event_id LIMIT 201",
                (event.request_id, event.card_id),
            )
            rows: Final = await chain.fetchall()
        return ErrorLogDetail(event=event, attempts=tuple(
            ErrorLogRecord.model_validate(row["payload"]) for row in rows[:200]
        ), has_more=len(rows) > 200)

    async def prune(self, before: datetime) -> None:
        async with _connection(self._database_url) as connection:
            await connection.execute("DELETE FROM account_pool_error_log WHERE occurred_at < %s", (before,))
