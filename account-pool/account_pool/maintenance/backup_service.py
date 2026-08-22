"""编排 PostgreSQL 流式加密备份、完整性校验和显式确认恢复。"""

from __future__ import annotations

import base64
import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from pydantic import AwareDatetime, ValidationError

from account_pool.maintenance.backup_crypto import decrypt_verified_dump, encrypt_dump, verify_encrypted_dump
from account_pool.maintenance.backup_models import (
    BackupCreated,
    BackupFailure,
    BackupFailureCode,
    BackupManifest,
    BackupResult,
    RestoreCompleted,
    RestoreResult,
)
from account_pool.maintenance.postgres_tools import PostgresTools

Clock = Callable[[], AwareDatetime]


def utc_now() -> AwareDatetime:
    return datetime.now(UTC)


class PostgresBackupService:
    def __init__(self, tools: PostgresTools, key: bytes, key_id: str, *, clock: Clock = utc_now) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM requires a 32-byte key")
        if not key_id or len(key_id) > 100 or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for character in key_id
        ):
            raise ValueError("backup key_id contains unsupported characters")
        self._tools: Final = tools
        self._key: Final = key
        self._key_id: Final = key_id
        self._clock: Final = clock

    def backup(self, database_url: str, output_root: Path) -> BackupResult:
        created_at: Final = self._clock()
        archive_id: Final = f"postgres-{created_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        destination: Final = output_root / archive_id
        temporary: Final = output_root / f".{archive_id}.tmp"
        nonce: Final = os.urandom(12)
        try:
            output_root.mkdir(parents=True, exist_ok=True)
            temporary.mkdir()
            encrypted_path: Final = temporary / "database.dump.aesgcm"
            encrypted: Final = encrypt_dump(
                path=encrypted_path,
                key=self._key,
                nonce=nonce,
                associated_data=archive_id.encode("ascii"),
                producer=lambda consume: self._tools.dump(database_url, consume),
            )
            if encrypted is None:
                return BackupFailure(code=BackupFailureCode.DUMP_FAILED, retryable=True)
            if encrypted.plaintext_size == 0:
                return BackupFailure(code=BackupFailureCode.DUMP_FAILED, retryable=True)
            manifest: Final = BackupManifest(
                archive_id=archive_id,
                created_at=created_at,
                key_id=self._key_id,
                nonce_base64=_encode(nonce),
                tag_base64=_encode(encrypted.tag),
                plaintext_sha256=encrypted.plaintext_sha256,
                ciphertext_sha256=encrypted.ciphertext_sha256,
                plaintext_size=encrypted.plaintext_size,
                ciphertext_size=encrypted.ciphertext_size,
            )
            _write_manifest(temporary / "manifest.json", manifest)
            temporary.replace(destination)
            verified: Final = self.verify(destination)
            if isinstance(verified, BackupFailure):
                return verified
            return BackupCreated(archive_directory=destination, manifest=verified)
        except FileNotFoundError:
            return BackupFailure(code=BackupFailureCode.TOOL_UNAVAILABLE, retryable=False)
        except ValueError:
            return BackupFailure(code=BackupFailureCode.CONFIGURATION_INVALID, retryable=False)
        except OSError:
            return BackupFailure(code=BackupFailureCode.ARCHIVE_WRITE_FAILED, retryable=True)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def verify(self, archive_directory: Path) -> BackupManifest | BackupFailure:
        try:
            manifest: Final = BackupManifest.model_validate_json(
                (archive_directory / "manifest.json").read_bytes()
            )
            valid: Final = verify_encrypted_dump(
                source=archive_directory / "database.dump.aesgcm",
                key=self._key,
                nonce=_decode(manifest.nonce_base64),
                tag=_decode(manifest.tag_base64),
                associated_data=manifest.archive_id.encode("ascii"),
                expected_plaintext_sha256=manifest.plaintext_sha256,
                expected_ciphertext_sha256=manifest.ciphertext_sha256,
                expected_plaintext_size=manifest.plaintext_size,
                expected_ciphertext_size=manifest.ciphertext_size,
            )
            return manifest if valid else BackupFailure(code=BackupFailureCode.VERIFICATION_FAILED, retryable=False)
        except InvalidTag:
            return BackupFailure(code=BackupFailureCode.VERIFICATION_FAILED, retryable=False)
        except (OSError, ValueError, ValidationError):
            return BackupFailure(code=BackupFailureCode.INVALID_MANIFEST, retryable=False)

    def restore(self, database_url: str, archive_directory: Path, confirmation: str) -> RestoreResult:
        plaintext: Final = archive_directory / f".{uuid4().hex}.restore.dump"
        try:
            manifest: Final = BackupManifest.model_validate_json(
                (archive_directory / "manifest.json").read_bytes()
            )
            if confirmation != manifest.archive_id:
                return BackupFailure(code=BackupFailureCode.CONFIRMATION_REQUIRED, retryable=False)
            valid: Final = decrypt_verified_dump(
                source=archive_directory / "database.dump.aesgcm",
                destination=plaintext,
                key=self._key,
                nonce=_decode(manifest.nonce_base64),
                tag=_decode(manifest.tag_base64),
                associated_data=manifest.archive_id.encode("ascii"),
                expected_plaintext_sha256=manifest.plaintext_sha256,
                expected_ciphertext_sha256=manifest.ciphertext_sha256,
                expected_plaintext_size=manifest.plaintext_size,
                expected_ciphertext_size=manifest.ciphertext_size,
            )
            if not valid:
                return BackupFailure(code=BackupFailureCode.VERIFICATION_FAILED, retryable=False)
            if not self._tools.validate(plaintext):
                return BackupFailure(code=BackupFailureCode.RESTORE_VALIDATION_FAILED, retryable=False)
            if not self._tools.restore(database_url, plaintext):
                return BackupFailure(code=BackupFailureCode.RESTORE_FAILED, retryable=False)
            return RestoreCompleted(archive_id=manifest.archive_id)
        except FileNotFoundError:
            return BackupFailure(code=BackupFailureCode.TOOL_UNAVAILABLE, retryable=False)
        except InvalidTag:
            return BackupFailure(code=BackupFailureCode.VERIFICATION_FAILED, retryable=False)
        except ValueError:
            return BackupFailure(code=BackupFailureCode.CONFIGURATION_INVALID, retryable=False)
        except (OSError, ValidationError):
            return BackupFailure(code=BackupFailureCode.INVALID_MANIFEST, retryable=False)
        finally:
            plaintext.unlink(missing_ok=True)


def _write_manifest(path: Path, manifest: BackupManifest) -> None:
    content: Final = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.b64decode(f"{value}{'=' * (-len(value) % 4)}", altchars=b"-_", validate=True)
