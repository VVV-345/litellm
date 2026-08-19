"""为 Admin UI 代理固定的 account-pool 管理接口，避免浏览器接触内部服务令牌。"""

from __future__ import annotations

import os
from typing import Annotated, Final, Literal, cast
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

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
) -> Response:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as client:
        return await _forward_with_client(
            request=request,
            method=method,
            path=path,
            client=client,
            actor=actor,
        )


async def _forward_with_client(
    request: Request,
    method: _Method,
    path: str,
    client: httpx.AsyncClient,
    actor: ActorEnvelope | None = None,
) -> Response:
    base_url: Final = os.environ.get("ACCOUNT_POOL_INTERNAL_URL", "http://127.0.0.1:4100").rstrip("/")
    internal_token: Final = os.environ.get("ACCOUNT_POOL_INTERNAL_TOKEN")
    if not internal_token:
        raise HTTPException(status_code=503, detail="ACCOUNT_POOL_INTERNAL_TOKEN is not configured")
    # 内部身份头只在服务端生成，浏览器提交的同名头不会被透传到 Account Pool。
    headers: Final = {
        "accept": "application/json",
        **({"content-type": "application/json"} if method in _BODY_METHODS else {}),
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
    content: Final = await request.body() if method in _BODY_METHODS else None
    query_bytes: Final = cast(bytes, request.scope.get("query_string", b""))
    query: Final = query_bytes.decode("ascii")
    upstream_url: Final = f"{base_url}{path}{f'?{query}' if query else ''}"
    try:
        upstream: Final = await client.request(
            method=method,
            url=upstream_url,
            headers=headers,
            content=content,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Account pool service is unavailable") from exc
    response_headers: Final = {
        name: value for name, value in upstream.headers.items() if name.lower() in _RESPONSE_HEADER_ALLOWLIST
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


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


@router.get("/channels/{channel_id}/parser-runs")
async def list_channel_parser_runs(
    channel_id: UUID,
    request: Request,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> Response:
    _require_proxy_admin(user_api_key_dict)
    return await _forward(request=request, method="GET", path=f"/api/channels/{channel_id}/parser-runs")


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
