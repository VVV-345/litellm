"""按保留期限归档最早月份，并仅在归档验证成功后删除在线事件。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from pydantic import AwareDatetime

from account_pool.retention.archive import ArchiveWriter
from account_pool.retention.models import (
    RetentionArchived,
    RetentionFailure,
    RetentionIdle,
    RetentionPolicy,
    RetentionRunResult,
    RetentionScope,
)
from account_pool.retention.repository import RetentionRepository

Clock = Callable[[], AwareDatetime]


class RetentionRunner(Protocol):
    async def run_once(self) -> RetentionRunResult: ...


def utc_now() -> AwareDatetime:
    return datetime.now(UTC)


class EventRetentionService:
    def __init__(
        self,
        *,
        repository: RetentionRepository,
        archive: ArchiveWriter,
        policy: RetentionPolicy,
        clock: Clock = utc_now,
    ) -> None:
        self._repository: Final = repository
        self._archive: Final = archive
        self._policy: Final = policy
        self._clock: Final = clock

    async def run_once(self) -> RetentionRunResult:
        now: Final = self._clock()
        standard: Final = await self._repository.load_oldest_month(
            scope=RetentionScope.STANDARD,
            before=_complete_month_cutoff(now - timedelta(days=self._policy.event_retention_days)),
            limit=self._policy.batch_size,
        )
        if isinstance(standard, RetentionFailure):
            return standard
        audit: Final = await self._repository.load_oldest_month(
            scope=RetentionScope.AUDIT,
            before=_complete_month_cutoff(now - timedelta(days=self._policy.audit_retention_days)),
            limit=self._policy.batch_size,
        )
        if isinstance(audit, RetentionFailure):
            return audit
        candidates: Final = tuple(batch for batch in (standard, audit) if batch is not None)
        if not candidates:
            return RetentionIdle()
        loaded: Final = min(
            candidates,
            key=lambda batch: (batch.window.started_at, batch.scope.value),
        )
        archived: Final = self._archive.write_verified(loaded)
        if isinstance(archived, RetentionFailure):
            return archived
        deleted: Final = await self._repository.delete_archived(archived.event_ids)
        if isinstance(deleted, RetentionFailure):
            return deleted
        return RetentionArchived(manifest=archived, deleted_event_count=deleted)


def _complete_month_cutoff(value: AwareDatetime) -> AwareDatetime:
    return datetime(value.year, value.month, 1, tzinfo=value.tzinfo)
