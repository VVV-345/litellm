from __future__ import annotations

from typing import Final

from pydantic import JsonValue

from litellm.proxy.management_endpoints.account_pool_cockpit_admin import (
    CockpitAdminClient,
    CockpitFailure,
    valid_cockpit_auth_name,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_receipt_contracts import (
    CredentialReceiptRejection,
    NativeCredentialBindings,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_scope_service import (
    CockpitScopeManifest,
    SubjectReader,
    scope_models_visible,
)


def _alias(name: str) -> str:
    return name.strip().casefold().removesuffix(".json")


def _canonical_fields(data: dict[str, JsonValue], fields: tuple[str, ...]) -> bool:
    return not any(key.casefold() == field.casefold() and key != field for key in data for field in fields)


def _canonical(data: dict[str, JsonValue]) -> bool:
    fields: Final = ("accounts", "apiKeys", "modelIds", "excludedModels", "accountModelRules", "modelAliases")
    if not _canonical_fields(data, fields):
        return False
    sections: Final = (
        ("accounts", ("id", "authId", "authKind", "accessTokenOnly", "upstreamApiKey")),
        ("apiKeys", ("id", "label", "key", "enabled", "accountIds", "allowedModels")),
        ("accountModelRules", ("accountId", "excludedModels")),
        ("modelAliases", ("sourceModel", "alias")),
    )
    return all(
        _canonical_fields(item, names)
        for section, names in sections
        for entries in (data.get(section),)
        if isinstance(entries, list)
        for item in entries
        if isinstance(item, dict)
    )


class NativeCredentialObservation:
    def __init__(self, subjects: SubjectReader, target: CockpitAdminClient) -> None:
        self._subjects: Final = subjects
        self._target: Final = target

    async def read(self, subject_hash: str) -> NativeCredentialBindings | CredentialReceiptRejection:
        scope: Final = await self._subjects.read(subject_hash)
        if scope is None or scope.subject_hash != subject_hash:
            return CredentialReceiptRejection("subject_unverified")
        snapshot: Final = await self._target.read_manifest()
        if isinstance(snapshot, CockpitFailure):
            return CredentialReceiptRejection("read_unavailable")
        if not _canonical(snapshot.data):
            return CredentialReceiptRejection("target_auth_unsupported")
        manifest: Final = CockpitScopeManifest.model_validate(snapshot.data)
        accounts: Final = manifest.accounts or ()
        matches: Final = tuple(account for account in accounts if account.id.strip().lower() == str(scope.card_id))
        if not matches:
            return CredentialReceiptRejection("target_account_missing")
        if len(matches) != 1:
            return CredentialReceiptRejection("target_account_collision")
        account: Final = matches[0]
        if (
            account.id != str(scope.card_id)
            or not valid_cockpit_auth_name(account.authId)
            or account.authKind not in ("", "oauth")
            or account.accessTokenOnly
            or account.upstreamApiKey.strip()
        ):
            return CredentialReceiptRejection("target_auth_unsupported")
        if sum(_alias(item.authId) == _alias(account.authId) for item in accounts) != 1:
            return CredentialReceiptRejection("target_credential_collision")
        if not scope_models_visible(manifest, scope):
            return CredentialReceiptRejection("subject_unverified")
        files: Final = await self._target.list_auth_files()
        if isinstance(files, CockpitFailure):
            return CredentialReceiptRejection("read_unavailable")
        if sum(_alias(item.id) == _alias(account.authId) for item in files) > 1:
            return CredentialReceiptRejection("target_credential_collision")
        matched: Final = tuple(item for item in files if item.id == account.authId and item.size > 0)
        if len(matched) != 1:
            return CredentialReceiptRejection("target_credential_missing")
        return NativeCredentialBindings(
            scope=scope,
            target_account_id=scope.card_id,
            target_auth_id=account.authId,
            target_file_revision=matched[0].etag,
            target_file_size=matched[0].size,
            manifest_revision=snapshot.revision,
        )
