"""统一号池请求身份与内部转发授权；客户端不能指定内部卡片身份或绕过标准鉴权。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import time
from collections.abc import Mapping
from contextvars import ContextVar
from types import MappingProxyType
from typing import Final, Literal, Protocol, TypedDict, runtime_checkable
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from starlette.requests import Request
from typing_extensions import ReadOnly

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import Resolution, ResolveRequest
from litellm.proxy.management_endpoints.account_pool_management_models import CardKeyStatus
from litellm.repositories.table_repositories import DeletedVerificationTokenRepository
from litellm.repositories.verification_token_repository import VerificationTokenRepository
from litellm.types.router import RetryPolicy, UpdateRouterConfig

INTERNAL_PREFIX: Final = "/account_pool/internal/forward/"
CARD_ROUTES: Final = (
    "/models",
    "/chat/completions",
    "/responses",
    "/responses/compact",
    "/images/generations",
    "/v1/models",
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/responses/compact",
    "/v1/images/generations",
    "/v1/realtime",
)


class PoolIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)
    key_hash: str
    request_id: UUID
    card_id: UUID | None = None
    binding_id: UUID | None = None
    headers: tuple[tuple[str, str], ...] = ()
    router_settings: UpdateRouterConfig | None = None


class ForwardRetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    rate_limit: int = Field(ge=0, le=4)
    timeout: int = Field(ge=0, le=4)
    server_error: int = Field(ge=0, le=4)
    backoff_ms: int = Field(ge=0, le=60000)

    def retries(self, status: int) -> int:
        if status == 429:
            return self.rate_limit
        if status in (408, 504):
            return self.timeout
        return self.server_error if status in (500, 502, 503) else 0


def forwarding_retry_policy(
    identity: PoolIdentity,
    model: str,
    default_retries: int,
    retry_policy: RetryPolicy | None,
    model_policies: Mapping[str, RetryPolicy] | None,
    retry_after: float,
) -> ForwardRetryPolicy:
    key: Final = identity.router_settings
    groups: Final = key.model_group_retry_policy if key and key.model_group_retry_policy is not None else model_policies
    global_policy: Final = key.retry_policy if key and key.retry_policy is not None else retry_policy
    policy: Final = (groups or {}).get(model, global_policy) or RetryPolicy()
    retries: Final = key.num_retries if key and key.num_retries is not None else default_retries
    delay: Final = key.retry_after if key and key.retry_after is not None else retry_after
    return ForwardRetryPolicy(
        rate_limit=max(
            0, min(4, policy.RateLimitErrorRetries if policy.RateLimitErrorRetries is not None else retries)
        ),
        timeout=max(0, min(4, policy.TimeoutErrorRetries if policy.TimeoutErrorRetries is not None else retries)),
        server_error=max(
            0, min(4, policy.InternalServerErrorRetries if policy.InternalServerErrorRetries is not None else retries)
        ),
        backoff_ms=max(0, min(60000, int(delay * 1000))),
    )


class ForwardTicket(BaseModel):
    model_config = ConfigDict(frozen=True)
    identity: PoolIdentity
    account_id: UUID
    expires: int
    retry_policy: ForwardRetryPolicy | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)


def forwarding_timeout(identity: PoolIdentity, params: Mapping[str, object], streaming: bool) -> int | None:
    key: Final = identity.router_settings
    selected: Final = (
        key.timeout
        if key is not None and key.timeout is not None
        else params.get("stream_timeout", params.get("timeout"))
        if streaming
        else params.get("timeout")
    )
    if not isinstance(selected, (int, float)) or not math.isfinite(selected) or selected <= 0:
        return None
    return max(1, min(3600, math.ceil(selected)))


class CardScope(BaseModel):
    card_id: UUID | None = Field(default=None, alias="account_pool_card_id")
    binding_id: UUID | None = Field(default=None, alias="account_pool_binding_id")


class KeyAuthScope(BaseModel):
    metadata: CardScope = Field(default_factory=CardScope)
    router_settings: UpdateRouterConfig | None = None


class LegacyRouteScope(BaseModel):
    metadata: CardScope = Field(default_factory=CardScope)
    allowed_routes: tuple[str, ...] | None = None


def legacy_card_route_alias_allowed(route: str, auth: UserAPIKeyAuth) -> bool:
    if route not in CARD_ROUTES:
        return False
    try:
        scope: Final = LegacyRouteScope.model_validate(auth, from_attributes=True)
    except ValueError:
        return False
    if scope.metadata.card_id is None or scope.metadata.binding_id is None:
        return False
    alias: Final = route.removeprefix("/v1") if route.startswith("/v1/") else "/v1" + route
    return alias in CARD_ROUTES and alias in (scope.allowed_routes or ())


class CardName(BaseModel):
    name: str


async def validate_key_account_scope(
    metadata: Mapping[str, object] | None,
    existing_metadata: Mapping[str, object] | None,
    auth: UserAPIKeyAuth,
) -> None:
    from litellm.proxy.common_utils.resource_ownership import is_proxy_admin
    from litellm.proxy.management_endpoints.account_pool_gateway import http_client

    supplied: Final = metadata or {}
    existing: Final = existing_metadata or {}
    for field in ("account_pool_card_id", "account_pool_binding_id"):
        if field in existing and field in supplied and supplied[field] != existing[field]:
            raise HTTPException(400, "Account binding is immutable; create a new virtual key for another account")
    if "account_pool_binding_id" in supplied and supplied["account_pool_binding_id"] != existing.get(
        "account_pool_binding_id"
    ):
        raise HTTPException(400, "Legacy account binding IDs are managed by the server")
    if (
        "account_pool_card_id" not in supplied
        or supplied["account_pool_card_id"] is None
        or existing.get("account_pool_card_id")
    ):
        return
    if not is_proxy_admin(auth):
        raise HTTPException(403, "Only proxy admins can bind a virtual key to an account")
    try:
        scope: Final = CardScope.model_validate(supplied)
    except ValueError:
        raise HTTPException(400, "Invalid account ID") from None
    try:
        async with http_client() as client:
            response: Final = await client.get(
                os.getenv("ACCOUNT_POOL_MANAGER_URL", "http://account-pool:8091").rstrip("/")
                + f"/api/environments/{scope.card_id}",
                headers={"Authorization": "Bearer " + signing_key().decode()},
            )
    except httpx.HTTPError:
        raise HTTPException(503, "Account validation is unavailable") from None
    if response.status_code == 404:
        raise HTTPException(400, "Account does not exist")
    if response.status_code != 200:
        raise HTTPException(503, "Account validation is unavailable")
    try:
        CardName.model_validate_json(response.content)
    except ValueError:
        raise HTTPException(503, "Account validation is unavailable") from None


class CompletedPoolAttempt(BaseModel):
    request_id: UUID = Field(alias="llm_provider-x-account-pool-request-id")
    account_id: UUID = Field(alias="llm_provider-x-account-pool-account-id")
    attempt: int = Field(alias="llm_provider-x-account-pool-attempt", ge=1, le=10)


def completed_pool_metadata(metadata: Mapping[str, object], headers: object) -> dict[str, object]:
    if not isinstance(headers, Mapping) or metadata.get("account_pool_request_id") is None:
        return dict(metadata)
    try:
        completed: Final = CompletedPoolAttempt.model_validate(headers)
    except ValueError:
        return dict(metadata)
    if str(completed.request_id) != metadata["account_pool_request_id"]:
        return dict(metadata)
    return {
        **metadata,
        "account_pool_account_id": str(completed.account_id),
        "account_pool_attempt_count": completed.attempt,
    }


class ScopeMetadata(TypedDict):
    account_pool_card_id: ReadOnly[str]
    account_pool_binding_id: ReadOnly[str]


class TokenFilter(TypedDict):
    token: ReadOnly[str]


class BlockedKey(TypedDict):
    blocked: ReadOnly[bool]


@runtime_checkable
class KeyCreator(Protocol):
    async def __call__(
        self,
        *,
        request_type: Literal["key"],
        table_name: Literal["key"],
        token: str,
        key_alias: str,
        metadata: ScopeMetadata,
        allowed_routes: tuple[str, ...],
    ) -> object: ...


@runtime_checkable
class DeletedTokens(Protocol):
    async def find_first(self, *, where: Mapping[str, object]) -> object | None: ...


@runtime_checkable
class KeyCache(Protocol):
    def delete_cache(self, key: str) -> None: ...


@runtime_checkable
class DeletedTokenSource(Protocol):
    @property
    def table(self) -> object: ...


def deleted_tokens(value: object) -> DeletedTokens:
    if not isinstance(value, DeletedTokens):
        raise HTTPException(503, "Deleted key repository unavailable")
    return value


def deleted_token_source(value: object) -> DeletedTokens:
    if not isinstance(value, DeletedTokenSource):
        raise HTTPException(503, "Deleted key repository unavailable")
    return deleted_tokens(value.table)


def key_cache(value: object) -> KeyCache:
    if not isinstance(value, KeyCache):
        raise HTTPException(503, "Virtual key cache unavailable")
    return value


pool_identity: Final[ContextVar[PoolIdentity | None]] = ContextVar("account_pool_identity", default=None)


def bind_router_settings(settings: Mapping[str, object]) -> None:
    identity: Final = pool_identity.get()
    if identity is not None:
        pool_identity.set(identity.model_copy(update={"router_settings": UpdateRouterConfig.model_validate(settings)}))


def signing_key() -> bytes:
    secret: Final = os.getenv("ACCOUNT_POOL_MANAGER_TOKEN", "")
    if len(secret) < 32:
        raise HTTPException(503, "Account pool integration is not configured")
    return secret.encode()


def create_ticket(
    identity: PoolIdentity,
    account_id: UUID,
    retry_policy: ForwardRetryPolicy | None = None,
    timeout_seconds: int | None = None,
) -> str:
    body: Final = ForwardTicket(
        identity=identity.model_copy(update={"router_settings": None}),
        account_id=account_id,
        expires=int(time.time()) + 120,
        retry_policy=retry_policy,
        timeout_seconds=timeout_seconds,
    )
    encoded: Final = base64.urlsafe_b64encode(body.model_dump_json().encode()).decode()
    signature: Final = hmac.new(signing_key(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_ticket(raw: str, account_id: UUID) -> ForwardTicket:
    if len(raw) > 16384:
        raise HTTPException(401, "Invalid internal forwarding ticket")
    encoded, _, signature = raw.partition(".")
    expected: Final = hmac.new(signing_key(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Invalid internal forwarding ticket")
    ticket: Final = ForwardTicket.model_validate_json(base64.urlsafe_b64decode(encoded))
    if ticket.expires < time.time() or ticket.account_id != account_id:
        raise HTTPException(401, "Expired or mismatched internal forwarding ticket")
    if ticket.identity.card_id is not None and ticket.identity.card_id != account_id:
        raise HTTPException(403, "Deployment is outside the card scope")
    return ticket


def forwarding_base(account_id: UUID) -> str:
    return (
        os.getenv("ACCOUNT_POOL_INTERNAL_URL", "http://127.0.0.1:4000").rstrip("/")
        + INTERNAL_PREFIX
        + str(account_id)
        + "/v1"
    )


def check_deployment(environment_id: object) -> None:
    identity: Final = pool_identity.get()
    if identity is not None and identity.card_id is not None and str(environment_id) != str(identity.card_id):
        raise HTTPException(403, "Deployment is outside the card scope")


def bind_identity(request: Request, auth: UserAPIKeyAuth) -> None:
    scope: Final = KeyAuthScope.model_validate(auth, from_attributes=True)
    metadata: Final = scope.metadata
    resolution: Final[object] = getattr(request.state, "account_pool_resolution", None)
    card: Final = resolution.card_id if isinstance(resolution, Resolution) else metadata.card_id
    binding: Final = resolution.key_id if isinstance(resolution, Resolution) else metadata.binding_id
    if card is not None and request.url.path not in CARD_ROUTES:
        raise HTTPException(403, "This endpoint is not available to card keys")
    identity: Final = PoolIdentity(
        key_hash=auth.api_key or "master",
        request_id=uuid4(),
        card_id=card,
        binding_id=binding,
        router_settings=scope.router_settings,
        headers=tuple(
            (name, value)
            for name, value in request.headers.items()
            if name
            in (
                "user-agent",
                "originator",
                "x-app",
                "x-litellm-session-id",
                "x-session-id",
                "session-id",
                "session_id",
                "conversation_id",
                "x-claude-code-session-id",
                "thread-id",
                "x-session-affinity",
            )
        ),
    )
    request.state.account_pool_request_id = identity.request_id  # rebind-ok: share ID with ASGI middleware
    pool_identity.set(identity)


async def resolve_card_key(key: str, request: Request | None) -> CardScope:
    import httpx

    from litellm.proxy.management_endpoints.account_pool_gateway import http_client, manager_control
    from litellm.proxy.management_endpoints.account_pool_gateway_client import ControlError

    try:
        async with http_client() as client:
            resolution: Final = await manager_control(client).resolve(ResolveRequest(card_key=key))
    except ControlError as error:
        raise HTTPException(error.status, "Card key is revoked or unavailable") from None
    except httpx.HTTPError:
        raise HTTPException(503, "Account pool control plane is unavailable") from None
    if request is not None:
        request.state.account_pool_resolution = resolution  # rebind-ok: reuse identity in standard auth
    return CardScope(account_pool_card_id=resolution.card_id, account_pool_binding_id=resolution.key_id)


async def register_card_key(key: str, request: Request | None = None, status: CardKeyStatus | None = None) -> None:
    from litellm.proxy.management_endpoints import key_management_endpoints
    from litellm.proxy.proxy_server import prisma_client

    if prisma_client is None:
        raise HTTPException(503, "Account pool virtual keys require the LiteLLM database")
    hashed: Final = hashlib.sha256(key.encode()).hexdigest()
    repository: Final = VerificationTokenRepository(prisma_client)
    deleted_table: Final = deleted_token_source(DeletedVerificationTokenRepository(prisma_client))
    key_filter: Final[TokenFilter] = {"token": hashed}
    if await deleted_table.find_first(where=key_filter) is not None:
        raise HTTPException(401, "This virtual key was revoked")
    existing: Final = await repository.find_by_id(hashed)
    if existing is not None:
        return
    if status is None and not key.startswith("cpk_"):
        raise HTTPException(401, "This virtual key is not registered")
    scope: Final = (
        await resolve_card_key(key, request)
        if status is None
        else CardScope(account_pool_card_id=status.card_id, account_pool_binding_id=status.key_id)
    )
    create_key: Final = TypeAdapter(object).validate_python(vars(key_management_endpoints)["generate_key_helper_fn"])
    if not isinstance(create_key, KeyCreator):
        raise HTTPException(503, "Virtual key registration unavailable")
    from litellm.proxy.management_endpoints.account_pool_gateway import http_client

    async with http_client() as client:
        card_response: Final = await client.get(
            os.getenv("ACCOUNT_POOL_MANAGER_URL", "http://account-pool:8091").rstrip("/")
            + f"/api/environments/{scope.card_id}",
            headers=MappingProxyType({"Authorization": "Bearer " + signing_key().decode()}),
        )
        card_response.raise_for_status()
        card_name: Final = CardName.model_validate_json(card_response.content).name
    scope_metadata: Final[ScopeMetadata] = {
        "account_pool_card_id": str(scope.card_id),
        "account_pool_binding_id": str(scope.binding_id),
    }
    await create_key(
        request_type="key",
        table_name="key",
        token=key,
        key_alias=f"号池 {card_name}",
        metadata=scope_metadata,
        allowed_routes=CARD_ROUTES,
    )
    if await deleted_table.find_first(where=key_filter) is not None:
        blocked: Final[BlockedKey] = {"blocked": True}
        await repository.update(hashed, blocked, id_field="token")
        raise HTTPException(401, "This virtual key was revoked")


async def block_card_keys(binding_id: UUID) -> None:
    from litellm.proxy.proxy_server import prisma_client, user_api_key_cache

    if prisma_client is None:
        raise HTTPException(503, "LiteLLM database unavailable")
    repository: Final = VerificationTokenRepository(prisma_client)
    keys: Final = await repository.find_by_account_pool_binding(str(binding_id))
    cache: Final = key_cache(user_api_key_cache)
    blocked: Final[BlockedKey] = {"blocked": True}
    for key in keys:
        if key.token is not None:
            await repository.update(key.token, blocked, id_field="token")
            cache.delete_cache(key.token)
