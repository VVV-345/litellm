"""验证号池管理代理的管理员权限、内部令牌和响应脱敏。"""

from __future__ import annotations

import base64
import json
from typing import Final, Literal
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.management_endpoints import account_pool_endpoints
from litellm.proxy.management_endpoints.account_pool_actor import ActorEnvelope
from litellm.proxy.management_endpoints.account_pool_endpoints import (
    UpstreamModelDiscoveryProxyRequest,
    _forward_with_client,  # pyright: ignore[reportPrivateUsage]  # 测试内部转发安全边界
    _require_proxy_admin,  # pyright: ignore[reportPrivateUsage]  # 测试管理员权限边界
    authorize_account_pool,
    create_catalog_channel,
    delete_catalog_channel,
    delete_external_channel_deployment,
    delete_model_routing_candidate,
    detach_catalog_channel,
    discover_upstream_models,
    get_account_pool_overview,
    get_catalog_channel,
    get_catalog_channel_aggregate,
    get_catalog_channel_health,
    get_channel_operation,
    get_model_routing_policy,
    get_model_routing_table,
    import_catalog_channel,
    list_account_pool_events,
    list_pool_models,
    list_upstream_providers,
    probe_catalog_channel_health,
    reconcile_catalog_channel,
    router,
    update_catalog_channel,
    update_model_routing_candidate,
    update_model_routing_policy,
    validate_provider_service,
)

_Method = Literal["GET", "POST", "PUT", "DELETE"]
_JSON_OBJECT: Final = TypeAdapter(dict[str, object])
_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_BINDING_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_OPERATION_ID: Final = UUID("30000000-0000-0000-0000-000000000003")


def _actor_action(actor: ActorEnvelope) -> object:
    encoded_claims: Final = actor.token.split(".")[1]
    claims: Final = _JSON_OBJECT.validate_json(
        base64.urlsafe_b64decode(f"{encoded_claims}{'=' * (-len(encoded_claims) % 4)}")
    )
    return claims["action"]


def test_account_pool_management_rejects_non_admin() -> None:
    auth: Final = UserAPIKeyAuth(api_key="hashed", user_role=LitellmUserRoles.INTERNAL_USER)

    with pytest.raises(HTTPException) as error:
        _require_proxy_admin(auth)

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_account_pool_authorize_accepts_proxy_admin_only() -> None:
    admin: Final = UserAPIKeyAuth(api_key="hashed", user_role=LitellmUserRoles.PROXY_ADMIN)
    viewer: Final = UserAPIKeyAuth(api_key="hashed", user_role=LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY)

    assert await authorize_account_pool(admin) == {"ok": True}
    with pytest.raises(HTTPException) as error:
        await authorize_account_pool(viewer)

    assert error.value.status_code == 403


def test_account_pool_router_exposes_channel_lifecycle_and_operation_lookup() -> None:
    routes: Final = {
        (method, route.path)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert {
        ("GET", "/account_pool/channels"),
        ("GET", "/account_pool/overview"),
        ("GET", "/account_pool/events"),
        ("GET", "/account_pool/upstream-providers"),
        ("POST", "/account_pool/provider-services/validate"),
        ("POST", "/account_pool/upstream-providers/discover-models"),
        ("POST", "/account_pool/channels"),
        ("GET", "/account_pool/channels/{channel_id}"),
        ("GET", "/account_pool/channels/{channel_id}/aggregate"),
        ("PUT", "/account_pool/channels/{channel_id}"),
        ("POST", "/account_pool/channels/import"),
        ("POST", "/account_pool/channels/{channel_id}/detach"),
        ("DELETE", "/account_pool/channels/{channel_id}"),
        (
            "POST",
            "/account_pool/channels/{channel_id}/bindings/{binding_id}/delete-external-deployment",
        ),
        ("POST", "/account_pool/channels/{channel_id}/reconcile"),
        ("POST", "/account_pool/channels/{channel_id}/health-probe"),
        ("GET", "/account_pool/channels/{channel_id}/health"),
        ("GET", "/account_pool/operations/{operation_id}"),
        ("GET", "/account_pool/models"),
        ("GET", "/account_pool/models/{model:path}/routing-table"),
        ("GET", "/account_pool/models/{model:path}/routing-policy"),
        ("PUT", "/account_pool/models/{model:path}/routing-policy"),
        ("PUT", "/account_pool/models/{model:path}/routing-candidates/{binding_id}"),
        ("DELETE", "/account_pool/models/{model:path}/routing-candidates/{binding_id}"),
    }.issubset(routes)


@pytest.mark.asyncio
async def test_validate_provider_service_requires_proxy_admin_and_forwards_to_internal_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forwarded: list[tuple[_Method, str]] = []

    async def forward(request: Request, method: _Method, path: str) -> Response:
        forwarded.append((method, path))
        return Response(status_code=200)

    monkeypatch.setattr(account_pool_endpoints, "_forward", forward)
    request: Final = Request({"type": "http", "method": "POST", "path": "/account_pool/provider-services/validate"})
    admin: Final = UserAPIKeyAuth(api_key="hashed", user_role=LitellmUserRoles.PROXY_ADMIN)
    viewer: Final = UserAPIKeyAuth(api_key="hashed", user_role=LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY)

    response: Final = await validate_provider_service(request, admin)

    assert response.status_code == 200
    assert forwarded == [("POST", "/api/provider-services/validate")]
    with pytest.raises(HTTPException) as error:
        await validate_provider_service(request, viewer)

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_upstream_provider_endpoints_are_admin_only_and_forward_without_parser_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forwarded: list[tuple[_Method, str, bytes | None]] = []

    async def forward(
        request: Request,
        method: _Method,
        path: str,
        content: bytes | None = None,
    ) -> Response:
        forwarded.append((method, path, content))
        return Response(status_code=200)

    monkeypatch.setattr(account_pool_endpoints, "_forward", forward)
    request: Final = Request({"type": "http", "method": "POST", "path": "/account_pool/upstream-providers"})
    admin: Final = UserAPIKeyAuth(api_key="hashed", user_role=LitellmUserRoles.PROXY_ADMIN)
    viewer: Final = UserAPIKeyAuth(api_key="hashed", user_role=LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY)

    listed: Final = await list_upstream_providers(request, admin)
    discovery_request: Final = UpstreamModelDiscoveryProxyRequest(
        provider_id="openai",
        upstream_url="https://models.example/v1",
        api_key="one-time-key",
    )
    discovered: Final = await discover_upstream_models(request, discovery_request, admin)

    assert listed.status_code == 200
    assert discovered.status_code == 200
    assert forwarded[0] == ("GET", "/api/upstream-providers", None)
    assert forwarded[1][:2] == ("POST", "/api/upstream-providers/discover-models")
    assert json.loads(forwarded[1][2] or b"{}") == {
        "provider_id": "openai",
        "api_base": "https://models.example/v1",
        "api_key": "one-time-key",
    }
    with pytest.raises(HTTPException) as error:
        await discover_upstream_models(request, discovery_request, viewer)

    assert error.value.status_code == 403

@pytest.mark.asyncio
async def test_channel_lifecycle_writes_use_request_bound_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_ACTOR_SECRET", "actor-signing-secret-with-at-least-32-bytes")
    forwarded: list[tuple[_Method, str, ActorEnvelope | None]] = []

    async def forward(
        request: Request,
        method: _Method,
        path: str,
        actor: ActorEnvelope | None = None,
    ) -> Response:
        forwarded.append((method, path, actor))
        return Response(status_code=202)

    monkeypatch.setattr(account_pool_endpoints, "_forward", forward)
    admin: Final = UserAPIKeyAuth(
        api_key="hashed",
        user_id="admin-user",
        user_role=LitellmUserRoles.PROXY_ADMIN,
    )
    request: Final = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/account_pool/channels",
            "headers": (
                (b"x-request-id", b"lifecycle-request"),
                (b"x-account-pool-actor", b"browser-forged-actor"),
                (b"x-account-pool-request-id", b"browser-forged-request"),
            ),
        }
    )

    await create_catalog_channel(request, admin)
    await update_catalog_channel(_CHANNEL_ID, request, admin)
    await import_catalog_channel(request, admin)
    await detach_catalog_channel(_CHANNEL_ID, request, admin)
    await delete_catalog_channel(_CHANNEL_ID, request, admin)
    await delete_external_channel_deployment(_CHANNEL_ID, _BINDING_ID, request, admin)
    await reconcile_catalog_channel(_CHANNEL_ID, request, admin)
    await probe_catalog_channel_health(_CHANNEL_ID, request, admin)

    assert tuple((method, path, _actor_action(actor)) for method, path, actor in forwarded if actor is not None) == (
        ("POST", "/api/channels", "channel:create"),
        ("PUT", f"/api/channels/{_CHANNEL_ID}", "channel:update"),
        ("POST", "/api/channels/import", "channel:import"),
        ("POST", f"/api/channels/{_CHANNEL_ID}/detach", "channel:detach"),
        ("DELETE", f"/api/channels/{_CHANNEL_ID}", "channel:delete"),
        (
            "POST",
            f"/api/channels/{_CHANNEL_ID}/bindings/{_BINDING_ID}/delete-external-deployment",
            "channel:delete_external_deployment",
        ),
        ("POST", f"/api/channels/{_CHANNEL_ID}/reconcile", "channel:reconcile"),
        ("POST", f"/api/channels/{_CHANNEL_ID}/health-probe", "health:probe"),
    )
    assert all(actor is not None and actor.request_id == "lifecycle-request" for _, _, actor in forwarded)
    assert all(actor is not None and actor.token != "browser-forged-actor" for _, _, actor in forwarded)


@pytest.mark.asyncio
async def test_channel_detail_and_operation_lookup_are_unsigned_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    forwarded: list[tuple[_Method, str, ActorEnvelope | None]] = []

    async def forward(
        request: Request,
        method: _Method,
        path: str,
        actor: ActorEnvelope | None = None,
    ) -> Response:
        forwarded.append((method, path, actor))
        return Response(status_code=200)

    monkeypatch.setattr(account_pool_endpoints, "_forward", forward)
    admin: Final = UserAPIKeyAuth(api_key="hashed", user_role=LitellmUserRoles.PROXY_ADMIN)
    request: Final = Request({"type": "http", "method": "GET", "path": "/account_pool/channels"})

    await get_catalog_channel(_CHANNEL_ID, request, admin)
    await get_catalog_channel_aggregate(_CHANNEL_ID, request, admin)
    await get_catalog_channel_health(_CHANNEL_ID, request, admin)
    await get_account_pool_overview(request, admin)
    await list_account_pool_events(request, admin)
    await get_channel_operation(_OPERATION_ID, request, admin)

    assert forwarded == [
        ("GET", f"/api/channels/{_CHANNEL_ID}", None),
        ("GET", f"/api/channels/{_CHANNEL_ID}/aggregate", None),
        ("GET", f"/api/channels/{_CHANNEL_ID}/health", None),
        ("GET", "/api/overview", None),
        ("GET", "/api/events", None),
        ("GET", f"/api/operations/{_OPERATION_ID}", None),
    ]


@pytest.mark.asyncio
async def test_routing_reads_preserve_models_with_slashes_without_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    forwarded: list[tuple[_Method, str, ActorEnvelope | None]] = []

    async def forward(
        request: Request,
        method: _Method,
        path: str,
        actor: ActorEnvelope | None = None,
    ) -> Response:
        forwarded.append((method, path, actor))
        return Response(status_code=200)

    monkeypatch.setattr(account_pool_endpoints, "_forward", forward)
    admin: Final = UserAPIKeyAuth(api_key="hashed", user_role=LitellmUserRoles.PROXY_ADMIN)
    request: Final = Request({"type": "http", "method": "GET", "path": "/account_pool/models"})

    await list_pool_models(request, admin)
    await get_model_routing_table("openai/gpt-4o", request, admin)
    await get_model_routing_policy("openai/gpt-4o", request, admin)

    assert forwarded == [
        ("GET", "/api/models", None),
        ("GET", "/api/models/openai%2Fgpt-4o/routing-table", None),
        ("GET", "/api/models/openai%2Fgpt-4o/routing-policy", None),
    ]


@pytest.mark.asyncio
async def test_routing_mutations_use_request_bound_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_ACTOR_SECRET", "actor-signing-secret-with-at-least-32-bytes")
    forwarded: list[tuple[_Method, str, ActorEnvelope | None]] = []

    async def forward(
        request: Request,
        method: _Method,
        path: str,
        actor: ActorEnvelope | None = None,
    ) -> Response:
        forwarded.append((method, path, actor))
        return Response(status_code=200)

    monkeypatch.setattr(account_pool_endpoints, "_forward", forward)
    admin: Final = UserAPIKeyAuth(
        api_key="hashed",
        user_id="admin-user",
        user_role=LitellmUserRoles.PROXY_ADMIN,
    )
    request: Final = Request(
        {
            "type": "http",
            "method": "PUT",
            "path": "/account_pool/models/openai/gpt-4o/routing-policy",
            "headers": ((b"x-request-id", b"routing-request"),),
        }
    )

    await update_model_routing_policy("openai/gpt-4o", request, admin)
    await update_model_routing_candidate("openai/gpt-4o", _BINDING_ID, request, admin)
    await delete_model_routing_candidate("openai/gpt-4o", _BINDING_ID, request, admin)

    assert tuple((method, path, _actor_action(actor)) for method, path, actor in forwarded if actor is not None) == (
        ("PUT", "/api/models/openai%2Fgpt-4o/routing-policy", "routing_policy:update"),
        (
            "PUT",
            f"/api/models/openai%2Fgpt-4o/routing-candidates/{_BINDING_ID}",
            "routing_candidate:update",
        ),
        (
            "DELETE",
            f"/api/models/openai%2Fgpt-4o/routing-candidates/{_BINDING_ID}",
            "routing_candidate:delete",
        ),
    )


@pytest.mark.asyncio
async def test_forward_adds_service_token_and_filters_upstream_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_INTERNAL_URL", "http://account-pool:4100")
    monkeypatch.setenv("ACCOUNT_POOL_INTERNAL_TOKEN", "service-secret")
    captured: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            status_code=200,
            headers={
                "content-type": "application/json",
                "content-disposition": 'attachment; filename="snapshot.json"',
                "x-content-type-options": "nosniff",
                "x-upstream-secret": "hidden",
            },
            json=[{"provider_id": "glm_official"}],
        )

    request: Final = Request(
        {"type": "http", "method": "GET", "path": "/account_pool/provider-services", "headers": ()}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        response: Final = await _forward_with_client(
            request=request,
            method="GET",
            path="/api/provider-services",
            client=client,
        )

    assert captured[0].headers["x-account-pool-token"] == "service-secret"
    assert str(captured[0].url) == "http://account-pool:4100/api/provider-services"
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"] == 'attachment; filename="snapshot.json"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "x-upstream-secret" not in response.headers


@pytest.mark.asyncio
async def test_forward_preserves_authenticated_query_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_INTERNAL_URL", "http://account-pool:4100")
    monkeypatch.setenv("ACCOUNT_POOL_INTERNAL_TOKEN", "service-secret")
    captured: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code=200, json={"runs": []})

    request: Final = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/account_pool/channels/channel-id/parser-runs",
            "query_string": b"limit=5",
            "headers": (),
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        response: Final = await _forward_with_client(
            request=request,
            method="GET",
            path="/api/channels/channel-id/parser-runs",
            client=client,
        )

    assert response.status_code == 200
    assert str(captured[0].url) == "http://account-pool:4100/api/channels/channel-id/parser-runs?limit=5"


@pytest.mark.asyncio
async def test_forward_injects_actor_and_preserves_delete_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_INTERNAL_URL", "http://account-pool:4100")
    monkeypatch.setenv("ACCOUNT_POOL_INTERNAL_TOKEN", "service-secret")
    captured: list[httpx.Request] = []
    body: Final = b'{"override_id":"override-1","reason":"restore"}'

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code=200, json={"status": "created"})

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    request: Final = Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": "/account_pool/channels/channel-id/overrides/subscription/balance",
            "headers": (
                (b"idempotency-key", b"lifecycle-idempotency-key"),
                (b"x-account-pool-token", b"browser-forged-service-token"),
                (b"x-account-pool-actor", b"browser-forged-actor"),
                (b"x-account-pool-request-id", b"browser-forged-request"),
            ),
        },
        receive=receive,
    )
    actor: Final = ActorEnvelope(token="signed-actor-token", request_id="request-123")
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        response: Final = await _forward_with_client(
            request=request,
            method="DELETE",
            path="/api/channels/channel-id/overrides/subscription/balance",
            client=client,
            actor=actor,
        )

    assert response.status_code == 200
    assert captured[0].content == body
    assert captured[0].headers["content-type"] == "application/json"
    assert captured[0].headers["idempotency-key"] == "lifecycle-idempotency-key"
    assert captured[0].headers["x-account-pool-token"] == "service-secret"
    assert captured[0].headers["x-account-pool-actor"] == "signed-actor-token"
    assert captured[0].headers["x-account-pool-request-id"] == "request-123"


@pytest.mark.asyncio
async def test_forward_fails_closed_without_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCOUNT_POOL_INTERNAL_TOKEN", raising=False)
    request: Final = Request({"type": "http", "method": "GET", "path": "/account_pool/accounts"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        with pytest.raises(HTTPException) as error:
            await _forward_with_client(request=request, method="GET", path="/api/accounts", client=client)

    assert error.value.status_code == 503
