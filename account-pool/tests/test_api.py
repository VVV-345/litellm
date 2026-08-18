"""验证号池管理接口、内部鉴权和 OpenAI 兼容网关。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import httpx
import pytest
from account_pool.app import create_app
from account_pool.config import Settings
from account_pool.models import AccountView, LiteLLMStatus, ManagementResult, ModelSummary, RouteEntry, StatsView
from account_pool.store import MemoryStateStore
from pydantic import BaseModel, ConfigDict, TypeAdapter


class _ForwardedRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    metadata: dict[str, str]


class _GatewayError(BaseModel):
    type: str


class _GatewayErrorResponse(BaseModel):
    error: _GatewayError


_MODEL_SUMMARIES_ADAPTER: Final = TypeAdapter(tuple[ModelSummary, ...])
_ROUTE_ENTRIES_ADAPTER: Final = TypeAdapter(tuple[RouteEntry, ...])
_ACCOUNT_VIEWS_ADAPTER: Final = TypeAdapter(tuple[AccountView, ...])
_JSON_OBJECT_ADAPTER: Final = TypeAdapter(dict[str, object])


def settings(
    config_path: Path | None = None,
    admin_key: str | None = None,
    internal_token: str | None = "test-service-token",
) -> Settings:
    return Settings(
        config_path=config_path or Path(__file__).resolve().parents[1] / "config" / "accounts.demo.yaml",
        store_mode="memory",
        redis_url="redis://unused",
        litellm_url="http://litellm.internal",
        litellm_admin_key=admin_key,
        lease_ttl_seconds=60,
        internal_token=internal_token,
    )


@pytest.mark.asyncio
async def test_management_api_requires_configured_internal_token() -> None:
    app: Final = create_app(settings=settings(internal_token="service-secret"), store=MemoryStateStore())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            missing: Final = await client.get("/api/provider-services")
            invalid: Final = await client.get("/api/provider-services", headers={"x-account-pool-token": "wrong"})
            valid: Final = await client.get(
                "/api/provider-services", headers={"x-account-pool-token": "service-secret"}
            )
            health: Final = await client.get("/healthz")

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200
    assert health.status_code == 200


@pytest.mark.asyncio
async def test_management_api_fails_closed_without_configured_token() -> None:
    app: Final = create_app(settings=settings(internal_token=None), store=MemoryStateStore())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.get("/api/accounts")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_standalone_ui_uses_litellm_admin_authentication() -> None:
    def litellm(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/account_pool/authorize":
            return httpx.Response(status_code=404)
        token: Final = request.headers.get("authorization")
        if token == "Bearer admin-secret":
            return httpx.Response(status_code=200, json={"ok": True})
        return httpx.Response(status_code=401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(litellm)) as proxy_client:
        app: Final = create_app(
            settings=settings(admin_key="service-admin-key"),
            store=MemoryStateStore(),
            proxy_client=proxy_client,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://account-pool",
                follow_redirects=False,
            ) as client:
                root: Final = await client.get("/")
                ui: Final = await client.get("/ui/")
                missing: Final = await client.get("/ui-api/stats")
                invalid: Final = await client.get(
                    "/ui-api/stats", headers={"authorization": "Bearer invalid"}
                )
                viewer: Final = await client.get(
                    "/ui-api/stats", headers={"authorization": "Bearer viewer-secret"}
                )
                valid: Final = await client.get(
                    "/ui-api/stats", headers={"authorization": "Bearer admin-secret"}
                )

    assert root.status_code == 307
    assert root.headers["location"] == "/ui/"
    assert ui.status_code == 200
    assert "号池调度器" in ui.text
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert viewer.status_code == 401
    assert valid.status_code == 200


@pytest.mark.asyncio
async def test_management_api_renders_pool_state() -> None:
    app: Final = create_app(settings=settings(), store=MemoryStateStore())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://account-pool",
            headers={"x-account-pool-token": "test-service-token"},
        ) as client:
            stats_response: Final = await client.get("/api/stats")
            models_response: Final = await client.get("/api/models")
            routes_response: Final = await client.get("/api/models/gpt-4o/routing-table")

    stats: Final = StatsView.model_validate_json(stats_response.content)
    models: Final = _MODEL_SUMMARIES_ADAPTER.validate_json(models_response.content)
    routes: Final = _ROUTE_ENTRIES_ADAPTER.validate_json(routes_response.content)
    assert stats == StatsView(models=2, accounts=3, available_accounts=3, inflight=0, max_concurrency=19)
    assert {item.model for item in models} == {"gpt-4o", "gpt-4o-mini"}
    assert all("api_key" not in json.dumps(item.model_dump()) for item in routes)


@pytest.mark.asyncio
async def test_gateway_rewrites_model_to_selected_litellm_deployment_and_releases_lease() -> None:
    captured: list[_ForwardedRequest] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        payload: Final = _ForwardedRequest.model_validate_json(request.content)
        captured.append(payload)
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            json={
                "id": "chatcmpl-test",
                "model": payload.model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 7},
            },
        )

    store: Final = MemoryStateStore()
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as proxy_client:
        app: Final = create_app(settings=settings(), store=store, proxy_client=proxy_client)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://account-pool",
                headers={"x-account-pool-token": "test-service-token"},
            ) as client:
                response: Final = await client.post(
                    "/v1/chat/completions",
                    headers={"authorization": "Bearer client-key", "x-request-id": "request-123"},
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hello"}]},
                )
                routes_response: Final = await client.get("/api/models/gpt-4o/routing-table")

    routes: Final = _ROUTE_ENTRIES_ADAPTER.validate_json(routes_response.content)
    assert response.status_code == 200
    assert captured[0].model == "pool-gpt4o-primary"
    assert captured[0].metadata["account_pool_request_id"] == "request-123"
    assert routes[0].inflight == 0
    assert routes[0].quota.total == 2_499_968


@pytest.mark.asyncio
async def test_gateway_returns_capacity_error_without_calling_litellm() -> None:
    calls: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status_code=200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as proxy_client:
        app: Final = create_app(settings=settings(), store=MemoryStateStore(), proxy_client=proxy_client)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://account-pool",
                headers={"x-account-pool-token": "test-service-token"},
            ) as client:
                response: Final = await client.post("/v1/chat/completions", json={"model": "missing-model"})

    error: Final = _GatewayErrorResponse.model_validate_json(response.content)
    assert response.status_code == 503
    assert error.error.type == "account_pool_unavailable"
    assert calls == []


@pytest.mark.asyncio
async def test_channel_crud_and_policy_updates_sync_litellm_without_persisting_api_key(tmp_path: Path) -> None:
    source: Final = Path(__file__).resolve().parents[1] / "config" / "accounts.demo.yaml"
    config_path: Final = tmp_path / "accounts.yaml"
    config_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    admin_requests: list[httpx.Request] = []

    def litellm(request: httpx.Request) -> httpx.Response:
        admin_requests.append(request)
        if request.url.path == "/health/liveliness":
            return httpx.Response(status_code=200, json={"status": "ok"})
        if request.url.path == "/model/info":
            return httpx.Response(status_code=200, json={"data": []})
        if request.url.path == "/model/new":
            payload: Final = _JSON_OBJECT_ADAPTER.validate_json(request.content)
            model_info: Final = _JSON_OBJECT_ADAPTER.validate_python(payload["model_info"])
            return httpx.Response(status_code=200, json={"model_id": model_info["id"]})
        if request.url.path.startswith("/model/") or request.url.path == "/model/delete":
            return httpx.Response(status_code=200, json={})
        return httpx.Response(status_code=404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(litellm)) as proxy_client:
        app: Final = create_app(
            settings=settings(config_path=config_path, admin_key="admin-secret"),
            store=MemoryStateStore(),
            proxy_client=proxy_client,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://account-pool",
                headers={"x-account-pool-token": "test-service-token"},
            ) as client:
                status_response: Final = await client.get("/api/litellm/status")
                create_response: Final = await client.post(
                    "/api/accounts",
                    json={
                        "id": "managed-channel",
                        "display_name": "Managed Channel",
                        "provider": "openai",
                        "base_url_display": "https://provider.example/v1",
                        "max_concurrency": 4,
                        "priority": 70,
                        "weight": 2,
                        "quotas": {"unit": "tokens", "total": 10000, "five_hour": 4000, "weekly": 8000},
                        "api_key": "provider-secret",
                        "deployments": [{"public_model": "managed-model", "provider_model": "openai/real-model"}],
                    },
                )
                accounts_response: Final = await client.get("/api/accounts")
                policy_response: Final = await client.put(
                    "/api/models/managed-model/policy",
                    json={"strategy": "priority"},
                )
                update_response: Final = await client.put(
                    "/api/accounts/managed-channel",
                    json={
                        "id": "managed-channel",
                        "display_name": "Managed Channel Updated",
                        "provider": "openai",
                        "base_url_display": "https://provider.example/v1",
                        "max_concurrency": 6,
                        "priority": 75,
                        "weight": 2,
                        "quotas": {"unit": "tokens", "total": 10000, "five_hour": 4000, "weekly": 8000},
                        "deployments": [
                            {
                                "public_model": "managed-model",
                                "provider_model": "openai/real-model-v2",
                                "litellm_model_id": _created_deployment_id(admin_requests),
                            }
                        ],
                    },
                )
                models_response: Final = await client.get("/api/models")
                delete_response: Final = await client.delete("/api/accounts/managed-channel")

    status: Final = LiteLLMStatus.model_validate_json(status_response.content)
    created: Final = ManagementResult.model_validate_json(create_response.content)
    accounts: Final = _ACCOUNT_VIEWS_ADAPTER.validate_json(accounts_response.content)
    policy: Final = ManagementResult.model_validate_json(policy_response.content)
    updated: Final = ManagementResult.model_validate_json(update_response.content)
    models: Final = _MODEL_SUMMARIES_ADAPTER.validate_json(models_response.content)
    deleted: Final = ManagementResult.model_validate_json(delete_response.content)
    persisted: Final = config_path.read_text(encoding="utf-8")
    new_request: Final = next(request for request in admin_requests if request.url.path == "/model/new")
    new_payload: Final = _JSON_OBJECT_ADAPTER.validate_json(new_request.content)
    new_params: Final = _JSON_OBJECT_ADAPTER.validate_python(new_payload["litellm_params"])
    patch_request: Final = next(request for request in admin_requests if request.method == "PATCH")
    patch_payload: Final = _JSON_OBJECT_ADAPTER.validate_json(patch_request.content)
    patch_params: Final = _JSON_OBJECT_ADAPTER.validate_python(patch_payload["litellm_params"])

    assert status.manageable
    assert created.ok and policy.ok and updated.ok and deleted.ok
    assert next(account for account in accounts if account.id == "managed-channel").deployments[0].managed_by_pool
    assert next(model for model in models if model.model == "managed-model").strategy == "priority"
    assert new_params["api_key"] == "provider-secret"
    assert "api_key" not in patch_params
    assert "provider-secret" not in persisted
    assert "/model/delete" in {request.url.path for request in admin_requests}


def _created_deployment_id(requests: list[httpx.Request]) -> str:
    request: Final = next(item for item in requests if item.url.path == "/model/new")
    payload: Final = _JSON_OBJECT_ADAPTER.validate_json(request.content)
    model_info: Final = _JSON_OBJECT_ADAPTER.validate_python(payload["model_info"])
    deployment_id: Final = model_info["id"]
    assert isinstance(deployment_id, str)
    return deployment_id
