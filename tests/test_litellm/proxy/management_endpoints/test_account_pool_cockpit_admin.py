"""验证 LiteLLM 内独立 Cockpit 管理客户端，不连接真实服务。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

import httpx
import pytest
from pydantic import SecretStr

_ETAG: Final = '"sha256-' + "a" * 64 + '"'
_NEXT: Final = '"sha256-' + "b" * 64 + '"'
_URL: Final = "http://cockpit.test:8317"
_TOKEN: Final = SecretStr("s" * 32)
_MODULE: Final = "litellm.proxy.management_endpoints.account_pool_cockpit_admin"
_MANIFEST: Final = {"accounts": [{"id": "a"}], "apiKeys": [{"enabled": True}], "future": {"nested": [1]}}


@pytest.fixture
def admin() -> ModuleType:
    return importlib.import_module(_MODULE)


def test_new_module_can_be_resolved() -> None:
    assert importlib.util.find_spec(_MODULE) is not None


@pytest.mark.asyncio
async def test_manifest_roundtrip_preserves_unknown_fields_and_sends_if_match(admin: ModuleType) -> None:
    requests: Final[list[httpx.Request]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer " + "s" * 32
        if request.method == "GET":
            return httpx.Response(200, json=_MANIFEST, headers={"ETag": _ETAG})
        assert request.headers["if-match"] == _ETAG
        assert json.loads(request.content) == _MANIFEST
        return httpx.Response(202, json={"status": "persisted", "restartRequired": True}, headers={"ETag": _NEXT})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = admin.CockpitAdminClient(admin.CockpitAdminConfig(url=_URL, token=_TOKEN), http)
        snapshot = await client.read_manifest()
        assert isinstance(snapshot, admin.ManifestSnapshot)
        assert snapshot.account_count == 1 and snapshot.enabled_key_count == 1
        result = await client.write_manifest(snapshot.data, snapshot.revision)
        assert not isinstance(result, admin.CockpitFailure)
        assert result.revision == _NEXT
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/admin/v1/manifest"),
        ("PUT", "/admin/v1/manifest"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,category", [(302, "http"), (412, "precondition"), (503, "http"), (None, "transport")])
async def test_http_and_transport_errors_are_not_retried_or_redirected(
    admin: ModuleType, status: int | None, category: str
) -> None:
    requests: Final[list[httpx.Request]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status is None:
            raise httpx.ConnectError("PRIVATE http://private.test", request=request)
        return httpx.Response(status, text="PRIVATE-TOKEN", headers={"Location": "http://private.test/token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
        client = admin.CockpitAdminClient(admin.CockpitAdminConfig(url=_URL, token=_TOKEN), http)
        result = await client.read_manifest()

    assert isinstance(result, admin.CockpitFailure)
    assert (result.category, result.status_code) == (category, status)
    assert len(requests) == 1
    assert "PRIVATE" not in repr(result) and "private.test" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "etag"), [(b"not json", _ETAG), (b"[]", _ETAG), (b"{}", ""), (b"{}", "W/" + _ETAG)]
)
async def test_invalid_manifest_response_is_rejected(admin: ModuleType, content: bytes, etag: str) -> None:
    response = httpx.Response(200, content=content, headers={"ETag": etag})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as http:
        client = admin.CockpitAdminClient(admin.CockpitAdminConfig(url=_URL, token=_TOKEN), http)
        result = await client.read_manifest()
    assert isinstance(result, admin.CockpitFailure) and result.category == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["persistedRevision", "activeRevision"])
async def test_runtime_apply_rejects_revision_mismatch(admin: ModuleType, field: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/admin/v1/policy-apply"
        assert request.headers["if-match"] == _ETAG
        body: Final = {
            "persistedRevision": _ETAG,
            "activeRevision": _ETAG,
            "applySupported": True,
            "blockedReason": None,
        }
        body[field] = _NEXT
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = admin.CockpitAdminClient(admin.CockpitAdminConfig(url=_URL, token=_TOKEN), http)
        result = await client.apply_policy(_ETAG)
    assert isinstance(result, admin.CockpitFailure) and result.category == "invalid_response"


@pytest.mark.asyncio
async def test_credential_writes_are_conditional_and_restart_is_explicit(admin: ModuleType) -> None:
    requests: Final[list[httpx.Request]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/admin/v1/restart"
            return httpx.Response(202, json={"status": "restart_scheduled"})
        if request.method == "DELETE":
            assert request.headers["if-match"] == _NEXT
            return httpx.Response(202, json={"status": "deleted", "restartRequired": True})
        assert request.content == b'{"type":"codex"}'
        if "if-none-match" in request.headers:
            assert request.headers["if-none-match"] == "*" and "if-match" not in request.headers
        else:
            assert request.headers["if-match"] == _ETAG
        return httpx.Response(202, json={"status": "persisted", "restartRequired": True}, headers={"ETag": _NEXT})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = admin.CockpitAdminClient(admin.CockpitAdminConfig(url=_URL, token=_TOKEN), http)
        created = await client.put_auth_file("credential.json", b'{"type":"codex"}')
        updated = await client.put_auth_file("credential.json", b'{"type":"codex"}', _ETAG)
        deleted = await client.delete_auth_file("credential.json", _NEXT)
        assert all(not isinstance(receipt, admin.CockpitFailure) for receipt in (created, updated, deleted))
        assert all(request.url.path != "/admin/v1/restart" for request in requests)
        restarted = await client.restart()
        assert not isinstance(restarted, admin.CockpitFailure)
    assert [(request.method, request.url.path) for request in requests] == [
        ("PUT", "/admin/v1/auth-files/credential.json"),
        ("PUT", "/admin/v1/auth-files/credential.json"),
        ("DELETE", "/admin/v1/auth-files/credential.json"),
        ("POST", "/admin/v1/restart"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,expected",
    [
        ("non_policy_change", "non_policy_change"),
        ("unsupported_policy", "unsupported_policy"),
        ("PRIVATE", None),
        (None, None),
        (False, None),
        (1, None),
        ([], None),
        ({}, None),
    ],
)
async def test_runtime_error_codes_preserve_contract(admin: ModuleType, code: object, expected: str | None) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(422, json={"error": {"code": code}}))
    ) as http:
        client = admin.CockpitAdminClient(admin.CockpitAdminConfig(url=_URL, token=_TOKEN), http)
        result = await client.apply_policy(_ETAG)
    assert isinstance(result, admin.CockpitFailure)
    assert result.category == ("http" if expected else "invalid_response")
    assert result.status_code == 422 and result.code == expected
    assert "PRIVATE" not in repr(result)


_ISOLATED_SCRIPT: Final = r"""
import asyncio, importlib.abc, sys
sys.path.insert(0, sys.argv[1])
class RejectAccountPool(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] == "account_pool":
            raise ModuleNotFoundError("account_pool is isolated")
sys.meta_path.insert(0, RejectAccountPool())
import httpx
from pydantic import SecretStr
from litellm.proxy.management_endpoints import account_pool_cockpit_admin as module
config = module.CockpitAdminConfig(url="http://cockpit.test:8317", token=SecretStr("s" * 32))
async def verify_client():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(204))) as http:
        module.CockpitAdminClient(config, http)
asyncio.run(verify_client())
print("ACCOUNT_POOL_ISOLATED_IMPORT_OK")
"""


def test_imports_without_legacy_account_pool() -> None:
    root = Path(__file__).resolve().parents[4]
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _ISOLATED_SCRIPT, str(root)], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ACCOUNT_POOL_ISOLATED_IMPORT_OK"
