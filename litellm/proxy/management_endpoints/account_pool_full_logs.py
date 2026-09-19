"""本模块将完整对话压缩保存在独立持久化库，摘要索引不依赖日常日志的保留期限。"""

from __future__ import annotations

import gzip
import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import Final, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from litellm.proxy.management_endpoints.account_pool_gateway_contracts import FinishRequest
from litellm.proxy.management_endpoints.account_pool_timing import TimingPhase


class FullLogSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    request_id: UUID
    card_id: UUID
    account_id: UUID
    key_id: UUID
    session_id: str | None
    started_at: datetime
    finished_at: datetime
    model: str
    requested_model: str
    attempt: int
    result: FinishRequest
    transport: Literal["http", "sse", "websocket"]
    incomplete: bool = False
    truncated: bool = False
    skip_failed: bool = False


class FullLogRecord(FullLogSummary):
    request: JsonValue
    response: JsonValue


class FullLogTotals(BaseModel):
    requests: int = 0
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float | None = None
    unknown_cost_attempts: int = 0


class FullLogPage(BaseModel):
    items: tuple[FullLogSummary, ...]
    has_more: bool
    totals: FullLogTotals = Field(default_factory=FullLogTotals)


class FullLogStorageStats(BaseModel):
    location: str
    backend: str = "sqlite-gzip"
    row_count: int
    allocated_bytes: int


class FullLogQuery(BaseModel):
    card_id: UUID | None = None
    key_id: UUID | None = None
    request_id: UUID | None = None
    session_id: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=256)
    http_status: int | None = Field(default=None, ge=100, le=599)
    incomplete: bool | None = None
    occurred_from: AwareDatetime | None = None
    occurred_to: AwareDatetime | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def ordered_time_range(self) -> FullLogQuery:
        if self.occurred_from and self.occurred_to and self.occurred_from > self.occurred_to:
            raise ValueError("开始时间不能晚于结束时间")
        return self


class FullLogStore:
    def __init__(self, root: Path) -> None:
        self.root: Final = root
        self.path: Final = root / "conversations.sqlite3"

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection: Final = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                connection.execute("PRAGMA secure_delete=ON")
                connection.execute("PRAGMA auto_vacuum=FULL")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS conversations (event_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, "
                    "card_id TEXT NOT NULL, session_id TEXT, started_at REAL NOT NULL, summary TEXT NOT NULL, body BLOB NOT NULL)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS conversations_time ON conversations(started_at, event_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS conversations_session ON conversations(session_id, started_at)"
                )
                connection.execute("CREATE INDEX IF NOT EXISTS conversations_request ON conversations(request_id)")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS failed_requests (request_id TEXT PRIMARY KEY, expires REAL NOT NULL)"
                )
                yield connection
        finally:
            connection.close()

    @contextmanager
    def timing_connection(self) -> Generator[sqlite3.Connection]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection: Final = sqlite3.connect(self.root / "timings.sqlite3", timeout=1)
        try:
            with connection:
                connection.execute("PRAGMA auto_vacuum=FULL")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS request_timings (request_id TEXT NOT NULL, segment_id TEXT NOT NULL, attempt INTEGER NOT NULL, "
                    "phase TEXT NOT NULL, duration_ms REAL NOT NULL, status INTEGER NOT NULL, recorded_at REAL NOT NULL, "
                    "PRIMARY KEY(request_id, segment_id, phase))"
                )
                connection.execute("CREATE INDEX IF NOT EXISTS request_timings_age ON request_timings(recorded_at)")
                yield connection
        finally:
            connection.close()

    def save_timing(self, request_id: UUID, phases: tuple[TimingPhase, ...]) -> None:
        now: Final = datetime.now(timezone.utc).timestamp()
        with self.timing_connection() as connection:
            connection.executemany(
                "INSERT INTO request_timings VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(request_id, segment_id, phase) DO UPDATE SET duration_ms=excluded.duration_ms, "
                "status=excluded.status, recorded_at=excluded.recorded_at",
                (
                    (
                        str(request_id),
                        str(phase.segment_id),
                        phase.attempt,
                        phase.phase,
                        phase.duration_ms,
                        phase.status,
                        now,
                    )
                    for phase in phases
                ),
            )
            connection.execute("DELETE FROM request_timings WHERE recorded_at < ?", (now - 7 * 86400,))
            connection.execute(
                "DELETE FROM request_timings WHERE rowid <= (SELECT MAX(rowid) - 100000 FROM request_timings)"
            )

    def timing(self, request_id: UUID) -> tuple[TimingPhase, ...]:
        if not (self.root / "timings.sqlite3").exists():
            return ()
        with self.timing_connection() as connection:
            rows: Final = connection.execute(
                "SELECT attempt, phase, duration_ms, status, segment_id FROM request_timings WHERE request_id = ? ORDER BY recorded_at, phase",
                (str(request_id),),
            ).fetchall()
        return tuple(
            TimingPhase(attempt=row[0], phase=row[1], duration_ms=row[2], status=row[3], segment_id=row[4])
            for row in rows
        )

    def append(self, record: FullLogRecord) -> None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                record.skip_failed
                and connection.execute(
                    "SELECT 1 FROM failed_requests WHERE request_id = ? AND expires > ?",
                    (str(record.request_id), datetime.now(timezone.utc).timestamp()),
                ).fetchone()
            ):
                return
            connection.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(event_id) DO NOTHING",
                (
                    str(record.event_id),
                    str(record.request_id),
                    str(record.card_id),
                    record.session_id,
                    record.started_at.timestamp(),
                    FullLogSummary.model_validate(record.model_dump()).model_dump_json(),
                    gzip.compress(record.model_dump_json().encode(), compresslevel=5),
                ),
            )

    def limit_storage(self, max_storage_mb: int) -> int:
        if not self.path.exists() or max_storage_mb <= 0:
            return 0
        with self.connection() as connection:
            deleted: Final = connection.execute(
                "DELETE FROM conversations WHERE event_id IN ("
                "SELECT event_id FROM (SELECT event_id, sum(length(body) + length(summary)) OVER "
                "(ORDER BY started_at DESC, event_id DESC) AS running_bytes FROM conversations) "
                "WHERE running_bytes > ? ORDER BY running_bytes DESC LIMIT 1000)",
                (max_storage_mb * 1024 * 1024,),
            ).rowcount
        return deleted

    def reject_request(self, request_id: UUID) -> None:
        now: Final = datetime.now(timezone.utc).timestamp()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM failed_requests WHERE expires < ?", (now,))
            connection.execute(
                "INSERT INTO failed_requests VALUES (?, ?) ON CONFLICT(request_id) DO UPDATE SET expires=excluded.expires",
                (str(request_id), now + 86400),
            )
            connection.execute(
                "DELETE FROM conversations WHERE request_id = ? AND json_extract(summary, '$.skip_failed') = 1",
                (str(request_id),),
            )

    def query(self, query: FullLogQuery) -> FullLogPage:
        if not self.path.exists():
            return FullLogPage(items=(), has_more=False)
        conditions: Final = tuple(
            (column, value)
            for column, value in (
                ("card_id = ?", str(query.card_id) if query.card_id else None),
                ("request_id = ?", str(query.request_id) if query.request_id else None),
                ("session_id = ?", query.session_id),
                ("json_extract(summary, '$.key_id') = ?", str(query.key_id) if query.key_id else None),
                ("json_extract(summary, '$.model') = ?", query.model),
                ("json_extract(summary, '$.result.http_status') = ?", query.http_status),
                ("json_extract(summary, '$.incomplete') = ?", query.incomplete),
                ("started_at >= ?", query.occurred_from.timestamp() if query.occurred_from else None),
                ("started_at <= ?", query.occurred_to.timestamp() if query.occurred_to else None),
            )
            if value is not None
        )
        where: Final = " AND ".join(column for column, _ in conditions) or "1=1"
        order: Final = "ASC" if query.session_id else "DESC"
        with self.connection() as connection:
            rows: Final = TypeAdapter(tuple[tuple[str], ...]).validate_python(
                connection.execute(
                    f"SELECT summary FROM conversations WHERE {where} ORDER BY started_at {order}, event_id {order} LIMIT ? OFFSET ?",
                    (*tuple(value for _, value in conditions), query.limit + 1, query.offset),
                ).fetchall()
            )
            totals: Final = TypeAdapter(tuple[int, int, int, int, int, int, float | None, int]).validate_python(
                connection.execute(
                    "SELECT count(DISTINCT request_id), count(*), "
                    "coalesce(sum(json_extract(summary, '$.result.input_tokens')), 0), "
                    "coalesce(sum(json_extract(summary, '$.result.output_tokens')), 0), "
                    "coalesce(sum(json_extract(summary, '$.result.cache_read_input_tokens')), 0), "
                    "coalesce(sum(json_extract(summary, '$.result.cache_creation_input_tokens')), 0), "
                    "sum(json_extract(summary, '$.result.cost_usd')), "
                    "count(*) - count(json_extract(summary, '$.result.cost_usd')) "
                    f"FROM conversations WHERE {where}",
                    tuple(value for _, value in conditions),
                ).fetchone()
            )
        return FullLogPage(
            items=tuple(FullLogSummary.model_validate_json(row[0]) for row in rows[: query.limit]),
            has_more=len(rows) > query.limit,
            totals=FullLogTotals(
                requests=totals[0],
                attempts=totals[1],
                input_tokens=totals[2],
                output_tokens=totals[3],
                cache_read_input_tokens=totals[4],
                cache_creation_input_tokens=totals[5],
                cost_usd=totals[6],
                unknown_cost_attempts=totals[7],
            ),
        )

    def detail(self, event_id: UUID) -> FullLogRecord | None:
        if not self.path.exists():
            return None
        with self.connection() as connection:
            row: Final = TypeAdapter[tuple[bytes] | None](tuple[bytes] | None).validate_python(
                connection.execute("SELECT body FROM conversations WHERE event_id = ?", (str(event_id),)).fetchone()
            )
        return None if row is None else FullLogRecord.model_validate_json(gzip.decompress(row[0]))

    def prune(self, days: int | None, limit: int | None = None) -> int:
        if not self.path.exists():
            return 0
        before: Final = (
            (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() if days is not None else float("inf")
        )
        with self.connection() as connection:
            deleted: Final = connection.execute(
                "DELETE FROM conversations WHERE event_id IN (SELECT event_id FROM conversations "
                "WHERE started_at < ? ORDER BY started_at LIMIT ?)",
                (before, limit if limit is not None else -1),
            ).rowcount
        return deleted

    def storage(self) -> FullLogStorageStats:
        if not self.path.exists():
            return FullLogStorageStats(location=str(self.path), row_count=0, allocated_bytes=0)
        with self.connection() as connection:
            row: Final = TypeAdapter(tuple[int]).validate_python(
                connection.execute("SELECT count(*) FROM conversations").fetchone()
            )
        return FullLogStorageStats(location=str(self.path), row_count=row[0], allocated_bytes=self.path.stat().st_size)


@cache
def full_log_store() -> FullLogStore:
    return FullLogStore(Path(os.getenv("ACCOUNT_POOL_FULL_LOG_DIR", "/var/lib/litellm/full-logs")))
