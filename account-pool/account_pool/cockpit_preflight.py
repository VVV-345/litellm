"""只读核对卡片授权与 Cockpit 账号投影，不迁移凭据、不授权、不改变请求路由。"""

from __future__ import annotations

from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from account_pool.application.effective_policy import effective_policy
from account_pool.card_keys import CardKeyService
from account_pool.cockpit_admin import AuthFileMetadata, CockpitAdminClient, CockpitFailure
from account_pool.cockpit_manifest import CockpitAccountManifest, CockpitTargetAccount, auth_aliases
from account_pool.domain import ChannelKind, EnvironmentRecord, EnvironmentStatus, SupplierKind
from account_pool.policies import PolicyRepository
from account_pool.ports import EnvironmentRepository
from account_pool.settings import AccountPoolSettingsRepository, settings_for_card


class CockpitPreflightRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    card_id: UUID
    subject_kind: Literal["legacy_card_key", "litellm_key"]
    expected_key_id: UUID | None = None
    candidate_ids: tuple[UUID, ...] | None = Field(default=None, max_length=256)


class CockpitNativeMappingRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    card_id: UUID
    candidate_ids: tuple[UUID, ...] | None = Field(default=None, max_length=256)


PreflightBlocker = Literal[
    "source_card_missing",
    "source_card_unavailable",
    "subject_binding_unverified",
    "empty_candidates",
    "duplicate_candidates",
    "outside_authorized_scope",
    "source_account_missing",
    "supplier_mismatch",
    "account_unavailable",
    "source_credential_missing",
    "supplier_mapping_unsupported",
    "target_account_missing",
    "target_account_collision",
    "target_credential_missing",
    "target_auth_kind_unsupported",
    "target_identity_unverified",
    "strategy_mapping_unverified",
    "legacy_account_scope_unsupported",
    "source_credential_collision",
    "target_credential_collision",
]


class CockpitAccountPreflight(BaseModel):
    model_config = ConfigDict(frozen=True)

    card_id: UUID
    target_account_id: UUID
    source_version: int | None
    blockers: tuple[PreflightBlocker, ...]


class CockpitPreflightReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    card_id: UUID
    subject_kind: Literal["legacy_card_key", "litellm_key"]
    authorized_account_ids: tuple[UUID, ...]
    scope_valid: bool
    source_strategy: Literal["auto", "random", "priority", "quota", "plan", "expiry", "custom"]
    policy_version: int
    settings_version: int | None
    persisted_manifest_revision: str = Field(pattern=r'^"sha256-[0-9a-f]{64}"$')
    accounts: tuple[CockpitAccountPreflight, ...]
    blockers: tuple[PreflightBlocker, ...]
    ready_to_migrate: Literal[False] = False
    pending_checks: tuple[
        Literal[
            "runtime_eligibility_unverified",
            "credentials_and_ownership_unverified",
            "models_proxy_quota_unmapped",
            "policy_behavior_unverified",
            "native_subject_mapping_unimplemented",
            "native_runtime_authorization_unverified",
            "runtime_activation_unverified",
        ],
        ...,
    ] = (
        "runtime_eligibility_unverified",
        "credentials_and_ownership_unverified",
        "models_proxy_quota_unmapped",
        "policy_behavior_unverified",
        "native_subject_mapping_unimplemented",
        "runtime_activation_unverified",
    )


def _available(record: EnvironmentRecord) -> bool:
    return (
        record.enabled
        and not record.manual_cooldown
        and not record.configuration_pending
        and not record.auth_file_disabled
        and record.status == EnvironmentStatus.READY
    )


CockpitPreflightManifest = CockpitAccountManifest
source_available_for_cockpit_preflight: Final = _available
cockpit_auth_aliases: Final = auth_aliases


def _account_report(
    account_id: UUID,
    source: EnvironmentRecord | None,
    origin: EnvironmentRecord | None,
    authorized: tuple[UUID, ...],
    targets: tuple[CockpitTargetAccount, ...],
    files: tuple[AuthFileMetadata, ...],
    records: tuple[EnvironmentRecord, ...],
) -> CockpitAccountPreflight:
    scope_errors: Final[tuple[PreflightBlocker, ...]] = (
        () if account_id in authorized else ("outside_authorized_scope",)
    )
    if source is None:
        return CockpitAccountPreflight(
            card_id=account_id,
            target_account_id=account_id,
            source_version=None,
            blockers=(*scope_errors, "source_account_missing"),
        )
    matches: Final = tuple(target for target in targets if target.id.strip() == str(account_id))
    checks: Final[tuple[tuple[bool, PreflightBlocker], ...]] = (
        (
            origin is not None and (source.channel != origin.channel or source.supplier != origin.supplier),
            "supplier_mismatch",
        ),
        (not _available(source), "account_unavailable"),
        (
            source.channel != ChannelKind.CLIPROXYAPI or source.supplier != SupplierKind.OPENAI_CODEX,
            "supplier_mapping_unsupported",
        ),
        (not source.auth_file_name, "source_credential_missing"),
        (
            any(record.id != source.id and set(record.credential_fingerprints).intersection(source.credential_fingerprints)
                for record in records),
            "source_credential_collision",
        ),
        (not matches, "target_account_missing"),
        (len(matches) > 1, "target_account_collision"),
        (len(matches) == 1 and not any(file.id == matches[0].authId for file in files), "target_credential_missing"),
        (
            len(matches) == 1 and (matches[0].authKind not in ("", "oauth")
                                  or matches[0].accessTokenOnly or bool(matches[0].upstreamApiKey.strip())),
            "target_auth_kind_unsupported",
        ),
        (
            len(matches) == 1 and (
                sum(bool(auth_aliases(target.authId) & auth_aliases(matches[0].authId)) for target in targets) > 1
                or sum(bool(auth_aliases(file.id) & auth_aliases(matches[0].authId)) for file in files) > 1
            ),
            "target_credential_collision",
        ),
        (bool(matches), "target_identity_unverified"),
    )
    return CockpitAccountPreflight(
        card_id=account_id,
        target_account_id=account_id,
        source_version=source.version,
        blockers=(*scope_errors, *(code for failed, code in checks if failed)),
    )


async def cockpit_preflight(
    request: CockpitPreflightRequest,
    environments: EnvironmentRepository,
    policies: PolicyRepository,
    keys: CardKeyService | None,
    client: CockpitAdminClient,
    settings: AccountPoolSettingsRepository | None = None,
    *,
    native_card_id: UUID | None = None,
) -> CockpitPreflightReport | CockpitFailure:
    records: Final = await environments.list()
    origin: Final = next((record for record in records if record.id == request.card_id), None)
    stored: Final = await policies.get(request.card_id)
    global_settings: Final = await settings.get() if settings is not None else None
    policy: Final = effective_policy(
        stored.policy,
        settings_for_card(global_settings.values, request.card_id) if global_settings is not None else None,
        inherit=stored.version == 0,
    )
    binding: Final = await keys.status(request.card_id) if keys is not None and request.subject_kind == "legacy_card_key" else None
    # native_card_id 仅由内部管理接口接收 LiteLLM 服务端证明，公开请求不能提供。
    verified_subject: Final = (
        request.subject_kind == "legacy_card_key"
        and binding is not None
        and binding.revoked_at is None
        and request.expected_key_id == binding.key_id
    ) or (request.subject_kind == "litellm_key" and native_card_id == request.card_id)
    authorized: Final = tuple(
        record.id
        for record in records
        if origin is not None
        and _available(origin)
        and verified_subject
        and record.channel == origin.channel
        and record.supplier == origin.supplier
        and (record.id == origin.id or (request.subject_kind == "legacy_card_key" and policy.routing.fallback_enabled))
    )
    candidates: Final = authorized if request.candidate_ids is None else request.candidate_ids
    snapshot: Final = await client.read_manifest()
    if isinstance(snapshot, CockpitFailure):
        return snapshot
    files: Final = await client.list_auth_files()
    if isinstance(files, CockpitFailure):
        return files
    try:
        manifest: Final = CockpitAccountManifest.model_validate(snapshot.data)
    except ValidationError:
        return CockpitFailure("invalid_response")
    accounts: Final = tuple(
        _account_report(
            account_id,
            next((record for record in records if record.id == account_id), None),
            origin,
            authorized,
            manifest.accounts or (),
            files,
            tuple(records),
        )
        for account_id in candidates
    )
    checks: Final[tuple[tuple[bool, PreflightBlocker], ...]] = (
        (origin is None, "source_card_missing"),
        (origin is not None and not _available(origin), "source_card_unavailable"),
        (not verified_subject, "subject_binding_unverified"),
        (not candidates, "empty_candidates"),
        (len(set(candidates)) != len(candidates), "duplicate_candidates"),
        (bool(policy.account_ids), "legacy_account_scope_unsupported"),
        (True, "strategy_mapping_unverified"),
    )
    return CockpitPreflightReport(
        card_id=request.card_id,
        subject_kind=request.subject_kind,
        authorized_account_ids=authorized,
        scope_valid=bool(candidates)
        and len(set(candidates)) == len(candidates)
        and all(account in authorized for account in candidates),
        source_strategy=policy.routing.strategy,
        policy_version=stored.version,
        settings_version=global_settings.version if global_settings is not None else None,
        persisted_manifest_revision=snapshot.revision,
        accounts=accounts,
        blockers=tuple(code for failed, code in checks if failed),
        pending_checks=(
            "runtime_eligibility_unverified", "credentials_and_ownership_unverified", "models_proxy_quota_unmapped",
            "policy_behavior_unverified",
            "native_runtime_authorization_unverified" if native_card_id is not None and verified_subject
            else "native_subject_mapping_unimplemented",
            "runtime_activation_unverified",
        ),
    )