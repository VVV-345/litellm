"""本模块只接收 LiteLLM 已授权的内部转发，公共密钥走标准鉴权和请求处理。"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
from collections.abc import Callable
from types import MappingProxyType
from typing import Final, TypedDict
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from fastapi import HTTPException
from pydantic import JsonValue, TypeAdapter
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocket
from typing_extensions import ReadOnly

from litellm.proxy.management_endpoints.account_pool_gateway_client import ControlError, GatewayControl, ManagerControl
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import (
    AcquireRejected,
    AcquireRequest,
    FinishRequest,
    Lease,
    Resolution,
    ResolveRequest,
)
from litellm.proxy.management_endpoints.account_pool_gateway_forwarder import forward
from litellm.proxy.management_endpoints.account_pool_integration import (
    INTERNAL_PREFIX,
    ForwardTicket,
    pool_identity,
    verify_ticket,
)
from litellm.proxy.management_endpoints.account_pool_routing import Rejected, routes
from litellm.proxy.management_endpoints.account_pool_websocket import (
    DefaultWebSocketDialer,
    WebSocketDialer,
    forward_websocket,
)

_BODY: Final = TypeAdapter(dict[str, JsonValue])
_PATHS: Final = frozenset(("/v1/chat/completions", "/v1/responses", "/v1/responses/compact", "/v1/images/generations"))
_WEBSOCKET_PATHS: Final = frozenset(("/v1/responses", "/v1/realtime"))
_MAX_BODY: Final = 16 * 1024 * 1024
_NO_STORE_HEADERS: Final = MappingProxyType({"Cache-Control": "no-store"})
_SESSION_HEADERS: Final = (
    "x-litellm-session-id",
    "thread-id",
    "conversation_id",
    "x-claude-code-session-id",
    "x-session-id",
    "session-id",
    "session_id",
    "x-session-affinity",
    "x-client-request-id",
)


class _ModelEntry(TypedDict):
    id: ReadOnly[str]
    object: ReadOnly[str]
    created: ReadOnly[int]
    owned_by: ReadOnly[str]


class _ModelList(TypedDict):
    object: ReadOnly[str]
    data: ReadOnly[tuple[_ModelEntry, ...]]


class _ErrorDetail(TypedDict):
    message: ReadOnly[str]
    type: ReadOnly[str]


class _ErrorEnvelope(TypedDict):
    error: ReadOnly[_ErrorDetail]


class TrustedControl:
    def __init__(self, control: GatewayControl, ticket: ForwardTicket) -> None:
        self.control: Final = control
        self.ticket: Final = ticket

    def identity_fields(self) -> dict[str, object]:
        identity: Final = self.ticket.identity
        return {
            "card_key": "",
            "trusted_card_id": self.ticket.account_id,
            "trusted_key_id": identity.binding_id or uuid5(NAMESPACE_URL, identity.key_hash),
            "binding_id": identity.binding_id,
        }

    async def resolve(self, request: ResolveRequest) -> Resolution:
        return await self.control.resolve(
            ResolveRequest.model_validate({**request.model_dump(), **self.identity_fields()})
        )

    async def acquire(self, request: AcquireRequest) -> Lease | AcquireRejected:
        return await self.control.acquire(
            AcquireRequest.model_validate({**request.model_dump(), **self.identity_fields()})
        )

    async def finish(self, request: FinishRequest) -> None:
        await self.control.finish(request)


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=120, trust_env=False, follow_redirects=False)


def manager_control(client: httpx.AsyncClient) -> GatewayControl:
    token: Final = os.getenv("ACCOUNT_POOL_MANAGER_TOKEN", "")
    if len(token) < 32:
        raise ControlError(503)
    return ManagerControl(client, os.getenv("ACCOUNT_POOL_MANAGER_URL", "http://account-pool:8091"), token)


class AccountPoolGatewayMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        control_factory: Callable[[httpx.AsyncClient], GatewayControl] = manager_control,
        client_factory: Callable[[], httpx.AsyncClient] = http_client,
        websocket_dialer: WebSocketDialer | None = None,
    ) -> None:
        self.app: Final = app
        self.control_factory: Final = control_factory
        self.client_factory: Final = client_factory
        self.websocket_dialer: Final = websocket_dialer or DefaultWebSocketDialer()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        reset: Final = pool_identity.set(None)
        try:
            if scope["type"] in ("http", "websocket") and scope.get("path", "").startswith(INTERNAL_PREFIX):
                await self.internal(scope, receive, send)
            else:

                async def track_outcome(message: Message) -> None:
                    if (
                        message["type"] == "http.response.start"
                        and message["status"] >= 400
                        and os.getenv("ACCOUNT_POOL_MANAGER_TOKEN")
                        and scope.get("path") in _PATHS
                    ):
                        request_id: Final = scope.get("state", {}).get("account_pool_request_id")
                        if isinstance(request_id, UUID):
                            from litellm.proxy.management_endpoints.account_pool_full_logs import full_log_store

                            try:
                                await asyncio.to_thread(full_log_store().reject_request, request_id)
                            except Exception:
                                from litellm._logging import verbose_proxy_logger

                                verbose_proxy_logger.warning(
                                    "Account pool final outcome persistence failed: %s", request_id
                                )
                    await send(message)

                await self.app(scope, receive, track_outcome)
        finally:
            pool_identity.reset(reset)

    async def internal(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            account, _, path = scope["path"][len(INTERNAL_PREFIX) :].partition("/")
            ticket: Final = verify_ticket(
                Headers(scope=scope).get("authorization", "").removeprefix("Bearer "), UUID(account)
            )
            forwarded_scope: Final[Scope] = {
                **scope,
                "path": "/" + path,
                "raw_path": ("/" + path).encode(),
                "headers": [
                    *(
                        item
                        for item in scope["headers"]
                        if item[0].decode().lower() not in dict(ticket.identity.headers)
                    ),
                    *((name.encode(), value.encode()) for name, value in ticket.identity.headers),
                ],
                "state": {
                    **scope.get("state", {}),
                    "account_pool_request_id": ticket.identity.request_id,
                    "account_pool_standard_accounting": True,
                },
            }
            gateway: Final = AccountPoolGatewayMiddleware(
                self.app,
                lambda client: TrustedControl(self.control_factory(client), ticket),
                self.client_factory,
                self.websocket_dialer,
            )
            if scope["type"] == "websocket":
                await gateway._dispatch_websocket(forwarded_scope, receive, send, "internal-forward-key")
            else:
                await gateway._dispatch_http(forwarded_scope, receive, send, "internal-forward-key")
        except (HTTPException, ValueError) as error:
            if scope["type"] == "websocket":
                await WebSocket(scope, receive, send).close(code=1008)
            else:
                await error_response(
                    error.status_code if isinstance(error, HTTPException) else 400,
                    "Invalid internal account pool request",
                )(scope, receive, send)

    async def dispatch_card(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers: Final = Headers(scope=scope)
        scheme, _, key = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not key.strip().startswith("cpk_"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await self._dispatch_websocket(scope, receive, send, key.strip())
            return
        await self._dispatch_http(scope, receive, send, key.strip())

    async def _dispatch_websocket(self, scope: Scope, receive: Receive, send: Send, key: str) -> None:
        websocket: Final = WebSocket(scope, receive, send)
        if not 16 <= len(key) <= 256:
            await websocket.close(code=1008, reason="Invalid account pool key")
            return
        await self.websocket(websocket, key)

    async def _dispatch_http(self, scope: Scope, receive: Receive, send: Send, key: str) -> None:
        if not 16 <= len(key) <= 256:
            await error_response(401, "Invalid account pool key")(scope, receive, send)
            return
        request: Final = Request(scope, receive)
        headers: Final = request.headers
        path: Final = request.url.path
        if path == "/v1/models" and request.method == "GET":
            await self.models(request, key, send)
            return
        # 卡片 Key 不可降级到普通鉴权或管理接口，只允许明确支持的协议路径。
        if path not in _PATHS or request.method != "POST":
            await error_response(403, "This endpoint is not available to account pool keys")(scope, receive, send)
            return
        if headers.get("content-encoding", "identity") != "identity":
            await error_response(415, "Compressed request bodies are not supported")(scope, receive, send)
            return
        if headers.get("content-type", "").split(";")[0].strip() != "application/json":
            await error_response(415, "Use application/json")(scope, receive, send)
            return
        try:
            async with self.client_factory() as client:
                control: Final = self.control_factory(client)
                initial_resolution: Final = await control.resolve(ResolveRequest(card_key=key))
                payload: Final = await read_payload(request)
                model: Final = payload.get("model")
                if not isinstance(model, str) or not model.strip() or len(model) > 256:
                    raise ValueError("Invalid model")
                session: Final = session_hash(headers, model)
                resolution: Final = (
                    initial_resolution
                    if session is None
                    else await control.resolve(ResolveRequest(card_key=key, session_hash=session))
                )
                image_tools: Final = payload.get("tools")
                has_images: Final = isinstance(image_tools, list) and any(
                    isinstance(item, dict) and item.get("type") == "image_generation" for item in image_tools
                )
                stream: Final = payload.get("stream") is True
                selected: Final = routes(resolution, model, path, headers, has_images, stream)
                if isinstance(selected, Rejected):
                    await error_response(selected.status, selected.message)(scope, receive, send)
                    return
                await forward(request, payload, key, session, resolution, selected, control, client, send)
        except ClientDisconnect:
            return
        except ControlError as error:
            await error_response(error.status, "Account pool key or control plane is unavailable")(scope, receive, send)
        except ValueError:
            await error_response(400, "Invalid account pool request body")(scope, receive, send)
        except OverflowError:
            await error_response(413, "Request body exceeds 16 MiB")(scope, receive, send)
        except httpx.HTTPError:
            await error_response(503, "Account pool control plane is unavailable")(scope, receive, send)

    async def websocket(self, websocket: WebSocket, key: str) -> None:
        if websocket.url.path not in _WEBSOCKET_PATHS:
            await websocket.close(code=1008, reason="This endpoint is not available to account pool keys")
            return
        model: Final = websocket.query_params.get("model", "").strip()
        if not model or len(model) > 256:
            await websocket.close(code=1008, reason="A valid model query parameter is required")
            return
        try:
            async with self.client_factory() as client:
                control: Final = self.control_factory(client)
                initial_resolution: Final = await control.resolve(ResolveRequest(card_key=key))
                session: Final = session_hash(websocket.headers, model)
                resolution: Final = (
                    initial_resolution
                    if session is None
                    else await control.resolve(ResolveRequest(card_key=key, session_hash=session))
                )
                selected: Final = routes(
                    resolution,
                    model,
                    websocket.url.path,
                    websocket.headers,
                    websocket=True,
                )
                if isinstance(selected, Rejected):
                    await websocket.close(code=1008, reason=selected.message)
                    return
                await forward_websocket(
                    websocket,
                    key,
                    model,
                    session,
                    resolution,
                    selected,
                    control,
                    self.websocket_dialer,
                )
        except ControlError:
            await websocket.close(code=1013, reason="Account pool control plane is unavailable")
        except (ValueError, httpx.HTTPError):
            await websocket.close(code=1011, reason="Account pool WebSocket request failed")

    async def models(self, request: Request, key: str, send: Send) -> None:
        try:
            async with self.client_factory() as client:
                resolution: Final = await self.control_factory(client).resolve(ResolveRequest(card_key=key))
            models: Final = tuple(model_entry(name) for name in model_names(resolution))
            content: Final[_ModelList] = {"object": "list", "data": models}
            response: Final = JSONResponse(
                content,
                headers=_NO_STORE_HEADERS,
            )
            await response(request.scope, request.receive, send)
        except (ControlError, httpx.HTTPError, ValueError) as error:
            status: Final = error.status if isinstance(error, ControlError) else 503
            await error_response(status, "Account pool model catalog is unavailable")(
                request.scope, request.receive, send
            )


async def read_payload(request: Request) -> dict[str, JsonValue]:
    buffer: Final = io.BytesIO()
    async for chunk in request.stream():
        if buffer.tell() + len(chunk) > _MAX_BODY:
            raise OverflowError
        buffer.write(chunk)
    return _BODY.validate_json(buffer.getvalue())


def session_hash(headers: Headers, model: str) -> str | None:
    session: Final = next((value for name in _SESSION_HEADERS if (value := headers.get(name))), None)
    if not session:
        return None
    if len(session) > 512:
        raise ValueError("Session identifier is too long")
    return hashlib.sha256(f"{model}:{session}".encode()).hexdigest()


def model_names(resolution: Resolution) -> tuple[str, ...]:
    underlying: Final = frozenset(
        name
        for account in resolution.candidates
        for name in account.enabled_models
        if name not in account.policy.excluded_models and name not in resolution.policy.excluded_models
    )
    account_aliases: Final = frozenset(
        alias.alias
        for account in resolution.candidates
        for alias in account.policy.model_aliases
        if alias.target in account.enabled_models
        and alias.alias not in account.policy.excluded_models
        and alias.target not in account.policy.excluded_models
        and alias.alias not in resolution.policy.excluded_models
        and alias.target not in resolution.policy.excluded_models
    )
    aliases: Final = frozenset(
        alias.alias
        for alias in resolution.policy.model_aliases
        if alias.target in underlying | account_aliases and alias.alias not in resolution.policy.excluded_models
    )
    return tuple(sorted(underlying | account_aliases | aliases))


def model_entry(name: str) -> _ModelEntry:
    entry: Final[_ModelEntry] = {"id": name, "object": "model", "created": 0, "owned_by": "account-pool"}
    return entry


def error_response(status: int, message: str) -> JSONResponse:
    content: Final[_ErrorEnvelope] = {
        "error": {"message": message, "type": "account_pool_error"},
    }
    return JSONResponse(
        content,
        status_code=status,
        headers=_NO_STORE_HEADERS,
    )
