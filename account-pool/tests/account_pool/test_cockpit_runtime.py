"""验证策略运行版本及显式应用边界，不启动服务或调用供应商。"""

from __future__ import annotations

import asyncio
from typing import Final

import httpx
import pytest
from fastapi.testclient import TestClient
from test_cockpit_policy import R1, R2, ManifestFixture, app_for


class RuntimeFixture(ManifestFixture):
    def __init__(self, *, status: int = 200, mode: str = "ok") -> None:
        super().__init__()
        self.status = status
        self.mode = mode
        self.active = R1
        self.revision = R2

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.headers["Authorization"] == "Bearer " + "s" * 32
        assert request.url.path in {"/admin/v1/policy-runtime", "/admin/v1/policy-apply"}
        if request.method == "POST":
            assert request.headers["If-Match"] == R2
            if self.mode == "lost":
                self.active = R2
                raise httpx.ReadTimeout("PRIVATE", request=request)
            if self.status != 200:
                return httpx.Response(self.status, json={"error": {"code": self.mode, "message": "PRIVATE"}})
            self.active = R2
        if self.mode == "malformed":
            return httpx.Response(200, json={"PRIVATE": "SECRET"})
        payload = {
            "persistedRevision": self.revision,
            "activeRevision": R1 if self.mode == "wrong_revision" else self.active,
            "applySupported": True,
            "blockedReason": None,
            "secret": "PRIVATE",
        }
        if self.mode == "wrong_persisted":
            payload["persistedRevision"] = R1
        if self.mode == "contradictory":
            payload["blockedReason"] = "unsupported_policy"
        if self.mode == "coerced_bool":
            payload["applySupported"] = "true"
        if self.mode == "unknown_active":
            payload["activeRevision"] = None
        return httpx.Response(200, json=payload)


def test_runtime_requires_explicit_apply_and_reports_exact_revision() -> None:
    fixture: Final = RuntimeFixture()
    app, http = app_for(fixture)
    with TestClient(app) as client:
        before: Final = client.get("/api/cockpit/policy/runtime")
        applied: Final = client.post("/api/cockpit/policy/apply", json={"revision": R2})
        after: Final = client.get("/api/cockpit/policy/runtime")
    asyncio.run(http.aclose())
    assert before.status_code == applied.status_code == after.status_code == 200
    assert before.json()["active_revision"] == R1
    assert before.json()["persisted_revision"] == R2
    assert applied.json()["active_revision"] == after.json()["active_revision"] == R2
    assert applied.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in applied.text and "secret" not in applied.text
    assert [(r.method, r.url.path) for r in fixture.calls] == [
        ("GET", "/admin/v1/policy-runtime"), ("POST", "/admin/v1/policy-apply"),
        ("GET", "/admin/v1/policy-runtime"),
    ]


@pytest.mark.parametrize("status,mode,expected,detail", [
    (412, "manifest_changed", 409, "revision_conflict"),
    (422, "non_policy_change", 422, "non_policy_change"),
    (422, "unsupported_policy", 422, "unsupported_policy"),
    (422, "PRIVATE", 503, "apply_outcome_unknown"),
    (200, "lost", 503, "apply_outcome_unknown"),
    (200, "malformed", 503, "apply_outcome_unknown"),
    (200, "wrong_revision", 503, "apply_outcome_unknown"),
    (200, "wrong_persisted", 503, "apply_outcome_unknown"),
    (200, "contradictory", 503, "apply_outcome_unknown"),
    (200, "coerced_bool", 503, "apply_outcome_unknown"),
    (200, "unknown_active", 503, "apply_outcome_unknown"),
])
def test_apply_failures_are_safe_and_never_retried(status: int, mode: str, expected: int, detail: str) -> None:
    fixture: Final = RuntimeFixture(status=status, mode=mode)
    app, http = app_for(fixture)
    with TestClient(app) as client:
        response: Final = client.post("/api/cockpit/policy/apply", json={"revision": R2})
    asyncio.run(http.aclose())
    assert response.status_code == expected and response.json()["detail"] == detail
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in response.text and len(fixture.calls) == 1


@pytest.mark.parametrize("body", [{}, {"revision": "bad"}, {"revision": R2, "restart": True}])
def test_apply_rejects_invalid_input_before_any_request(body: dict[str, object]) -> None:
    fixture: Final = RuntimeFixture()
    app, http = app_for(fixture)
    with TestClient(app) as client:
        response: Final = client.post("/api/cockpit/policy/apply", json=body)
    asyncio.run(http.aclose())
    assert response.status_code == 422 and fixture.calls == []


def test_runtime_contract_matches_public_models() -> None:
    from account_pool.cockpit_runtime import CockpitPolicyApply, CockpitPolicyRuntime

    from litellm.proxy.management_endpoints.account_pool_management_models import (
        CockpitPolicyApply as PublicApply,
    )
    from litellm.proxy.management_endpoints.account_pool_management_models import (
        CockpitPolicyRuntime as PublicRuntime,
    )

    assert CockpitPolicyApply.model_json_schema() == PublicApply.model_json_schema()
    assert CockpitPolicyRuntime.model_json_schema() == PublicRuntime.model_json_schema()


@pytest.mark.parametrize("mode", ["malformed", "contradictory", "coerced_bool", "unknown_active"])
def test_unconfirmed_runtime_read_cannot_become_active(mode: str) -> None:
    fixture: Final = RuntimeFixture(mode=mode)
    app, http = app_for(fixture)
    with TestClient(app) as client:
        response: Final = client.get("/api/cockpit/policy/runtime")
    asyncio.run(http.aclose())
    assert response.status_code == 502 and response.json() == {"detail": "invalid_response"}
    assert len(fixture.calls) == 1
