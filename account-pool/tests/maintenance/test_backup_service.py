"""验证 PostgreSQL 备份加密、校验失败保护和恢复确认。"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from account_pool.maintenance.backup_models import BackupCreated, BackupFailure, BackupFailureCode, RestoreCompleted
from account_pool.maintenance.backup_service import PostgresBackupService

_NOW: Final = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
_DUMP: Final = b"postgresql custom dump with protected database contents"


class FakePostgresTools:
    def __init__(self, *, dump_succeeds: bool = True, validation_succeeds: bool = True) -> None:
        self.dump_succeeds = dump_succeeds
        self.validation_succeeds = validation_succeeds
        self.restore_calls = 0
        self.restored_content: bytes | None = None

    def dump(self, database_url: str, consume: Callable[[bytes], None]) -> bool:
        assert database_url == "postgresql://user:password@db/database"
        consume(_DUMP)
        return self.dump_succeeds

    def validate(self, archive_path: Path) -> bool:
        assert archive_path.read_bytes() == _DUMP
        return self.validation_succeeds

    def restore(self, database_url: str, archive_path: Path) -> bool:
        assert database_url == "postgresql://user:password@db/database"
        self.restore_calls += 1
        self.restored_content = archive_path.read_bytes()
        return True


def _service(tools: FakePostgresTools) -> PostgresBackupService:
    return PostgresBackupService(tools, b"a" * 32, "backup-key", clock=lambda: _NOW)


def test_backup_encrypts_dump_and_can_be_verified(tmp_path: Path) -> None:
    service: Final = _service(FakePostgresTools())

    result: Final = service.backup("postgresql://user:password@db/database", tmp_path)

    assert isinstance(result, BackupCreated)
    ciphertext: Final = (result.archive_directory / "database.dump.aesgcm").read_bytes()
    assert _DUMP not in ciphertext
    assert service.verify(result.archive_directory) == result.manifest


def test_restore_requires_exact_archive_id_confirmation(tmp_path: Path) -> None:
    tools: Final = FakePostgresTools()
    service: Final = _service(tools)
    backup: Final = service.backup("postgresql://user:password@db/database", tmp_path)
    assert isinstance(backup, BackupCreated)

    rejected: Final = service.restore(
        "postgresql://user:password@db/database",
        backup.archive_directory,
        "wrong-archive",
    )
    restored: Final = service.restore(
        "postgresql://user:password@db/database",
        backup.archive_directory,
        backup.manifest.archive_id,
    )

    assert isinstance(rejected, BackupFailure)
    assert rejected.code == BackupFailureCode.CONFIRMATION_REQUIRED
    assert isinstance(restored, RestoreCompleted)
    assert tools.restore_calls == 1
    assert tools.restored_content == _DUMP


def test_tampered_ciphertext_is_rejected_before_restore(tmp_path: Path) -> None:
    tools: Final = FakePostgresTools()
    service: Final = _service(tools)
    backup: Final = service.backup("postgresql://user:password@db/database", tmp_path)
    assert isinstance(backup, BackupCreated)
    encrypted_path: Final = backup.archive_directory / "database.dump.aesgcm"
    encrypted_path.write_bytes(encrypted_path.read_bytes() + b"tampered")

    result: Final = service.restore(
        "postgresql://user:password@db/database",
        backup.archive_directory,
        backup.manifest.archive_id,
    )

    assert isinstance(result, BackupFailure)
    assert result.code == BackupFailureCode.VERIFICATION_FAILED
    assert tools.restore_calls == 0


def test_failed_dump_does_not_publish_archive(tmp_path: Path) -> None:
    service: Final = _service(FakePostgresTools(dump_succeeds=False))

    result: Final = service.backup("postgresql://user:password@db/database", tmp_path)

    assert isinstance(result, BackupFailure)
    assert result.code == BackupFailureCode.DUMP_FAILED
    assert tuple(tmp_path.iterdir()) == ()
