"""Verify native Cockpit policy services without a live Manager."""

from __future__ import annotations

import importlib.util
import json

import httpx
import pytest

from litellm.proxy.management_endpoints.account_pool_cockpit_admin import (
    CockpitAdminClient,
    CockpitAdminConfig,
)

REVISION = '"sha256-' + "a" * 64 + '"'
NEW_REVISION = '"sha256-' + "b" * 64 + '"'
MANIFEST = {
    "routingStrategy": "auto",
    "accounts": [{"id": "acct-a", "authKind": "oauth"}],
    "customRoutingRules": [{"accountId": "acct-a"}],
}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _admin(http):
    return CockpitAdminClient(CockpitAdminConfig(url="http://127.0.0.1:8787", token="x" * 32), http)


def _rule(service, account_id="acct-a", **values):
    return service.CockpitRoutingRule(account_id=account_id, **values)


def _update(service, rules=(), strategy="auto"):
    return service.CockpitPolicyUpdate(revision=REVISION, routing_strategy=strategy, custom_rules=rules)


@pytest.fixture()
def service():
    import litellm.proxy.management_endpoints.account_pool_cockpit_policy as module

    return module


def test_policy_module_exists():
    assert importlib.util.find_spec("litellm.proxy.management_endpoints.account_pool_cockpit_policy") is not None


@pytest.mark.asyncio
async def test_read_is_safe_and_missing_client_is_503(service):
    seen = []
    payload = {
        **MANIFEST,
        "accounts": [
            {
                "id": "acct-a",
                "authKind": "oauth",
                "authId": "auth-secret",
                "upstreamApiKey": "sk-upstream-secret",
                "privateAccount": "account-secret",
            }
        ],
        "apiKeys": [{"enabled": True, "key": "sk-root-secret"}],
        "privateRoot": "root-secret",
    }

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"ETag": REVISION}, json=payload)

    async with _client(handler) as http:
        view = await service.read_cockpit_policy(_admin(http))
    assert type(view) is service.CockpitPolicyView
    assert (view.revision, view.account_ids) == (REVISION, ("acct-a",))
    assert view.editable is True
    assert view.runtime_activation == "restart_required_or_unverified"
    dumped = view.model_dump_json()
    assert "apiKeys" not in dumped
    for secret in (
        "sk-upstream-secret",
        "auth-secret",
        "account-secret",
        "sk-root-secret",
        "root-secret",
    ):
        assert secret not in dumped
    assert [request.method for request in seen] == ["GET"]
    read_missing = await service.read_cockpit_policy(None)
    save_missing = await service.save_cockpit_policy(None, _update(service))
    for failure in (read_missing, save_missing):
        assert (failure.status, failure.code) == (503, "unavailable")
        assert type(failure) is service.PolicyFailure
        assert type(failure).__module__ == service.__name__


@pytest.mark.asyncio
async def test_save_is_single_cas_and_preserves_unknown_fields(service):
    seen, bodies = [], []
    manifest = {
        **MANIFEST,
        "customRoutingRules": [
            {
                "accountId": "acct-a",
                "priority": 4,
                "weight": 2,
                "isBackup": False,
                "isPreferred": False,
                "futureRule": {"keep": ["rule"]},
            }
        ],
        "futureRoot": {"keep": ["root"]},
        "apiKeys": [{"key": "FIXTURE-KEY", "enabled": True}],
        "accounts": [{"id": "acct-a", "authKind": "oauth", "authId": "FIXTURE-AUTH"}],
    }

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, headers={"ETag": REVISION}, json=manifest)
        bodies.append(json.loads(request.content))
        return httpx.Response(
            202,
            headers={"ETag": NEW_REVISION},
            json={"status": "persisted", "restartRequired": True},
        )

    update = _update(service, (_rule(service, priority=9, weight=3, is_preferred=True),), strategy="custom")
    async with _client(handler) as http:
        result = await service.save_cockpit_policy(_admin(http), update)
    assert [(request.method, request.url.path) for request in seen] == [
        ("GET", "/admin/v1/manifest"),
        ("PUT", "/admin/v1/manifest"),
    ]
    assert seen[1].headers["If-Match"] == REVISION
    body = bodies[0]
    assert body["futureRoot"] == {"keep": ["root"]}
    assert body["routingStrategy"] == "custom"
    assert body["apiKeys"] == manifest["apiKeys"]
    assert body["accounts"] == manifest["accounts"]
    assert manifest["routingStrategy"] == "auto"
    assert body["customRoutingRules"][0]["futureRule"] == {"keep": ["rule"]}
    assert body["customRoutingRules"][0]["priority"] == 9
    assert result.revision == NEW_REVISION
    assert result.runtime_activation == "restart_required_or_unverified"
    assert result.editable is True and result.blocked_reason is None


@pytest.mark.asyncio
async def test_stale_revision_conflicts_without_write(service):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"ETag": NEW_REVISION}, json=MANIFEST)

    async with _client(handler) as http:
        result = await service.save_cockpit_policy(_admin(http), _update(service))
    assert (result.status, result.code) == (409, "revision_conflict")
    assert type(result) is service.PolicyFailure
    assert [(request.method, request.url.path) for request in seen] == [("GET", "/admin/v1/manifest")]


@pytest.mark.asyncio
async def test_invalid_policy_is_rejected_without_write(service):
    cases = (
        ("unknown", "auto", "oauth", (_rule(service, "acct-x"),), "invalid_rule_accounts"),
        ("duplicate", "auto", "oauth", (_rule(service), _rule(service)), "invalid_rule_accounts"),
        ("credentials", "auto", "api_key", (_rule(service),), "unsupported_account_or_rule"),
        ("strategy", "legacy", "oauth", (_rule(service),), "unsupported_strategy"),
    )
    for label, strategy, auth_kind, rules, code in cases:
        writes = []

        def handler(request, strategy=strategy, auth_kind=auth_kind):
            if request.method == "PUT":
                writes.append(request)
            return httpx.Response(
                200,
                headers={"ETag": REVISION},
                json={
                    **MANIFEST,
                    "routingStrategy": strategy,
                    "accounts": [{"id": "acct-a", "authKind": auth_kind}],
                },
            )

        update = service.CockpitPolicyUpdate(revision=REVISION, routing_strategy="auto", custom_rules=rules)
        async with _client(handler) as http:
            result = await service.save_cockpit_policy(_admin(http), update)
        assert (result.status, result.code) == (422, code), label
        assert writes == [], label


@pytest.mark.asyncio
async def test_native_failures_map_to_policy_failure(service):
    cases = (
        ("get_timeout", (503, "unavailable"), True),
        ("get_503", (503, "unavailable"), True),
        ("get_bad", (502, "unavailable"), True),
        ("put_timeout", (503, "write_outcome_unknown"), False),
        ("put_412", (409, "revision_conflict"), False),
        ("put_503", (503, "write_outcome_unknown"), False),
        ("put_bad", (503, "write_outcome_unknown"), False),
        ("put_200", (502, "write_rejected"), False),
    )
    for mode, expected, is_read in cases:
        seen = []

        def handler(request, mode=mode):
            seen.append((request.method, request.url.path))
            if mode == "get_timeout" and request.method == "GET":
                raise httpx.ReadTimeout("timeout", request=request)
            if mode == "put_timeout" and request.method == "PUT":
                raise httpx.ReadTimeout("timeout", request=request)
            if request.method == "GET":
                if mode == "get_503":
                    return httpx.Response(503)
                if mode == "get_bad":
                    return httpx.Response(200, headers={"ETag": REVISION}, content=b"{")
                return httpx.Response(200, headers={"ETag": REVISION}, json=MANIFEST)
            if mode == "put_412":
                return httpx.Response(412)
            if mode == "put_503":
                return httpx.Response(503)
            if mode == "put_bad":
                return httpx.Response(202, content=b"{")
            if mode == "put_200":
                return httpx.Response(200)
            raise AssertionError(mode)

        async with _client(handler) as http:
            client = _admin(http)
            if is_read:
                result = await service.read_cockpit_policy(client)
            else:
                result = await service.save_cockpit_policy(client, _update(service))
        assert type(result) is service.PolicyFailure, mode
        assert (result.status, result.code) == expected, mode
        assert type(result).__module__ == service.__name__, mode
        methods = ["GET"] if is_read else ["GET", "PUT"]
        assert [method for method, _ in seen] == methods, mode
