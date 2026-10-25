"""Verify native runtime activation contracts and isolated imports."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from litellm.proxy.management_endpoints.account_pool_cockpit_admin import (
    CockpitAdminClient,
    CockpitAdminConfig,
)

REVISION = '"sha256-' + "a" * 64 + '"'
OTHER = '"sha256-' + "b" * 64 + '"'


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _admin(http):
    return CockpitAdminClient(CockpitAdminConfig(url="http://127.0.0.1:8787", token="x" * 32), http)


def _state(persisted=REVISION, active=REVISION):
    return {
        "persistedRevision": persisted,
        "activeRevision": active,
        "applySupported": True,
        "blockedReason": None,
    }


@pytest.fixture()
def service():
    import litellm.proxy.management_endpoints.account_pool_cockpit_runtime as module

    return module


def test_runtime_module_exists():
    assert importlib.util.find_spec("litellm.proxy.management_endpoints.account_pool_cockpit_runtime") is not None


@pytest.mark.asyncio
async def test_none_client_is_unavailable(service):
    request = service.CockpitPolicyApply(revision=REVISION)
    read = await service.read_cockpit_runtime(None)
    applied = await service.apply_cockpit_policy(None, request)
    for result in (read, applied):
        assert (result.status, result.code) == (503, "unavailable")
        assert type(result) is service.RuntimeFailure
        assert type(result).__module__ == service.__name__


@pytest.mark.asyncio
async def test_read_reports_distinct_versions_without_apply(service):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json=_state(REVISION, OTHER))

    async with _client(handler) as http:
        result = await service.read_cockpit_runtime(_admin(http))
    assert (result.persisted_revision, result.active_revision) == (REVISION, OTHER)
    assert result.apply_supported is True and result.blocked_reason is None
    assert seen == [("GET", "/admin/v1/policy-runtime")]


@pytest.mark.asyncio
async def test_apply_is_explicit_and_checks_if_match(service):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        assert request.headers["If-Match"] == REVISION
        return httpx.Response(200, json=_state(REVISION, REVISION))

    async with _client(handler) as http:
        result = await service.apply_cockpit_policy(_admin(http), service.CockpitPolicyApply(revision=REVISION))
    assert (result.persisted_revision, result.active_revision) == (REVISION, REVISION)
    assert seen == [("POST", "/admin/v1/policy-apply")]


@pytest.mark.parametrize(
    ("status_code", "native_code", "expected"),
    (
        (412, None, (409, "revision_conflict")),
        (422, "non_policy_change", (422, "non_policy_change")),
        (422, "unsupported_policy", (422, "unsupported_policy")),
    ),
)
@pytest.mark.asyncio
async def test_apply_http_failures(service, status_code, native_code, expected):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        body = {} if native_code is None else {"error": {"code": native_code}}
        return httpx.Response(status_code, json=body)

    async with _client(handler) as http:
        result = await service.apply_cockpit_policy(_admin(http), service.CockpitPolicyApply(revision=REVISION))
    assert (result.status, result.code) == expected
    assert type(result) is service.RuntimeFailure
    assert seen == [("POST", "/admin/v1/policy-apply")]


@pytest.mark.asyncio
async def test_apply_unknown_outcomes_never_retry_or_restart(service):
    for mode in ("timeout", "503", "bad_body", "version_mismatch", "persisted_mismatch", "unknown_422"):
        seen = []

        def handler(request, mode=mode):
            seen.append((request.method, request.url.path))
            if mode == "timeout":
                raise httpx.ReadTimeout("timeout", request=request)
            if mode == "503":
                return httpx.Response(503)
            if mode == "persisted_mismatch":
                return httpx.Response(200, json=_state(OTHER, REVISION))
            if mode == "unknown_422":
                return httpx.Response(422, json={"error": {"code": "PRIVATE-UNKNOWN"}})
            if mode == "bad_body":
                return httpx.Response(200, content=b"{")
            return httpx.Response(200, json=_state(REVISION, OTHER))

        async with _client(handler) as http:
            result = await service.apply_cockpit_policy(_admin(http), service.CockpitPolicyApply(revision=REVISION))
        assert (result.status, result.code) == (503, "apply_outcome_unknown"), mode
        assert type(result) is service.RuntimeFailure
        assert seen == [("POST", "/admin/v1/policy-apply")], mode


@pytest.mark.asyncio
async def test_read_malformed_is_invalid_response(service):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, content=b"{")

    async with _client(handler) as http:
        result = await service.read_cockpit_runtime(_admin(http))
    assert (result.status, result.code) == (502, "invalid_response")
    assert type(result) is service.RuntimeFailure
    assert seen == [("GET", "/admin/v1/policy-runtime")]


_ISOLATED_SCRIPT = r"""
import asyncio
import importlib.abc
import sys

sys.path.insert(0, __ROOT__)


class RejectAccountPool(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'account_pool' or fullname.startswith('account_pool.'):
            raise ImportError('account_pool must be isolated')
        return None


sys.meta_path.insert(0, RejectAccountPool())

import httpx
from litellm.proxy.management_endpoints import account_pool_cockpit_admin as admin
from litellm.proxy.management_endpoints import account_pool_cockpit_failure as failure
from litellm.proxy.management_endpoints import account_pool_cockpit_policy as policy
from litellm.proxy.management_endpoints import account_pool_cockpit_runtime as runtime

REV = '"sha256-' + 'a' * 64 + '"'
seen = []


def handler(request):
    seen.append((request.method, request.url.path))
    if request.url.path == '/admin/v1/manifest':
        return httpx.Response(200, headers={'ETag': REV}, json={
            'routingStrategy': 'auto',
            'accounts': [{'id': 'acct-a', 'authKind': 'oauth'}],
            'customRoutingRules': [],
        })
    if request.url.path == '/admin/v1/policy-runtime':
        return httpx.Response(200, json={
            'persistedRevision': REV,
            'activeRevision': REV,
            'applySupported': True,
            'blockedReason': None,
        })
    raise AssertionError(request.url.path)


async def main():
    missing_policy = await policy.read_cockpit_policy(None)
    missing_runtime = await runtime.read_cockpit_runtime(None)
    assert (missing_policy.status, missing_policy.code) == (503, 'unavailable')
    assert (missing_runtime.status, missing_runtime.code) == (503, 'unavailable')
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = admin.CockpitAdminClient(
            admin.CockpitAdminConfig(url='http://127.0.0.1:8787', token='x' * 32),
            http,
        )
        view = await policy.read_cockpit_policy(client)
        state = await runtime.read_cockpit_runtime(client)
        assert view.account_ids == ('acct-a',)
        assert state.persisted_revision == REV
        assert seen == [
            ('GET', '/admin/v1/manifest'),
            ('GET', '/admin/v1/policy-runtime'),
        ]
    finally:
        await http.aclose()


asyncio.run(main())
assert 'account_pool' not in sys.modules
assert failure.CockpitFailure is admin.CockpitFailure
assert policy.CockpitFailure is admin.CockpitFailure
assert runtime.CockpitFailure is admin.CockpitFailure
assert failure.precondition(admin.CockpitFailure('precondition', 412))
assert admin.CockpitFailure.__module__ == admin.__name__
print('ISOLATED-OK')
"""


def test_isolated_process_imports_native_services_only():
    root = Path(__file__).resolve().parents[4]
    script = _ISOLATED_SCRIPT.replace("__ROOT__", repr(str(root)))
    env = dict(os.environ)
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ISOLATED-OK"
