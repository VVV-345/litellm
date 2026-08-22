"""验证事件归档加密、校验、幂等和冲突保护。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import UUID

from account_pool.events.models import EventLogEntry, EventOperationalSummary, EventQueryOutcome
from account_pool.retention.archive import EncryptedEventArchive, decode_archive_key
from account_pool.retention.models import (
    ArchiveManifest,
    ArchiveWindow,
    RetentionBatch,
    RetentionFailure,
    RetentionScope,
)

_NOW: Final = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _event(event_id: str) -> EventLogEntry:
    operation_id: Final = UUID("20000000-0000-0000-0000-000000000001")
    return EventLogEntry(
        event_id=UUID(event_id),
        event_type="parser_task_completed",
        occurred_at=datetime(2026, 4, 2, 1, 0, tzinfo=UTC),
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
            operation_id=operation_id,
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
        events=(_event("10000000-0000-0000-0000-000000000001"),),
    )


def test_archive_is_encrypted_verified_and_idempotent(tmp_path: Path) -> None:
    archive: Final = EncryptedEventArchive(tmp_path, b"a" * 32, "test-key", clock=lambda: _NOW)

    first: Final = archive.write_verified(_batch())
    second: Final = archive.write_verified(_batch())

    assert isinstance(first, ArchiveManifest)
    assert second == first
    archive_directory: Final = tmp_path / "2026" / "04" / first.archive_id
    ciphertext: Final = (archive_directory / "events.aesgcm").read_bytes()
    assert b"parser_task_completed" not in ciphertext
    assert ArchiveManifest.model_validate_json((archive_directory / "manifest.json").read_bytes()) == first


def test_archive_rejects_existing_content_from_another_key(tmp_path: Path) -> None:
    first_archive: Final = EncryptedEventArchive(tmp_path, b"a" * 32, "test-key", clock=lambda: _NOW)
    other_archive: Final = EncryptedEventArchive(tmp_path, b"b" * 32, "test-key", clock=lambda: _NOW)
    assert isinstance(first_archive.write_verified(_batch()), ArchiveManifest)

    result: Final = other_archive.write_verified(_batch())

    assert isinstance(result, RetentionFailure)
    assert result.retryable is False


def test_archive_key_requires_urlsafe_base64_encoded_32_bytes() -> None:
    encoded: Final = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE"

    assert decode_archive_key(encoded) == b"a" * 32
