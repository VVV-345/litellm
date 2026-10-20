"""本模块在 PostgreSQL 保存反馈和限权凭证，以版本条件更新防止并发覆盖。"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from psycopg.types.json import Jsonb

from account_pool.feedback_models import (
    FeedbackAttachmentRecord,
    FeedbackIssue,
    FeedbackPage,
    FeedbackQuery,
    FeedbackStats,
    FeedbackTokenRecord,
)
from account_pool.repository import database_connection

_SCHEMA: Final = (
    """CREATE TABLE IF NOT EXISTS account_pool_feedback (
        id uuid PRIMARY KEY, revision bigint NOT NULL CHECK (revision > 0),
        updated_at timestamptz NOT NULL, payload jsonb NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS account_pool_feedback_status_idx "
    "ON account_pool_feedback ((payload->>'status'), updated_at, id)",
    """CREATE TABLE IF NOT EXISTS account_pool_feedback_attachments (
        id uuid PRIMARY KEY, issue_id uuid NOT NULL REFERENCES account_pool_feedback(id) ON DELETE CASCADE,
        filename text NOT NULL, content_type text NOT NULL, size integer NOT NULL CHECK (size > 0), content bytea NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS account_pool_feedback_tokens (
        id uuid PRIMARY KEY, name text NOT NULL, token_hash text UNIQUE NOT NULL,
        created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, revoked_at timestamptz
    )""",
)
_FILTER: Final = """(%s = 'all' OR (%s = 'unresolved' AND payload->>'status' <> 'resolved')
    OR payload->>'status' = %s)
    AND (payload->>'title' ILIKE %s OR payload->>'description' ILIKE %s)"""


class PostgresFeedbackRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
            for statement in _SCHEMA:
                await connection.execute(statement)

    async def insert(self, issue: FeedbackIssue) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "INSERT INTO account_pool_feedback (id,revision,updated_at,payload) VALUES (%s,%s,%s,%s)",
                (issue.id, issue.revision, issue.updated_at, Jsonb(issue.model_dump(mode="json"))),
            )

    async def get(self, issue_id: UUID) -> FeedbackIssue | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT payload FROM account_pool_feedback WHERE id=%s", (issue_id,))
            row: Final = await cursor.fetchone()
        return None if row is None else FeedbackIssue.model_validate(row["payload"])

    async def replace(self, issue: FeedbackIssue, expected_revision: int) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "UPDATE account_pool_feedback SET payload=%s,revision=%s,updated_at=%s WHERE id=%s AND revision=%s",
                (Jsonb(issue.model_dump(mode="json")), issue.revision, issue.updated_at, issue.id, expected_revision),
            )
            return cursor.rowcount == 1

    async def delete(self, issue_id: UUID, revision: int) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "DELETE FROM account_pool_feedback WHERE id=%s AND revision=%s", (issue_id, revision),
            )
            return cursor.rowcount == 1

    async def page(self, query: FeedbackQuery) -> FeedbackPage:
        pattern: Final = "%" + query.q + "%"
        parameters: Final = (query.status, query.status, query.status, pattern, pattern)
        async with database_connection(self._database_url) as connection:
            count_cursor: Final = await connection.execute(
                "SELECT count(*) AS total FROM account_pool_feedback WHERE " + _FILTER, parameters,
            )
            count: Final = await count_cursor.fetchone()
            stats_cursor: Final = await connection.execute(
                """SELECT count(*) FILTER (WHERE payload->>'status'='pending') AS pending,
                count(*) FILTER (WHERE payload->>'status'='in_progress') AS in_progress,
                count(*) FILTER (WHERE payload->>'status'='pending_verification') AS pending_verification,
                count(*) FILTER (WHERE payload->>'status'='resolved') AS resolved FROM account_pool_feedback""",
            )
            stats: Final = await stats_cursor.fetchone()
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_feedback WHERE " + _FILTER
                + " ORDER BY CASE payload->>'priority' WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,"
                " payload->>'created_at',id LIMIT %s OFFSET %s", (*parameters, query.limit, query.offset),
            )
            rows: Final = await cursor.fetchall()
        return FeedbackPage(
            items=tuple(FeedbackIssue.model_validate(row["payload"]) for row in rows),
            total=int(str(count["total"])) if count is not None else 0,
            stats=FeedbackStats.model_validate(stats),
        )

    async def add_attachment(
        self, issue: FeedbackIssue, expected_revision: int, attachment: FeedbackAttachmentRecord,
    ) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "UPDATE account_pool_feedback SET payload=%s,revision=%s,updated_at=%s WHERE id=%s AND revision=%s",
                (Jsonb(issue.model_dump(mode="json")), issue.revision, issue.updated_at, issue.id, expected_revision),
            )
            if cursor.rowcount != 1:
                return False
            await connection.execute(
                "INSERT INTO account_pool_feedback_attachments "
                "(id,issue_id,filename,content_type,size,content) VALUES (%s,%s,%s,%s,%s,%s)",
                (attachment.id, attachment.issue_id, attachment.filename, attachment.content_type,
                 attachment.size, attachment.content),
            )
        return True

    async def get_attachment(
        self, issue_id: UUID, attachment_id: UUID,
    ) -> FeedbackAttachmentRecord | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT id,issue_id,filename,content_type,size,content FROM account_pool_feedback_attachments "
                "WHERE issue_id=%s AND id=%s",
                (issue_id, attachment_id),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else FeedbackAttachmentRecord.model_validate(row)

    async def insert_token(self, record: FeedbackTokenRecord) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "INSERT INTO account_pool_feedback_tokens (id,name,token_hash,created_at,expires_at) VALUES (%s,%s,%s,%s,%s)",
                (record.id, record.name, record.token_hash, record.created_at, record.expires_at),
            )

    async def find_token(self, token_hash: str) -> FeedbackTokenRecord | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT * FROM account_pool_feedback_tokens WHERE token_hash=%s", (token_hash,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else FeedbackTokenRecord.model_validate(row)

    async def list_tokens(self) -> tuple[FeedbackTokenRecord, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT * FROM account_pool_feedback_tokens ORDER BY created_at DESC,id",
            )
            rows: Final = await cursor.fetchall()
        return tuple(FeedbackTokenRecord.model_validate(row) for row in rows)

    async def revoke_token(self, token_id: UUID, now: datetime) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "UPDATE account_pool_feedback_tokens SET revoked_at=COALESCE(revoked_at,%s) WHERE id=%s", (now, token_id),
            )
            return cursor.rowcount == 1
