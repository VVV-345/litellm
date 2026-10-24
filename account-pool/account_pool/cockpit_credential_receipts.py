"""内部只读凭据字节对应回执；不接入 HTTP、不证明提供商身份，也不授权或执行迁移。"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Final, Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError, model_validator

from account_pool.cockpit_admin import CockpitAdminClient, CockpitFailure, valid_cockpit_auth_name
from account_pool.cockpit_preflight import (
    CockpitPreflightManifest,
    cockpit_auth_aliases,
    source_available_for_cockpit_preflight,
)
from account_pool.credential_ownership import CredentialConflict, CredentialOwnership, credential_identity
from account_pool.domain import ChannelKind, EnvironmentRecord, SupplierKind
from account_pool.shared.secrets import EnvironmentSecretDeriver, SecretPurpose

_HASH: Final = r"^[0-9a-f]{64}$"
_ETAG: Final = r'^"sha256-[0-9a-f]{64}"$'
_JSON: Final = TypeAdapter(dict[str, JsonValue])
_LIFETIME: Final = 300
# Manager 不加载 LiteLLM；这些原生哨兵不是有限模型名。
_NATIVE_MODEL_SENTINELS: Final = frozenset(("all-team-models", "all-proxy-models", "no-default-models"))
Digest = Annotated[str, Field(pattern=_HASH)]


class TrustedCredentialScope(BaseModel):
    """仅由注入的可信读取器返回；构造 DTO 本身不是主体或模型权限证明。"""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    subject_hash: Digest
    card_id: UUID
    model_scope: Literal["restricted", "unrestricted"]
    models: tuple[str, ...]

    @model_validator(mode="after")
    def explicit_scope(self) -> Self:
        if ((self.model_scope == "restricted") != bool(self.models)
                or len(set(self.models)) != len(self.models)
                or bool(_NATIVE_MODEL_SENTINELS.intersection(self.models))
                or any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model) is None for model in self.models)):
            raise ValueError("Scope must contain an explicit model allowlist or be unrestricted")
        return self


class TrustedSubjectReader(Protocol):
    """每次重新验证原生主体、单卡及静态模型范围；未验证、动态权限或不可用时返回 None。"""

    async def read(self, subject_hash: str) -> TrustedCredentialScope | None: ...


class CredentialSourceReader(Protocol):
    async def list(self) -> tuple[EnvironmentRecord, ...]: ...


class CredentialDownloader(Protocol):
    async def download_auth_file(self, record: EnvironmentRecord, filename: str) -> tuple[bytes, str]: ...


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
    "invalid_subject", "subject_unverified", "invalid_clock", "read_unavailable", "state_changed",
    "source_missing", "source_unavailable", "source_credential_missing", "source_auth_unsupported",
    "source_identity_mismatch", "source_ownership_unverified", "source_credential_collision",
    "target_account_missing", "target_account_collision", "target_credential_missing",
    "target_credential_collision", "target_auth_unsupported", "target_content_mismatch",
    "invalid_receipt", "receipt_time_invalid", "stale_receipt",
]


@dataclass(frozen=True, slots=True)
class CredentialReceiptRejection:
    code: ReceiptRejectionCode
    status: Literal["rejected"] = "rejected"


def _canonical(payload: CredentialReceiptPayload) -> bytes:
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def _oauth_content(content: bytes) -> bool:
    try:
        values: Final = _JSON.validate_json(content)
    except ValidationError:
        return False
    return (
        values.get("type") == "codex"
        and all(isinstance(values.get(key), str) and bool(str(values[key]).strip())
                for key in ("access_token", "refresh_token"))
        and all(values.get(key, "") in ("", "oauth") for key in ("auth_mode", "openai_auth_mode"))
        and values.get("disabled", False) is False
        and values.get("token_type", "Bearer") in ("", "Bearer")
        and not any(key in values for key in (
            "api_key", "private_key", "service_account", "personal_access_token", "at_token",
            "agent_runtime_id", "agentRuntimeId", "agent_private_key", "agentPrivateKey",
        ))
    )


def _canonical_target_fields(data: dict[str, JsonValue]) -> bool:
    # Go 结构体按大小写不敏感方式解码；Pydantic 忽略的别名不能改变目标身份或认证模式。
    if any(key.casefold() == "accounts" and key != "accounts" for key in data):
        return False
    accounts: Final = data.get("accounts")
    if not isinstance(accounts, list):
        return True
    return not any(
        key.casefold() == canonical.casefold() and key != canonical
        for account in accounts if isinstance(account, dict)
        for key in account
        for canonical in ("id", "authId", "authKind", "accessTokenOnly", "upstreamApiKey")
    )


class CockpitCredentialReceiptService:
    def __init__(
        self,
        subjects: TrustedSubjectReader,
        sources: CredentialSourceReader,
        downloader: CredentialDownloader,
        ownership: CredentialOwnership,
        target: CockpitAdminClient,
        secrets: EnvironmentSecretDeriver,
        clock: Callable[[], int],
    ) -> None:
        self._subjects: Final = subjects
        self._sources: Final = sources
        self._downloader: Final = downloader
        self._ownership: Final = ownership
        self._target: Final = target
        self._secrets: Final = secrets
        self._clock: Final = clock
        self._key: Final = secrets.derive(UUID(int=0), SecretPurpose.COCKPIT_CREDENTIAL_RECEIPT).encode()

    async def issue(self, subject_hash: str) -> CredentialReceipt | CredentialReceiptRejection:
        issued_at: Final = self._clock()
        if type(issued_at) is not int or issued_at <= 0:
            return CredentialReceiptRejection("invalid_clock")
        bindings: Final = await self._stable_observation(subject_hash)
        if isinstance(bindings, CredentialReceiptRejection):
            return bindings
        if not issued_at <= self._clock() < issued_at + _LIFETIME:
            return CredentialReceiptRejection("receipt_time_invalid")
        payload: Final = CredentialReceiptPayload(
            issued_at=issued_at, expires_at=issued_at + _LIFETIME, bindings=bindings,
        )
        return CredentialReceipt(payload=payload, signature=hmac.new(self._key, _canonical(payload), hashlib.sha256).hexdigest())

    async def revalidate(self, subject_hash: str, encoded: str) -> CredentialReceipt | CredentialReceiptRejection:
        if re.fullmatch(_HASH, subject_hash) is None:
            return CredentialReceiptRejection("invalid_subject")
        try:
            receipt: Final = CredentialReceipt.model_validate_json(encoded)
        except ValidationError:
            return CredentialReceiptRejection("invalid_receipt")
        expected: Final = hmac.new(self._key, _canonical(receipt.payload), hashlib.sha256).hexdigest()
        if (not hmac.compare_digest(expected, receipt.signature)
                or receipt.payload.bindings.scope.subject_hash != subject_hash):
            return CredentialReceiptRejection("invalid_receipt")
        now: Final = self._clock()
        if type(now) is not int or not receipt.payload.issued_at <= now < receipt.payload.expires_at:
            return CredentialReceiptRejection("receipt_time_invalid")
        bindings: Final = await self._stable_observation(subject_hash)
        if isinstance(bindings, CredentialReceiptRejection):
            return bindings
        if bindings != receipt.payload.bindings:
            return CredentialReceiptRejection("stale_receipt")
        if not receipt.payload.issued_at <= self._clock() < receipt.payload.expires_at:
            return CredentialReceiptRejection("receipt_time_invalid")
        return receipt

    async def _stable_observation(self, subject_hash: str) -> CredentialBindings | CredentialReceiptRejection:
        if re.fullmatch(_HASH, subject_hash) is None:
            return CredentialReceiptRejection("invalid_subject")
        # 两次当前读取只排除已观察到的变更，不构成跨服务事务或运行时授权快照。
        try:
            first: Final = await self._observe(subject_hash)
            if isinstance(first, CredentialReceiptRejection):
                return first
            second: Final = await self._observe(subject_hash)
            if isinstance(second, CredentialReceiptRejection):
                return second
            return first if first == second else CredentialReceiptRejection("state_changed")
        except Exception:
            # 读取端口可能带回认证响应；内部失败也只返回固定代码，不传播凭据或错误正文。
            return CredentialReceiptRejection("read_unavailable")

    async def _observe(self, subject_hash: str) -> CredentialBindings | CredentialReceiptRejection:
        scope: Final = await self._subjects.read(subject_hash)
        if scope is None or scope.subject_hash != subject_hash:
            return CredentialReceiptRejection("subject_unverified")
        records: Final = await self._sources.list()
        sources: Final = tuple(record for record in records if record.id == scope.card_id)
        if not sources:
            return CredentialReceiptRejection("source_missing")
        if len(sources) != 1:
            return CredentialReceiptRejection("source_credential_collision")
        source: Final = sources[0]
        if not source_available_for_cockpit_preflight(source):
            return CredentialReceiptRejection("source_unavailable")
        if source.channel != ChannelKind.CLIPROXYAPI or source.supplier != SupplierKind.OPENAI_CODEX:
            return CredentialReceiptRejection("source_auth_unsupported")
        if not source.auth_file_name or not valid_cockpit_auth_name(source.auth_file_name):
            return CredentialReceiptRejection("source_credential_missing")
        fingerprints: Final = frozenset(source.credential_fingerprints)
        if any(record.id != source.id and fingerprints.intersection(record.credential_fingerprints) for record in records):
            return CredentialReceiptRejection("source_credential_collision")
        if not await self._ownership.owns(source.id, source.credential_fingerprints):
            return CredentialReceiptRejection("source_ownership_unverified")
        content, _ = await self._downloader.download_auth_file(source, source.auth_file_name)
        if not _oauth_content(content):
            return CredentialReceiptRejection("source_auth_unsupported")
        try:
            identity: Final = credential_identity(content, source.supplier.value, self._secrets)
        except CredentialConflict:
            return CredentialReceiptRejection("source_identity_mismatch")
        if not fingerprints or fingerprints != frozenset(identity.fingerprints):
            return CredentialReceiptRejection("source_identity_mismatch")
        if not await self._ownership.owns(source.id, identity.fingerprints):
            return CredentialReceiptRejection("source_ownership_unverified")
        snapshot: Final = await self._target.read_manifest()
        if isinstance(snapshot, CockpitFailure):
            return CredentialReceiptRejection("read_unavailable")
        if not _canonical_target_fields(snapshot.data):
            return CredentialReceiptRejection("target_auth_unsupported")
        files: Final = await self._target.list_auth_files()
        if isinstance(files, CockpitFailure):
            return CredentialReceiptRejection("read_unavailable")
        manifest: Final = CockpitPreflightManifest.model_validate(snapshot.data)
        targets: Final = manifest.accounts or ()
        matches: Final = tuple(target for target in targets if target.id.strip().lower() == str(source.id))
        if not matches:
            return CredentialReceiptRejection("target_account_missing")
        if len(matches) != 1:
            return CredentialReceiptRejection("target_account_collision")
        target: Final = matches[0]
        if (target.id != str(source.id) or not valid_cockpit_auth_name(target.authId)
                or target.authKind not in ("", "oauth") or target.accessTokenOnly or target.upstreamApiKey.strip()):
            return CredentialReceiptRejection("target_auth_unsupported")
        aliases: Final = cockpit_auth_aliases(target.authId)
        if (sum(bool(cockpit_auth_aliases(item.authId) & aliases) for item in targets) != 1
                or sum(bool(cockpit_auth_aliases(item.id) & aliases) for item in files) > 1):
            return CredentialReceiptRejection("target_credential_collision")
        mapped: Final = tuple(item for item in files if item.id == target.authId)
        if len(mapped) != 1:
            return CredentialReceiptRejection("target_credential_missing")
        digest: Final = hashlib.sha256(content).hexdigest()
        if mapped[0].etag != f'"sha256-{digest}"' or mapped[0].size != len(content):
            return CredentialReceiptRejection("target_content_mismatch")
        return CredentialBindings(
            scope=scope,
            source_version=source.version,
            source_file=source.auth_file_name,
            source_state_digest=hashlib.sha256(source.model_dump_json(include={
                "channel", "supplier", "version", "auth_file_name", "enabled", "status", "manual_cooldown",
                "configuration_pending", "auth_file_disabled", "enabled_models", "available_models",
                "authorization_flow", "desired_configuration_version", "observed_configuration_version",
            }).encode()).hexdigest(),
            source_fingerprint_digest=hashlib.sha256(json.dumps(sorted(fingerprints)).encode()).hexdigest(),
            source_content_digest=digest,
            source_size=len(content),
            target_account_id=source.id,
            target_auth_id=target.authId,
            target_file_revision=mapped[0].etag,
            manifest_revision=snapshot.revision,
        )
