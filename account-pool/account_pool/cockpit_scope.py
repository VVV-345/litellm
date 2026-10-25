"""发布已核对原生主体的 Cockpit 授权范围投影；只写暂存条目，不激活或迁移凭据。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Final, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from account_pool.cockpit_admin import CockpitAdminClient, CockpitFailure, ManifestSnapshot
from account_pool.cockpit_credential_receipts import (
    CredentialBindings,
    CredentialReceipt,
    CredentialReceiptRejection,
    TrustedCredentialScope,
)
from account_pool.cockpit_failure import precondition, unavailable, write_outcome_unknown
from account_pool.cockpit_manifest import CockpitScopeManifest, wildcard_match
from account_pool.shared.secrets import EnvironmentSecretDeriver, SecretPurpose

_HASH: Final = r"^[0-9a-f]{64}$"
_ETAG: Final = r'^"sha256-[0-9a-f]{64}"$'
_RESERVED_PREFIX: Final = "litellm-subject-"
_RESERVED_LABEL_PREFIX: Final = "LiteLLM native subject "
_MAX_RECEIPT: Final = 16384

SubjectHash = Annotated[str, Field(pattern=_HASH)]
Revision = Annotated[str, Field(pattern=_ETAG)]
ScopeState = Literal["absent", "staged", "active", "blocked"]
ModelScope = Literal["none", "restricted"]
ScopeFailureCode = Literal[
    "invalid_receipt",
    "subject_unverified",
    "receipt_stale",
    "scope_unverified",
    "account_missing",
    "model_scope_unsupported",
    "model_unmapped",
    "manifest_changed",
    "publication_conflict",
    "revision_conflict",
    "invalid_manifest",
    "invalid_response",
    "unavailable",
    "write_rejected",
    "write_outcome_unknown",
]

_STATUS: Final[Mapping[ScopeFailureCode, int]] = MappingProxyType(
    {
        "invalid_receipt": 422,
        "subject_unverified": 409,
        "receipt_stale": 409,
        "scope_unverified": 422,
        "account_missing": 422,
        "model_scope_unsupported": 422,
        "model_unmapped": 422,
        "manifest_changed": 409,
        "publication_conflict": 409,
        "revision_conflict": 409,
        "invalid_manifest": 422,
        "invalid_response": 502,
        "unavailable": 503,
        "write_rejected": 502,
        "write_outcome_unknown": 503,
    }
)
_RECEIPT_REJECTIONS: Final[Mapping[str, ScopeFailureCode]] = MappingProxyType(
    {
        "invalid_subject": "invalid_receipt",
        "invalid_receipt": "invalid_receipt",
        "receipt_time_invalid": "invalid_receipt",
        "subject_unverified": "subject_unverified",
        "stale_receipt": "receipt_stale",
        "state_changed": "receipt_stale",
        "read_unavailable": "unavailable",
        "invalid_clock": "unavailable",
    }
)


class CockpitScopePublishRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_hash: SubjectHash
    receipt: str = Field(min_length=1, max_length=_MAX_RECEIPT)
    revision: Revision


class CockpitScopeRevokeRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_hash: SubjectHash
    revision: Revision


class CockpitSubjectScopeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject_hash: str
    state: ScopeState
    account_ids: tuple[str, ...]
    model_scope: ModelScope
    models: tuple[str, ...]
    persisted_manifest_revision: str | None = None
    runtime_activation: Literal["restart_required_or_unverified"] = "restart_required_or_unverified"
    ready_to_migrate: Literal[False] = False


class CockpitSubjectRouteState(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject_hash: str
    state: ScopeState
    account_ids: tuple[str, ...] = ()
    model_scope: ModelScope = "none"
    models: tuple[str, ...] = ()
    origin: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    persisted_manifest_revision: str | None = None
    runtime_activation: Literal["restart_required_or_unverified"] = "restart_required_or_unverified"


@dataclass(frozen=True, slots=True)
class ScopeFailure:
    status: int
    code: ScopeFailureCode


class ReceiptRevalidator(Protocol):
    async def revalidate(self, subject_hash: str, encoded: str) -> CredentialReceipt | CredentialReceiptRejection: ...


class SubjectReader(Protocol):
    async def read(self, subject_hash: str) -> TrustedCredentialScope | None: ...


def _failure(code: ScopeFailureCode) -> ScopeFailure:
    return ScopeFailure(_STATUS[code], code)


def _manifest(snapshot: ManifestSnapshot) -> CockpitScopeManifest | ScopeFailure:
    try:
        return CockpitScopeManifest.model_validate(snapshot.data)
    except ValidationError:
        return _failure("invalid_response")


def _reserved_id(subject_hash: str) -> str:
    return _RESERVED_PREFIX + subject_hash


def subject_routing_token(secrets: EnvironmentSecretDeriver, subject_hash: str) -> str:
    identity: Final = uuid5(NAMESPACE_URL, "litellm-cockpit-subject:" + subject_hash)
    return secrets.derive(identity, SecretPurpose.COCKPIT_SUBJECT_ROUTING)


class _ReservedKey(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")

    id: str
    label: str = ""
    key: str = Field(default="", repr=False)
    enabled: bool = False
    accountIds: list[str] | None = None
    allowedModels: list[str] | None = None


def _find_reserved(
    manifest: CockpitScopeManifest, subject_hash: str
) -> tuple[dict[str, JsonValue], _ReservedKey] | None:
    expected: Final = _reserved_id(subject_hash)
    for raw in manifest.apiKeys or ():
        if raw.get("id") != expected:
            continue
        try:
            return raw, _ReservedKey.model_validate(raw)
        except ValidationError:
            return raw, _ReservedKey(id=expected, label="", key="", enabled=False)
    return None


def _entry_valid(
    entry: _ReservedKey,
    subject_hash: str,
    token: str,
    account_ids: tuple[str, ...],
) -> bool:
    return (
        entry.id == _reserved_id(subject_hash)
        and entry.label.startswith(_RESERVED_LABEL_PREFIX)
        and entry.key == token
        and tuple(entry.accountIds or ()) == account_ids
        and bool(entry.allowedModels)
    )


def _visible(model: str, manifest: CockpitScopeManifest, account_id: UUID) -> bool:
    catalog: Final = {value.strip().casefold() for value in manifest.modelIds or () if value.strip()}
    if model.strip().casefold() not in catalog:
        return False
    if any(wildcard_match(rule, model) for rule in manifest.excludedModels or ()):
        return False
    rule: Final = next(
        (item for item in manifest.accountModelRules or () if item.accountId == str(account_id)),
        None,
    )
    return rule is None or not any(wildcard_match(rule, model) for rule in rule.excludedModels or ())


def _manifest_problem(manifest: CockpitScopeManifest, extra: dict[str, JsonValue] | None = None) -> bool:
    seen_accounts: set[str] = set()
    for account in manifest.accounts or ():
        identifier = account.id.strip()
        if not identifier or identifier in seen_accounts:
            return True
        seen_accounts.add(identifier)
    seen_ids: set[str] = set()
    seen_values: set[str] = set()
    for raw in (*(manifest.apiKeys or ()), *((extra,) if extra is not None else ())):
        key_id = raw.get("id")
        if not isinstance(key_id, str) or not key_id.strip() or key_id in seen_ids:
            return True
        seen_ids.add(key_id)
        enabled = raw.get("enabled", False)
        value = raw.get("key", "")
        if not isinstance(enabled, bool) or not isinstance(value, str):
            return True
        if not enabled:
            continue
        if not value.strip() or value in seen_values:
            return True
        seen_values.add(value)
        accounts = raw.get("accountIds", [])
        if not isinstance(accounts, list) or not accounts:
            return True
        if any(not isinstance(item, str) or not item.strip() or item not in seen_accounts for item in accounts):
            return True
    return False


def _desired_entry(subject_hash: str, card_id: UUID, models: tuple[str, ...], token: str) -> dict[str, JsonValue]:
    return {
        "id": _reserved_id(subject_hash),
        "label": _RESERVED_LABEL_PREFIX + subject_hash[:12],
        "key": token,
        "accountIds": [str(card_id)],
        "allowedModels": list(models),
        "enabled": False,
    }


@dataclass(frozen=True, slots=True)
class _Publication:
    entry: dict[str, JsonValue]
    card_id: UUID
    models: tuple[str, ...]


def _prepared_publication(
    request: CockpitScopePublishRequest,
    bindings: CredentialBindings,
    snapshot: ManifestSnapshot,
    manifest: CockpitScopeManifest,
    token: str,
) -> _Publication | ScopeFailure:
    if request.revision != snapshot.revision or bindings.manifest_revision != snapshot.revision:
        return _failure("manifest_changed")
    account_id: Final = bindings.target_account_id
    if bindings.scope.model_scope != "restricted" or not bindings.scope.models:
        return _failure("model_scope_unsupported")
    account = next((item for item in manifest.accounts or () if item.id == str(account_id)), None)
    if account is None:
        return _failure("account_missing")
    if (
        account.authKind not in ("", "oauth")
        or account.accessTokenOnly
        or account.upstreamApiKey.strip()
        or account.authId != bindings.target_auth_id
    ):
        return _failure("scope_unverified")
    models: Final = tuple(bindings.scope.models)
    if any(not _visible(model, manifest, account_id) for model in models):
        return _failure("model_unmapped")
    return _Publication(
        entry=_desired_entry(request.subject_hash, account_id, models, token), card_id=account_id, models=models
    )


class CockpitScopeService:
    def __init__(
        self,
        receipts: ReceiptRevalidator | None,
        target: CockpitAdminClient,
        secrets: EnvironmentSecretDeriver,
        origin: str | None = None,
        subjects: SubjectReader | None = None,
    ) -> None:
        self._receipts: Final = receipts
        self._target: Final = target
        self._secrets: Final = secrets
        self._origin: Final = origin
        self._subjects: Final = subjects

    async def read(self, subject_hash: str) -> CockpitSubjectScopeView | ScopeFailure:
        snapshot: Final = await self._target.read_manifest()
        if isinstance(snapshot, CockpitFailure):
            return _read_failure(snapshot)
        manifest: Final = _manifest(snapshot)
        if isinstance(manifest, ScopeFailure):
            return manifest
        return self._view(subject_hash, snapshot, manifest)

    async def publish(self, request: CockpitScopePublishRequest) -> CockpitSubjectScopeView | ScopeFailure:
        if self._receipts is None:
            return _failure("unavailable")
        receipt: Final = await self._receipts.revalidate(request.subject_hash, request.receipt)
        if isinstance(receipt, CredentialReceiptRejection):
            return _failure(_RECEIPT_REJECTIONS.get(receipt.code, "scope_unverified"))
        snapshot: Final = await self._target.read_manifest()
        if isinstance(snapshot, CockpitFailure):
            return _read_failure(snapshot)
        manifest: Final = _manifest(snapshot)
        if isinstance(manifest, ScopeFailure):
            return _failure("invalid_manifest")
        token: Final = subject_routing_token(self._secrets, request.subject_hash)
        prepared: Final = _prepared_publication(
            request,
            receipt.payload.bindings,
            snapshot,
            manifest,
            token,
        )
        if isinstance(prepared, ScopeFailure):
            return prepared
        existing: Final = _find_reserved(manifest, request.subject_hash)
        if any(
            raw.get("key") == token and raw.get("id") != _reserved_id(request.subject_hash)
            for raw in manifest.apiKeys or ()
        ):
            return _failure("publication_conflict")
        if existing is not None:
            raw, entry = existing
            if not _entry_valid(entry, request.subject_hash, token, (str(prepared.card_id),)) or raw != prepared.entry:
                return _failure("publication_conflict")
            if _manifest_problem(manifest):
                return _failure("invalid_manifest")
            return self._view(request.subject_hash, snapshot, manifest)
        if _manifest_problem(manifest, prepared.entry):
            return _failure("invalid_manifest")
        data: Final[dict[str, JsonValue]] = {
            **snapshot.data,
            "apiKeys": [*(manifest.apiKeys or ()), prepared.entry],
        }
        receipt_after: Final = await self._target.write_manifest(data, snapshot.revision)
        if isinstance(receipt_after, CockpitFailure):
            return _write_failure(receipt_after)
        if receipt_after.revision is None:
            return _failure("write_outcome_unknown")
        return CockpitSubjectScopeView(
            subject_hash=request.subject_hash,
            state="staged",
            account_ids=(str(prepared.card_id),),
            model_scope="restricted",
            models=prepared.models,
            persisted_manifest_revision=receipt_after.revision,
        )

    async def revoke(self, request: CockpitScopeRevokeRequest) -> CockpitSubjectScopeView | ScopeFailure:
        snapshot: Final = await self._target.read_manifest()
        if isinstance(snapshot, CockpitFailure):
            return _read_failure(snapshot)
        if request.revision != snapshot.revision:
            return _failure("revision_conflict")
        manifest: Final = _manifest(snapshot)
        if isinstance(manifest, ScopeFailure):
            return _failure("invalid_manifest")
        expected: Final = _reserved_id(request.subject_hash)
        remaining: Final = tuple(raw for raw in manifest.apiKeys or () if raw.get("id") != expected)
        if len(remaining) == len(manifest.apiKeys or ()):
            return CockpitSubjectScopeView(
                subject_hash=request.subject_hash,
                state="absent",
                account_ids=(),
                model_scope="none",
                models=(),
                persisted_manifest_revision=snapshot.revision,
            )
        if _manifest_problem(manifest.model_copy(update={"apiKeys": list(remaining)})):
            return _failure("invalid_manifest")
        data: Final[dict[str, JsonValue]] = {**snapshot.data, "apiKeys": list(remaining)}
        receipt: Final = await self._target.write_manifest(data, snapshot.revision)
        if isinstance(receipt, CockpitFailure):
            return _write_failure(receipt)
        if receipt.revision is None:
            return _failure("write_outcome_unknown")
        return CockpitSubjectScopeView(
            subject_hash=request.subject_hash,
            state="absent",
            account_ids=(),
            model_scope="none",
            models=(),
            persisted_manifest_revision=receipt.revision,
        )

    async def route_state(self, subject_hash: str) -> CockpitSubjectRouteState | ScopeFailure:
        snapshot: Final = await self._target.read_manifest()
        if isinstance(snapshot, CockpitFailure):
            return _read_failure(snapshot)
        manifest: Final = _manifest(snapshot)
        if isinstance(manifest, ScopeFailure):
            return manifest
        found: Final = _find_reserved(manifest, subject_hash)
        if found is None:
            return CockpitSubjectRouteState(
                subject_hash=subject_hash,
                state="absent",
                persisted_manifest_revision=snapshot.revision,
            )
        _, entry = found
        token: Final = subject_routing_token(self._secrets, subject_hash)
        account_ids: Final = tuple(entry.accountIds or ())
        models: Final = tuple(entry.allowedModels or ())
        if not _entry_valid(entry, subject_hash, token, account_ids) or not account_ids:
            return CockpitSubjectRouteState(
                subject_hash=subject_hash,
                state="blocked",
                persisted_manifest_revision=snapshot.revision,
            )
        if not entry.enabled:
            return CockpitSubjectRouteState(
                subject_hash=subject_hash,
                state="staged",
                account_ids=account_ids,
                model_scope="restricted",
                models=models,
                persisted_manifest_revision=snapshot.revision,
            )
        if self._origin is None or self._subjects is None:
            return CockpitSubjectRouteState(
                subject_hash=subject_hash,
                state="blocked",
                account_ids=account_ids,
                model_scope="restricted",
                models=models,
                persisted_manifest_revision=snapshot.revision,
            )
        current: TrustedCredentialScope | None
        try:
            current = await self._subjects.read(subject_hash)
        except Exception:  # noqa: BLE001  # a failed subject read must fail closed
            current = None
        if not _subject_matches(current, subject_hash, account_ids, models):
            return CockpitSubjectRouteState(
                subject_hash=subject_hash,
                state="blocked",
                account_ids=account_ids,
                model_scope="restricted",
                models=models,
                persisted_manifest_revision=snapshot.revision,
            )
        return CockpitSubjectRouteState(
            subject_hash=subject_hash,
            state="active",
            account_ids=account_ids,
            model_scope="restricted",
            models=models,
            origin=self._origin,
            api_key=token,
            persisted_manifest_revision=snapshot.revision,
        )

    def _view(
        self,
        subject_hash: str,
        snapshot: ManifestSnapshot,
        manifest: CockpitScopeManifest,
    ) -> CockpitSubjectScopeView | ScopeFailure:
        found: Final = _find_reserved(manifest, subject_hash)
        if found is None:
            return CockpitSubjectScopeView(
                subject_hash=subject_hash,
                state="absent",
                account_ids=(),
                model_scope="none",
                models=(),
                persisted_manifest_revision=snapshot.revision,
            )
        _, entry = found
        token: Final = subject_routing_token(self._secrets, subject_hash)
        account_ids: Final = tuple(entry.accountIds or ())
        models: Final = tuple(entry.allowedModels or ())
        if not _entry_valid(entry, subject_hash, token, account_ids) or not account_ids:
            return CockpitSubjectScopeView(
                subject_hash=subject_hash,
                state="blocked",
                account_ids=(),
                model_scope="none",
                models=(),
                persisted_manifest_revision=snapshot.revision,
            )
        if not entry.enabled:
            state: ScopeState = "staged"
        elif self._origin is not None:
            state = "active"
        else:
            state = "blocked"
        return CockpitSubjectScopeView(
            subject_hash=subject_hash,
            state=state,
            account_ids=account_ids,
            model_scope="restricted",
            models=models,
            persisted_manifest_revision=snapshot.revision,
        )


def _subject_matches(
    subject: TrustedCredentialScope | None,
    subject_hash: str,
    account_ids: tuple[str, ...],
    models: tuple[str, ...],
) -> bool:
    return (
        subject is not None
        and subject.subject_hash == subject_hash
        and subject.model_scope == "restricted"
        and len(subject.models) == len(models)
        and frozenset(subject.models) == frozenset(models)
        and account_ids == (str(subject.card_id),)
    )


def _read_failure(failure: CockpitFailure) -> ScopeFailure:
    if unavailable(failure):
        return _failure("unavailable")
    return _failure("invalid_response")


def _write_failure(failure: CockpitFailure) -> ScopeFailure:
    if precondition(failure):
        return _failure("revision_conflict")
    if write_outcome_unknown(failure):
        return _failure("write_outcome_unknown")
    return _failure("write_rejected")


def valid_subject_hash(value: str) -> bool:
    return re.fullmatch(_HASH, value) is not None