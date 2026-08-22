"""提供 PostgreSQL 加密备份、校验和恢复的命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal, cast

from pydantic import Field, model_validator

from account_pool.maintenance.backup_models import (
    BackupCreated,
    BackupFailure,
    BackupFailureCode,
    BackupManifest,
    RestoreCompleted,
)
from account_pool.maintenance.backup_service import PostgresBackupService
from account_pool.maintenance.postgres_tools import SubprocessPostgresTools
from account_pool.models import FrozenModel
from account_pool.retention.archive import decode_archive_key


class _CliArguments(FrozenModel):
    command: Literal["backup", "verify", "restore"]
    path: str = Field(min_length=1)
    confirm: str | None = None

    @model_validator(mode="after")
    def validate_restore_confirmation(self) -> _CliArguments:
        if self.command == "restore" and self.confirm is None:
            raise ValueError("restore requires confirmation")
        return self


CommandResult = BackupCreated | BackupFailure | BackupManifest | RestoreCompleted


def main(argv: Sequence[str] | None = None) -> int:
    arguments: Final = _CliArguments.model_validate(cast(object, vars(_parser().parse_args(argv))))
    database_url: Final = os.environ.get("DATABASE_URL")
    key_value: Final = os.environ.get("ACCOUNT_POOL_BACKUP_KEY")
    key_id: Final = os.environ.get("ACCOUNT_POOL_BACKUP_KEY_ID", "default")
    if key_value is None or (arguments.command != "verify" and database_url is None):
        return _emit(BackupFailure(code=BackupFailureCode.CONFIGURATION_INVALID, retryable=False), 2)
    try:
        service: Final = PostgresBackupService(
            SubprocessPostgresTools(
                pg_dump=os.environ.get("ACCOUNT_POOL_PG_DUMP", "pg_dump"),
                pg_restore=os.environ.get("ACCOUNT_POOL_PG_RESTORE", "pg_restore"),
            ),
            decode_archive_key(key_value),
            key_id,
        )
    except ValueError:
        return _emit(BackupFailure(code=BackupFailureCode.CONFIGURATION_INVALID, retryable=False), 2)
    result: Final = _execute(service, database_url, arguments)
    return _emit(result, 1 if isinstance(result, BackupFailure) else 0)


def _execute(service: PostgresBackupService, database_url: str | None, arguments: _CliArguments) -> CommandResult:
    match arguments.command:
        case "backup":
            if database_url is None:
                return BackupFailure(code=BackupFailureCode.CONFIGURATION_INVALID, retryable=False)
            return service.backup(database_url, Path(arguments.path))
        case "verify":
            return service.verify(Path(arguments.path))
        case "restore":
            if arguments.confirm is None or database_url is None:
                return BackupFailure(code=BackupFailureCode.CONFIRMATION_REQUIRED, retryable=False)
            return service.restore(database_url, Path(arguments.path), arguments.confirm)


def _emit(result: CommandResult, exit_code: int) -> int:
    sys.stdout.write(f"{result.model_dump_json()}\n")
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser: Final = argparse.ArgumentParser(description="Account Pool PostgreSQL encrypted backup utility")
    subcommands: Final = parser.add_subparsers(dest="command", required=True)
    backup: Final = subcommands.add_parser("backup")
    backup.add_argument("--path", required=True)
    verify: Final = subcommands.add_parser("verify")
    verify.add_argument("--path", required=True)
    restore: Final = subcommands.add_parser("restore")
    restore.add_argument("--path", required=True)
    restore.add_argument("--confirm", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
