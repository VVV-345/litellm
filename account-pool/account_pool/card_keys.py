"""本模块负责号池卡片 Key 的生成、哈希和公开响应，禁止持久化或返回旧 Key 明文。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from account_pool.domain import utc_now
from account_pool.result import Failure, FailureCode, Result, Success


_PREFIX: Final = "cpk_"
_TOKEN_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class CardKeyRecord:
    key_id: UUID
    card_id: UUID
    key_hash: str = field(repr=False)
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class IssuedCardKey:
    record: CardKeyRecord
    plaintext: str = field(repr=False)


def issue_card_key(card_id: UUID, key_id: UUID, created_at: datetime) -> IssuedCardKey:
    """生成高熵卡片 Key；调用方只能在本次响应中取得明文。"""
    plaintext: Final = f"{_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"
    record: Final = CardKeyRecord(
        key_id=key_id,
        card_id=card_id,
        key_hash=hash_card_key(plaintext),
        created_at=created_at,
        revoked_at=None,
        last_used_at=None,
    )
    return IssuedCardKey(record=record, plaintext=plaintext)


def hash_card_key(plaintext: str) -> str:
    """对随机凭据做不可逆哈希，数据库泄露时不能直接恢复可用 Key。"""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def matches_card_key(plaintext: str, record: CardKeyRecord) -> bool:
    """使用常量时间比较校验 Key，并拒绝已撤销凭据。"""
    return record.revoked_at is None and hmac.compare_digest(hash_card_key(plaintext), record.key_hash)


class CardKeyStatus(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    key_id: UUID
    card_id: UUID
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


class CardKeyIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CardKeyStatus
    key: str


class CardKeyChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_key_id: UUID


class CardKeyRepository(Protocol):
    async def get(self, card_id: UUID) -> CardKeyRecord | None: ...

    async def save(self, record: CardKeyRecord, expected_key_id: UUID | None) -> bool: ...

    async def revoke(self, card_id: UUID, expected_key_id: UUID, revoked_at: datetime) -> bool: ...


class CardKeyService:
    def __init__(self, repository: CardKeyRepository) -> None:
        self._repository: Final = repository

    async def status(self, card_id: UUID) -> CardKeyStatus | None:
        record: Final = await self._repository.get(card_id)
        return None if record is None else CardKeyStatus.model_validate(record)

    async def issue(self, card_id: UUID, expected_key_id: UUID | None = None) -> Result[CardKeyIssue]:
        issued: Final = issue_card_key(card_id, uuid4(), utc_now())
        if not await self._repository.save(issued.record, expected_key_id):
            return Failure(FailureCode.CONFLICT, "Card is unavailable or key has changed; refresh before retrying")
        return Success(CardKeyIssue(status=CardKeyStatus.model_validate(issued.record), key=issued.plaintext))

    async def revoke(self, card_id: UUID, expected_key_id: UUID) -> Result[None]:
        if not await self._repository.revoke(card_id, expected_key_id, utc_now()):
            return Failure(FailureCode.CONFLICT, "Card key has changed; refresh before retrying")
        return Success(None)
