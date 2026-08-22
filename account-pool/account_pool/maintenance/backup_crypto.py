"""流式加密 PostgreSQL dump，并在恢复前验证认证标签与校验和。"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ChunkWriter = Callable[[Callable[[bytes], None]], bool]


@dataclass(frozen=True, slots=True)
class EncryptedDumpMetadata:
    tag: bytes
    plaintext_sha256: str
    ciphertext_sha256: str
    plaintext_size: int
    ciphertext_size: int


def encrypt_dump(
    *,
    path: Path,
    key: bytes,
    nonce: bytes,
    associated_data: bytes,
    producer: ChunkWriter,
) -> EncryptedDumpMetadata | None:
    encryptor: Final = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(associated_data)
    plaintext_hash: Final = hashlib.sha256()
    ciphertext_hash: Final = hashlib.sha256()
    plaintext_size = 0
    ciphertext_size = 0
    with path.open("xb") as stream:

        def write(chunk: bytes) -> None:
            nonlocal plaintext_size, ciphertext_size
            encrypted: Final = encryptor.update(chunk)
            plaintext_hash.update(chunk)
            ciphertext_hash.update(encrypted)
            plaintext_size += len(chunk)
            ciphertext_size += len(encrypted)
            stream.write(encrypted)

        succeeded: Final = producer(write)
        if not succeeded:
            return None
        final: Final = encryptor.finalize()
        ciphertext_hash.update(final)
        ciphertext_size += len(final)
        stream.write(final)
        stream.flush()
        os.fsync(stream.fileno())
    return EncryptedDumpMetadata(
        tag=encryptor.tag,
        plaintext_sha256=plaintext_hash.hexdigest(),
        ciphertext_sha256=ciphertext_hash.hexdigest(),
        plaintext_size=plaintext_size,
        ciphertext_size=ciphertext_size,
    )


def decrypt_verified_dump(
    *,
    source: Path,
    destination: Path,
    key: bytes,
    nonce: bytes,
    tag: bytes,
    associated_data: bytes,
    expected_plaintext_sha256: str,
    expected_ciphertext_sha256: str,
    expected_plaintext_size: int,
    expected_ciphertext_size: int,
) -> bool:
    decryptor: Final = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    decryptor.authenticate_additional_data(associated_data)
    plaintext_hash: Final = hashlib.sha256()
    ciphertext_hash: Final = hashlib.sha256()
    plaintext_size = 0
    ciphertext_size = 0
    with source.open("rb") as encrypted, _secure_binary_writer(destination) as plaintext:
        while chunk := encrypted.read(1024 * 1024):
            decrypted = decryptor.update(chunk)
            ciphertext_hash.update(chunk)
            plaintext_hash.update(decrypted)
            ciphertext_size += len(chunk)
            plaintext_size += len(decrypted)
            plaintext.write(decrypted)
        final: Final = decryptor.finalize()
        plaintext_hash.update(final)
        plaintext_size += len(final)
        plaintext.write(final)
        plaintext.flush()
        os.fsync(plaintext.fileno())
    return (
        plaintext_hash.hexdigest() == expected_plaintext_sha256
        and ciphertext_hash.hexdigest() == expected_ciphertext_sha256
        and plaintext_size == expected_plaintext_size
        and ciphertext_size == expected_ciphertext_size
    )


def _secure_binary_writer(path: Path) -> BinaryIO:
    descriptor: Final = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def verify_encrypted_dump(
    *,
    source: Path,
    key: bytes,
    nonce: bytes,
    tag: bytes,
    associated_data: bytes,
    expected_plaintext_sha256: str,
    expected_ciphertext_sha256: str,
    expected_plaintext_size: int,
    expected_ciphertext_size: int,
) -> bool:
    decryptor: Final = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    decryptor.authenticate_additional_data(associated_data)
    plaintext_hash: Final = hashlib.sha256()
    ciphertext_hash: Final = hashlib.sha256()
    plaintext_size = 0
    ciphertext_size = 0
    with source.open("rb") as encrypted:
        while chunk := encrypted.read(1024 * 1024):
            decrypted = decryptor.update(chunk)
            ciphertext_hash.update(chunk)
            plaintext_hash.update(decrypted)
            ciphertext_size += len(chunk)
            plaintext_size += len(decrypted)
        final: Final = decryptor.finalize()
        plaintext_hash.update(final)
        plaintext_size += len(final)
    return (
        plaintext_hash.hexdigest() == expected_plaintext_sha256
        and ciphertext_hash.hexdigest() == expected_ciphertext_sha256
        and plaintext_size == expected_plaintext_size
        and ciphertext_size == expected_ciphertext_size
    )
