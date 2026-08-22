"""以 AES-256-GCM 加密、校验并原子保存脱敏事件归档。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import AwareDatetime, JsonValue, TypeAdapter, ValidationError

from account_pool.events.models import EventLogEntry
from account_pool.retention.models import (
    ArchiveManifest,
    RetentionBatch,
    RetentionFailure,
    RetentionFailureCode,
)

Clock = Callable[[], AwareDatetime]
_JSON_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)


class ArchiveWriter(Protocol):
    def write_verified(self, batch: RetentionBatch) -> ArchiveManifest | RetentionFailure: ...


def utc_now() -> AwareDatetime:
    return datetime.now(UTC)


def decode_archive_key(value: str) -> bytes:
    try:
        padding: Final = "=" * (-len(value) % 4)
        decoded: Final = base64.b64decode(f"{value}{padding}", altchars=b"-_", validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as error:
        raise ValueError("archive key must be URL-safe base64") from error
    if len(decoded) != 32:
        raise ValueError("archive key must decode to 32 bytes")
    return decoded


class EncryptedEventArchive:
    def __init__(
        self,
        root: Path,
        key: bytes,
        key_id: str,
        *,
        clock: Clock = utc_now,
    ) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM requires a 32-byte key")
        if not key_id or len(key_id) > 100 or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for character in key_id
        ):
            raise ValueError("archive key_id contains unsupported characters")
        self._root: Final = root
        self._cipher: Final = AESGCM(key)
        self._key_id: Final = key_id
        self._clock: Final = clock

    def write_verified(self, batch: RetentionBatch) -> ArchiveManifest | RetentionFailure:
        try:
            payload: Final = _serialize_batch(batch)
            plaintext_sha256: Final = hashlib.sha256(payload).hexdigest()
            archive_id: Final = _archive_id(batch, plaintext_sha256)
            destination: Final = self._destination(batch, archive_id)
            if destination.exists():
                return self._verify_existing(destination, batch, payload)
            nonce: Final = os.urandom(12)
            ciphertext: Final = self._cipher.encrypt(nonce, payload, archive_id.encode("ascii"))
            manifest: Final = _manifest(
                batch=batch,
                archive_id=archive_id,
                plaintext_sha256=plaintext_sha256,
                ciphertext=ciphertext,
                nonce=nonce,
                key_id=self._key_id,
                created_at=self._clock(),
            )
            temporary: Final = destination.with_name(f".{archive_id}.{uuid4().hex}.tmp")
            try:
                self._write_directory(temporary, manifest, ciphertext)
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    temporary.replace(destination)
                except OSError:
                    if not destination.exists():
                        raise
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
            return self._verify_existing(destination, batch, payload)
        except (OSError, ValueError, ValidationError):
            return RetentionFailure(code=RetentionFailureCode.ARCHIVE_WRITE_FAILED, retryable=True)

    def _destination(self, batch: RetentionBatch, archive_id: str) -> Path:
        return self._root / f"{batch.window.started_at.year:04d}" / f"{batch.window.started_at.month:02d}" / archive_id

    def _write_directory(self, directory: Path, manifest: ArchiveManifest, ciphertext: bytes) -> None:
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir()
        _write_synced(directory / "events.aesgcm", ciphertext)
        manifest_bytes: Final = _canonical_json(cast(JsonValue, manifest.model_dump(mode="json"))) + b"\n"
        _write_synced(directory / "manifest.json", manifest_bytes)

    def _verify_existing(
        self,
        directory: Path,
        expected_batch: RetentionBatch,
        expected_payload: bytes,
    ) -> ArchiveManifest | RetentionFailure:
        try:
            manifest: Final = ArchiveManifest.model_validate_json((directory / "manifest.json").read_bytes())
            ciphertext: Final = (directory / "events.aesgcm").read_bytes()
            nonce: Final = base64.urlsafe_b64decode(f"{manifest.nonce_base64}{'=' * (-len(manifest.nonce_base64) % 4)}")
            plaintext: Final = self._cipher.decrypt(nonce, ciphertext, manifest.archive_id.encode("ascii"))
            valid: Final = (
                manifest.scope == expected_batch.scope
                and manifest.window == expected_batch.window
                and manifest.event_ids == tuple(event.event_id for event in expected_batch.events)
                and manifest.key_id == self._key_id
                and manifest.plaintext_sha256 == hashlib.sha256(plaintext).hexdigest()
                and manifest.ciphertext_sha256 == hashlib.sha256(ciphertext).hexdigest()
                and plaintext == expected_payload
                and _validate_payload(plaintext) == expected_batch.events
            )
            if not valid:
                return RetentionFailure(code=RetentionFailureCode.ARCHIVE_CONFLICT, retryable=False)
            return manifest
        except (InvalidTag, OSError, ValueError, ValidationError, binascii.Error):
            return RetentionFailure(code=RetentionFailureCode.ARCHIVE_VERIFICATION_FAILED, retryable=False)


def _serialize_batch(batch: RetentionBatch) -> bytes:
    return b"".join(
        _canonical_json(
            cast(
                JsonValue,
                {
                    "archive_schema_version": 1,
                    "event": event.model_dump(mode="json"),
                },
            )
        )
        + b"\n"
        for event in batch.events
    )


def _validate_payload(payload: bytes) -> tuple[EventLogEntry, ...]:
    lines: Final = tuple(line for line in payload.splitlines() if line)
    documents: Final = tuple(_JSON_VALUE.validate_json(line) for line in lines)
    return tuple(_decode_document(document) for document in documents)


def _decode_document(document: JsonValue) -> EventLogEntry:
    if not isinstance(document, dict) or set(document) != {"archive_schema_version", "event"}:
        raise ValueError("invalid archive document")
    if document["archive_schema_version"] != 1:
        raise ValueError("unsupported archive schema version")
    return EventLogEntry.model_validate(document["event"])


def _archive_id(batch: RetentionBatch, plaintext_sha256: str) -> str:
    month: Final = batch.window.started_at.strftime("%Y-%m")
    return f"{batch.scope.value}-{month}-{plaintext_sha256[:16]}"


def _manifest(
    *,
    batch: RetentionBatch,
    archive_id: str,
    plaintext_sha256: str,
    ciphertext: bytes,
    nonce: bytes,
    key_id: str,
    created_at: AwareDatetime,
) -> ArchiveManifest:
    return ArchiveManifest(
        archive_id=archive_id,
        scope=batch.scope,
        window=batch.window,
        event_count=len(batch.events),
        event_ids=tuple(event.event_id for event in batch.events),
        plaintext_sha256=plaintext_sha256,
        ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
        encryption="AES-256-GCM",
        key_id=key_id,
        nonce_base64=base64.urlsafe_b64encode(nonce).decode("ascii").rstrip("="),
        created_at=created_at,
    )


def _canonical_json(value: JsonValue) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _write_synced(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
