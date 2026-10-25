"""Native receipt values only; a constructed DTO is not a verified receipt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self

from litellm.proxy.management_endpoints.account_pool_cockpit_subject_reader import Digest, TrustedCredentialScope

_ETAG: Final = r'^"sha256-[0-9a-f]{64}"$'
_LIFETIME: Final = 300


class CredentialBindings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    scope: TrustedCredentialScope
    source_version: int = Field(ge=0)
    source_file: str = Field(repr=False)
    source_state_digest: Digest
    source_fingerprint_digest: Digest
    source_content_digest: Digest
    source_size: int = Field(gt=0)
    target_account_id: UUID
    target_auth_id: str = Field(repr=False)
    target_file_revision: str = Field(pattern=_ETAG)
    manifest_revision: str = Field(pattern=_ETAG)


class CredentialReceiptPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    version: Literal[1] = 1
    purpose: Literal["cockpit-owned-artifact-dry-run"] = "cockpit-owned-artifact-dry-run"
    issued_at: int = Field(gt=0, strict=True)
    expires_at: int = Field(gt=0, strict=True)
    bindings: CredentialBindings

    @model_validator(mode="after")
    def bounded_lifetime(self) -> Self:
        if self.expires_at - self.issued_at != _LIFETIME:
            raise ValueError("Invalid receipt lifetime")
        return self


class CredentialReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    status: Literal["owned_artifact_correspondence"] = "owned_artifact_correspondence"
    ready_to_migrate: Literal[False] = False
    payload: CredentialReceiptPayload
    signature: Digest = Field(repr=False)


ReceiptRejectionCode = Literal[
    "invalid_subject",
    "subject_unverified",
    "invalid_clock",
    "read_unavailable",
    "state_changed",
    "source_missing",
    "source_unavailable",
    "source_credential_missing",
    "source_auth_unsupported",
    "source_identity_mismatch",
    "source_ownership_unverified",
    "source_credential_collision",
    "target_account_missing",
    "target_account_collision",
    "target_credential_missing",
    "target_credential_collision",
    "target_auth_unsupported",
    "target_content_mismatch",
    "invalid_receipt",
    "receipt_time_invalid",
    "stale_receipt",
]


@dataclass(frozen=True, slots=True)
class CredentialReceiptRejection:
    code: ReceiptRejectionCode
    status: Literal["rejected"] = "rejected"
