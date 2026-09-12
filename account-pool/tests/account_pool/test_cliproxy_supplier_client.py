"""测试 CLIProxyAPI 按供应商选择管理端点和凭据。"""

from __future__ import annotations

import json
from typing import Final
from uuid import uuid4

import httpx
import pytest
import yaml
from account_pool.channels.cliproxyapi.client import AuthorizationStart, HttpCLIProxyClient
from account_pool.channels.cliproxyapi.settings_sync import CLIProxySettingsSynchronizer
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.domain import (
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    OAuthCallback,
    Provider,
    ProxyMode,
    QuotaSnapshot,
    SupplierKind,
    utc_now,
)
from account_pool.policies import AccountPolicy, AntigravityPolicy, ClaudePolicy, CodexPolicy, KimiPolicy, XaiPolicy
from account_pool.secrets import EnvironmentSecretDeriver
from account_pool.settings import (
    AccountPoolSettings,
    OAuthRequestScopedErrorRule,
    PayloadModelRule,
    PayloadRule,
    PayloadSettings,
)


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
    ("name", "auth_file_plan_type", "expected_auth_file_plan_type"),
    (
        ("codex-user-prolite.json", None, "prolite"),
        ("codex-user-pro-max.json", None, "promax"),
        ("codex-user.json", "pro_lite", "prolite"),
    ),
)
async def test_read_codex_account_exposes_subscription_and_auth_file_plan(
    name: str, auth_file_plan_type: str | None, expected_auth_file_plan_type: str
) -> None:
    record: Final = _record()
    supplier: Final = SupplierRegistry.default().get(SupplierKind.OPENAI_CODEX)
    auth_file: Final = {
        "name": name,
        "provider": "codex",
        "id_token": {
            "plan_type": "pro",
            "chatgpt_subscription_active_until": "2090-01-02T03:04:05Z",
        },
        **({"auth_file_plan_type": auth_file_plan_type} if auth_file_plan_type is not None else {}),
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(200, json={"files": [auth_file]}, request=request)
        return httpx.Response(200, json={"models": [{"id": "gpt-5-codex"}]}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier)
    await client.aclose()

    assert observed.quota.plan_type == "pro"
    assert observed.quota.auth_file_plan_type == expected_auth_file_plan_type
    assert observed.quota.subscription_active_until is not None
    assert observed.quota.subscription_active_until.isoformat() == "2090-01-02T03:04:05+00:00"


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
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(200, text="host: 0.0.0.0\nport: 8317\n", request=request)
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "path"),
    (
        (SupplierKind.GEMINI, "/v0/management/gemini-api-key"),
        (SupplierKind.GEMINI_INTERACTIONS, "/v0/management/interactions-api-key"),
    ),
)
async def test_write_direct_api_key_uses_supplier_array_contract(kind: SupplierKind, path: str) -> None:
    record: Final = _record().model_copy(update={"supplier": kind})
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    await proxy.write_direct_api_key(
        record,
        SupplierRegistry.default().get(kind),
        api_key="secret-key",
        prefix="team/",
        priority=4,
        weight=7,
        base_url="https://example.test/v1",
        headers={"x-team": "alpha"},
        proxy_url="http://proxy.test:7890",
    )
    await client.aclose()

    assert requests[0].url.path == path
    assert json.loads(requests[0].content) == [
        {
            "api-key": "secret-key",
            "prefix": "team/",
            "priority": 4,
            "weight": 7,
            "headers": {"x-team": "alpha"},
            "base-url": "https://example.test/v1",
            "proxy-url": "http://proxy.test:7890",
        }
    ]


@pytest.mark.asyncio
async def test_apply_configuration_patches_direct_key_excluded_models() -> None:
    record: Final = _record().model_copy(
        update={
            "supplier": SupplierKind.GEMINI,
            "available_models": ("gemini-2.5-pro", "gemini-2.5-flash"),
            "enabled_models": ("gemini-2.5-pro",),
        }
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    await proxy.apply_configuration(
        record,
        SupplierRegistry.default().get(SupplierKind.GEMINI),
        EnvironmentConfiguration(
            name="test",
            concurrency_limit=2,
            enabled=True,
            manual_cooldown=False,
            proxy_mode=ProxyMode.DEFAULT_GATEWAY,
            enabled_models=("gemini-2.5-pro",),
        ),
    )
    await client.aclose()

    patch: Final = next(request for request in requests if request.method == "PATCH")
    assert patch.url.path == "/v0/management/gemini-api-key"
    assert json.loads(patch.content) == {
        "index": 0,
        "value": {"excluded-models": ["gemini-2.5-flash"]},
    }
    assert not any(request.url.path.endswith("oauth-excluded-models") for request in requests)


@pytest.mark.asyncio
async def test_import_vertex_credential_uses_file_and_location_fields() -> None:
    record: Final = _record().model_copy(update={"supplier": SupplierKind.VERTEX})
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    await proxy.import_vertex_credential(record, "service.json", b'{"project_id":"demo"}', "asia-east1")
    await client.aclose()

    request: Final = requests[0]
    assert request.url.path == "/v0/management/vertex/import"
    assert b'name="location"' in request.content
    assert b"asia-east1" in request.content
    assert b'name="file"; filename="service.json"' in request.content


@pytest.mark.asyncio
async def test_auth_file_management_uses_cockpit_endpoints() -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "codex.json", "auth_index": "1"})
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=b'{"type":"codex"}', headers={"content-type": "application/json"}, request=request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"id": "gpt-5-codex"}]}, request=request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    content: Final = await proxy.download_auth_file(record, "codex.json")
    await proxy.patch_auth_file_status(record, "codex.json", "1", True)
    await proxy.patch_auth_file_fields(record, "codex.json", {"priority": 3})
    await proxy.delete_auth_file(record, "codex.json")
    models: Final = await proxy.get_auth_file_models(record, "codex.json")
    await client.aclose()

    assert content[0] == b'{"type":"codex"}'
    assert models == ("gpt-5-codex",)
    assert tuple(request.url.path for request in requests) == (
        "/v0/management/auth-files/download",
        "/v0/management/auth-files/status",
        "/v0/management/auth-files/fields",
        "/v0/management/auth-files",
        "/v0/management/auth-files/models",
    )
    assert json.loads(requests[1].content) == {"name": "codex.json", "auth_index": "1", "disabled": True}
    assert json.loads(requests[2].content) == {"name": "codex.json", "priority": 3}


@pytest.mark.asyncio
async def test_install_plugin_uses_version_and_source_query() -> None:
    record: Final = _record()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "installed"}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    result: Final = await proxy.install_plugin(record, "example-plugin", "1.2.3", "official")
    await client.aclose()

    assert result == {"status": "installed"}
    assert len(requests) == 1
    assert requests[0].url.path == "/v0/management/plugin-store/example-plugin/install"
    assert dict(requests[0].url.params) == {"version": "1.2.3", "source": "official"}


@pytest.mark.asyncio
async def test_apply_global_settings_syncs_oauth_maps_for_all_suppliers() -> None:
    record: Final = _record()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_global_settings(
        record,
        AccountPoolSettings(
            oauth_excluded_models=("gpt-4", "claude-3"),
            oauth_model_aliases={"codex": (("gpt-5", "gpt-5-codex"),)},
            oauth_request_scoped_errors={
                "codex": (
                    OAuthRequestScopedErrorRule(
                        status=400,
                        match=("context_window_exceeded",),
                        match_regexr=("context.*window",),
                        action="stop",
                    ),
                )
            },
            payload=PayloadSettings(
                override=(
                    PayloadRule(
                        models=(PayloadModelRule(name="gpt-*", protocol="responses"),),
                        params={"stream": True},
                    ),
                )
            ),
        ),
    )
    await client.aclose()

    excluded: Final = next(request for request in requests if request.url.path.endswith("oauth-excluded-models"))
    assert json.loads(excluded.content) == {
        "codex": ["gpt-4", "claude-3"],
        "claude": ["gpt-4", "claude-3"],
        "antigravity": ["gpt-4", "claude-3"],
        "kimi": ["gpt-4", "claude-3"],
        "xai": ["gpt-4", "claude-3"],
    }
    aliases: Final = next(request for request in requests if request.url.path.endswith("oauth-model-alias"))
    assert json.loads(aliases.content) == {"codex": [{"name": "gpt-5", "alias": "gpt-5-codex"}]}
    errors: Final = next(request for request in requests if request.url.path.endswith("oauth-request-scoped-errors"))
    assert json.loads(errors.content) == {
        "codex": [
            {
                "status": 400,
                "match": ["context_window_exceeded"],
                "match-regexr": ["context.*window"],
                "action": "stop",
            }
        ]
    }
    config: Final = next(
        request for request in requests if request.url.path.endswith("/config.yaml") and request.method == "PUT"
    )
    assert yaml.safe_load(config.content)["payload"]["override"] == [
        {
            "models": [
                {
                    "name": "gpt-*",
                    "protocol": "responses",
                    "headers": {},
                    "from-protocol": "",
                    "match": [],
                    "not-match": [],
                    "exist": [],
                    "not-exist": [],
                }
            ],
            "params": {"stream": True},
        }
    ]


@pytest.mark.asyncio
async def test_apply_global_settings_clears_oauth_aliases_when_empty() -> None:
    record: Final = _record()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(200, text="host: 0.0.0.0\npayload:\n  override:\n    - params:\n        old: true\n", request=request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_global_settings(record, AccountPoolSettings())
    await client.aclose()

    aliases: Final = next(request for request in requests if request.url.path.endswith("oauth-model-alias"))
    assert json.loads(aliases.content) == {}
    errors: Final = next(request for request in requests if request.url.path.endswith("oauth-request-scoped-errors"))
    assert json.loads(errors.content) == {}
    config: Final = next(
        request for request in requests if request.url.path.endswith("/config.yaml") and request.method == "PUT"
    )
    assert yaml.safe_load(config.content)["payload"] == {
        "default": [],
        "default-raw": [],
        "override": [],
        "override-raw": [],
        "filter": [],
    }


@pytest.mark.asyncio
async def test_apply_policy_syncs_yaml_settings_without_an_auth_file() -> None:
    record: Final = _record()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(
                200,
                text=(
                    "host: 0.0.0.0\n"
                    "codex:\n  keep: unchanged\n"
                    "xai:\n  api-key:\n    - api-key: hidden\n"
                    "antigravity:\n  project-id: preserved\n"
                ),
                request=request,
            )
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_policy(
        record,
        AccountPolicy(
            codex=CodexPolicy(identity_confuse=True, disable_codex_cloaking=True),
            xai=XaiPolicy(inject_x_search=True),
            antigravity=AntigravityPolicy(
                sensitive_words=(" alpha ", "beta", "alpha"),
                signature_cache_enabled=False,
                signature_bypass_strict=True,
            ),
        ),
    )
    await client.aclose()

    config: Final = next(
        request for request in requests if request.url.path.endswith("/config.yaml") and request.method == "PUT"
    )
    document: Final = yaml.safe_load(config.content)
    assert document["codex"] == {
        "keep": "unchanged",
        "identity-confuse": True,
        "disable-codex-cloaking": True,
    }
    assert document["xai"] == {"api-key": [{"api-key": "hidden"}], "inject-x-search": True}
    assert document["antigravity"] == {"project-id": "preserved", "sensitive-words": ["alpha", "beta"]}
    assert document["antigravity-signature-cache-enabled"] is False
    assert document["antigravity-signature-bypass-strict"] is True
    assert not any(request.url.path.endswith("/auth-files/fields") for request in requests)


@pytest.mark.asyncio
async def test_apply_codex_policy_syncs_auth_file_metadata_and_yaml_settings() -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "codex.json"})
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(200, text="host: 0.0.0.0\ncodex:\n  keep: unchanged\n", request=request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_policy(
        record,
        AccountPolicy(
            codex=CodexPolicy(
                identity_fingerprint_mode="session",
                cli_only=True,
                allow_app_server=True,
                identity_confuse=True,
                disable_codex_cloaking=True,
            )
        ),
    )
    await client.aclose()

    patch_request: Final = next(request for request in requests if request.url.path.endswith("/auth-files/fields"))
    assert json.loads(patch_request.content) == {
        "name": "codex.json",
        "codex_fingerprint_mode": "session",
        "codex_cli_only": True,
        "codex_cli_only_allow_app_server": True,
    }
    config: Final = next(
        request for request in requests if request.url.path.endswith("/config.yaml") and request.method == "PUT"
    )
    assert yaml.safe_load(config.content)["codex"] == {
        "keep": "unchanged",
        "identity-confuse": True,
        "disable-codex-cloaking": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy,expected_fields",
    (
        (
            AccountPolicy(
                claude=ClaudePolicy(
                    fingerprint_profile="claude-code-cli",
                    cloak_mode="always",
                    rebuild_mid_system_message=True,
                )
            ),
            {
                "fingerprint_profile": "claude-code-cli",
                "cloak_mode": "always",
                "rebuild_mid_system_message": True,
            },
        ),
        (AccountPolicy(kimi=KimiPolicy(fingerprint_profile="claude-code-cli")), {"fingerprint_profile": "claude-code-cli"}),
    ),
)
async def test_apply_policy_syncs_only_auth_file_metadata(
    policy: AccountPolicy, expected_fields: dict[str, object]
) -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "provider.json"})
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(200, text="host: 0.0.0.0\n", request=request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_policy(record, policy)
    await client.aclose()

    patch_request: Final = next(request for request in requests if request.url.path.endswith("/auth-files/fields"))
    assert json.loads(patch_request.content) == {"name": "provider.json", **expected_fields}
    assert not any(request.url.path.endswith("/config.yaml") and request.method == "PUT" for request in requests)
