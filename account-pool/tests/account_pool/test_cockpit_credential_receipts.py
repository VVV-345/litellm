"""离线验证可信源凭据与目标字节对应回执，不访问数据库或提供商。"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Final
from uuid import UUID

import httpx
import pytest
from account_pool.channels.cliproxyapi.client import HttpCLIProxyClient
from account_pool.cockpit_admin import CockpitAdminClient, CockpitAdminConfig
from account_pool.credential_ownership import CredentialOwnership, credential_identity
from account_pool.domain import EnvironmentRecord, EnvironmentStatus, Provider, ProxyMode, QuotaSnapshot, SupplierKind
from account_pool.shared.secrets import EnvironmentSecretDeriver, SecretPurpose
from pydantic import JsonValue, SecretStr

if TYPE_CHECKING:
    from account_pool.cockpit_credential_receipts import CockpitCredentialReceiptService, TrustedCredentialScope

CARD: Final = UUID("00000000-0000-0000-0000-000000000001")
OTHER: Final = UUID("00000000-0000-0000-0000-000000000002")
SUBJECT: Final = "a" * 64
REVISION: Final = '"sha256-' + "b" * 64 + '"'
CONTENT: Final = b'{"type":"codex","access_token":"FAKE-ACCESS","refresh_token":"FAKE-REFRESH"}'
SECRETS: Final = EnvironmentSecretDeriver("FAKE-SEED-ONLY")


def scope(**updates: object) -> TrustedCredentialScope:
    from account_pool.cockpit_credential_receipts import TrustedCredentialScope

    return TrustedCredentialScope.model_validate({
        "subject_hash": SUBJECT, "card_id": CARD, "model_scope": "restricted", "models": ("fixture-model",),
        **updates,
    })


@dataclass
class Readers:
    current_scope: TrustedCredentialScope | None
    records: tuple[EnvironmentRecord, ...]
    content: bytes = field(default=CONTENT, repr=False)
    accounts: list[dict[str, JsonValue]] = field(default_factory=lambda: [
        {"id": str(CARD), "authId": "target.json", "authKind": "oauth"},
    ])
    files: list[dict[str, JsonValue]] = field(default_factory=lambda: [
        {"id": "target.json", "etag": '"sha256-' + hashlib.sha256(CONTENT).hexdigest() + '"', "size": len(CONTENT)},
    ])
    revision: str = REVISION
    now: int = 1000
    calls: list[str] = field(default_factory=list)
    unavailable: str = ""
    change_on_second: str = ""
    subject_reads: int = 0
    revoke_during_download: CredentialOwnership | None = None
    manifest_override: dict[str, JsonValue] | None = None

    async def read(self, subject_hash: str) -> TrustedCredentialScope | None:
        assert subject_hash == SUBJECT
        self.subject_reads += 1
        if self.unavailable == "subject":
            raise RuntimeError("FAKE-ACCESS")
        if self.subject_reads == 2:
            if self.change_on_second == "subject":
                self.current_scope = scope(models=("different-model",))
            elif self.change_on_second == "source":
                self.records = (self.records[0].model_copy(update={"version": 9}),)
            elif self.change_on_second == "target":
                self.revision = '"sha256-' + "c" * 64 + '"'
        return self.current_scope

    async def list(self) -> tuple[EnvironmentRecord, ...]:
        if self.unavailable == "source":
            raise RuntimeError("FAKE-REFRESH")
        return self.records

    def clock(self) -> int:
        return self.now

    async def http(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.method + " " + request.url.path)
        assert request.method == "GET", "receipt must never write or activate"
        if request.url.path == "/v0/management/auth-files/download":
            assert request.url.host == "cliproxy-" + CARD.hex
            assert request.url.params["name"] == "source.json"
            assert request.headers["X-Management-Key"] == SECRETS.derive(CARD, SecretPurpose.MANAGEMENT)
            if self.unavailable == "download":
                return httpx.Response(500, content=b"FAKE-ACCESS")
            if self.revoke_during_download is not None:
                await self.revoke_during_download.retain(CARD)
            return httpx.Response(200, content=self.content, headers={"content-type": "application/json"})
        assert request.headers["Authorization"] == "Bearer " + "m" * 32
        if self.unavailable == "target":
            return httpx.Response(500, content=b"FAKE-REFRESH")
        if request.url.path == "/admin/v1/manifest":
            return httpx.Response(200, headers={"ETag": self.revision}, json=(
                self.manifest_override if self.manifest_override is not None else {"accounts": self.accounts}
            ))
        assert request.url.path == "/admin/v1/auth-files"
        return httpx.Response(200, json={"files": self.files})


@asynccontextmanager
async def harness() -> AsyncGenerator[tuple[CockpitCredentialReceiptService, Readers, CredentialOwnership]]:
    from account_pool.cockpit_credential_receipts import CockpitCredentialReceiptService

    fingerprints: Final = credential_identity(CONTENT, "openai_codex", SECRETS).fingerprints
    record: Final = EnvironmentRecord(
        id=CARD, version=3, name="Fixture", provider=Provider.OPENAI, status=EnvironmentStatus.READY,
        enabled=True, manual_cooldown=False, concurrency_limit=1, proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None, available_models=("fixture-model",), enabled_models=("fixture-model",),
        auth_file_name="source.json", auth_index=None, credential_fingerprints=fingerprints, quota=QuotaSnapshot(),
        cooldown_until=None, oauth_state=None, oauth_expires_at=None, last_error=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    readers: Final = Readers(scope(), (record,))
    ownership: Final = CredentialOwnership()
    await ownership.claim(CARD, fingerprints)
    async with httpx.AsyncClient(transport=httpx.MockTransport(readers.http)) as http:
        service: Final = CockpitCredentialReceiptService(
            readers, readers, HttpCLIProxyClient(SECRETS, http), ownership,
            CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("m" * 32)), http),
            SECRETS, readers.clock,
        )
        yield service, readers, ownership


@pytest.mark.asyncio
async def test_owned_oauth_bytes_issue_replayable_dry_run_only_receipt() -> None:
    async with harness() as (service, readers, ownership):
        receipt: Final = await service.issue(SUBJECT)
        assert receipt.status == "owned_artifact_correspondence"
        assert receipt.ready_to_migrate is False
        assert receipt.payload.bindings.scope.card_id == CARD
        assert receipt.payload.bindings.scope.models == ("fixture-model",)
        assert receipt.payload.bindings.source_version == 3
        assert receipt.payload.bindings.target_auth_id == "target.json"
        assert receipt.payload.issued_at == 1000 and receipt.payload.expires_at == 1300
        assert receipt.payload.bindings.source_content_digest == hashlib.sha256(CONTENT).hexdigest()
        encoded: Final = receipt.model_dump_json()
        assert "FAKE" not in encoded + repr(receipt) + repr(service)
        assert await service.revalidate(SUBJECT, encoded) == receipt
        assert await ownership.owns(CARD, readers.records[0].credential_fingerprints)
        assert readers.subject_reads == 4
        assert len(readers.calls) == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,models", [("restricted", ("fixture-a", "fixture-b")), ("unrestricted", ())])
async def test_trusted_explicit_model_scopes_are_bound(kind: str, models: tuple[str, ...]) -> None:
    async with harness() as (service, readers, _):
        readers.current_scope = scope(model_scope=kind, models=models)
        receipt: Final = await service.issue(SUBJECT)
        assert receipt.status == "owned_artifact_correspondence"
        assert receipt.payload.bindings.scope.models == models
        assert receipt.payload.bindings.scope.model_scope == kind


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["subject", "source", "target"])
async def test_changes_during_observation_do_not_issue_receipt(changed: str) -> None:
    async with harness() as (service, readers, _):
        readers.change_on_second = changed
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected"
        assert result.code == "state_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["subject", "version", "manifest", "file", "content", "name", "ownership", "state"])
async def test_replay_rechecks_current_bindings(changed: str) -> None:
    async with harness() as (service, readers, ownership):
        receipt: Final = await service.issue(SUBJECT)
        assert receipt.status == "owned_artifact_correspondence"
        if changed == "subject":
            readers.current_scope = scope(models=("different-model",))
        elif changed == "version":
            readers.records = (readers.records[0].model_copy(update={"version": 4}),)
        elif changed == "manifest":
            readers.revision = '"sha256-' + "c" * 64 + '"'
        elif changed == "file":
            readers.files[0]["etag"] = '"sha256-' + "d" * 64 + '"'
        elif changed == "content":
            readers.content = CONTENT + b" "
            readers.files[0].update({"etag": '"sha256-' + hashlib.sha256(readers.content).hexdigest() + '"',
                                     "size": len(readers.content)})
        elif changed == "name":
            readers.accounts[0]["authId"] = "new.json"
            readers.files[0]["id"] = "new.json"
        elif changed == "ownership":
            await ownership.retain(CARD)
        else:
            readers.records = (readers.records[0].model_copy(update={"enabled": False}),)
        rejected: Final = await service.revalidate(SUBJECT, receipt.model_dump_json())
        assert rejected.status == "rejected"
        assert rejected.code in ("stale_receipt", "target_content_mismatch", "source_ownership_unverified", "source_unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["subject", "source", "download", "target"])
async def test_dependency_failures_do_not_leak_credentials(unavailable: str) -> None:
    async with harness() as (service, readers, _):
        readers.unavailable = unavailable
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected"
        assert "FAKE" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [
    {"enabled": False}, {"manual_cooldown": True}, {"configuration_pending": True},
    {"auth_file_disabled": True}, {"status": EnvironmentStatus.DISABLED},
])
async def test_unavailable_source_never_downloads(update: dict[str, object]) -> None:
    async with harness() as (service, readers, _):
        readers.records = (readers.records[0].model_copy(update=update),)
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected"
        assert result.code == "source_unavailable"
        assert not readers.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("case,code", [
    ("missing", "source_missing"), ("supplier", "source_auth_unsupported"),
    ("no_file", "source_credential_missing"), ("unsafe_file", "source_credential_missing"),
    ("fingerprints", "source_identity_mismatch"), ("ownership", "source_ownership_unverified"),
    ("collision", "source_credential_collision"), ("subject", "subject_unverified"),
])
async def test_metadata_claims_never_replace_source_provenance(case: str, code: str) -> None:
    async with harness() as (service, readers, ownership):
        if case == "missing":
            readers.records = ()
        elif case == "supplier":
            readers.records = (readers.records[0].model_copy(update={"supplier": SupplierKind.KIMI}),)
        elif case in ("no_file", "unsafe_file"):
            readers.records = (readers.records[0].model_copy(update={
                "auth_file_name": None if case == "no_file" else "../source.json",
            }),)
        elif case == "fingerprints":
            readers.records = (readers.records[0].model_copy(update={"credential_fingerprints": ("f" * 64,)}),)
            await ownership.claim(CARD, ("f" * 64,))
        elif case == "ownership":
            await ownership.retain(CARD)
        elif case == "collision":
            readers.records = (*readers.records, readers.records[0].model_copy(update={"id": OTHER}))
        else:
            readers.current_scope = None
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected"
        assert result.code == code


@pytest.mark.asyncio
async def test_same_source_basename_in_isolated_card_is_not_collision() -> None:
    async with harness() as (service, readers, _):
        readers.records = (*readers.records, readers.records[0].model_copy(update={
            "id": OTHER, "credential_fingerprints": ("f" * 64,),
        }))
        assert (await service.issue(SUBJECT)).status == "owned_artifact_correspondence"


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [
    {"type": "gemini"}, {"access_token": ""}, {"refresh_token": ""}, {"api_key": "FAKE-API"},
    {"personal_access_token": "FAKE-PAT"}, {"at_token": "FAKE-PAT"}, {"auth_mode": "agentIdentity"},
    {"openai_auth_mode": "personal_access_token"}, {"auth_mode": "oauth", "openai_auth_mode": "agentIdentity"},
    {"agent_private_key": "FAKE-PRIVATE"}, {"disabled": True}, {"token_type": "Basic"},
])
async def test_non_oauth_or_contradictory_source_auth_is_rejected(updates: dict[str, JsonValue]) -> None:
    async with harness() as (service, readers, _):
        readers.content = json.dumps({
            "type": "codex", "access_token": "FAKE-ACCESS", "refresh_token": "FAKE-REFRESH", **updates,
        }).encode()
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected"
        assert result.code == "source_auth_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize("case,code", [
    ("missing", "target_account_missing"), ("duplicate", "target_account_collision"),
    ("alias", "target_credential_collision"), ("shared", "target_credential_collision"),
    ("file_alias", "target_credential_collision"), ("no_file", "target_credential_missing"),
    ("path", "target_auth_unsupported"), ("auth_kind", "target_auth_unsupported"),
    ("api_key", "target_auth_unsupported"), ("access_only", "target_auth_unsupported"),
    ("digest", "target_content_mismatch"), ("size", "target_content_mismatch"),
])
async def test_unique_target_projection_and_exact_bytes_are_required(case: str, code: str) -> None:
    async with harness() as (service, readers, _):
        if case == "missing":
            readers.accounts = []
        elif case == "duplicate":
            readers.accounts.append(dict(readers.accounts[0]))
        elif case in ("alias", "shared"):
            readers.accounts.append({"id": str(OTHER), "authId": "dir/TARGET.JSON" if case == "alias" else "target.json"})
        elif case == "file_alias":
            readers.files.append({**readers.files[0], "id": "TARGET.JSON"})
        elif case == "no_file":
            readers.files = []
        elif case == "path":
            readers.accounts[0]["authId"] = "dir/target.json"
        elif case == "auth_kind":
            readers.accounts[0]["authKind"] = "agent_identity"
        elif case == "api_key":
            readers.accounts[0]["upstreamApiKey"] = "FAKE-API"
        elif case == "access_only":
            readers.accounts[0]["accessTokenOnly"] = True
        elif case == "digest":
            readers.files[0]["etag"] = '"sha256-' + "f" * 64 + '"'
        else:
            readers.files[0]["size"] = len(CONTENT) + 1
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected"
        assert result.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize("now,code", [(0, "invalid_clock"), (-1, "invalid_clock")])
async def test_nonpositive_clock_cannot_issue(now: int, code: str) -> None:
    async with harness() as (service, readers, _):
        readers.now = now
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected"
        assert result.code == code
        assert not readers.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("now,accepted", [(999, False), (1000, True), (1299, True), (1300, False), (1301, False)])
async def test_receipt_future_and_expiry_boundaries(now: int, accepted: bool) -> None:
    async with harness() as (service, readers, _):
        receipt: Final = await service.issue(SUBJECT)
        assert receipt.status == "owned_artifact_correspondence"
        readers.now = now
        result: Final = await service.revalidate(SUBJECT, receipt.model_dump_json())
        assert (result.status == "owned_artifact_correspondence") == accepted


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["tampered", "foreign", "subject", "zero", "negative", "future", "long_lifetime", "raw_token"])
async def test_untrusted_or_malformed_receipts_cannot_authorize_reads(case: str) -> None:
    async with harness() as (service, readers, _):
        receipt: Final = await service.issue(SUBJECT)
        assert receipt.status == "owned_artifact_correspondence"
        original_calls: Final = len(readers.calls)
        payload: Final = receipt.payload.model_dump(mode="json")
        if case == "zero":
            payload.update({"issued_at": 0, "expires_at": 300})
        elif case == "negative":
            payload.update({"issued_at": -1, "expires_at": 299})
        elif case == "future":
            payload.update({"issued_at": 1001, "expires_at": 1301})
        elif case == "long_lifetime":
            payload["expires_at"] = 100000
        elif case == "tampered":
            payload["expires_at"] = 1299
        key: Final = (EnvironmentSecretDeriver("FOREIGN-SEED") if case == "foreign" else SECRETS).derive(
            UUID(int=0), SecretPurpose.COCKPIT_CREDENTIAL_RECEIPT,
        ).encode()
        signature: Final = hmac.new(
            key, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256,
        ).hexdigest()
        encoded: Final = json.dumps({**receipt.model_dump(mode="json"), "payload": payload,
                                    "signature": receipt.signature if case == "tampered" else signature})
        result: Final = await service.revalidate(
            "sk-FAKE-TOKEN" if case == "raw_token" else "f" * 64 if case == "subject" else SUBJECT, encoded,
        )
        assert result.status == "rejected"
        assert "FAKE" not in repr(result)
        assert len(readers.calls) == original_calls


@pytest.mark.parametrize("models", [
    ("all-team-models",), ("all-proxy-models",), ("no-default-models",),
    ("fixture-model", "all-team-models"), ("fixture-model", "all-proxy-models"),
    ("fixture-model", "no-default-models"),
])
def test_native_model_sentinels_cannot_become_finite_receipt_scope(models: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        scope(models=models)


@pytest.mark.asyncio
async def test_denied_ownership_never_downloads_source_credentials() -> None:
    async with harness() as (service, readers, ownership):
        await ownership.retain(CARD)
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected" and result.code == "source_ownership_unverified"
        assert not readers.calls


@pytest.mark.asyncio
async def test_ownership_revoked_during_download_cannot_issue_receipt() -> None:
    async with harness() as (service, readers, ownership):
        readers.revoke_during_download = ownership
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected" and result.code == "source_ownership_unverified"
        assert readers.calls == ["GET /v0/management/auth-files/download"]


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical,alias,value", [
    ("id", "ID", str(OTHER)), ("authId", "AUTHID", "other.json"),
    ("authKind", "AUTHKIND", "api_key"), ("accessTokenOnly", "ACCESSTOKENONLY", True),
    ("upstreamApiKey", "UPSTREAMAPIKEY", "FAKE-API"),
    ("authId", "AuthId", "other.json"), ("authKind", "aUtHkInD", "api_key"),
])
@pytest.mark.parametrize("colliding", [False, True])
async def test_case_variant_account_fields_cannot_change_go_projection(
    canonical: str, alias: str, value: JsonValue, colliding: bool,
) -> None:
    async with harness() as (service, readers, _):
        canonical_account: Final[dict[str, JsonValue]] = {
            **readers.accounts[0], "accessTokenOnly": False, "upstreamApiKey": "",
        }
        readers.accounts = [{
            **{key: item for key, item in canonical_account.items() if colliding or key != canonical},
            alias: value,
        }]
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected" and result.code == "target_auth_unsupported"
        assert "FAKE" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["Accounts", "ACCOUNTS", "aCcOuNtS"])
@pytest.mark.parametrize("colliding", [False, True])
async def test_case_variant_accounts_collection_cannot_replace_go_projection(alias: str, colliding: bool) -> None:
    async with harness() as (service, readers, _):
        readers.manifest_override = {
            **({"accounts": [dict(readers.accounts[0])]} if colliding else {}),
            alias: [{"id": str(OTHER), "authId": "other.json"}],
        }
        result: Final = await service.issue(SUBJECT)
        assert result.status == "rejected" and result.code == "target_auth_unsupported"


@pytest.mark.asyncio
async def test_unrelated_manifest_metadata_does_not_block_canonical_projection() -> None:
    async with harness() as (service, readers, _):
        readers.accounts[0].update({"displayName": "Fixture", "metadata": {"AUTHID": "not-an-account-field"}})
        readers.manifest_override = {
            "accounts": [dict(readers.accounts[0])], "futureOptions": {"Accounts": []}, "AccountsBackup": [],
        }
        assert (await service.issue(SUBJECT)).status == "owned_artifact_correspondence"
