"""为 Admin UI 代理固定的 account-pool 管理接口，避免浏览器接触内部服务令牌。"""

from __future__ import annotations

import json
import os
from typing import Annotated, Final, Literal, cast
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, SecretStr

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_actor import (
    AccountPoolActorAction,
    ActorEnvelope,
    ActorSigningFailureCode,
    sign_actor_envelope,
)

router: Final = APIRouter(prefix="/account_pool", tags=["Account Pool"])

_Method = Literal["GET", "POST", "PUT", "DELETE"]
_RESPONSE_HEADER_ALLOWLIST: Final = frozenset(
    {"content-type", "cache-control", "content-disposition", "x-content-type-options"}
)
_BODY_METHODS: Final = frozenset({"POST", "PUT", "DELETE"})


class UpstreamModelDiscoveryProxyRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    upstream_url: str = Field(min_length=1)
    api_key: SecretStr


def _require_proxy_admin(user_api_key_dict: UserAPIKeyAuth) -> None:
    if user_api_key_dict.user_role != LitellmUserRoles.PROXY_ADMIN:
        raise HTTPException(status_code=403, detail="Only proxy admins can manage the account pool")


@router.get("/authorize")
async def authorize_account_pool(
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> dict[str, bool]:
    _require_proxy_admin(user_api_key_dict)
    return {"ok": True}


async def _forward(
    request: Request,
    method: _Method,
    path: str,
    actor: ActorEnvelope | None = None,
    content: bytes | None = None,
) -> Response:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as client:
        return await _forward_with_client(
            request=request,
            method=method,
            path=path,
            client=client,
            actor=actor,
            content=content,
        )


async def _forward_with_client(
    request: Request,
    method: _Method,
    path: str,
    client: httpx.AsyncClient,
    actor: ActorEnvelope | None = None,
    content: bytes | None = None,
) -> Response:
    base_url: Final = os.environ.get("ACCOUNT_POOL_INTERNAL_URL", "http://127.0.0.1:4100").rstrip("/")
    internal_token: Final = os.environ.get("ACCOUNT_POOL_INTERNAL_TOKEN")
    if not internal_token:
        raise HTTPException(status_code=503, detail="ACCOUNT_POOL_INTERNAL_TOKEN is not configured")
    # 内部身份头只在服务端生成，浏览器提交的同名头不会被透传到 Account Pool。
    idempotency_key: Final = request.headers.get("idempotency-key")
    headers: Final = {
        "accept": "application/json",
        **({"content-type": "application/json"} if method in _BODY_METHODS else {}),
        **({"idempotency-key": idempotency_key} if idempotency_key is not None else {}),
        "x-account-pool-token": internal_token,
        **(
            {
                "x-account-pool-actor": actor.token,
                "x-account-pool-request-id": actor.request_id,
            }
            if actor is not None
            else {}
        ),
    }
    request_content: Final = content if content is not None else await request.body() if method in _BODY_METHODS else None
    query_bytes: Final = cast(bytes, request.scope.get("query_string", b""))
    query: Final = query_bytes.decode("ascii")
    upstream_url: Final = f"{base_url}{path}{f'?{query}' if query else ''}"
    try:
        upstream: Final = await client.request(
            method=method,
            url=upstream_url,
            headers=headers,
            content=request_content,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Account pool service is unavailable") from exc
    response_headers: Final = {
        name: value for name, value in upstream.headers.items() if name.lower() in _RESPONSE_HEADER_ALLOWLIST
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


async def _forward_channel_mutation(
    *,
    request: Request,
    user_api_key_dict: UserAPIKeyAuth,
    method: Literal["POST", "PUT", "DELETE"],
    path: str,
    action: AccountPoolActorAction,
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    actor: Final = _actor_for_request(
        request=request,
        user_api_key_dict=user_api_key_dict,
        action=action,
    )
    return await _forward(request=request, method=method, path=path, actor=actor)


@router.get("/provider-services")
async def list_provider_services(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path="/api/provider-services")


@router.post("/provider-services/validate")
async def validate_provider_service(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="POST", path="/api/provider-services/validate")


@router.get("/upstream-providers")
async def list_upstream_providers(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path="/api/upstream-providers")


@router.post("/upstream-providers/discover-models")
async def discover_upstream_models(
    request: Request,
    body: UpstreamModelDiscoveryProxyRequest,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    content: Final = json.dumps(
        {
            "provider_id": body.provider_id,
            "api_base": body.upstream_url,
            "api_key": body.api_key.get_secret_value(),
        },
        separators=(",", ":"),
    ).encode()
    return await _forward(
        request=request,
        method="POST",
        path="/api/upstream-providers/discover-models",
        content=content,
    )


@router.get("/channels")
async def list_catalog_channels(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path="/api/channels")


@router.get("/overview")
async def get_account_pool_overview(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path="/api/overview")


@router.get("/events")
async def list_account_pool_events(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path="/api/events")


@router.post("/channels")
async def create_catalog_channel(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="POST",
        path="/api/channels",
        action=AccountPoolActorAction.CHANNEL_CREATE,
    )


@router.post("/channels/import")
async def import_catalog_channel(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="POST",
        path="/api/channels/import",
        action=AccountPoolActorAction.CHANNEL_IMPORT,
    )


@router.get("/channels/{channel_id}")
async def get_catalog_channel(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path=f"/api/channels/{channel_id}")


@router.get("/channels/{channel_id}/aggregate")
async def get_catalog_channel_aggregate(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path=f"/api/channels/{channel_id}/aggregate")


@router.put("/channels/{channel_id}")
async def update_catalog_channel(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="PUT",
        path=f"/api/channels/{channel_id}",
        action=AccountPoolActorAction.CHANNEL_UPDATE,
    )


@router.post("/channels/{channel_id}/detach")
async def detach_catalog_channel(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="POST",
        path=f"/api/channels/{channel_id}/detach",
        action=AccountPoolActorAction.CHANNEL_DETACH,
    )


@router.delete("/channels/{channel_id}")
async def delete_catalog_channel(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="DELETE",
        path=f"/api/channels/{channel_id}",
        action=AccountPoolActorAction.CHANNEL_DELETE,
    )


@router.post("/channels/{channel_id}/bindings/{binding_id}/delete-external-deployment")
async def delete_external_channel_deployment(
    channel_id: UUID,
    binding_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="POST",
        path=f"/api/channels/{channel_id}/bindings/{binding_id}/delete-external-deployment",
        action=AccountPoolActorAction.CHANNEL_DELETE_EXTERNAL_DEPLOYMENT,
    )


@router.post("/channels/{channel_id}/reconcile")
async def reconcile_catalog_channel(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="POST",
        path=f"/api/channels/{channel_id}/reconcile",
        action=AccountPoolActorAction.CHANNEL_RECONCILE,
    )


@router.post("/channels/{channel_id}/health-probe")
async def probe_catalog_channel_health(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="POST",
        path=f"/api/channels/{channel_id}/health-probe",
        action=AccountPoolActorAction.HEALTH_PROBE,
    )


@router.get("/channels/{channel_id}/health")
async def get_catalog_channel_health(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path=f"/api/channels/{channel_id}/health")


@router.get("/operations/{operation_id}")
async def get_channel_operation(
    operation_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path=f"/api/operations/{operation_id}")


@router.get("/accounts")
async def list_pool_accounts(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path="/api/accounts")


@router.post("/accounts")
async def create_pool_account(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="POST", path="/api/accounts")


@router.put("/accounts/{account_id}")
async def update_pool_account(
    account_id: str,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    encoded_account_id: Final = quote(account_id, safe="")
    return await _forward(request=request, method="PUT", path=f"/api/accounts/{encoded_account_id}")


@router.delete("/accounts/{account_id}")
async def delete_pool_account(
    account_id: str,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    encoded_account_id: Final = quote(account_id, safe="")
    return await _forward(request=request, method="DELETE", path=f"/api/accounts/{encoded_account_id}")


@router.get("/models")
async def list_pool_models(
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path="/api/models")


@router.get("/models/{model:path}/routing-table")
async def get_model_routing_table(
    model: str,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    encoded_model: Final = quote(model, safe="")
    return await _forward(request=request, method="GET", path=f"/api/models/{encoded_model}/routing-table")


@router.get("/models/{model:path}/routing-policy")
async def get_model_routing_policy(
    model: str,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    encoded_model: Final = quote(model, safe="")
    return await _forward(request=request, method="GET", path=f"/api/models/{encoded_model}/routing-policy")


@router.put("/models/{model:path}/routing-policy")
async def update_model_routing_policy(
    model: str,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    encoded_model: Final = quote(model, safe="")
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="PUT",
        path=f"/api/models/{encoded_model}/routing-policy",
        action=AccountPoolActorAction.ROUTING_POLICY_UPDATE,
    )


@router.put("/models/{model:path}/routing-candidates/{binding_id}")
async def update_model_routing_candidate(
    model: str,
    binding_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    encoded_model: Final = quote(model, safe="")
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="PUT",
        path=f"/api/models/{encoded_model}/routing-candidates/{binding_id}",
        action=AccountPoolActorAction.ROUTING_CANDIDATE_UPDATE,
    )


@router.put("/models/{model:path}/routing-order")
async def update_model_routing_order(
    model: str,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    encoded_model: Final = quote(model, safe="")
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="PUT",
        path=f"/api/models/{encoded_model}/routing-order",
        action=AccountPoolActorAction.ROUTING_ORDER_UPDATE,
    )


@router.delete("/models/{model:path}/routing-candidates/{binding_id}")
async def delete_model_routing_candidate(
    model: str,
    binding_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    encoded_model: Final = quote(model, safe="")
    return await _forward_channel_mutation(
        request=request,
        user_api_key_dict=user_api_key_dict,
        method="DELETE",
        path=f"/api/models/{encoded_model}/routing-candidates/{binding_id}",
        action=AccountPoolActorAction.ROUTING_CANDIDATE_DELETE,
    )


@router.get("/channels/{channel_id}/parser-runs")
async def list_channel_parser_runs(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path=f"/api/channels/{channel_id}/parser-runs")


@router.post("/channels/{channel_id}/parse")
async def start_channel_parser_task(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    actor: Final = _actor_for_request(
        request=request,
        user_api_key_dict=user_api_key_dict,
        action=AccountPoolActorAction.PARSER_START,
    )
    return await _forward(
        request=request,
        method="POST",
        path=f"/api/channels/{channel_id}/parse",
        actor=actor,
    )


@router.get("/channels/{channel_id}/parser-tasks/{task_id}")
async def get_channel_parser_task(
    channel_id: UUID,
    task_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(
        request=request,
        method="GET",
        path=f"/api/channels/{channel_id}/parser-tasks/{task_id}",
    )


@router.get("/channels/{channel_id}/effective-data")
async def get_channel_effective_data(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path=f"/api/channels/{channel_id}/effective-data")


@router.get("/channels/{channel_id}/snapshot")
async def get_channel_parser_snapshot(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path=f"/api/channels/{channel_id}/snapshot")


@router.get("/channels/{channel_id}/export")
async def export_channel_parser_snapshot(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path=f"/api/channels/{channel_id}/export")


@router.post("/channels/{channel_id}/import")
async def import_channel_parser_snapshot(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    actor: Final = _actor_for_request(
        request=request,
        user_api_key_dict=user_api_key_dict,
        action=AccountPoolActorAction.SNAPSHOT_IMPORT,
    )
    return await _forward(
        request=request,
        method="POST",
        path=f"/api/channels/{channel_id}/import",
        actor=actor,
    )


@router.put("/channels/{channel_id}/overrides")
async def set_channel_parser_override(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    actor: Final = _actor_for_request(
        request=request,
        user_api_key_dict=user_api_key_dict,
        action=AccountPoolActorAction.OVERRIDE_SET,
    )
    return await _forward(
        request=request,
        method="PUT",
        path=f"/api/channels/{channel_id}/overrides",
        actor=actor,
    )


@router.delete("/channels/{channel_id}/overrides/{field_path:path}")
async def revoke_channel_parser_override(
    channel_id: UUID,
    field_path: str,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    actor: Final = _actor_for_request(
        request=request,
        user_api_key_dict=user_api_key_dict,
        action=AccountPoolActorAction.OVERRIDE_REVOKE,
    )
    encoded_path: Final = quote(field_path.strip("/"), safe="/")
    return await _forward(
        request=request,
        method="DELETE",
        path=f"/api/channels/{channel_id}/overrides/{encoded_path}",
        actor=actor,
    )


def _actor_for_request(
    request: Request,
    user_api_key_dict: UserAPIKeyAuth,
    action: AccountPoolActorAction,
) -> ActorEnvelope:
    supplied_request_id: Final = request.headers.get("x-request-id")
    request_id: Final = supplied_request_id or str(uuid4())
    role: Final = None if user_api_key_dict.user_role is None else user_api_key_dict.user_role.value
    result: Final = sign_actor_envelope(
        user_id=user_api_key_dict.user_id,
        role=role,
        request_id=request_id,
        action=action,
        secret=os.environ.get("ACCOUNT_POOL_ACTOR_SECRET"),
    )
    if isinstance(result, ActorEnvelope):
        return result
    if result.code == ActorSigningFailureCode.CONFIGURATION:
        raise HTTPException(status_code=503, detail="ACCOUNT_POOL_ACTOR_SECRET is not configured securely")
    if result.code == ActorSigningFailureCode.INVALID_REQUEST_ID:
        raise HTTPException(status_code=400, detail="x-request-id is invalid")
    raise HTTPException(status_code=403, detail="Authenticated administrator identity is unavailable")
