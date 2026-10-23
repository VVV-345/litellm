"""验证 Cockpit 映射预检只读、授权收窄及供应商隔离，不发送模型请求。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import httpx
import pytest
from account_pool.api import create_router
from account_pool.card_keys import CardKeyService, issue_card_key
from account_pool.cockpit_admin import CockpitAdminClient, CockpitAdminConfig, CockpitFailure
from account_pool.cockpit_preflight import CockpitPreflightReport, CockpitPreflightRequest, cockpit_preflight
from account_pool.domain import EnvironmentRecord, EnvironmentStatus, SupplierKind, utc_now
from account_pool.policies import AccountPolicy, PolicyView, RoutingPolicy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue, SecretStr
from test_account_pool import FakeCLIProxy, MemoryRepository, _record, _service
from test_management import MemoryKeys, MemoryPolicies

REVISION: Final = '"sha256-' + "a" * 64 + '"'


async def check(
    records: tuple[EnvironmentRecord, ...],
    *,
    candidates: tuple[UUID, ...] | None = None,
    fallback: bool = False,
    subject: str = "legacy_card_key",
    key_state: str = "active",
    manifest: dict[str, JsonValue] | None = None,
    strategy: str = "auto",
    preferred: tuple[UUID, ...] = (),
) -> CockpitPreflightReport:
    environments: Final = MemoryRepository(records[0])
    environments.records = {record.id: record for record in records}
    keys: Final = MemoryKeys()
    issued: Final = issue_card_key(records[0].id, uuid4(), utc_now()).record
    if key_state != "missing":
        keys.records[records[0].id] = replace(issued, revoked_at=utc_now()) if key_state == "revoked" else issued
    policies: Final = MemoryPolicies()
    policies.records[records[0].id] = PolicyView(
        card_id=records[0].id,
        version=2,
        policy=AccountPolicy(
            routing=RoutingPolicy.model_validate(
                {"fallback_enabled": fallback, "strategy": strategy, "preferred_account_ids": preferred}
            )
        ),
    )
    calls: Final[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method + " " + request.url.path)
        assert request.method == "GET"
        if request.url.path.endswith("manifest"):
            return httpx.Response(200, headers={"ETag": REVISION}, json=manifest or {"accounts": []})
        return httpx.Response(200, json={"files": [{"id": "private.json", "etag": REVISION, "size": 20}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result: Final = await cockpit_preflight(
            CockpitPreflightRequest.model_validate(
                {
                    "card_id": records[0].id,
                    "candidate_ids": candidates,
                    "subject_kind": subject,
                    "expected_key_id": uuid4() if key_state == "stale" else issued.key_id,
                }
            ),
            environments,
            policies,
            CardKeyService(keys),
            CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http),
        )
    assert environments.records == {record.id: record for record in records}
    assert policies.records[records[0].id].version == 2
    assert calls == ["GET /admin/v1/manifest", "GET /admin/v1/auth-files"]
    assert not isinstance(result, CockpitFailure)
    return result


@pytest.mark.asyncio
async def test_scope_is_server_derived_and_preference_cannot_authorize_another_card() -> None:
    a, b = _record(status=EnvironmentStatus.READY), _record(status=EnvironmentStatus.READY)
    report: Final = await check((a, b), candidates=(b.id,), preferred=(b.id,))
    assert report.authorized_account_ids == (a.id,)
    assert report.scope_valid is False
    assert "outside_authorized_scope" in report.accounts[0].blockers
    assert report.ready_to_migrate is False


@pytest.mark.asyncio
async def test_legacy_fallback_can_only_include_same_supplier_scope() -> None:
    a, b = _record(status=EnvironmentStatus.READY), _record(status=EnvironmentStatus.READY)
    c: Final = _record(status=EnvironmentStatus.READY).model_copy(update={"supplier": SupplierKind.KIMI})
    report: Final = await check((a, b, c), fallback=True)
    assert set(report.authorized_account_ids) == {a.id, b.id}
    assert {item.card_id for item in report.accounts} == {a.id, b.id}
    assert report.scope_valid is True
    assert report.persisted_manifest_revision == REVISION
    assert report.ready_to_migrate is False
    assert "runtime_eligibility_unverified" in report.pending_checks


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["missing", "revoked", "stale"])
async def test_missing_revoked_or_rotated_key_never_grants_scope(state: str) -> None:
    a: Final = _record(status=EnvironmentStatus.READY)
    report: Final = await check((a,), key_state=state)
    assert report.authorized_account_ids == ()
    assert "subject_binding_unverified" in report.blockers
    assert report.scope_valid is False


@pytest.mark.asyncio
async def test_native_subject_cannot_inherit_legacy_fallback_scope() -> None:
    a, b = _record(status=EnvironmentStatus.READY), _record(status=EnvironmentStatus.READY)
    report: Final = await check((a, b), subject="litellm_key", fallback=True, candidates=(b.id,))
    assert report.authorized_account_ids == ()
    assert "subject_binding_unverified" in report.blockers
    assert "outside_authorized_scope" in report.accounts[0].blockers


@pytest.mark.asyncio
async def test_empty_and_unknown_candidates_are_explicit_failures() -> None:
    a: Final = _record(status=EnvironmentStatus.READY)
    empty: Final = await check((a,), candidates=())
    assert "empty_candidates" in empty.blockers and empty.scope_valid is False
    missing: Final = await check((a,), candidates=(uuid4(),))
    assert "source_account_missing" in missing.accounts[0].blockers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates,code",
    [
        ({"enabled": False}, "account_unavailable"),
        ({"manual_cooldown": True}, "account_unavailable"),
        ({"configuration_pending": True}, "account_unavailable"),
        ({"auth_file_disabled": True}, "account_unavailable"),
        ({"auth_file_name": None}, "source_credential_missing"),
        ({"supplier": SupplierKind.KIMI}, "supplier_mismatch"),
    ],
)
async def test_ineligible_and_cross_supplier_candidates_are_blocked(updates: dict[str, object], code: str) -> None:
    a: Final = _record(status=EnvironmentStatus.READY)
    b: Final = _record(status=EnvironmentStatus.READY).model_copy(update=updates)
    report: Final = await check((a, b), fallback=True, candidates=(b.id,))
    assert code in report.accounts[0].blockers
    assert report.ready_to_migrate is False


@pytest.mark.asyncio
async def test_disabled_origin_does_not_grant_fallback_scope() -> None:
    a: Final = _record(status=EnvironmentStatus.READY).model_copy(update={"enabled": False})
    b: Final = _record(status=EnvironmentStatus.READY)
    report: Final = await check((a, b), fallback=True, candidates=(b.id,))
    assert report.authorized_account_ids == ()
    assert "source_card_unavailable" in report.blockers


@pytest.mark.asyncio
async def test_duplicate_target_and_credentials_are_not_identity_proof() -> None:
    a: Final = _record(status=EnvironmentStatus.READY)
    present: Final = await check(
        (a,),
        manifest={
            "accounts": [
                {"id": str(a.id), "authId": "private.json", "email": "PRIVATE-EMAIL", "accessToken": "PRIVATE-TOKEN"}
            ]
        },
    )
    assert "target_identity_unverified" in present.accounts[0].blockers
    assert "PRIVATE" not in present.model_dump_json() and "private.json" not in present.model_dump_json()
    duplicate: Final = await check((a,), manifest={"accounts": [{"id": str(a.id)}, {"id": str(a.id)}]})
    assert "target_account_collision" in duplicate.accounts[0].blockers


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["auto", "random", "priority", "quota", "plan", "expiry", "custom"])
async def test_strategy_is_never_silently_translated_to_cockpit_default(strategy: str) -> None:
    report: Final = await check((_record(status=EnvironmentStatus.READY),), strategy=strategy)
    assert report.source_strategy == strategy
    assert "strategy_mapping_unverified" in report.blockers


def test_preflight_manager_requires_auth_and_rejects_client_supplied_acl(tmp_path: Path) -> None:
    app: Final = FastAPI()
    app.include_router(
        create_router(_service(_record(status=EnvironmentStatus.READY), FakeCLIProxy(), tmp_path), "m" * 32)
    )
    with TestClient(app) as client:
        assert client.post("/api/cockpit/preflight", json={"card_id": str(uuid4())}).status_code == 401
        rejected: Final = client.post(
            "/api/cockpit/preflight",
            headers={"Authorization": "Bearer " + "m" * 32},
            json={"card_id": str(uuid4()), "subject_kind": "legacy_card_key", "authorized_account_ids": []},
        )
        assert rejected.status_code == 422
        disabled: Final = client.post(
            "/api/cockpit/preflight",
            headers={"Authorization": "Bearer " + "m" * 32},
            json={"card_id": str(uuid4()), "subject_kind": "legacy_card_key"},
        )
        assert disabled.status_code == 503
        assert disabled.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_preflight_inherits_effective_settings_and_handles_bad_manifest() -> None:
    from account_pool.cockpit_admin import CockpitFailure
    from account_pool.settings import AccountPoolSettings, AccountPoolSettingsView

    a: Final = _record(status=EnvironmentStatus.READY)
    keys: Final = MemoryKeys()
    issued: Final = issue_card_key(a.id, uuid4(), utc_now()).record
    keys.records[a.id] = issued

    class StoredSettings:
        async def get(self) -> AccountPoolSettingsView:
            return AccountPoolSettingsView(version=7, values=AccountPoolSettings(default_route="random"))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"ETag": REVISION},
                json={"accounts": []} if request.url.path.endswith("manifest") else {"files": []},
            )
        )
    ) as http:
        report: Final = await cockpit_preflight(
            CockpitPreflightRequest(card_id=a.id, subject_kind="legacy_card_key", expected_key_id=issued.key_id),
            MemoryRepository(a),
            MemoryPolicies(),
            CardKeyService(keys),
            CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http),
            StoredSettings(),
        )
    assert not isinstance(report, CockpitFailure)
    assert report.source_strategy == "random" and report.policy_version == 0 and report.settings_version == 7
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"ETag": REVISION},
                json={"accounts": [{"id": 4, "key": "PRIVATE"}]}
                if request.url.path.endswith("manifest")
                else {"files": []},
            )
        )
    ) as http:
        invalid: Final = await cockpit_preflight(
            CockpitPreflightRequest(card_id=a.id, subject_kind="legacy_card_key"),
            MemoryRepository(a),
            MemoryPolicies(),
            CardKeyService(keys),
            CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http),
        )
    assert invalid == CockpitFailure("invalid_response")


def test_manager_preflight_reads_only_and_matches_public_contract(tmp_path: Path) -> None:
    from account_pool.cockpit_preflight import CockpitPreflightReport

    from litellm.proxy.management_endpoints.account_pool_management_models import (
        CockpitPreflightReport as PublicReport,
    )
    from litellm.proxy.management_endpoints.account_pool_management_models import (
        CockpitPreflightRequest as PublicRequest,
    )

    a: Final = _record(status=EnvironmentStatus.READY)
    keys: Final = MemoryKeys()
    issued: Final = issue_card_key(a.id, uuid4(), utc_now()).record
    keys.records[a.id] = issued
    calls: Final[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method + " " + request.url.path)
        return httpx.Response(
            200,
            headers={"ETag": REVISION},
            json={"accounts": []} if request.url.path.endswith("manifest") else {"files": []},
        )

    http: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app: Final = FastAPI()
    app.include_router(
        create_router(
            _service(a, FakeCLIProxy(), tmp_path),
            "m" * 32,
            environments=MemoryRepository(a),
            policies=MemoryPolicies(),
            keys=CardKeyService(keys),
            cockpit_admin=CockpitAdminClient(
                CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http
            ),
        )
    )
    with TestClient(app) as client:
        response: Final = client.post(
            "/api/cockpit/preflight",
            headers={"Authorization": "Bearer " + "m" * 32},
            json={"card_id": str(a.id), "subject_kind": "legacy_card_key", "expected_key_id": str(issued.key_id)},
        )
    asyncio.run(http.aclose())
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert calls == ["GET /admin/v1/manifest", "GET /admin/v1/auth-files"]
    assert PublicReport.model_validate_json(response.content).scope_valid is True
    assert PublicReport.model_json_schema() == CockpitPreflightReport.model_json_schema()
    assert PublicRequest.model_json_schema() == CockpitPreflightRequest.model_json_schema()


@pytest.mark.asyncio
async def test_duplicate_candidates_and_unmapped_supplier_are_blocked() -> None:
    a: Final = _record(status=EnvironmentStatus.READY).model_copy(update={"supplier": SupplierKind.KIMI})
    report: Final = await check((a,), candidates=(a.id, a.id))
    assert report.scope_valid is False and "duplicate_candidates" in report.blockers
    assert "supplier_mapping_unsupported" in report.accounts[0].blockers
