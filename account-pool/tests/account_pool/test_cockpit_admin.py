"""验证 Cockpit 服务端管理契约，不连接真实供应商或执行切流。"""

from __future__ import annotations

import json
from typing import Final

import httpx
import pytest
from account_pool.cockpit_admin import CockpitAdminClient, CockpitAdminConfig, CockpitFailure, ManifestSnapshot
from pydantic import SecretStr

_ETAG: Final = '"sha256-' + "a" * 64 + '"'
_NEXT: Final = '"sha256-' + "b" * 64 + '"'
_CONFIG: Final = {"url": "http://cockpit.test:8317", "token": SecretStr("s" * 32)}
_MANIFEST: Final = {
    "accounts": [{"id": "private-id", "email": "private@example.test"}],
    "apiKeys": [{"key": "private-key", "enabled": True}, {"enabled": False}],
    "future": {"nested": [1, None, "preserve"]},
}


@pytest.mark.asyncio
async def test_manifest_roundtrip_preserves_unknown_fields_and_uses_conditional_write() -> None:
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
        client: Final = CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http)
        snapshot: Final = await client.read_manifest()
        assert isinstance(snapshot, ManifestSnapshot)
        assert snapshot.account_count == 1
        assert snapshot.enabled_key_count == 1
        assert snapshot.revision == _ETAG
        result: Final = await client.write_manifest(snapshot.data, snapshot.revision)
        assert not isinstance(result, CockpitFailure)
        assert result.revision == _NEXT
    assert [(r.method, r.url.path) for r in requests] == [("GET", "/admin/v1/manifest"), ("PUT", "/admin/v1/manifest")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,category", [(302, "http"), (412, "precondition"), (428, "precondition"), (503, "http")]
)
async def test_errors_are_sanitized_and_never_retried_or_redirected(status: int, category: str) -> None:
    requests: Final[list[httpx.Request]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, text="PRIVATE-TOKEN", headers={"Location": "http://private.test/token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
        result: Final = await CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http).read_manifest()
    assert isinstance(result, CockpitFailure)
    assert result.category == category and result.status_code == status
    assert len(requests) == 1
    assert "PRIVATE" not in repr(result) and "private.test" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,etag",
    [
        (b"not json", _ETAG),
        (b"[]", _ETAG),
        (b"{}", ""),
        (b"{}", "W/" + _ETAG),
        (b'{"accounts":"secret"}', _ETAG),
        (b'{"apiKeys":[{"enabled":"true"}]}', _ETAG),
    ],
)
async def test_invalid_manifest_success_is_not_reported_as_healthy(content: bytes, etag: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content, headers={"ETag": etag}))
    ) as http:
        result: Final = await CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http).read_manifest()
    assert isinstance(result, CockpitFailure) and result.category == "invalid_response"


@pytest.mark.asyncio
async def test_transport_error_does_not_expose_private_url() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private-token-private-url", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result: Final = await CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http).read_manifest()
    assert isinstance(result, CockpitFailure) and result.category == "transport"
    assert "private" not in repr(result)


@pytest.mark.asyncio
async def test_credentials_have_conditional_mutations_and_restart_is_explicit() -> None:
    requests: Final[list[httpx.Request]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"files": [{"id": "credential.json", "etag": _ETAG, "size": 16}]})
        if request.method == "POST":
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
        client: Final = CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http)
        files: Final = await client.list_auth_files()
        assert not isinstance(files, CockpitFailure) and files[0].id == "credential.json"
        assert not isinstance(await client.put_auth_file("credential.json", b'{"type":"codex"}'), CockpitFailure)
        assert not isinstance(await client.put_auth_file("credential.json", b'{"type":"codex"}', _ETAG), CockpitFailure)
        assert not isinstance(await client.delete_auth_file("credential.json", _NEXT), CockpitFailure)
        assert all(r.url.path != "/admin/v1/restart" for r in requests)
        assert not isinstance(await client.restart(), CockpitFailure)
    assert len(requests) == 5 and requests[-1].url.path == "/admin/v1/restart"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../a.json", "..\\a.json", ".a.json", "a.json?x", "a.json#x", "a.json\n", "a.txt"])
async def test_invalid_credential_names_never_reach_transport(name: str) -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid name reached transport")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client: Final = CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http)
        for result in (await client.put_auth_file(name, b"{}"), await client.delete_auth_file(name, _ETAG)):
            assert isinstance(result, CockpitFailure) and result.category == "invalid_input"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"status": "persisted"},
        {"status": "persisted", "restartRequired": False},
        {"status": "persisted", "restartRequired": 1},
        {"status": "active", "restartRequired": True},
    ],
)
async def test_mutation_requires_literal_persisted_and_restart_required(body: dict[str, object]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(202, json=body, headers={"ETag": _NEXT}))
    ) as http:
        result: Final = await CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http).write_manifest({}, _ETAG)
    assert isinstance(result, CockpitFailure) and result.category == "invalid_response"


@pytest.mark.asyncio
async def test_real_world_auth_file_names_are_encoded_without_changing_identity() -> None:
    name: Final = "codex-user@example.test-plus.json"

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/admin/v1/auth-files/" + name
        return httpx.Response(202, headers={"ETag": _NEXT}, json={"status": "persisted", "restartRequired": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result: Final = await CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http).put_auth_file(name, b"{}")
    assert not isinstance(result, CockpitFailure)


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", ["*", "", "bad", "W/" + _ETAG, _ETAG + ", " + _NEXT])
async def test_invalid_revisions_are_rejected_before_network(revision: str) -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid revision reached transport")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client: Final = CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http)
        for result in (
            await client.write_manifest({}, revision),
            await client.put_auth_file("auth.json", b"{}", revision),
            await client.delete_auth_file("auth.json", revision),
        ):
            assert isinstance(result, CockpitFailure) and result.category == "invalid_input"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"files": None},
        {"files": [{"id": "../x.json", "etag": _ETAG, "size": 1}]},
        {"files": [{"id": "x.json", "etag": "bad", "size": 1}]},
        {"files": [{"id": "x.json", "etag": _ETAG, "size": -1}]},
    ],
)
async def test_invalid_credential_metadata_is_rejected(payload: dict[str, object]) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http:
        result: Final = await CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http).list_auth_files()
    assert isinstance(result, CockpitFailure) and result.category == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["账号.json", "codex#user%.json"])
async def test_supported_cockpit_names_remain_usable_without_url_injection(name: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/admin/v1/auth-files/" + name
        assert request.url.query == b"" and request.url.fragment == ""
        return httpx.Response(202, headers={"ETag": _NEXT}, json={"status": "persisted", "restartRequired": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result: Final = await CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http).put_auth_file(name, b"{}")
    assert not isinstance(result, CockpitFailure)


@pytest.mark.parametrize("url", ["http://cockpit.test:SECRET-PORT", "http://[SECRET-HOST]"])
def test_invalid_origin_parser_errors_do_not_echo_private_values(url: str) -> None:
    with pytest.raises(ValueError) as error:
        CockpitAdminConfig(url=url, token=SecretStr("s" * 32))
    assert "SECRET" not in str(error.value)


@pytest.mark.asyncio
async def test_cockpit_mutation_preconditions_do_not_trigger_retry_or_restart() -> None:
    requests: Final[list[httpx.Request]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(412, text="PRIVATE-CONFLICT")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client: Final = CockpitAdminClient(CockpitAdminConfig(**_CONFIG), http)
        for result in (
            await client.write_manifest({}, _ETAG),
            await client.put_auth_file("auth.json", b"{}", _ETAG),
            await client.delete_auth_file("auth.json", _ETAG),
        ):
            assert isinstance(result, CockpitFailure) and result.category == "precondition"
            assert result.status_code == 412
    assert len(requests) == 3 and all(r.url.path != "/admin/v1/restart" for r in requests)
