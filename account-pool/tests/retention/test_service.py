"""验证归档失败不删除事件、成功后精确删除及独立审计保留期。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from account_pool.events.models import EventLogEntry, EventOperationalSummary, EventQueryOutcome
from account_pool.retention.models import (
    ArchiveManifest,
    ArchiveWindow,
    RetentionArchived,
    RetentionBatch,
    RetentionFailure,
    RetentionFailureCode,
    RetentionIdle,
    RetentionPolicy,
    RetentionScope,
)
from account_pool.retention.service import EventRetentionService
from pydantic import AwareDatetime

_NOW: Final = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _event() -> EventLogEntry:
    return EventLogEntry(
        event_id=UUID("10000000-0000-0000-0000-000000000001"),
        event_type="parser_task_completed",
        occurred_at=datetime(2026, 4, 2, tzinfo=UTC),
        channel_id=UUID("30000000-0000-0000-0000-000000000001"),
        actor_type="system",
        actor_id="account_pool_parser_task",
        outcome=EventQueryOutcome.SUCCEEDED,
        safe_details={
            "kind": "parser_task_completed",
            "task_id": "40000000-0000-0000-0000-000000000001",
            "parser_run_id": "50000000-0000-0000-0000-000000000001",
            "provider_id": "openai_compatible",
        },
        operational=EventOperationalSummary(
            source="parser_task",
            operation_id=UUID("20000000-0000-0000-0000-000000000001"),
            outcome="succeeded",
        ),
    )


def _batch() -> RetentionBatch:
    return RetentionBatch(
        scope=RetentionScope.STANDARD,
        window=ArchiveWindow(
            started_at=datetime(2026, 4, 1, tzinfo=UTC),
            ended_at=datetime(2026, 5, 1, tzinfo=UTC),
        ),
        events=(_event(),),
    )


class FakeRepository:
    def __init__(self, batches: tuple[RetentionBatch | RetentionFailure | None, ...]) -> None:
        self.batches = iter(batches)
        self.loads: list[tuple[RetentionScope, AwareDatetime, int]] = []
        self.deleted: tuple[UUID, ...] = ()

    async def load_oldest_month(
        self,
        *,
        scope: RetentionScope,
        before: AwareDatetime,
        limit: int,
    ) -> RetentionBatch | RetentionFailure | None:
        self.loads.append((scope, before, limit))
        return next(self.batches)

    async def delete_archived(self, event_ids: tuple[UUID, ...]) -> int | RetentionFailure:
        self.deleted = event_ids
        return len(event_ids)


class FakeArchive:
    def __init__(self, result: ArchiveManifest | RetentionFailure) -> None:
        self.result = result

    def write_verified(self, batch: RetentionBatch) -> ArchiveManifest | RetentionFailure:
        del batch
        return self.result


def _manifest() -> ArchiveManifest:
    batch: Final = _batch()
    return ArchiveManifest(
        archive_id="standard-2026-04-0123456789abcdef",
        scope=RetentionScope.STANDARD,
        window=batch.window,
        event_count=1,
        event_ids=(batch.events[0].event_id,),
        plaintext_sha256="a" * 64,
        ciphertext_sha256="b" * 64,
        encryption="AES-256-GCM",
        key_id="test-key",
        nonce_base64="YWFhYWFhYWFhYWFh",
        created_at=_NOW,
    )


async def test_archive_failure_never_deletes_online_events() -> None:
    repository: Final = FakeRepository((_batch(), None))
    service: Final = EventRetentionService(
        repository=repository,
        archive=FakeArchive(RetentionFailure(code=RetentionFailureCode.ARCHIVE_VERIFICATION_FAILED, retryable=False)),
        policy=RetentionPolicy(),
        clock=lambda: _NOW,
    )

    result: Final = await service.run_once()

    assert isinstance(result, RetentionFailure)
    assert repository.deleted == ()


async def test_verified_archive_deletes_only_manifest_event_ids() -> None:
    repository: Final = FakeRepository((_batch(), None))
    service: Final = EventRetentionService(
        repository=repository,
        archive=FakeArchive(_manifest()),
        policy=RetentionPolicy(batch_size=20),
        clock=lambda: _NOW,
    )

    result: Final = await service.run_once()

    assert isinstance(result, RetentionArchived)
    assert repository.deleted == _manifest().event_ids
    assert repository.loads == [
        (RetentionScope.STANDARD, datetime(2026, 5, 1, tzinfo=UTC), 20),
        (RetentionScope.AUDIT, datetime(2026, 5, 1, tzinfo=UTC), 20),
    ]


async def test_audit_scope_uses_longer_retention_after_standard_is_idle() -> None:
    repository: Final = FakeRepository((None, None))
    service: Final = EventRetentionService(
        repository=repository,
        archive=FakeArchive(_manifest()),
        policy=RetentionPolicy(event_retention_days=90, audit_retention_days=180),
        clock=lambda: _NOW,
    )

    result: Final = await service.run_once()

    assert isinstance(result, RetentionIdle)
    assert repository.loads == [
        (RetentionScope.STANDARD, datetime(2026, 5, 1, tzinfo=UTC), 10_000),
        (RetentionScope.AUDIT, datetime(2026, 2, 1, tzinfo=UTC), 10_000),
    ]


def test_audit_retention_cannot_be_shorter_than_standard_retention() -> None:
    with pytest.raises(ValueError, match="cannot be shorter"):
        RetentionPolicy(event_retention_days=90, audit_retention_days=30)
