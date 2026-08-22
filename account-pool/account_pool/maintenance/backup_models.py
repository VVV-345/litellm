"""定义 PostgreSQL 加密备份清单及备份恢复结果契约。"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field

from account_pool.models import FrozenModel


class BackupManifest(FrozenModel):
    schema_version: Literal[1] = 1
    archive_id: str = Field(pattern=r"^postgres-\d{8}T\d{6}Z-[0-9a-f]{8}$")
    created_at: AwareDatetime
    database_format: Literal["postgresql-custom"] = "postgresql-custom"
    encryption: Literal["AES-256-GCM"] = "AES-256-GCM"
    key_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    nonce_base64: str = Field(min_length=16, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    tag_base64: str = Field(min_length=20, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    plaintext_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ciphertext_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plaintext_size: int = Field(ge=1)
    ciphertext_size: int = Field(ge=1)


class BackupFailureCode(StrEnum):
    CONFIGURATION_INVALID = "configuration_invalid"
    TOOL_UNAVAILABLE = "tool_unavailable"
    DUMP_FAILED = "dump_failed"
    ARCHIVE_WRITE_FAILED = "archive_write_failed"
    INVALID_MANIFEST = "invalid_manifest"
    VERIFICATION_FAILED = "verification_failed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    RESTORE_VALIDATION_FAILED = "restore_validation_failed"
    RESTORE_FAILED = "restore_failed"


class BackupFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: BackupFailureCode
    retryable: bool


class BackupCreated(FrozenModel):
    status: Literal["created"] = "created"
    archive_directory: Path
    manifest: BackupManifest


class RestoreCompleted(FrozenModel):
    status: Literal["restored"] = "restored"
    archive_id: str


BackupResult = BackupCreated | BackupFailure
RestoreResult = RestoreCompleted | BackupFailure
