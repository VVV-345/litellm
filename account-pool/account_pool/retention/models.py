"""定义事件保留策略、月度归档批次、清单和失败契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from account_pool.events.models import EventLogEntry
from account_pool.models import FrozenModel


class RetentionScope(StrEnum):
    STANDARD = "standard"
    AUDIT = "audit"


class RetentionPolicy(FrozenModel):
    event_retention_days: int = Field(default=90, ge=1)
    audit_retention_days: int = Field(default=90, ge=1)
    batch_size: int = Field(default=10_000, ge=1, le=100_000)

    @model_validator(mode="after")
    def validate_audit_retention(self) -> Self:
        if self.audit_retention_days < self.event_retention_days:
            raise ValueError("audit retention cannot be shorter than event retention")
        return self


class ArchiveWindow(FrozenModel):
    started_at: AwareDatetime
    ended_at: AwareDatetime

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.started_at >= self.ended_at:
            raise ValueError("archive window must have positive duration")
        return self


class RetentionBatch(FrozenModel):
    scope: RetentionScope
    window: ArchiveWindow
    events: tuple[EventLogEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_events(self) -> Self:
        if any(not (self.window.started_at <= event.occurred_at < self.window.ended_at) for event in self.events):
            raise ValueError("archive events must belong to the declared window")
        if any((event.audit is not None) != (self.scope == RetentionScope.AUDIT) for event in self.events):
            raise ValueError("archive events do not match the retention scope")
        return self


class ArchiveManifest(FrozenModel):
    schema_version: int = 1
    archive_id: str = Field(pattern=r"^(standard|audit)-\d{4}-\d{2}-[0-9a-f]{16}$")
    scope: RetentionScope
    window: ArchiveWindow
    event_count: int = Field(ge=1)
    event_ids: tuple[UUID, ...] = Field(min_length=1)
    plaintext_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ciphertext_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    encryption: str = Field(pattern=r"^AES-256-GCM$")
    key_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    nonce_base64: str = Field(min_length=16, max_length=32)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_event_count(self) -> Self:
        if self.event_count != len(self.event_ids) or len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("archive manifest event IDs must be unique and match the count")
        return self


class RetentionFailureCode(StrEnum):
    DATABASE_UNAVAILABLE = "database_unavailable"
    INVALID_STORED_DATA = "invalid_stored_data"
    ARCHIVE_WRITE_FAILED = "archive_write_failed"
    ARCHIVE_VERIFICATION_FAILED = "archive_verification_failed"
    ARCHIVE_CONFLICT = "archive_conflict"


class RetentionFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: RetentionFailureCode
    retryable: bool


class RetentionIdle(FrozenModel):
    status: Literal["idle"] = "idle"


class RetentionArchived(FrozenModel):
    status: Literal["archived"] = "archived"
    manifest: ArchiveManifest
    deleted_event_count: int = Field(ge=0)


RetentionRunResult = RetentionArchived | RetentionFailure | RetentionIdle
