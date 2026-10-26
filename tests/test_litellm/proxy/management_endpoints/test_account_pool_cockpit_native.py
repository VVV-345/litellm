import asyncio
import hashlib
import json
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_cockpit_subject_reader import TrustedCredentialScope

SUBJECT = "a" * 64
CARD = UUID(int=1)
PREFIX = "/account_pool/cockpit/native"
SECRET = "COCKPIT-ADMIN-SECRET-32-CHARACTERS-LONG"


class Cockpit:
    def __init__(self):
        self.data = {
            "accounts": [{"id": str(CARD), "authId": "fixture.json", "authKind": "oauth"}],
            "modelIds": ["fixture-model"],
            "apiKeys": [],
        }
        self.revision = '"sha256-' + "b" * 64 + '"'
        self.active = self.revision
        self.revoked = False
        self.credential_present = True
        self.calls = []

    async def read(self, subject_hash):
        if self.revoked:
            return None
        return TrustedCredentialScope(
            subject_hash=subject_hash, card_id=CARD, model_scope="restricted", models=("fixture-model",)
        )

    def handle(self, request):
        assert request.url.host == "cockpit.test", "Manager or legacy fallback must not be called"
        self.calls.append((request.method, request.url.path))
        if request.url.path == "/admin/v1/auth-files":
            if not self.credential_present:
                return httpx.Response(200, json={"files": []})
            return httpx.Response(
                200, json={"files": [{"id": "fixture.json", "etag": '"sha256-' + "c" * 64 + '"', "size": 123}]}
            )
        if request.url.path == "/admin/v1/policy-runtime":
            return httpx.Response(
                200,
                json={
                    "persistedRevision": self.revision,
                    "activeRevision": self.active,
                    "applySupported": self.active == self.revision,
                    "blockedReason": None if self.active == self.revision else "non_policy_change",
                },
            )
        assert request.url.path == "/admin/v1/manifest"
        if request.method == "PUT":
            if request.headers["If-Match"] != self.revision:
                return httpx.Response(412)
            self.data = json.loads(request.content)
            self.revision = '"sha256-' + hashlib.sha256(request.content).hexdigest() + '"'
            return httpx.Response(
                202, json={"status": "persisted", "restartRequired": True}, headers={"ETag": self.revision}
            )
        assert request.method == "GET"
        return httpx.Response(200, json=self.data, headers={"ETag": self.revision})


def setup(user=LitellmUserRoles.PROXY_ADMIN, data_url="http://cockpit.test"):
    from litellm.proxy.management_endpoints.account_pool_cockpit_native import (
        NativeCockpitConfig,
        NativeCockpitServices,
    )
    from litellm.proxy.management_endpoints.account_pool_cockpit_native_api import create_native_cockpit_router

    cockpit = Cockpit()
    http = httpx.AsyncClient(transport=httpx.MockTransport(cockpit.handle))
    config = NativeCockpitConfig(
        admin_url="http://cockpit.test",
        admin_token=SecretStr(SECRET),
        data_url=data_url,
        receipt_key=SecretStr("RECEIPT-KEY-32-CHARACTERS-FIXTURE-ONLY"),
        routing_seed=SecretStr("ROUTING-SEED-32-CHARACTERS-FIXTURE-ONLY"),
    )
    services = NativeCockpitServices(config, http=http, subjects=cockpit, clock=lambda: 1000)
    app = FastAPI()
    app.include_router(create_native_cockpit_router(lambda: services))
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=user)
    return cockpit, services, http, app


def test_native_admin_workflow_does_not_use_manager_or_expose_secrets():
    cockpit, services, http, app = setup()
    try:
        with TestClient(app) as client:
            accounts = client.get(PREFIX + "/accounts")
            assert accounts.status_code == 200 and accounts.json()[0]["account_id"] == str(CARD)
            preflight = client.post(PREFIX + "/preflight", json={"key_hash": SUBJECT})
            assert preflight.status_code == 200 and preflight.json()["status"] == "eligible"
            issued = client.post(PREFIX + "/credential-receipts", json={"key_hash": SUBJECT})
            assert issued.status_code == 200 and issued.json()["status"] == "native_scope_verified"
            receipt = issued.json()["receipt"]
            verified = client.post(
                PREFIX + "/credential-receipts/revalidate", json={"key_hash": SUBJECT, "receipt": receipt}
            )
            assert verified.status_code == 200 and verified.json()["status"] == "native_scope_verified"
            published = client.post(
                PREFIX + "/scope", json={"subject_hash": SUBJECT, "receipt": receipt, "revision": cockpit.revision}
            )
            assert published.status_code == 200 and published.json()["scope"]["state"] == "staged"
            activated = client.post(
                PREFIX + "/scope/activate", json={"subject_hash": SUBJECT, "revision": cockpit.revision}
            )
            assert activated.status_code == 200 and activated.json()["runtime_state"] == "pending"
            assert asyncio.run(services.route_state(SUBJECT)).state == "blocked"
            cockpit.active = cockpit.revision
            confirmed = client.get(PREFIX + "/scope/" + SUBJECT)
            assert confirmed.json()["runtime_state"] == "active"
            assert asyncio.run(services.route_state(SUBJECT)).state == "active"
            cockpit.revoked = True
            blocked = client.get(PREFIX + "/scope/" + SUBJECT)
            assert blocked.json()["scope"]["state"] == "blocked"
            assert asyncio.run(services.route_state(SUBJECT)).api_key is None
            deleted = client.request(
                "DELETE", PREFIX + "/scope", json={"subject_hash": SUBJECT, "revision": cockpit.revision}
            )
            assert deleted.status_code == 200 and deleted.json()["scope"]["state"] == "absent"
            for response in (accounts, preflight, issued, verified, published, activated, confirmed, blocked, deleted):
                assert response.headers["cache-control"] == "no-store"
                assert SECRET not in response.text and '"api_key"' not in response.text
    finally:
        asyncio.run(http.aclose())


def test_native_admin_permission_is_checked_before_any_observation():
    cockpit, _, http, app = setup(LitellmUserRoles.INTERNAL_USER)
    try:
        with TestClient(app) as client:
            for method, path, body in [
                ("GET", "/accounts", None),
                ("POST", "/preflight", {"key_hash": SUBJECT}),
                ("POST", "/credential-receipts", {"key_hash": SUBJECT}),
                ("GET", "/scope/" + SUBJECT, None),
            ]:
                assert client.request(method, PREFIX + path, json=body).status_code == 403
            assert cockpit.calls == []
    finally:
        asyncio.run(http.aclose())


def test_native_policy_runtime_uses_cockpit_directly():
    cockpit, _, http, app = setup()
    try:
        with TestClient(app) as client:
            policy = client.get(PREFIX + "/policy")
            runtime = client.get(PREFIX + "/policy/runtime")
            assert policy.status_code == runtime.status_code == 200
            assert policy.json()["revision"] == runtime.json()["active_revision"] == cockpit.revision
    finally:
        asyncio.run(http.aclose())


def test_invalid_receipt_and_stale_revision_do_not_write():
    cockpit, _, http, app = setup()
    try:
        with TestClient(app) as client:
            invalid = client.post(
                PREFIX + "/scope",
                json={"subject_hash": SUBJECT, "receipt": "SECRET-INVALID-RECEIPT", "revision": cockpit.revision},
            )
            assert invalid.status_code == 422 and "SECRET" not in invalid.text
            issued = client.post(PREFIX + "/credential-receipts", json={"key_hash": SUBJECT}).json()
            bad = client.post(
                PREFIX + "/scope",
                json={"subject_hash": SUBJECT, "receipt": issued["receipt"], "revision": '"sha256-' + "f" * 64 + '"'},
            )
            assert bad.status_code == 409
            assert not any(method == "PUT" for method, _ in cockpit.calls)
            invalid = client.post(PREFIX + "/credential-receipts", json={"key_hash": "SECRET-RAW-KEY"})
            assert invalid.status_code == 422 and "SECRET" not in invalid.text
    finally:
        asyncio.run(http.aclose())


def test_new_mode_fails_closed_for_absent_scope_and_disables_legacy_background_work(monkeypatch):
    from fastapi import HTTPException

    from litellm.proxy.management_endpoints.account_pool_cockpit_route import (
        CockpitRouteResolver,
        RouteDecision,
        cockpit_route_for_request,
    )
    from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity
    from litellm.proxy.management_endpoints.account_pool_reconciler import (
        reconcile_configured_account_pool,
        start_reconciliation_loop,
    )

    monkeypatch.setenv("ACCOUNT_POOL_BACKEND", "cockpit")
    monkeypatch.setenv("ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_POOL_MANAGER_TOKEN", "FORBIDDEN-MANAGER-TOKEN-32-CHARACTERS")

    async def absent(_):
        return RouteDecision("absent")

    identity = PoolIdentity(key_hash=SUBJECT, request_id=UUID(int=3), card_id=CARD)
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            cockpit_route_for_request(
                identity,
                SUBJECT,
                {"model": "fixture-model"},
                "aresponses",
                resolver=CockpitRouteResolver(lookup=absent),
            )
        )
    assert error.value.status_code == 503
    assert asyncio.run(reconcile_configured_account_pool()) is False
    assert start_reconciliation_loop() is None


def test_default_native_lookup_reads_current_permissions_without_active_cache(monkeypatch):
    from litellm.proxy.management_endpoints import account_pool_cockpit_native as native
    from litellm.proxy.management_endpoints.account_pool_cockpit_route import CockpitRouteResolver
    from litellm.proxy.management_endpoints.account_pool_cockpit_scope_service import (
        CockpitScopeActivationRequest,
        CockpitScopePublishRequest,
    )

    cockpit, services, http, _ = setup()
    monkeypatch.setenv("ACCOUNT_POOL_BACKEND", "cockpit")
    monkeypatch.setattr(native, "native_cockpit_services", lambda: services)

    async def check():
        receipt = await services.receipts.issue(SUBJECT)
        await services.scope.publish(
            CockpitScopePublishRequest(
                subject_hash=SUBJECT, receipt=receipt.model_dump_json(), revision=cockpit.revision
            )
        )
        await services.scope.activate(CockpitScopeActivationRequest(subject_hash=SUBJECT, revision=cockpit.revision))
        cockpit.active = cockpit.revision
        resolver = CockpitRouteResolver()
        assert (await resolver.decision(SUBJECT)).state == "active"
        cockpit.revoked = True
        assert (await resolver.decision(SUBJECT)).state == "blocked"

    try:
        asyncio.run(check())
    finally:
        asyncio.run(http.aclose())


def test_native_mode_disables_legacy_transport_before_client_creation(monkeypatch):
    from litellm.proxy.management_endpoints.account_pool_endpoints import create_account_pool_router

    monkeypatch.setenv("ACCOUNT_POOL_BACKEND", "cockpit")
    calls = []

    def forbidden():
        calls.append("manager")
        raise AssertionError("Manager client must not be created")

    app = FastAPI()
    app.include_router(create_account_pool_router(forbidden))
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
    with TestClient(app) as client:
        assert client.get("/account_pool/environments").status_code == 503
        assert client.get("/account_pool/cockpit/policy").status_code == 503
    assert calls == []


def test_native_configuration_does_not_reuse_keys_or_fall_back_to_manager(monkeypatch):
    from pydantic import ValidationError

    from litellm.proxy.management_endpoints.account_pool_cockpit_native import (
        NativeCockpitConfig,
        NativeCockpitError,
        native_cockpit_enabled,
    )

    monkeypatch.setenv("ACCOUNT_POOL_BACKEND", "mistyped")
    with pytest.raises(ValueError):
        native_cockpit_enabled()
    monkeypatch.delenv("ACCOUNT_POOL_COCKPIT_RECEIPT_KEY", raising=False)
    with pytest.raises(NativeCockpitError):
        NativeCockpitConfig.from_environment()
    with pytest.raises(ValidationError):
        NativeCockpitConfig(
            admin_url="http://cockpit.test",
            data_url="http://cockpit.test",
            admin_token=SecretStr(SECRET),
            receipt_key=SecretStr(SECRET),
            routing_seed=SecretStr(SECRET),
        )


def test_ui_cannot_claim_active_after_credential_is_removed():
    cockpit, services, http, app = setup()
    try:
        with TestClient(app) as client:
            issued = client.post(PREFIX + "/credential-receipts", json={"key_hash": SUBJECT}).json()
            client.post(
                PREFIX + "/scope",
                json={"subject_hash": SUBJECT, "receipt": issued["receipt"], "revision": cockpit.revision},
            )
            client.post(PREFIX + "/scope/activate", json={"subject_hash": SUBJECT, "revision": cockpit.revision})
            cockpit.active = cockpit.revision
            cockpit.credential_present = False
            status = client.get(PREFIX + "/scope/" + SUBJECT)
            assert status.json()["runtime_state"] != "active"
            assert status.json()["scope"]["state"] == "blocked"
            assert asyncio.run(services.route_state(SUBJECT)).api_key is None
    finally:
        asyncio.run(http.aclose())


def test_native_request_reaches_local_cockpit_once_without_manager(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from litellm.proxy.management_endpoints import account_pool_cockpit_native as native
    from litellm.proxy.management_endpoints.account_pool_cockpit_scope_service import (
        CockpitScopeActivationRequest,
        CockpitScopePublishRequest,
    )
    from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity
    from litellm.proxy.route_llm_request import _route_request_single_attempt

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            content = json.dumps(
                {
                    "id": "resp_fixture",
                    "object": "response",
                    "created_at": 1000,
                    "status": "completed",
                    "model": "fixture-model",
                    "output": [
                        {
                            "id": "msg_fixture",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "fixture-ok", "annotations": []}],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cockpit, services, http, _ = setup(data_url=f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("ACCOUNT_POOL_BACKEND", "cockpit")
        monkeypatch.setenv("ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED", "true")
        monkeypatch.setattr(native, "native_cockpit_services", lambda: services)

        async def run():
            receipt = await services.receipts.issue(SUBJECT)
            await services.scope.publish(
                CockpitScopePublishRequest(
                    subject_hash=SUBJECT, receipt=receipt.model_dump_json(), revision=cockpit.revision
                )
            )
            await services.scope.activate(
                CockpitScopeActivationRequest(subject_hash=SUBJECT, revision=cockpit.revision)
            )
            cockpit.active = cockpit.revision
            reset = pool_identity.set(PoolIdentity(key_hash=SUBJECT, request_id=UUID(int=3), card_id=CARD))
            try:
                result = await _route_request_single_attempt(
                    {"model": "fixture-model", "input": "offline fixture", "num_retries": 3},
                    None,
                    None,
                    "aresponses",
                    UserAPIKeyAuth(token=SUBJECT),
                )
                assert result.output[0].content[0].text == "fixture-ok"
                assert result.usage.input_tokens == result.usage.output_tokens == 1
            finally:
                pool_identity.reset(reset)

        try:
            asyncio.run(run())
        finally:
            asyncio.run(http.aclose())
            server.shutdown()
            thread.join()
    assert len(requests) == 1 and requests[0][0] == "/v1/responses"
    assert requests[0][1]["input"] == "offline fixture"
