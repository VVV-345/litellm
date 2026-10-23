"""验证版本化 Cockpit 原生策略管理，只使用本地 HTTP fixture，不改凭据或运行态。"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Final

import httpx
import pytest
from account_pool.card_keys import CardKeyService
from account_pool.cockpit_admin import CockpitAdminClient, CockpitAdminConfig
from account_pool.domain import EnvironmentStatus
from account_pool.management_api import create_management_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_account_pool import MemoryRepository, _record
from test_management import MemoryKeys, MemoryLogs, MemoryPolicies

R1: Final = '"sha256-' + "a" * 64 + '"'
R2: Final = '"sha256-' + "b" * 64 + '"'
BODY: Final = {
    "revision": R1,
    "routing_strategy": "custom",
    "custom_rules": [{"account_id": "account-a", "priority": 5, "weight": 2, "is_backup": False, "is_preferred": True}],
}


class ManifestFixture:
    def __init__(self, *, mode: str = "ok") -> None:
        self.revision = R1
        self.mode = mode
        self.calls: list[httpx.Request] = []
        self.data = {
            "routingStrategy": "auto",
            "accounts": [{"id": "account-a", "authId": "private.json"}],
            "apiKeys": [{"id": "private", "enabled": True, "key": "PRIVATE", "accountIds": ["account-a"]}],
            "customRoutingRules": [{"accountId": "account-a", "priority": 1, "weight": 1, "futureRule": 42}],
            "futureRoot": {"secret": "PRIVATE"},
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        import json

        self.calls.append(request)
        assert request.url.path == "/admin/v1/manifest"
        if request.method == "GET":
            return httpx.Response(200, headers={"ETag": self.revision}, json=self.data)
        assert request.method == "PUT" and request.headers["If-Match"] == R1
        if self.mode == "conflict":
            return httpx.Response(412, text="PRIVATE")
        if self.mode == "lost":
            raise httpx.ReadTimeout("PRIVATE", request=request)
        self.data = json.loads(request.content)
        self.revision = R2
        return httpx.Response(202, headers={"ETag": R2}, json={"status": "persisted", "restartRequired": True})


def app_for(fixture: ManifestFixture) -> tuple[FastAPI, httpx.AsyncClient]:
    http: Final = httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle))
    admin: Final = CockpitAdminClient(CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http)
    app: Final = FastAPI()
    # 独立测试管理子路由；外层 Token 鉴权另由集成测试覆盖。
    app.include_router(
        create_management_router(
            CardKeyService(MemoryKeys()),
            MemoryLogs(),
            MemoryRepository(_record(status=EnvironmentStatus.READY)),
            lambda: None,
            MemoryPolicies(),
            cockpit_admin=admin,
        )
    )
    return app, http


def test_policy_read_save_preserves_credentials_and_unknown_fields_without_restart() -> None:
    fixture: Final = ManifestFixture()
    original: Final = copy.deepcopy(fixture.data)
    app, http = app_for(fixture)
    with TestClient(app) as client:
        before: Final = client.get("/api/cockpit/policy")
        saved: Final = client.put("/api/cockpit/policy", json=BODY)
        after: Final = client.get("/api/cockpit/policy")
    asyncio.run(http.aclose())
    assert before.status_code == 200 and before.json()["revision"] == R1
    assert saved.status_code == 200 and saved.json()["revision"] == R2
    assert saved.json()["runtime_activation"] == "restart_required_or_unverified"
    assert after.json()["routing_strategy"] == "custom"
    assert after.json()["custom_rules"][0]["priority"] == 5
    assert after.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in after.text and "private.json" not in after.text
    assert fixture.data["accounts"] == original["accounts"]
    assert fixture.data["apiKeys"] == original["apiKeys"]
    assert fixture.data["futureRoot"] == original["futureRoot"]
    assert fixture.data["customRoutingRules"][0]["futureRule"] == 42
    assert [(r.method, r.url.path) for r in fixture.calls] == [
        ("GET", "/admin/v1/manifest"),
        ("GET", "/admin/v1/manifest"),
        ("PUT", "/admin/v1/manifest"),
        ("GET", "/admin/v1/manifest"),
    ]


@pytest.mark.parametrize(
    "extra",
    [
        {"routing_strategy": "priority"},
        {"fallback_enabled": True},
        {"max_attempts": 4},
        {"session_affinity": True},
        {"account_ids": ["other"]},
        {"custom_rules": [{"account_id": "account-a", "weight": 0}]},
        {"custom_rules": [{"account_id": "account-a", "is_backup": True, "is_preferred": True}]},
    ],
)
def test_unsupported_policy_fields_are_rejected_before_upstream(extra: dict[str, object]) -> None:
    fixture: Final = ManifestFixture()
    app, http = app_for(fixture)
    with TestClient(app) as client:
        response: Final = client.put("/api/cockpit/policy", json={**BODY, **extra})
    asyncio.run(http.aclose())
    assert response.status_code == 422
    assert fixture.calls == []


@pytest.mark.parametrize("rules", [[{"account_id": "other"}], [{"account_id": "account-a"}] * 2])
def test_unknown_or_duplicate_rule_accounts_cannot_be_saved(rules: list[dict[str, str]]) -> None:
    fixture: Final = ManifestFixture()
    app, http = app_for(fixture)
    with TestClient(app) as client:
        response: Final = client.put("/api/cockpit/policy", json={**BODY, "custom_rules": rules})
    asyncio.run(http.aclose())
    assert response.status_code == 422
    assert all(r.method == "GET" for r in fixture.calls)


def test_two_admins_cannot_overwrite_new_revision() -> None:
    fixture: Final = ManifestFixture()
    app, http = app_for(fixture)
    with TestClient(app) as client:
        assert client.put("/api/cockpit/policy", json=BODY).status_code == 200
        stale: Final = client.put("/api/cockpit/policy", json={**BODY, "routing_strategy": "random"})
    asyncio.run(http.aclose())
    assert stale.status_code == 409
    assert fixture.data["routingStrategy"] == "custom"
    assert sum(r.method == "PUT" for r in fixture.calls) == 1


@pytest.mark.parametrize(
    "mode,status,detail", [("conflict", 409, "revision_conflict"), ("lost", 503, "write_outcome_unknown")]
)
def test_conditional_conflict_or_lost_receipt_never_retries_or_rolls_back(mode: str, status: int, detail: str) -> None:
    fixture: Final = ManifestFixture(mode=mode)
    app, http = app_for(fixture)
    with TestClient(app) as client:
        response: Final = client.put("/api/cockpit/policy", json=BODY)
    asyncio.run(http.aclose())
    assert response.status_code == status and response.json()["detail"] == detail
    assert "PRIVATE" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert [r.method for r in fixture.calls] == ["GET", "PUT"]


def test_policy_management_disabled_without_cockpit_client() -> None:
    app: Final = FastAPI()
    app.include_router(
        create_management_router(
            CardKeyService(MemoryKeys()),
            MemoryLogs(),
            MemoryRepository(_record(status=EnvironmentStatus.READY)),
            lambda: None,
            MemoryPolicies(),
        )
    )
    with TestClient(app) as client:
        assert client.get("/api/cockpit/policy").status_code == 503
        assert client.put("/api/cockpit/policy", json=BODY).status_code == 503
        assert client.get("/api/cockpit/policy/runtime").status_code == 503
        assert client.post("/api/cockpit/policy/apply", json={"revision": R1}).status_code == 503


@pytest.mark.parametrize("mode", ["api_key", "unknown", "empty", "future_strategy"])
def test_unsupported_pool_is_readable_but_not_writable(mode: str) -> None:
    fixture: Final = ManifestFixture()
    if mode in {"api_key", "unknown"}:
        fixture.data["accounts"][0]["authKind"] = mode
    elif mode == "empty":
        fixture.data["accounts"] = []
    else:
        fixture.data["routingStrategy"] = "future_strategy"
    app, http = app_for(fixture)
    with TestClient(app) as client:
        read: Final = client.get("/api/cockpit/policy")
        write: Final = client.put("/api/cockpit/policy", json=BODY)
    asyncio.run(http.aclose())
    assert read.status_code == 200 and read.json()["editable"] is False
    assert write.status_code == 422 and all(r.method == "GET" for r in fixture.calls)


def test_policy_contract_matches_proxy_and_preserves_outer_manager_authorization(tmp_path: Path) -> None:
    from account_pool.api import create_router
    from account_pool.cockpit_policy import CockpitPolicyUpdate, CockpitPolicyView
    from test_account_pool import FakeCLIProxy, _service

    from litellm.proxy.management_endpoints.account_pool_management_models import (
        CockpitPolicyUpdate as PublicUpdate,
    )
    from litellm.proxy.management_endpoints.account_pool_management_models import (
        CockpitPolicyView as PublicView,
    )

    assert CockpitPolicyUpdate.model_json_schema() == PublicUpdate.model_json_schema()
    assert CockpitPolicyView.model_json_schema() == PublicView.model_json_schema()
    fixture: Final = ManifestFixture()
    http: Final = httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle))
    record: Final = _record(status=EnvironmentStatus.READY)
    app: Final = FastAPI()
    app.include_router(
        create_router(
            _service(record, FakeCLIProxy(), tmp_path),
            "m" * 32,
            keys=CardKeyService(MemoryKeys()),
            logs=MemoryLogs(),
            environments=MemoryRepository(record),
            policies=MemoryPolicies(),
            cockpit_admin=CockpitAdminClient(
                CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("s" * 32)), http
            ),
        )
    )
    with TestClient(app) as client:
        assert client.put("/api/cockpit/policy", json=BODY).status_code == 401
        assert client.get("/api/cockpit/policy/runtime").status_code == 401
        assert client.post("/api/cockpit/policy/apply", json={"revision": R1}).status_code == 401
        assert fixture.calls == []
        assert (
            client.put("/api/cockpit/policy", json=BODY, headers={"Authorization": "Bearer " + "m" * 32}).status_code
            == 200
        )
    asyncio.run(http.aclose())


def test_invalid_manifest_never_becomes_default_policy() -> None:
    fixture: Final = ManifestFixture()
    fixture.data["customRoutingRules"] = [{"accountId": "account-a", "weight": "bad"}]
    app, http = app_for(fixture)
    with TestClient(app) as client:
        read: Final = client.get("/api/cockpit/policy")
        write: Final = client.put("/api/cockpit/policy", json=BODY)
    asyncio.run(http.aclose())
    assert read.status_code == write.status_code == 502
    assert all(r.method == "GET" for r in fixture.calls)

def test_more_than_256_accounts_is_explicitly_read_only() -> None:
    fixture: Final = ManifestFixture()
    fixture.data["accounts"] = [{"id": f"account-{i}"} for i in range(257)]
    fixture.data["customRoutingRules"] = []
    app, http = app_for(fixture)
    with TestClient(app) as client:
        response: Final = client.get("/api/cockpit/policy")
        write: Final = client.put("/api/cockpit/policy", json={**BODY, "custom_rules": []})
    asyncio.run(http.aclose())
    assert response.status_code == 200 and response.json()["editable"] is False
    assert write.status_code == 422
    assert all(r.method == "GET" for r in fixture.calls)
