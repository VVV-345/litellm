from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from typing import Annotated, Final, Protocol

from pydantic import Field, SecretStr, TypeAdapter, ValidationError

from litellm.proxy.management_endpoints.account_pool_cockpit_receipt_contracts import (
    CredentialBindings,
    CredentialReceipt,
    CredentialReceiptPayload,
    CredentialReceiptRejection,
    NativeCredentialBindings,
    NativeCredentialReceipt,
    NativeCredentialReceiptPayload,
)

Receipt = CredentialReceipt | NativeCredentialReceipt
Bindings = CredentialBindings | NativeCredentialBindings
_RECEIPT: Final[TypeAdapter[Receipt]] = TypeAdapter(Annotated[Receipt, Field(discriminator="status")])
_HASH: Final = re.compile(r"^[0-9a-f]{64}$")
_LIFETIME: Final = 300


class CredentialObservationReader(Protocol):
    """Trusted server observations only, never browser-supplied bindings."""

    async def read(self, subject_hash: str) -> Bindings | CredentialReceiptRejection: ...


def _canonical(payload: CredentialReceiptPayload | NativeCredentialReceiptPayload) -> bytes:
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


class CockpitCredentialReceiptService:
    def __init__(
        self,
        observations: CredentialObservationReader | None,
        signing_key: SecretStr,
        clock: Callable[[], int],
    ) -> None:
        if len(signing_key.get_secret_value().encode()) < 32:
            raise ValueError("Receipt signing key must contain at least 32 bytes")
        self._observations: Final = observations
        self._key: Final = signing_key
        self._clock: Final = clock

    def _now(self) -> int | None:
        try:
            value: Final = self._clock()
        except Exception:  # noqa: BLE001  # A clock failure must not issue an unbounded receipt.
            return None
        return value if type(value) is int and value > 0 else None

    def _signature(self, payload: CredentialReceiptPayload | NativeCredentialReceiptPayload) -> str:
        return hmac.new(self._key.get_secret_value().encode(), _canonical(payload), hashlib.sha256).hexdigest()

    async def issue(self, subject_hash: str) -> Receipt | CredentialReceiptRejection:
        if _HASH.fullmatch(subject_hash) is None:
            return CredentialReceiptRejection("invalid_subject")
        issued_at: Final = self._now()
        if issued_at is None:
            return CredentialReceiptRejection("invalid_clock")
        bindings: Final = await self._stable_observation(subject_hash)
        if isinstance(bindings, CredentialReceiptRejection):
            return bindings
        finished_at: Final = self._now()
        if finished_at is None or not issued_at <= finished_at < issued_at + _LIFETIME:
            return CredentialReceiptRejection("receipt_time_invalid")
        if isinstance(bindings, NativeCredentialBindings):
            payload: Final = NativeCredentialReceiptPayload(
                issued_at=issued_at, expires_at=issued_at + _LIFETIME, bindings=bindings
            )
            return NativeCredentialReceipt(payload=payload, signature=self._signature(payload))
        legacy: Final = CredentialReceiptPayload(
            issued_at=issued_at, expires_at=issued_at + _LIFETIME, bindings=bindings
        )
        return CredentialReceipt(payload=legacy, signature=self._signature(legacy))

    async def revalidate(self, subject_hash: str, encoded: str) -> Receipt | CredentialReceiptRejection:
        if _HASH.fullmatch(subject_hash) is None:
            return CredentialReceiptRejection("invalid_subject")
        if len(encoded) > 16384:
            return CredentialReceiptRejection("invalid_receipt")
        try:
            receipt: Final = _RECEIPT.validate_json(encoded)
        except ValidationError:
            return CredentialReceiptRejection("invalid_receipt")
        if (
            not hmac.compare_digest(self._signature(receipt.payload), receipt.signature)
            or receipt.payload.bindings.scope.subject_hash != subject_hash
        ):
            return CredentialReceiptRejection("invalid_receipt")
        now: Final = self._now()
        if now is None or not receipt.payload.issued_at <= now < receipt.payload.expires_at:
            return CredentialReceiptRejection("receipt_time_invalid")
        bindings: Final = await self._stable_observation(subject_hash)
        if isinstance(bindings, CredentialReceiptRejection):
            return bindings
        if bindings != receipt.payload.bindings:
            return CredentialReceiptRejection("stale_receipt")
        finished_at: Final = self._now()
        if finished_at is None or not receipt.payload.issued_at <= finished_at < receipt.payload.expires_at:
            return CredentialReceiptRejection("receipt_time_invalid")
        return receipt

    async def _stable_observation(self, subject_hash: str) -> Bindings | CredentialReceiptRejection:
        if self._observations is None:
            return CredentialReceiptRejection("read_unavailable")
        try:
            first: Final = await self._observations.read(subject_hash)
            if isinstance(first, CredentialReceiptRejection):
                return first
            if first.scope.subject_hash != subject_hash or first.scope.card_id != first.target_account_id:
                return CredentialReceiptRejection("subject_unverified")
            second: Final = await self._observations.read(subject_hash)
            if isinstance(second, CredentialReceiptRejection):
                return second
            return first if first == second else CredentialReceiptRejection("state_changed")
        except Exception:  # noqa: BLE001  # Observers can return errors containing credentials; expose only a fixed code.
            return CredentialReceiptRejection("read_unavailable")
