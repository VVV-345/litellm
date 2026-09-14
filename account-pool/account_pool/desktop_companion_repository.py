"""持久化桌面伴侣一次性票据，并原子执行领取与完成状态转换。"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from account_pool.desktop_companion import DesktopTicketCompleteRequest, DesktopTicketRecord
from account_pool.repository import database_connection

_RECORD: Final = TypeAdapter(DesktopTicketRecord)
_SCHEMA: Final = (
    """
    CREATE TABLE IF NOT EXISTS account_pool_desktop_tickets (
        ticket_id uuid PRIMARY KEY,
        secret_hash text NOT NULL,
        action jsonb NOT NULL,
        status text NOT NULL,
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        claimed_at timestamptz,
        completed_at timestamptz,
        result jsonb,
        error text
    )
    """,
    "CREATE INDEX IF NOT EXISTS account_pool_desktop_ticket_active_expiry_idx "
    "ON account_pool_desktop_tickets (expires_at) WHERE status IN ('pending', 'claimed')",
)


async def initialize_desktop_companion_schema(database_url: str) -> None:
    async with database_connection(database_url) as connection:
        for statement in _SCHEMA:
            await connection.execute(statement)


class PostgresDesktopTicketRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def create(self, record: DesktopTicketRecord) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "INSERT INTO account_pool_desktop_tickets "
                "(ticket_id, secret_hash, action, status, created_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    record.ticket_id,
                    record.secret_hash,
                    Jsonb(record.action.model_dump(mode="json")),
                    record.status,
                    record.created_at,
                    record.expires_at,
                ),
            )

    async def get(self, ticket_id: UUID, now: datetime) -> DesktopTicketRecord | None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "UPDATE account_pool_desktop_tickets SET status = 'expired', completed_at = %s "
                "WHERE ticket_id = %s AND status IN ('pending', 'claimed') AND expires_at <= %s",
                (now, ticket_id, now),
            )
            cursor: Final = await connection.execute(
                "SELECT * FROM account_pool_desktop_tickets WHERE ticket_id = %s",
                (ticket_id,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _record(row)

    async def claim(self, ticket_id: UUID, secret_hash: str, now: datetime) -> DesktopTicketRecord | None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "UPDATE account_pool_desktop_tickets SET status = 'expired', completed_at = %s "
                "WHERE ticket_id = %s AND status = 'pending' AND expires_at <= %s",
                (now, ticket_id, now),
            )
            cursor: Final = await connection.execute(
                "UPDATE account_pool_desktop_tickets SET status = 'claimed', claimed_at = %s "
                "WHERE ticket_id = %s AND secret_hash = %s AND status = 'pending' AND expires_at > %s "
                "RETURNING *",
                (now, ticket_id, secret_hash, now),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _record(row)

    async def complete(
        self,
        ticket_id: UUID,
        secret_hash: str,
        request: DesktopTicketCompleteRequest,
        now: datetime,
    ) -> DesktopTicketRecord | None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "UPDATE account_pool_desktop_tickets SET status = 'expired', completed_at = %s "
                "WHERE ticket_id = %s AND status = 'claimed' AND expires_at <= %s",
                (now, ticket_id, now),
            )
            cursor: Final = await connection.execute(
                "UPDATE account_pool_desktop_tickets "
                "SET status = %s, completed_at = %s, result = %s, error = %s "
                "WHERE ticket_id = %s AND secret_hash = %s AND status = 'claimed' AND expires_at > %s RETURNING *",
                (
                    request.status,
                    now,
                    None if request.result is None else Jsonb(request.result),
                    request.error,
                    ticket_id,
                    secret_hash,
                    now,
                ),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _record(row)


def _record(row: dict[str, object]) -> DesktopTicketRecord:
    return _RECORD.validate_python(row)
