"""本文件验证 OpenAI 兼容渠道的地址安全、模型前缀和密钥隔离。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Final
from uuid import uuid4

import httpx
import pytest
from account_pool.api import create_router
from account_pool.channels.openai_compatible import OpenAICompatibleChannel
from account_pool.channels.registry import ChannelRegistry
from account_pool.domain import (
    ChannelKind,
    CreateDirectCredentialEnvironmentRequest,
    DirectAPIKeyCredentialRequest,
    EnvironmentRecord,
    EnvironmentStatus,
    OpenAICompatibleConfiguration,
    OpenAICompatibleCreateRequest,
    OpenAICompatibleCredential,
    Provider,
    ProxyMode,
    QuotaSnapshot,
    SupplierKind,
)
from account_pool.secrets import EnvironmentSecretDeriver, StateCipher
from account_pool.service import EnvironmentService
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue, ValidationError
from test_account_pool import EmptyProfiles, FakeCLIProxy, FakeRuntime, MemoryRepository, _record, _settings


async def public_resolver(_: str, __: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


async def private_resolver(_: str, __: int) -> tuple[str, ...]:
    return ("10.0.0.8",)


@pytest.mark.parametrize("failure", ("http", "empty", "format"))
@pytest.mark.parametrize(("base", "endpoint"), (
    ("https://api.z.ai/api/coding/paas/v4", "https://api.z.ai/api/coding/paas/v4/models"),
    ("https://open.bigmodel.cn/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4/models"),
    ("https://opencode.ai/zen/go/v1", "https://opencode.ai/inference/go/v1/models"),
    ("https://api.example.com/v1", "https://api.example.com/v1/models"),
))
def test_bound_compatible_model_catalog_preserves_card_and_last_catalog(
    tmp_path: Path, base: str, endpoint: str, failure: str,
) -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    original: Final = _compatible_card(base, secrets)
    assert original.openai_compatible is not None
    record: Final = original.model_copy(update={
        "auth_file_name": "encrypted-api-keys",
        "openai_compatible": original.openai_compatible.model_copy(update={
            "prefix": "vendor/", "custom_models": ("model-a", "manual-model"),
        }),
        "available_models": ("vendor/model-a", "vendor/retired"),
        "enabled_models": ("vendor/model-a",),
    })
    repository: Final = MemoryRepository(record)
    calls: Final[list[str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.method == "GET" and str(request.url) == endpoint
        assert request.headers["Authorization"] == "Bearer synthetic-glm-key"
        if len(calls) > 1:
            if failure == "empty":
                return httpx.Response(200, json={"data": []})
            return httpx.Response(503 if failure == "http" else 200, json={"error": "private-upstream-body"})
        return httpx.Response(200, json={"data": [{"id": " model-a "}, {"id": "model-new"}, {"id": "model-new"}]})

    channel: Final = OpenAICompatibleChannel(
        secrets, httpx.AsyncClient(transport=httpx.MockTransport(upstream)), public_resolver,
    )
    registry: Final = replace(
        ChannelRegistry.default(), implementations=MappingProxyType({ChannelKind.OPENAI_COMPATIBLE: channel}),
    )
    service: Final = EnvironmentService(
        _settings(tmp_path), repository, FakeRuntime(), FakeCLIProxy(), EmptyProfiles(), secrets, channels=registry,
    )
    app: Final = FastAPI()
    app.include_router(create_router(service, "fixture-manager", environments=repository))
    with TestClient(app) as client:
        path: Final = f"/api/environments/{record.id}/model-catalog"
        assert client.post(path).status_code in (401, 403)
        assert not calls
        response: Final = client.post(path, headers={"Authorization": "Bearer fixture-manager"})
        assert response.status_code == 200
        assert response.json() == ["vendor/model-a", "vendor/model-new"]
        expected: Final = record.model_copy(update={"available_models": ("vendor/model-a", "vendor/model-new")})
        assert repository.records[record.id] == expected
        failed: Final = client.post(path, headers={"Authorization": "Bearer fixture-manager"})
        assert failed.status_code == 502
        assert "synthetic-glm-key" not in failed.text and "private-upstream-body" not in failed.text
        assert repository.records[record.id] == expected
        assert calls == [endpoint, endpoint]
        selected: Final = client.put(
            f"/api/environments/{record.id}", headers={"Authorization": "Bearer fixture-manager"}, json={
                "version": record.version, "name": record.name, "enabled": True, "manual_cooldown": False,
                "concurrency_limit": record.concurrency_limit, "proxy_mode": "default_gateway",
                "enabled_models": ["vendor/model-new"],
            },
        )
        assert selected.status_code == 200
        assert selected.json()["enabled_models"] == ["vendor/model-new"]
        assert repository.records[record.id].openai_compatible == record.openai_compatible


def _compatible_card(base_url: str, secrets: EnvironmentSecretDeriver) -> EnvironmentRecord:
    record: Final = _record(status=EnvironmentStatus.READY)
    return record.model_copy(update={
        "channel": ChannelKind.OPENAI_COMPATIBLE,
        "supplier": SupplierKind.OPENAI_COMPATIBLE,
        "openai_compatible": OpenAICompatibleConfiguration(
            base_url=base_url,
            test_model="model-a",
            credentials=(OpenAICompatibleCredential(
                api_key_ciphertext=StateCipher(secrets).seal(record.id, "synthetic-glm-key"),
            ),),
        ),
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", ((), ("vendor/model-new",)))
async def test_compatible_refresh_keeps_discovered_models_and_explicit_empty_selection(
    enabled: tuple[str, ...],
) -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    original: Final = _compatible_card("https://api.example.com/v1", secrets)
    assert original.openai_compatible is not None
    record: Final = original.model_copy(update={
        "auth_file_name": "encrypted-api-keys",
        "openai_compatible": original.openai_compatible.model_copy(update={
            "prefix": "vendor/", "custom_models": ("manual-model",),
        }),
        "available_models": ("vendor/model-a", "vendor/model-new"), "enabled_models": enabled,
    })
    calls: Final[list[str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-new"}]})
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        channel: Final = OpenAICompatibleChannel(secrets, http, public_resolver)
        refreshed: Final = await channel.read_account(record)
        assert refreshed.available_models == ("vendor/manual-model", "vendor/model-a", "vendor/model-new")
        assert refreshed.enabled_models == enabled
        failed: Final = await channel.read_account(refreshed)
        assert failed.available_models == refreshed.available_models and failed.enabled_models == enabled


@pytest.mark.parametrize("issuer", ["open.bigmodel.cn", "api.z.ai"])
@pytest.mark.parametrize("path", ["/api/paas/v4/", "/api/coding/paas/v4/"])
def test_glm_coding_plan_quota_is_read_only_in_manager(tmp_path: Path, issuer: str, path: str) -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    record: Final = _compatible_card(f"https://{issuer}{path}", secrets)
    repository: Final = MemoryRepository(record)
    requests: Final[list[tuple[str, str, str]]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url), request.headers["Authorization"]))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "model-a"}]})
        if len(requests) > 1:
            return httpx.Response(401)
        return httpx.Response(200, json={
            "code": 200, "success": True, "data": {"level": "pro", "limits": [
                {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "percentage": 100, "nextResetTime": 2_000_000_000_000},
                {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 25},
                {"type": "TIME_LIMIT", "unit": 5, "number": 1, "percentage": 10, "nextResetTime": 2_000_000_060_000},
            ]},
        })

    channel: Final = OpenAICompatibleChannel(
        secrets, httpx.AsyncClient(transport=httpx.MockTransport(upstream)), public_resolver,
    )
    registry: Final = replace(
        ChannelRegistry.default(), implementations=MappingProxyType({ChannelKind.OPENAI_COMPATIBLE: channel}),
    )
    service: Final = EnvironmentService(
        _settings(tmp_path), repository, FakeRuntime(), FakeCLIProxy(), EmptyProfiles(), secrets, channels=registry,
    )
    app: Final = FastAPI()
    app.include_router(create_router(service, "fixture-manager", environments=repository))
    with TestClient(app, headers={"Authorization": "Bearer fixture-manager"}) as client:
        response: Final = client.post(f"/api/environments/{record.id}/refresh")
        assert response.status_code == 200
        assert requests == [("GET", f"https://{issuer}/api/monitor/usage/quota/limit", "synthetic-glm-key")]
        value: Final = response.json()
        assert "synthetic-glm-key" not in response.text
        assert value["status"] == "ready" and value["quota"]["windows"] == []
        shown: Final = {window["window_minutes"]: window for window in value["display_quota"]["windows"]}
        assert shown[10080]["remaining_percent"] == 0 and shown[10080]["resets_at"] == "2033-05-18T03:33:20Z"
        assert shown[300]["remaining_percent"] == 75 and shown[300]["resets_at"] is None
        assert shown[43200]["name"] == "MCP Monthly" and shown[43200]["unit"] == "tasks"
        assert shown[43200]["remaining_percent"] == 90 and shown[43200]["resets_at"] == "2033-05-18T03:34:20Z"
        listed: Final = client.get("/api/environments").json()
        assert listed[0]["display_quota"] == value["display_quota"]
        bulk: Final = client.post("/api/quotas/refresh").json()
        assert bulk["failed_card_ids"] == [str(record.id)]
        assert bulk["refreshed"][0]["status"] == "ready"
        assert bulk["refreshed"][0]["display_quota"]["windows"] == value["display_quota"]["windows"]
        assert bulk["refreshed"][0]["display_quota"]["refresh_status"] == "failed"
    assert repository.records[record.id].quota == record.quota


@pytest.mark.asyncio
@pytest.mark.parametrize("base", [
    "https://opencode.ai/inference/go/openai/v1/",
    "https://opencode.ai/inference/go/v1",
    "https://opencode.ai/zen/go/v1",
    "https://opencode.ai/inference/openai/v1/",
    "https://opencode.ai/zen/v1",
])
async def test_opencode_go_reads_three_windows_without_altering_routing(base: str) -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    record: Final = _compatible_card(base, secrets)
    requests: Final[list[str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer synthetic-glm-key"
        return httpx.Response(200, json={"usage": {
            "rolling": {"status": "ok", "percent": 19.5, "resetsAt": "2090-01-01T17:00:00Z"},
            "weekly": {"status": "rate-limited", "percent": 100, "resetsAt": "2090-01-02T00:00:00Z"},
            "monthly": {"status": "ok", "percent": 24, "resetsAt": "2090-01-31T12:30:00Z"},
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        channel: Final = OpenAICompatibleChannel(secrets, http, public_resolver)
        observed: Final = await channel.read_account(record, refresh_quota=True)
        assert channel.gateway(observed) == channel.gateway(record)
    assert requests == ["https://opencode.ai/zen/go/v1/usage"]
    quota: Final = observed.display_quota
    assert quota is not None and quota.refresh_status == "complete" and quota.plan_type == "OpenCode Go"
    assert [(w.name, w.remaining_percent, w.resets_at) for w in quota.windows] == [
        ("5 hour", 80.5, datetime(2090, 1, 1, 17, tzinfo=timezone.utc)),
        ("Weekly", 0, datetime(2090, 1, 2, tzinfo=timezone.utc)),
        ("Monthly", 76, datetime(2090, 1, 31, 12, 30, tzinfo=timezone.utc)),
    ]
    assert not quota.balances and quota.prepaid_balance is None
    assert observed.model_copy(update={"display_quota": None}) == record


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "payload", "expected_status", "window_names"), [
    (403, {"error": {"message": "fixture-upstream-private"}}, "failed", ()),
    (302, {}, "failed", ()),
    (200, {"usage": {}}, "failed", ()),
    (200, {"usage": {"weekly": {"percent": 35}, "monthly": {"percent": "NaN"}}}, "partial", ("Weekly",)),
])
async def test_opencode_go_does_not_fill_missing_windows_or_follow_redirects(
    status: int, payload: dict[str, JsonValue], expected_status: str, window_names: tuple[str, ...],
) -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    record: Final = _compatible_card("https://opencode.ai/inference/go/openai/v1", secrets)
    requests: Final[list[str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(status, headers={"Location": "https://never-follow.example/"}, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), follow_redirects=True) as http:
        observed: Final = await OpenAICompatibleChannel(secrets, http, public_resolver).read_account(record, refresh_quota=True)
    assert requests == ["https://opencode.ai/zen/go/v1/usage"]
    quota: Final = observed.display_quota
    assert quota is not None and quota.refresh_status == expected_status
    assert tuple(w.name for w in quota.windows) == window_names
    assert all(w.resets_at is None for w in quota.windows)
    assert "fixture-upstream-private" not in (quota.refresh_error or "")
    assert observed.model_copy(update={"display_quota": None}) == record


@pytest.mark.asyncio
async def test_glm_reports_no_recognized_coding_plan_without_treating_it_as_zero() -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    record: Final = _compatible_card("https://api.z.ai/api/coding/paas/v4", secrets)

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 500, "success": False, "msg": "当前用户不存在coding plan"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        observed: Final = await OpenAICompatibleChannel(secrets, http, public_resolver).read_account(record, refresh_quota=True)
    quota: Final = observed.display_quota
    assert quota is not None and quota.refresh_status == "unsupported" and not quota.windows
    assert "个人套餐" in (quota.refresh_error or "")
    assert observed.model_copy(update={"display_quota": None}) == record


@pytest.mark.asyncio
@pytest.mark.parametrize(("status_code", "resolver", "expected_requests"), [
    (401, public_resolver, 1),
    (302, public_resolver, 1),
    (200, public_resolver, 1),
    (200, private_resolver, 0),
])
async def test_glm_quota_failures_are_unknown_and_do_not_destabilize_the_card(
    status_code, resolver, expected_requests,
) -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    record: Final = _compatible_card("https://api.z.ai/api/coding/paas/v4", secrets)
    requests: Final[list[str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(status_code, headers={"Location": "https://never-follow.example/"}, json={
            "code": 401, "success": False, "msg": "fixture-body-secret",
            "data": {"limits": [{"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 0}]},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), follow_redirects=True) as http:
        observed: Final = await OpenAICompatibleChannel(secrets, http, resolver).read_account(record, refresh_quota=True)
    assert requests == ["https://api.z.ai/api/monitor/usage/quota/limit"] * expected_requests
    quota: Final = observed.display_quota
    assert quota is not None and quota.refresh_status == "failed"
    assert quota.observed_at is None and not quota.windows
    assert quota.refresh_error is not None and "fixture-body-secret" not in quota.refresh_error
    assert observed.model_copy(update={"display_quota": None}) == record


@pytest.mark.asyncio
@pytest.mark.parametrize(("base_url", "endpoint", "authorization", "status", "payload", "expected_status"), [
    (
        "https://open.bigmodel.cn/api/paas/v4",
        "https://open.bigmodel.cn/api/monitor/usage/quota/limit", "synthetic-glm-key", 200,
        {"code": 500, "success": False, "msg": "当前用户不存在coding plan"}, "unsupported",
    ),
    (
        "https://api.z.ai/api/paas/v4",
        "https://api.z.ai/api/monitor/usage/quota/limit", "synthetic-glm-key", 200,
        {"code": 500, "success": False, "msg": "当前用户不存在coding plan"}, "unsupported",
    ),
    (
        "https://opencode.ai/inference/openai/v1",
        "https://opencode.ai/zen/go/v1/usage", "Bearer synthetic-glm-key", 403, {"usage": None}, "failed",
    ),
    (
        "https://opencode.ai/zen/v1",
        "https://opencode.ai/zen/go/v1/usage", "Bearer synthetic-glm-key", 403, {"usage": None}, "failed",
    ),
])
async def test_official_cards_without_plan_quota_stay_unknown_and_routable(
    base_url: str, endpoint: str, authorization: str, status: int, payload: dict[str, JsonValue], expected_status: str,
) -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    record: Final = _compatible_card(base_url, secrets)
    requests: Final[list[str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        assert request.method == "GET" and request.headers["Authorization"] == authorization
        return httpx.Response(status, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        channel: Final = OpenAICompatibleChannel(secrets, http, public_resolver)
        observed: Final = await channel.read_account(record, refresh_quota=True)
        assert channel.gateway(observed) == channel.gateway(record)
    assert requests == [endpoint]
    assert observed.display_quota is not None and observed.display_quota.refresh_status == expected_status
    assert not observed.display_quota.windows and not observed.display_quota.balances
    assert observed.display_quota.plan_type is None
    assert observed.display_quota.refresh_attempted_at is not None
    if status == 403:
        assert "HTTP 403" in (observed.display_quota.refresh_error or "")
    assert observed.model_copy(update={"display_quota": None}) == record


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", [
    "https://api.z.ai.example.com/api/coding/paas/v4",
    "https://opencode.ai.example.com/inference/go/openai/v1",
])
async def test_glm_like_model_on_a_different_issuer_does_not_cause_a_glm_quota_query(base_url: str) -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    record: Final = _compatible_card(base_url, secrets)

    def upstream(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{base_url}/models"
        return httpx.Response(200, json={"data": [{"id": "glm-model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        observed: Final = await OpenAICompatibleChannel(secrets, http, public_resolver).read_account(
            record, refresh_quota=True,
        )
    assert observed.display_quota is None and observed.available_models == ("glm-model",)


@pytest.mark.parametrize(
    ("base", "catalog_path"),
    (
        ("https://custom.example/v1", "/v1/models"),
        ("https://open.bigmodel.cn/api/paas/v4", "/api/paas/v4/models"),
        ("https://open.bigmodel.cn/api/coding/paas/v4", "/api/coding/paas/v4/models"),
        ("https://api.z.ai/api/coding/paas/v4", "/api/coding/paas/v4/models"),
        ("https://opencode.ai/inference/openai/v1", "/inference/openai/v1/models"),
        ("https://opencode.ai/inference/go/openai/v1", "/inference/go/v1/models"),
        ("https://opencode.ai/zen/go/v1", "/inference/go/v1/models"),
        ("https://opencode.ai.example.com/zen/go/v1", "/zen/go/v1/models"),
    ),
)
def test_manager_model_preview_and_provider_presets_use_existing_configuration(
    tmp_path: Path, base: str, catalog_path: str,
) -> None:
    console: Final = "https://opencode.ai/inference/openai/v1"
    go: Final = "https://opencode.ai/inference/go/openai/v1"
    secret: Final = EnvironmentSecretDeriver("s" * 32)
    settings: Final = _settings(tmp_path)
    records: Final = tuple(
        _record(status=EnvironmentStatus.READY).model_copy(update={
            "channel": ChannelKind.OPENAI_COMPATIBLE,
            "supplier": SupplierKind.OPENAI_COMPATIBLE,
            "openai_compatible": OpenAICompatibleConfiguration(
                base_url=url, test_model="fixture-model",
                credentials=(OpenAICompatibleCredential(api_key_ciphertext="saved-fixture"),),
            ),
        })
        for url in (
            "https://open.bigmodel.cn/api/paas/v4",
            "https://api.z.ai/api/paas/v4/",
            *((base,) if "/api/coding/paas/v4" in base else ()),
            console,
            f"{go}/",
            "https://custom.example/v1",
        )
    )
    repository: Final = MemoryRepository(records[0])
    repository.records.update({record.id: record for record in records[1:]})
    requests: Final[list[httpx.Request]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "fixture-model"}]})

    channel: Final = OpenAICompatibleChannel(
        secret, httpx.AsyncClient(transport=httpx.MockTransport(upstream)), public_resolver,
    )
    registry: Final = replace(
        ChannelRegistry.default(), implementations=MappingProxyType({ChannelKind.OPENAI_COMPATIBLE: channel}),
    )
    service: Final = EnvironmentService(
        settings, repository, FakeRuntime(), FakeCLIProxy(), EmptyProfiles(), secret, channels=registry,
    )
    app: Final = FastAPI()
    app.include_router(create_router(service, "fixture-manager"))
    path: Final = "/api/openai-compatible/models"
    payload: Final = {"base_url": base, "credential": {"api_key": "preview-key"}, "headers": [["X-Fixture", "yes"]]}
    with TestClient(app) as client:
        assert client.post(path, json=payload).status_code == 401
        client.headers["Authorization"] = "Bearer fixture-manager"
        rejected: Final = client.post(path, json={**payload, "credential": {"api_key": ["preview-key"]}})
        assert rejected.status_code == 422 and "preview-key" not in rejected.text
        assert not requests
        preview: Final = client.post(path, json=payload)
        assert preview.status_code == 200 and preview.json() == ["fixture-model"]
        assert len(requests) == 1
        assert requests[0].method == "GET"
        assert requests[0].url.host == httpx.URL(base).host
        assert requests[0].url.path == catalog_path
        assert requests[0].headers["Authorization"] == "Bearer preview-key"
        assert requests[0].headers["X-Fixture"] == "yes"
        catalog: Final = client.get("/api/provider-families")
        assert catalog.status_code == 200
    entries: Final = {family["kind"]: family for family in catalog.json()}
    assert entries["glm"]["card_count"] == (3 if "/api/coding/paas/v4" in base else 2)
    assert entries["glm"]["supplier"] == SupplierKind.OPENAI_COMPATIBLE
    assert entries["opencode"]["card_count"] == 2
    assert entries["opencode"]["supplier"] == SupplierKind.OPENAI_COMPATIBLE
    assert entries["openai_compatible"]["card_count"] == 1
    assert tuple(repository.records.values()) == records


@pytest.mark.parametrize(
    ("upstream_status", "reason"),
    (
        (401, "model_list_http_401"),
        (403, "model_list_http_403"),
        (404, "model_list_http_404"),
        (302, "model_list_http_302"),
        (200, "model_list_format"),
        (0, "model_list_timeout"),
        (-1, "model_list_network"),
    ),
)
def test_manager_preview_distinguishes_failures_without_leaking_secrets(
    tmp_path: Path, upstream_status: int, reason: str,
) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.example.com"
        if upstream_status == 0:
            raise httpx.ConnectTimeout("private-fixture")
        if upstream_status == -1:
            raise httpx.ProxyError("private-fixture")
        return httpx.Response(
            upstream_status,
            content=b'{"error":"private-fixture"}',
            headers=(("Location", "https://no-redirect.example.com"),),
        )

    secret: Final = EnvironmentSecretDeriver("s" * 32)
    channel: Final = OpenAICompatibleChannel(
        secret, httpx.AsyncClient(transport=httpx.MockTransport(upstream)), public_resolver,
    )
    registry: Final = replace(
        ChannelRegistry.default(), implementations=MappingProxyType({ChannelKind.OPENAI_COMPATIBLE: channel}),
    )
    service: Final = EnvironmentService(
        _settings(tmp_path), MemoryRepository(_record(status=EnvironmentStatus.READY)),
        FakeRuntime(), FakeCLIProxy(), EmptyProfiles(), secret, channels=registry,
    )
    app: Final = FastAPI()
    app.include_router(create_router(service, "fixture-manager"))
    with TestClient(app) as client:
        preview: Final = client.post(
            "/api/openai-compatible/models",
            content=b'{"base_url":"https://api.example.com/v1","credential":{"api_key":"preview-key"}}',
            headers=(("Authorization", "Bearer fixture-manager"), ("Content-Type", "application/json")),
        )
    assert preview.status_code == 502
    assert "private-fixture" not in preview.text and "preview-key" not in preview.text
    assert preview.json()["detail"] == reason

@pytest.mark.asyncio
async def test_preview_models_uses_credentials_and_headers_without_creating_a_card() -> None:
    requests: Final[list[httpx.Request]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}, {"id": "model-a"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        channel: Final = OpenAICompatibleChannel(
            client=client, secrets=EnvironmentSecretDeriver("s" * 32), resolver=public_resolver,
        )
        result: Final = await channel.discover_models_from_source(
            "https://api.example.com/v1", "preview-secret", (("x-feature", "enabled"),), None,
        )
    assert result == ("model-a", "model-b")
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.example.com/v1/models"
    assert requests[0].headers["authorization"] == "Bearer preview-secret"
    assert requests[0].headers["x-feature"] == "enabled"


@pytest.mark.asyncio
async def test_preview_models_rejects_private_dns_before_sending_credentials() -> None:
    def upstream(_: httpx.Request) -> httpx.Response:
        pytest.fail("Private destination must never receive credentials")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        channel: Final = OpenAICompatibleChannel(
            client=client, secrets=EnvironmentSecretDeriver("s" * 32), resolver=private_resolver,
        )
        with pytest.raises(ValueError, match="blocked address"):
            await channel.discover_models_from_source("https://api.example.com/v1", "preview-secret", (), None)


def test_openai_compatible_request_rejects_multiple_keys() -> None:
    with pytest.raises(ValueError, match="at most 1 item"):
        OpenAICompatibleCreateRequest(
            base_url="https://api.example.com/v1",
            test_model="upstream-model",
            api_keys=({"api_key": "first-key"}, {"api_key": "second-key"}),
        )


def test_openai_compatible_request_rejects_local_and_metadata_targets() -> None:
    for base_url in (
        "http://127.0.0.1/v1",
        "http://[::1]/v1",
        "http://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/v1",
        "https://user:password@example.com/v1",
    ):
        with pytest.raises(ValueError):
            OpenAICompatibleCreateRequest(
                base_url=base_url,
                test_model="upstream-model",
                api_keys=({"api_key": "secret"},),
            )


def test_openai_compatible_request_rejects_protected_headers() -> None:
    with pytest.raises(ValueError, match="protected transport header"):
        OpenAICompatibleCreateRequest(
            base_url="https://api.example.com/v1",
            test_model="upstream-model",
            api_keys=({"api_key": "secret"},),
            headers=(("Authorization", "should-not-be-accepted"),),
        )


@pytest.mark.parametrize(
    "base_url",
    (
        "http://generativelanguage.googleapis.com/v1beta",
        "https://127.0.0.1/v1beta",
        "https://metadata.google.internal/v1beta",
    ),
)
def test_direct_api_key_base_url_rejects_unsafe_targets(base_url: str) -> None:
    with pytest.raises(ValueError):
        DirectAPIKeyCredentialRequest(api_key="secret", base_url=base_url)


def test_gemini_direct_api_key_requires_official_endpoint() -> None:
    with pytest.raises(ValueError):
        CreateDirectCredentialEnvironmentRequest(
            name="Gemini",
            supplier=SupplierKind.GEMINI,
            credential=DirectAPIKeyCredentialRequest(api_key="secret", base_url="https://example.com/v1beta"),
        )


def test_xai_direct_api_key_accepts_safe_public_https_endpoint() -> None:
    request: Final = CreateDirectCredentialEnvironmentRequest(
        name="xAI",
        supplier=SupplierKind.XAI,
        credential=DirectAPIKeyCredentialRequest(api_key="secret", base_url="https://api.example.com/v1"),
    )
    assert request.credential.base_url is not None


@pytest.mark.asyncio
async def test_channel_rejects_hostname_that_resolves_to_private_network() -> None:
    secret_seed: Final = "s" * 32
    environment_id: Final = uuid4()
    cipher: Final = StateCipher(EnvironmentSecretDeriver(secret_seed))
    now: Final = datetime.now(timezone.utc)
    record: Final = EnvironmentRecord(
        id=environment_id,
        name="Blocked card",
        provider=Provider.OPENAI,
        channel=ChannelKind.OPENAI_COMPATIBLE,
        supplier=SupplierKind.OPENAI_COMPATIBLE,
        openai_compatible=OpenAICompatibleConfiguration(
            base_url="https://api.example.com/v1",
            test_model="chat-model",
            credentials=(OpenAICompatibleCredential(api_key_ciphertext=cipher.seal(environment_id, "secret-key")),),
        ),
        status=EnvironmentStatus.VALIDATING,
        enabled=True,
        manual_cooldown=False,
        concurrency_limit=1,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=(),
        enabled_models=(),
        auth_file_name=None,
        auth_index=None,
        quota=QuotaSnapshot(),
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )
    channel: Final = OpenAICompatibleChannel(EnvironmentSecretDeriver(secret_seed), resolver=private_resolver)
    try:
        with pytest.raises(ValueError, match="blocked address"):
            await channel.read_account(record)
    finally:
        await channel.close()


@pytest.mark.asyncio
async def test_channel_discovers_prefixed_models_and_keeps_keys_out_of_record_repr() -> None:
    secret_seed: Final = "s" * 32
    environment_id: Final = uuid4()
    cipher: Final = StateCipher(EnvironmentSecretDeriver(secret_seed))
    configuration: Final = OpenAICompatibleConfiguration(
        base_url="http://api.example.com/v1",
        prefix="vendor/",
        test_model="chat-model",
        credentials=(
            OpenAICompatibleCredential(api_key_ciphertext=cipher.seal(environment_id, "secret-key"), weight=2),
        ),
        custom_models=(),
    )
    now: Final = datetime.now(timezone.utc)
    record: Final = EnvironmentRecord(
        id=environment_id,
        name="Compatible card",
        provider=Provider.OPENAI,
        channel=ChannelKind.OPENAI_COMPATIBLE,
        supplier=SupplierKind.OPENAI_COMPATIBLE,
        openai_compatible=configuration,
        status=EnvironmentStatus.VALIDATING,
        enabled=True,
        manual_cooldown=False,
        concurrency_limit=1,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=(),
        enabled_models=(),
        auth_file_name=None,
        auth_index=None,
        quota=QuotaSnapshot(),
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "api.example.com"
        if request.headers["authorization"] == "Bearer secret-key":
            return httpx.Response(200, json={"data": [{"id": "chat-model"}]}, request=request)
        assert request.headers["authorization"] == "Bearer second-key"
        return httpx.Response(200, json={"data": [{"id": "embedding-model"}]}, request=request)

    channel: Final = OpenAICompatibleChannel(
        EnvironmentSecretDeriver(secret_seed),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=public_resolver,
    )
    try:
        observed: Final = await channel.read_account(record)
        gateway: Final = channel.gateway(observed)
    finally:
        await channel.close()

    assert observed.available_models == ("vendor/chat-model",)
    assert observed.enabled_models == observed.available_models
    assert gateway.credentials[0].api_key == "secret-key"
    assert "secret-key" not in repr(record)


@pytest.mark.asyncio
async def test_custom_models_receive_the_prefix_once() -> None:
    secret_seed: Final = "s" * 32
    environment_id: Final = uuid4()
    cipher: Final = StateCipher(EnvironmentSecretDeriver(secret_seed))
    now: Final = datetime.now(timezone.utc)
    record: Final = EnvironmentRecord(
        id=environment_id,
        name="Custom model card",
        provider=Provider.OPENAI,
        channel=ChannelKind.OPENAI_COMPATIBLE,
        supplier=SupplierKind.OPENAI_COMPATIBLE,
        openai_compatible=OpenAICompatibleConfiguration(
            base_url="https://api.example.com/v1",
            prefix="vendor/",
            test_model="chat-model",
            credentials=(OpenAICompatibleCredential(api_key_ciphertext=cipher.seal(environment_id, "secret-key")),),
            custom_models=("chat-model",),
        ),
        status=EnvironmentStatus.VALIDATING,
        enabled=True,
        manual_cooldown=False,
        concurrency_limit=1,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=(),
        enabled_models=(),
        auth_file_name=None,
        auth_index=None,
        quota=QuotaSnapshot(),
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    client: Final = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": []}, request=request))
    )
    channel: Final = OpenAICompatibleChannel(
        EnvironmentSecretDeriver(secret_seed), client=client, resolver=public_resolver
    )
    try:
        observed: Final = await channel.read_account(record)
    finally:
        await channel.close()

    assert observed.available_models == ("vendor/chat-model",)


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["https://api.z.ai/api/coding/paas/v4", "https://opencode.ai/zen/go/v1"])
@pytest.mark.parametrize("failure", ["http", "format", "timeout"])
async def test_plan_quota_failure_retains_last_successful_windows(base_url: str, failure: str) -> None:
    from account_pool.domain import QuotaWindow

    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    observed_at: Final = datetime.now(timezone.utc) - timedelta(minutes=1)
    previous: Final = QuotaSnapshot(
        observed_at=observed_at, refresh_status="complete", source="provider_api", plan_type="fixture-plan",
        windows=(QuotaWindow(name="5 hour", used_percent=100, remaining_percent=0, window_minutes=300),),
    )
    record: Final = _compatible_card(base_url, secrets).model_copy(update={"display_quota": previous})

    def upstream(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        return httpx.Response(401 if failure == "http" else 200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        refreshed: Final = await OpenAICompatibleChannel(secrets, http, public_resolver).read_account(record, refresh_quota=True)
    quota: Final = refreshed.display_quota
    assert quota is not None
    assert quota.windows == previous.windows
    assert quota.observed_at == previous.observed_at
    assert quota.plan_type == previous.plan_type
    assert quota.refresh_status == "failed" and quota.source == "stored_cache"
    assert quota.refresh_attempted_at is not None and quota.refresh_attempted_at > observed_at
    assert refreshed.quota == record.quota and refreshed.status == record.status


@pytest.mark.parametrize("base_url", ["https://api.z.ai/api/coding/paas/v4", "https://opencode.ai/zen/go/v1"])
def test_plan_windows_reach_routing_without_the_glm_mcp_tool_window(base_url: str) -> None:
    from account_pool.application.quota_state import routing_quota
    from account_pool.domain import QuotaWindow

    record: Final = _compatible_card(base_url, EnvironmentSecretDeriver("s" * 32))
    quota: Final = QuotaSnapshot(observed_at=datetime.now(timezone.utc), refresh_status="complete", windows=(
        QuotaWindow(name="5 hour", used_percent=25, remaining_percent=75, window_minutes=300),
        QuotaWindow(name="MCP Monthly", used_percent=100, remaining_percent=0, window_minutes=43200),
    ))
    observed: Final = routing_quota(record.model_copy(update={"display_quota": quota}))
    assert observed.windows[0].remaining_percent == 75
    assert tuple(w.name for w in observed.windows) == (("5 hour",) if "z.ai" in base_url else ("5 hour", "MCP Monthly"))
    assert record.display_quota is None


@pytest.mark.asyncio
async def test_a_successful_plan_refresh_replaces_failed_cache_and_clears_the_error() -> None:
    from account_pool.domain import QuotaWindow

    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    previous: Final = QuotaSnapshot(
        observed_at=datetime.now(timezone.utc) - timedelta(hours=1), source="stored_cache", refresh_status="failed",
        refresh_error="fixture previous failure", windows=(
            QuotaWindow(name="5 hour", used_percent=100, remaining_percent=0, window_minutes=300),
        ),
    )
    record: Final = _compatible_card("https://api.z.ai/api/coding/paas/v4", secrets).model_copy(update={"display_quota": previous})

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "data": {"limits": [
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 25},
        ]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        refreshed: Final = await OpenAICompatibleChannel(secrets, http, public_resolver).read_account(record, refresh_quota=True)
    quota: Final = refreshed.display_quota
    assert quota is not None and quota.windows[0].remaining_percent == 75
    assert quota.refresh_status == "complete" and quota.source == "provider_api"
    assert quota.refresh_error is None
    assert quota.observed_at is not None and previous.observed_at is not None
    assert quota.observed_at > previous.observed_at


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["https://api.z.ai/api/coding/paas/v4", "https://opencode.ai/zen/go/v1"])
@pytest.mark.parametrize("reset_seconds", [None, 3600, -1])
async def test_missing_plan_windows_keep_previous_exhaustion_until_reset_or_new_observation(
    base_url: str, reset_seconds: int | None,
) -> None:
    from account_pool.domain import QuotaWindow

    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    now: Final = datetime.now(timezone.utc)
    observed_at: Final = now - timedelta(minutes=15)
    previous: Final = QuotaSnapshot(observed_at=observed_at, source="provider_api", refresh_status="complete", windows=(
        QuotaWindow(name="5 hour", used_percent=100, remaining_percent=0, window_minutes=300,
                    resets_at=None if reset_seconds is None else now + timedelta(seconds=reset_seconds)),
        QuotaWindow(name="Weekly", used_percent=40, remaining_percent=60, window_minutes=10080),
    ))
    record: Final = _compatible_card(base_url, secrets).model_copy(update={"display_quota": previous})
    reset_at: Final = (now + timedelta(days=1)).isoformat()
    partial: Final = (
        {"code": 200, "data": {"limits": [{"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "percentage": 10}]}}
        if "z.ai" in base_url else {"usage": {"weekly": {"percent": 10, "resetsAt": reset_at}}}
    )
    complete: Final = (
        {"code": 200, "data": {"limits": [
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 30},
            {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "percentage": 10},
        ]}}
        if "z.ai" in base_url else {"usage": {
            "rolling": {"percent": 30, "resetsAt": reset_at}, "weekly": {"percent": 10, "resetsAt": reset_at},
            "monthly": {"percent": 20, "resetsAt": reset_at},
        }}
    )
    responses: Final = iter((partial, complete))

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=next(responses))

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        channel: Final = OpenAICompatibleChannel(secrets, http, public_resolver)
        refreshed: Final = await channel.read_account(record, refresh_quota=True)
        shown: Final = refreshed.display_quota
        assert shown is not None
        windows: Final = {window.name: window for window in shown.windows}
        assert windows["Weekly"].remaining_percent == 90
        if reset_seconds == -1:
            assert "5 hour" not in windows and shown.source == "provider_api"
            assert shown.observed_at is not None and shown.observed_at > observed_at
        else:
            assert windows["5 hour"] == previous.windows[0]
            assert shown.observed_at == observed_at
            assert shown.refresh_status == "partial" and shown.source == "stored_cache"
        recovered: Final = await channel.read_account(refreshed, refresh_quota=True)
        quota: Final = recovered.display_quota
        assert quota is not None and quota.refresh_status == "complete" and quota.source == "provider_api"
        assert quota.observed_at is not None and quota.observed_at > observed_at
        assert next(window.remaining_percent for window in quota.windows if window.name == "5 hour") == 70



def test_compatible_models_repr_hides_stored_secrets_without_changing_persistence() -> None:
    credential: Final = OpenAICompatibleCredential(
        api_key_ciphertext="fixture-key-ciphertext",
        proxy_url="http://fixture-user:fixture-password@proxy.example.test:8080",
        weight=2,
    )
    configuration: Final = OpenAICompatibleConfiguration(
        base_url="https://api.example.test/v1", test_model="fixture-model",
        credentials=(credential,), headers_ciphertext="fixture-headers-ciphertext",
    )
    for text in (repr(credential), str(credential), repr(configuration), str(configuration)):
        assert "fixture-key-ciphertext" not in text
        assert "fixture-password" not in text
        assert "fixture-headers-ciphertext" not in text
    assert credential.api_key_ciphertext == "fixture-key-ciphertext"
    assert credential.proxy_url == "http://fixture-user:fixture-password@proxy.example.test:8080"
    assert configuration.model_dump()["credentials"][0]["api_key_ciphertext"] == "fixture-key-ciphertext"
    assert configuration.model_dump()["headers_ciphertext"] == "fixture-headers-ciphertext"
    assert OpenAICompatibleConfiguration.model_validate_json(configuration.model_dump_json()) == configuration
    with pytest.raises(ValidationError, match="frozen"):
        setattr(credential, "api_key_ciphertext", "changed")
    with pytest.raises(ValidationError, match="frozen"):
        setattr(configuration, "credentials", ())


@pytest.mark.asyncio
async def test_model_discovery_deadline_includes_dns(monkeypatch) -> None:
    import asyncio
    from account_pool.channels import openai_compatible

    async def pending_dns(_host: str, _port: int) -> tuple[str, ...]:
        await asyncio.Event().wait()
        raise AssertionError("DNS must be cancelled")

    monkeypatch.setattr(openai_compatible, "_MODEL_LIST_TIMEOUT_SECONDS", 0.01)
    secrets = EnvironmentSecretDeriver("s" * 32)
    record = _compatible_card("https://api.example.test/v1", secrets)
    async with httpx.AsyncClient() as http, asyncio.timeout(2):
        channel = OpenAICompatibleChannel(secrets, client=http, resolver=pending_dns)
        with pytest.raises(openai_compatible.ModelListDiscoveryError, match="model_list_timeout"):
            await channel.discover_model_catalog(record)
        assert not await channel.data_plane_health_check(record)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.Response(503), httpx.Response(200, json={"data": []})])
@pytest.mark.parametrize("refresh_quota", [False, True])
async def test_cached_catalog_cannot_release_cooldown_until_live_models_return(
    tmp_path: Path, failure: httpx.Response, refresh_quota: bool,
) -> None:
    secrets = EnvironmentSecretDeriver("s" * 32)
    base = _compatible_card("https://api.example.test/v1", secrets)
    assert base.openai_compatible is not None
    card = base.model_copy(update={
        "status": EnvironmentStatus.COOLING_DOWN,
        "cooldown_until": datetime.now(timezone.utc) - timedelta(minutes=1),
        "available_models": ("cached",),
        "enabled_models": ("manual",),
        "automatic_cooldown": True,
        "openai_compatible": base.openai_compatible.model_copy(update={"custom_models": ("manual",)}),
    })
    upstream = failure
    requests = []
    def respond(request):
        requests.append(request.url.path)
        return upstream
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        channel = OpenAICompatibleChannel(secrets, http, public_resolver)
        channels = replace(ChannelRegistry.default(), implementations=MappingProxyType({card.channel: channel}))
        service = EnvironmentService(
            _settings(tmp_path), MemoryRepository(card), FakeRuntime(), FakeCLIProxy(), EmptyProfiles(), secrets,
            channels=channels,
        )
        assert (await channel.read_account(card)).available_models == ("cached", "manual")
        assert (await service._refresh_if_needed(card, refresh_quota=refresh_quota)).status is EnvironmentStatus.COOLING_DOWN
        upstream = httpx.Response(200, json={"data": [{"id": "live"}]})
        requests.clear()
        recovered = await service._refresh_if_needed(card, refresh_quota=refresh_quota)
        assert recovered.status is EnvironmentStatus.READY and recovered.cooldown_until is None
        assert not recovered.automatic_cooldown and channel.gateway(recovered).routable
        assert recovered.enabled_models == ("manual",)
        assert requests == ["/v1/models"]


@pytest.mark.asyncio
async def test_request_models_caps_unbounded_model_list_and_refresh_preserves_custom_models() -> None:
    from account_pool.channels.openai_compatible import _MAX_MODEL_LIST_BYTES

    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    base: Final = _compatible_card("https://api.example.test/v1", secrets)
    assert base.openai_compatible is not None
    record: Final = base.model_copy(update={
        "openai_compatible": base.openai_compatible.model_copy(update={
            "test_model": "manual-model",
            "prefix": "compat/",
            "custom_models": ("manual-model",),
        }),
        "available_models": ("compat/manual-model",),
        "enabled_models": ("compat/manual-model",),
    })

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-Oversized") == "1":
            return httpx.Response(200, content=b"x" * (_MAX_MODEL_LIST_BYTES + 1024))
        return httpx.Response(200, json={"data": [{"id": "remote-a"}, {"id": "manual-model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        channel: Final = OpenAICompatibleChannel(secrets, http, public_resolver)
        catalog: Final = await channel.discover_model_catalog(record)
        assert catalog == ("compat/remote-a", "compat/manual-model")
        refreshed_models: Final = await channel._discover_models(record)
        assert refreshed_models == ("compat/manual-model", "compat/remote-a")
        oversized: Final = await channel._request_models(
            "sk-fixture-key",
            None,
            {"X-Oversized": "1"},
            "https://api.example.test/v1",
            None,
            bounded=False,
        )
        assert oversized is None
