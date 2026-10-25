"""离线验证 Cockpit 授权范围发布只写暂存投影，不激活、不部署或读取真实凭据。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Final, Literal, TypedDict
from uuid import UUID

import httpx
import pytest
from account_pool.cockpit_admin import CockpitAdminClient, CockpitAdminConfig
from account_pool.cockpit_credential_receipts import (
    CredentialBindings,
    CredentialReceipt,
    CredentialReceiptPayload,
    CredentialReceiptRejection,
    ReceiptRejectionCode,
    TrustedCredentialScope,
)
from account_pool.cockpit_scope import (
    CockpitScopeActivationRequest,
    CockpitScopePublishRequest,
    CockpitScopeRevokeRequest,
    CockpitScopeService,
    CockpitSubjectRouteState,
    CockpitSubjectScopeView,
    ScopeFailure,
    subject_routing_token,
)
from account_pool.shared.secrets import EnvironmentSecretDeriver
from pydantic import SecretStr, TypeAdapter

CARD: Final = UUID("00000000-0000-0000-0000-000000000001")
SUBJECT: Final = "a" * 64
R1: Final = '"sha256-' + "b" * 64 + '"'
R2: Final = '"sha256-' + "c" * 64 + '"'
R3: Final = '"sha256-' + "d" * 64 + '"'
SECRETS: Final = EnvironmentSecretDeriver("FAKE-SEED-ONLY-32-CHARACTERS")


class _FixtureData(TypedDict):
    accounts: list[dict[str, object]]
    apiKeys: list[dict[str, object]]
    modelIds: list[str]
    futureRoot: dict[str, str]


@dataclass
class ScopeFixture:
    mode: str = "ok"
    revision: str = R1
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    data: _FixtureData = field(
        default_factory=lambda: {
            "accounts": [{"id": str(CARD), "authId": "target.json", "authKind": "oauth"}],
            "apiKeys": [{"id": "private", "enabled": True, "key": "PRIVATE", "accountIds": [str(CARD)]}],
            "modelIds": ["fixture-model"],
            "futureRoot": {"secret": "PRIVATE"},
        }
    )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.headers.get("If-Match")))
        assert request.url.path == "/admin/v1/manifest"
        if request.method == "GET":
            return httpx.Response(200, headers={"ETag": self.revision}, json=self.data)
        assert request.method == "PUT"
        assert request.headers["If-Match"] == self.revision
        if self.mode == "conflict":
            return httpx.Response(412, text="PRIVATE")
        if self.mode == "lost":
            raise httpx.ReadTimeout("PRIVATE", request=request)
        if self.mode == "rejected":
            return httpx.Response(400, text="PRIVATE")
        self.data = json.loads(request.content)
        self.revision = R2
        return httpx.Response(202, headers={"ETag": R2}, json={"status": "persisted", "restartRequired": True})


def receipt(
    *,
    models: tuple[str, ...] = ("fixture-model",),
    model_scope: Literal["restricted", "unrestricted"] = "restricted",
    manifest_revision: str = R1,
) -> CredentialReceipt:
    scope: Final = TrustedCredentialScope(subject_hash=SUBJECT, card_id=CARD, model_scope=model_scope, models=models)
    bindings: Final = CredentialBindings(
        scope=scope,
        source_version=1,
        source_file="source.json",
        source_state_digest=hashlib.sha256(b"state").hexdigest(),
        source_fingerprint_digest=hashlib.sha256(b"fingerprint").hexdigest(),
        source_content_digest=hashlib.sha256(b"content").hexdigest(),
        source_size=1,
        target_account_id=CARD,
        target_auth_id="target.json",
        target_file_revision='"sha256-' + hashlib.sha256(b"content").hexdigest() + '"',
        manifest_revision=manifest_revision,
    )
    payload: Final = CredentialReceiptPayload(issued_at=1000, expires_at=1300, bindings=bindings)
    return CredentialReceipt(payload=payload, signature="d" * 64)


class Receipts:
    def __init__(
        self,
        *,
        rejection: str | None = None,
        models: tuple[str, ...] = ("fixture-model",),
        model_scope: Literal["restricted", "unrestricted"] = "restricted",
        manifest_revision: str = R1,
    ) -> None:
        self.rejection: Final[ReceiptRejectionCode | None] = (
            None if rejection is None else TypeAdapter(ReceiptRejectionCode).validate_python(rejection)
        )
        self.models = models
        self.model_scope: Final[Literal["restricted", "unrestricted"]] = model_scope
        self.manifest_revision = manifest_revision

    async def revalidate(self, subject_hash: str, encoded: str) -> CredentialReceipt | CredentialReceiptRejection:
        assert subject_hash == SUBJECT and encoded
        if self.rejection is not None:
            return CredentialReceiptRejection(self.rejection)
        return receipt(models=self.models, model_scope=self.model_scope, manifest_revision=self.manifest_revision)


class Subjects:
    def __init__(self, scope: TrustedCredentialScope | None = None, *, fail: bool = False) -> None:
        self.scope = scope
        self.fail = fail
        self.calls: list[str] = []

    async def read(self, subject_hash: str) -> TrustedCredentialScope | None:
        self.calls.append(subject_hash)
        if self.fail:
            raise RuntimeError("PRIVATE")
        return self.scope


def current_scope(**updates: object) -> TrustedCredentialScope:
    return TrustedCredentialScope.model_validate(
        {
            "subject_hash": SUBJECT,
            "card_id": CARD,
            "model_scope": "restricted",
            "models": ("fixture-model",),
            **updates,
        }
    )


def service_for(
    fixture: ScopeFixture,
    receipts: Receipts,
    *,
    origin: str | None = "http://cockpit-data.test:8080",
    subjects: Subjects | None = None,
) -> tuple[CockpitScopeService, httpx.AsyncClient]:
    http: Final = httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle))
    admin: Final = CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http)
    return CockpitScopeService(receipts, admin, SECRETS, origin, subjects=subjects), http


def publish(fixture: ScopeFixture, receipts: Receipts, *, revision: str | None = None) -> object:
    service, http = service_for(fixture, receipts)
    try:
        return asyncio.run(
            service.publish(
                CockpitScopePublishRequest(
                    subject_hash=SUBJECT, receipt="encoded", revision=revision or fixture.revision
                )
            )
        )
    finally:
        asyncio.run(http.aclose())


def test_publish_writes_only_a_disabled_staged_projection_and_preserves_unknown_fields() -> None:
    fixture: Final = ScopeFixture()
    original: Final = copy.deepcopy(fixture.data)
    result: Final = publish(fixture, Receipts())
    assert isinstance(result, CockpitSubjectScopeView)
    assert result.state == "staged" and result.ready_to_migrate is False
    assert result.account_ids == (str(CARD),) and result.models == ("fixture-model",)
    assert "key" not in result.model_dump() and "PRIVATE" not in result.model_dump_json()
    assert fixture.data["apiKeys"][-1] == {
        "id": "litellm-subject-" + SUBJECT,
        "label": "LiteLLM native subject " + SUBJECT[:12],
        "key": subject_routing_token(SECRETS, SUBJECT),
        "accountIds": [str(CARD)],
        "allowedModels": ["fixture-model"],
        "enabled": False,
    }
    assert fixture.data["futureRoot"] == original["futureRoot"]
    assert fixture.data["apiKeys"][0] == original["apiKeys"][0]
    assert [(method, _) for method, _ in fixture.calls] == [("GET", None), ("PUT", R1)]


@pytest.mark.parametrize(
    "models,model_scope,detail",
    [
        ((), "unrestricted", "model_scope_unsupported"),
        (("outside-model",), "restricted", "model_unmapped"),
    ],
)
def test_publish_rejects_unrestricted_or_unmapped_models(
    models: tuple[str, ...], model_scope: Literal["restricted", "unrestricted"], detail: str
) -> None:
    fixture: Final = ScopeFixture()
    result: Final = publish(fixture, Receipts(models=models, model_scope=model_scope))
    assert isinstance(result, ScopeFailure)
    assert result.status == 422 and result.code == detail
    assert [method for method, _ in fixture.calls] == ["GET"]


def test_publish_requires_current_manifest_and_matching_receipt_revision() -> None:
    fixture: Final = ScopeFixture()
    stale: Final = publish(fixture, Receipts(), revision=R3)
    assert isinstance(stale, ScopeFailure) and stale.status == 409 and stale.code == "manifest_changed"
    fixture2: Final = ScopeFixture()
    mismatched: Final = publish(fixture2, Receipts(manifest_revision=R3))
    assert isinstance(mismatched, ScopeFailure) and mismatched.code == "manifest_changed"
    assert all(method == "GET" for method, _ in fixture.calls + fixture2.calls)


@pytest.mark.parametrize(
    "rejection,status,detail",
    [
        ("subject_unverified", 409, "subject_unverified"),
        ("stale_receipt", 409, "receipt_stale"),
        ("source_missing", 422, "scope_unverified"),
        ("read_unavailable", 503, "unavailable"),
    ],
)
def test_receipt_rejections_map_to_fixed_public_codes(rejection: str, status: int, detail: str) -> None:
    fixture: Final = ScopeFixture()
    service, http = service_for(fixture, Receipts(rejection=rejection))
    try:
        result: Final = asyncio.run(
            service.publish(CockpitScopePublishRequest(subject_hash=SUBJECT, receipt="encoded", revision=R1))
        )
    finally:
        asyncio.run(http.aclose())
    assert isinstance(result, ScopeFailure)
    assert (result.status, result.code) == (status, detail)
    assert fixture.calls == []


def test_existing_reserved_entry_with_other_scope_is_not_overwritten() -> None:
    fixture: Final = ScopeFixture()
    fixture.data["apiKeys"].append(
        {
            "id": "litellm-subject-" + SUBJECT,
            "label": "LiteLLM native subject " + SUBJECT[:12],
            "key": "other-key",
            "accountIds": [str(CARD)],
            "allowedModels": ["fixture-model"],
            "enabled": False,
        }
    )
    result: Final = publish(fixture, Receipts())
    assert isinstance(result, ScopeFailure)
    assert result.status == 409 and result.code == "publication_conflict"
    assert [method for method, _ in fixture.calls] == ["GET"]


def test_republish_is_idempotent_and_revoke_removes_only_the_reserved_entry() -> None:
    fixture: Final = ScopeFixture()
    receipts: Final = Receipts()
    service, http = service_for(fixture, receipts)
    try:
        first: Final = asyncio.run(
            service.publish(CockpitScopePublishRequest(subject_hash=SUBJECT, receipt="encoded", revision=R1))
        )
        service_second: Final = CockpitScopeService(
            Receipts(manifest_revision=R2),
            CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http),
            SECRETS,
            "http://cockpit-data.test:8080",
        )
        second: Final = asyncio.run(
            service_second.publish(CockpitScopePublishRequest(subject_hash=SUBJECT, receipt="encoded", revision=R2))
        )
        revoked: Final = asyncio.run(service.revoke(CockpitScopeRevokeRequest(subject_hash=SUBJECT, revision=R2)))
        absent_again: Final = asyncio.run(service.revoke(CockpitScopeRevokeRequest(subject_hash=SUBJECT, revision=R2)))
    finally:
        asyncio.run(http.aclose())
    assert isinstance(first, CockpitSubjectScopeView) and first.state == "staged"
    assert isinstance(second, CockpitSubjectScopeView) and second.persisted_manifest_revision == R2
    assert isinstance(revoked, CockpitSubjectScopeView) and revoked.state == "absent"
    assert isinstance(absent_again, CockpitSubjectScopeView) and absent_again.state == "absent"
    assert fixture.data["apiKeys"] == [{"id": "private", "enabled": True, "key": "PRIVATE", "accountIds": [str(CARD)]}]
    assert [method for method, _ in fixture.calls].count("PUT") == 2


@pytest.mark.parametrize(
    "mode,status,detail", [("conflict", 409, "revision_conflict"), ("lost", 503, "write_outcome_unknown")]
)
def test_write_conflict_or_lost_receipt_never_retries(mode: str, status: int, detail: str) -> None:
    fixture: Final = ScopeFixture(mode=mode)
    result: Final = publish(fixture, Receipts())
    assert isinstance(result, ScopeFailure)
    assert (result.status, result.code) == (status, detail)
    assert [method for method, _ in fixture.calls] == ["GET", "PUT"]


def test_route_state_is_staged_until_enabled_and_hides_token_from_repr() -> None:
    fixture: Final = ScopeFixture()
    subjects: Final = Subjects(current_scope())
    service, http = service_for(fixture, Receipts(), subjects=subjects)
    try:
        assert isinstance(
            asyncio.run(
                service.publish(CockpitScopePublishRequest(subject_hash=SUBJECT, receipt="encoded", revision=R1))
            ),
            CockpitSubjectScopeView,
        )
        staged: Final = asyncio.run(service.route_state(SUBJECT))
        fixture.data["apiKeys"][-1]["enabled"] = True
        active: Final = asyncio.run(service.route_state(SUBJECT))
        blocked_service: Final = CockpitScopeService(
            Receipts(),
            CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http),
            SECRETS,
            None,
            subjects=subjects,
        )
        blocked: Final = asyncio.run(blocked_service.route_state(SUBJECT))
    finally:
        asyncio.run(http.aclose())
    assert isinstance(staged, CockpitSubjectRouteState) and staged.state == "staged"
    assert staged.origin is None and staged.api_key is None
    assert subjects.calls == [SUBJECT]
    assert isinstance(active, CockpitSubjectRouteState) and active.state == "active"
    assert active.origin == "http://cockpit-data.test:8080"
    assert active.api_key == subject_routing_token(SECRETS, SUBJECT)
    assert isinstance(active.api_key, str) and active.api_key not in repr(active)
    assert isinstance(blocked, CockpitSubjectRouteState) and blocked.state == "blocked"


def test_scope_view_reports_absent_and_blocks_tampered_reserved_entry() -> None:
    fixture: Final = ScopeFixture()
    service, http = service_for(fixture, Receipts())
    try:
        absent: Final = asyncio.run(service.read(SUBJECT))
        fixture.data["apiKeys"].append(
            {
                "id": "litellm-subject-" + SUBJECT,
                "label": "other",
                "key": "other",
                "accountIds": [str(CARD)],
                "allowedModels": ["fixture-model"],
                "enabled": True,
            }
        )
        blocked: Final = asyncio.run(service.read(SUBJECT))
    finally:
        asyncio.run(http.aclose())
    assert isinstance(absent, CockpitSubjectScopeView) and absent.state == "absent"
    assert isinstance(blocked, CockpitSubjectScopeView) and blocked.state == "blocked"
    assert blocked.account_ids == () and blocked.models == ()


def test_scope_contract_matches_public_proxy_models() -> None:
    from litellm.proxy.management_endpoints.account_pool_management_models import (
        CockpitScopeActivationRequest as PublicActivation,
    )
    from litellm.proxy.management_endpoints.account_pool_management_models import (
        CockpitScopePublishRequest as PublicPublish,
    )
    from litellm.proxy.management_endpoints.account_pool_management_models import (
        CockpitScopeRevokeRequest as PublicRevoke,
    )
    from litellm.proxy.management_endpoints.account_pool_management_models import (
        CockpitSubjectScopeView as PublicView,
    )

    assert CockpitScopeActivationRequest.model_json_schema() == PublicActivation.model_json_schema()
    assert CockpitScopePublishRequest.model_json_schema() == PublicPublish.model_json_schema()
    assert CockpitScopeRevokeRequest.model_json_schema() == PublicRevoke.model_json_schema()
    assert CockpitSubjectScopeView.model_json_schema() == PublicView.model_json_schema()


class _EmptyKeys:
    async def status(self, _card_id: object) -> None:
        return None


class _EmptyLogs:
    async def record(self, *_args: object, **_kwargs: object) -> None:
        return None


class _EmptyEnvironments:
    async def get(self, _card_id: object) -> None:
        return None

    async def list(self) -> tuple[()]:
        return ()


class _EmptyPolicies:
    async def get(self, _card_id: object) -> None:
        return None


def test_scope_routes_require_manager_and_expose_only_staged_state() -> None:
    from account_pool.api import create_router
    from account_pool.card_keys import CardKeyService
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    fixture: Final = ScopeFixture()
    http: Final = httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle))
    admin: Final = CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http)
    service: Final = CockpitScopeService(Receipts(), admin, SECRETS, "http://cockpit-data.test:8080")
    app: Final = FastAPI()
    app.include_router(
        create_router(
            None,  # pyright: ignore[reportArgumentType]  # route handlers under test do not use the environment service
            "m" * 32,
            keys=CardKeyService(_EmptyKeys()),  # pyright: ignore[reportArgumentType]  # only card routes use the repository
            logs=_EmptyLogs(),  # pyright: ignore[reportArgumentType]  # only log routes use the service
            environments=_EmptyEnvironments(),  # pyright: ignore[reportArgumentType]  # only card routes list environments
            policies=_EmptyPolicies(),  # pyright: ignore[reportArgumentType]  # policy routes are not requested here
            cockpit_admin=admin,
            cockpit_scope=service,
        )
    )
    body: Final = {"subject_hash": SUBJECT, "receipt": "encoded", "revision": R1}
    with TestClient(app) as client:
        denied: Final = client.post("/api/cockpit/scope", json=body)
        published: Final = client.post("/api/cockpit/scope", json=body, headers={"Authorization": "Bearer " + "m" * 32})
        read: Final = client.get("/api/cockpit/scope/" + SUBJECT, headers={"Authorization": "Bearer " + "m" * 32})
        route: Final = client.get("/internal/cockpit/route/" + SUBJECT, headers={"Authorization": "Bearer " + "m" * 32})
        invalid: Final = client.get("/api/cockpit/scope/not-a-hash", headers={"Authorization": "Bearer " + "m" * 32})
        revoked: Final = client.request(
            "DELETE",
            "/api/cockpit/scope",
            json={"subject_hash": SUBJECT, "revision": R2},
            headers={"Authorization": "Bearer " + "m" * 32},
        )
    asyncio.run(http.aclose())
    assert denied.status_code == 401
    assert published.status_code == 200 and published.json()["state"] == "staged"
    assert published.headers["cache-control"] == "no-store"
    assert "key" not in published.json() and subject_routing_token(SECRETS, SUBJECT) not in published.text
    assert read.status_code == 200 and read.json()["state"] == "staged"
    assert route.status_code == 200 and route.json()["state"] == "staged"
    assert route.json()["api_key"] is None and route.json()["origin"] is None
    assert invalid.status_code == 422 and invalid.json()["detail"] == "invalid_receipt"
    assert revoked.status_code == 200 and revoked.json()["state"] == "absent"
    assert revoked.headers["cache-control"] == "no-store"


def test_route_state_revalidates_current_subject_and_fails_closed() -> None:
    fixture: Final = ScopeFixture()
    http: Final = httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle))
    admin: Final = CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http)

    def make(subjects: Subjects | None) -> CockpitScopeService:
        return CockpitScopeService(Receipts(), admin, SECRETS, "http://cockpit-data.test:8080", subjects=subjects)

    try:
        assert isinstance(
            asyncio.run(
                make(Subjects(current_scope())).publish(
                    CockpitScopePublishRequest(subject_hash=SUBJECT, receipt="encoded", revision=R1)
                )
            ),
            CockpitSubjectScopeView,
        )
        entries = fixture.data["apiKeys"]
        assert isinstance(entries, list) and isinstance(entries[-1], dict)
        entries[-1]["enabled"] = True
        matching: Final = asyncio.run(make(Subjects(current_scope())).route_state(SUBJECT))
        drifted: Final = asyncio.run(make(Subjects(current_scope(models=("other-model",)))).route_state(SUBJECT))
        no_reader: Final = asyncio.run(make(None).route_state(SUBJECT))
        failing: Final = asyncio.run(make(Subjects(fail=True)).route_state(SUBJECT))
        matching_view: Final = asyncio.run(make(Subjects(current_scope())).read(SUBJECT))
        drifted_view: Final = asyncio.run(make(Subjects(current_scope(models=("other-model",)))).read(SUBJECT))
        no_reader_view: Final = asyncio.run(make(None).read(SUBJECT))
    finally:
        asyncio.run(http.aclose())
    assert isinstance(matching, CockpitSubjectRouteState) and matching.state == "active"
    assert isinstance(drifted, CockpitSubjectRouteState) and drifted.state == "blocked"
    assert isinstance(no_reader, CockpitSubjectRouteState) and no_reader.state == "blocked"
    assert isinstance(failing, CockpitSubjectRouteState) and failing.state == "blocked"
    assert isinstance(matching_view, CockpitSubjectScopeView) and matching_view.state == "active"
    assert isinstance(drifted_view, CockpitSubjectScopeView) and drifted_view.state == "blocked"
    assert isinstance(no_reader_view, CockpitSubjectScopeView) and no_reader_view.state == "blocked"


def test_activation_and_deactivation_flip_the_reserved_entry() -> None:
    fixture: Final = ScopeFixture()
    http: Final = httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle))
    admin: Final = CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http)

    def make(subjects: Subjects | None) -> CockpitScopeService:
        return CockpitScopeService(Receipts(), admin, SECRETS, "http://cockpit-data.test:8080", subjects=subjects)

    try:
        assert isinstance(
            asyncio.run(
                make(Subjects(current_scope())).publish(
                    CockpitScopePublishRequest(subject_hash=SUBJECT, receipt="encoded", revision=R1)
                )
            ),
            CockpitSubjectScopeView,
        )
        activated: Final = asyncio.run(
            make(Subjects(current_scope())).activate(CockpitScopeActivationRequest(subject_hash=SUBJECT, revision=R2))
        )
        route: Final = asyncio.run(make(Subjects(current_scope())).route_state(SUBJECT))
        enabled_after_activate = fixture.data["apiKeys"][-1]["enabled"]
        deactivated: Final = asyncio.run(
            make(Subjects(current_scope())).deactivate(CockpitScopeActivationRequest(subject_hash=SUBJECT, revision=R2))
        )
    finally:
        asyncio.run(http.aclose())
    assert isinstance(activated, CockpitSubjectScopeView) and activated.state == "active"
    assert enabled_after_activate is True
    assert isinstance(route, CockpitSubjectRouteState) and route.state == "active"
    assert isinstance(deactivated, CockpitSubjectScopeView) and deactivated.state == "staged"
    assert fixture.data["apiKeys"][-1]["enabled"] is False


def test_activation_rejects_absent_drifted_or_unverified_scope() -> None:
    fixture: Final = ScopeFixture()
    http: Final = httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle))
    admin: Final = CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http)

    def make(subjects: Subjects | None) -> CockpitScopeService:
        return CockpitScopeService(Receipts(), admin, SECRETS, "http://cockpit-data.test:8080", subjects=subjects)

    try:
        absent: Final = asyncio.run(
            make(Subjects(current_scope())).activate(CockpitScopeActivationRequest(subject_hash=SUBJECT, revision=R1))
        )
        assert isinstance(
            asyncio.run(
                make(Subjects(current_scope())).publish(
                    CockpitScopePublishRequest(subject_hash=SUBJECT, receipt="encoded", revision=R1)
                )
            ),
            CockpitSubjectScopeView,
        )
        stale: Final = asyncio.run(
            make(Subjects(current_scope())).activate(CockpitScopeActivationRequest(subject_hash=SUBJECT, revision=R1))
        )
        drifted: Final = asyncio.run(
            make(Subjects(current_scope(models=("other-model",)))).activate(
                CockpitScopeActivationRequest(subject_hash=SUBJECT, revision=R2)
            )
        )
        no_reader: Final = asyncio.run(
            make(None).activate(CockpitScopeActivationRequest(subject_hash=SUBJECT, revision=R2))
        )
    finally:
        asyncio.run(http.aclose())
    assert isinstance(absent, ScopeFailure) and absent.code == "not_published"
    assert isinstance(stale, ScopeFailure) and stale.code == "revision_conflict"
    assert isinstance(drifted, ScopeFailure) and drifted.code == "activation_blocked"
    assert isinstance(no_reader, ScopeFailure) and no_reader.code == "activation_blocked"
    assert fixture.data["apiKeys"][-1]["enabled"] is False


def test_route_and_view_block_after_the_authorized_account_is_removed() -> None:
    fixture: Final = ScopeFixture()
    http: Final = httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle))
    admin: Final = CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http)

    def make() -> CockpitScopeService:
        return CockpitScopeService(
            Receipts(), admin, SECRETS, "http://cockpit-data.test:8080", subjects=Subjects(current_scope())
        )

    try:
        assert isinstance(
            asyncio.run(
                make().publish(CockpitScopePublishRequest(subject_hash=SUBJECT, receipt="encoded", revision=R1))
            ),
            CockpitSubjectScopeView,
        )
        assert isinstance(
            asyncio.run(make().activate(CockpitScopeActivationRequest(subject_hash=SUBJECT, revision=R2))),
            CockpitSubjectScopeView,
        )
        fixture.data["accounts"] = []
        route: Final = asyncio.run(make().route_state(SUBJECT))
        view: Final = asyncio.run(make().read(SUBJECT))
    finally:
        asyncio.run(http.aclose())
    assert isinstance(route, CockpitSubjectRouteState) and route.state == "blocked"
    assert isinstance(view, CockpitSubjectScopeView) and view.state == "blocked"
