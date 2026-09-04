"""测试 CLIProxyAPI 按供应商选择管理端点和凭据。"""

from __future__ import annotations

import json
from typing import Final
from uuid import uuid4

import httpx
import pytest

from account_pool.channels.cliproxyapi.client import AuthorizationStart, HttpCLIProxyClient
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.domain import EnvironmentConfiguration, EnvironmentRecord, EnvironmentStatus, OAuthCallback, Provider, ProxyMode, QuotaSnapshot, SupplierKind, utc_now
from account_pool.secrets import EnvironmentSecretDeriver


def _record() -> EnvironmentRecord:
    now: Final = utc_now()
    return EnvironmentRecord(
        id=uuid4(), name="test", provider=Provider.OPENAI, status=EnvironmentStatus.READY,
        enabled=True, manual_cooldown=False, concurrency_limit=2, proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None, available_models=("model-a",), enabled_models=("model-a",),
        auth_file_name=None, auth_index=None, quota=QuotaSnapshot(), cooldown_until=None,
        oauth_state=None, oauth_expires_at=None, last_error=None, created_at=now, updated_at=now,
    )

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected_path",
    (
        (SupplierKind.OPENAI_CODEX, "/v0/management/codex-auth-url"),
        (SupplierKind.ANTHROPIC_CLAUDE, "/v0/management/anthropic-auth-url"),
        (SupplierKind.GOOGLE_ANTIGRAVITY, "/v0/management/antigravity-auth-url"),
        (SupplierKind.KIMI, "/v0/management/kimi-auth-url"),
        (SupplierKind.XAI, "/v0/management/xai-auth-url"),
    ),
)
async def test_start_authorization_uses_exact_supplier_endpoint(
    kind: SupplierKind, expected_path: str
) -> None:
    record: Final = _record()
    supplier: Final = SupplierRegistry.default().get(kind)
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if supplier.authorization_flow.value == "device_code":
            return httpx.Response(200, json={"status": "ok", "state": "state", "user_code": "code", "expires_in": 600}, request=request)
        return httpx.Response(200, json={"status": "ok", "url": "https://example.test/auth", "state": "state"}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    result: Final = await proxy.start_authorization(record, supplier)
    await client.aclose()

    assert paths == [expected_path]
    assert isinstance(result, AuthorizationStart)
    if kind in (SupplierKind.KIMI, SupplierKind.XAI):
        assert result.user_code == "code"
        assert result.expires_in_seconds == 600


@pytest.mark.asyncio
async def test_read_account_selects_matching_type_and_model_file() -> None:
    record: Final = _record()
    supplier: Final = SupplierRegistry.default().get(SupplierKind.ANTHROPIC_CLAUDE)
    model_names: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={"files": [
                    {"name": "wrong.json", "provider": "other", "type": "other"},
                    {"name": "selected.json", "provider": "other", "type": "claude"},
                ]},
                request=request,
            )
        model_names.append(request.url.params["name"])
        return httpx.Response(200, json={"models": [{"id": "claude-model"}]}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier)
    await client.aclose()

    assert observed.auth_file_name == "selected.json"
    assert observed.available_models == ("claude-model",)
    assert model_names == ["selected.json"]

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "provider"),
    (
        (SupplierKind.OPENAI_CODEX, "codex"),
        (SupplierKind.ANTHROPIC_CLAUDE, "anthropic"),
        (SupplierKind.GOOGLE_ANTIGRAVITY, "antigravity"),
    ),
)
async def test_submit_callback_uses_supplier_provider_key(kind: SupplierKind, provider: str) -> None:
    record: Final = _record()
    supplier: Final = SupplierRegistry.default().get(kind)
    payloads: list[object] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    await proxy.submit_callback(record, supplier, OAuthCallback(state="state", code="code"))
    await client.aclose()

    assert payloads == [{"provider": provider, "state": "state", "code": "code", "error": ""}]


@pytest.mark.asyncio
async def test_apply_configuration_uses_supplier_exclusion_and_no_concurrency_endpoint() -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "claude.json"})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.ANTHROPIC_CLAUDE)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    await proxy.apply_configuration(record, supplier, EnvironmentConfiguration(
        name="test", concurrency_limit=2, enabled=True, manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY, enabled_models=("model-a",),
    ))
    await client.aclose()

    assert "/v0/management/concurrency-limit" not in tuple(request.url.path for request in requests)
    exclusion: Final = next(request for request in requests if request.url.path.endswith("oauth-excluded-models"))
    assert json.loads(exclusion.content) == {"claude": []}
