"""本模块为卡片 Key 提供 LiteLLM 公共模型入口，转发与普通 Key 及管理接口分离。"""

from __future__ import annotations

import hashlib
import io
import os
from collections.abc import Callable
from typing import Final

import httpx
from pydantic import JsonValue, TypeAdapter
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

from litellm.proxy.management_endpoints.account_pool_gateway_client import ControlError, GatewayControl, ManagerControl
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import Resolution, ResolveRequest
from litellm.proxy.management_endpoints.account_pool_gateway_forwarder import forward
from litellm.proxy.management_endpoints.account_pool_routing import Rejected, routes

_BODY: Final = TypeAdapter(dict[str, JsonValue])
_PATHS: Final = frozenset(("/v1/chat/completions", "/v1/responses", "/v1/responses/compact", "/v1/images/generations"))
_MAX_BODY: Final = 16 * 1024 * 1024


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
    ) -> None:
        self.app: Final = app
        self.control_factory: Final = control_factory
        self.client_factory: Final = client_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers: Final = Headers(scope=scope)
        scheme, _, key = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not key.strip().startswith("cpk_"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await WebSocket(scope, receive, send).close(code=1008, reason="Card WebSocket transport is not supported")
            return
        request: Final = Request(scope, receive)
        if not 16 <= len(key.strip()) <= 256:
            await error_response(401, "Invalid account pool key")(scope, receive, send)
            return
        path: Final = request.url.path
        if path == "/v1/models" and request.method == "GET":
            await self.models(request, key.strip(), send)
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
            payload: Final = await read_payload(request)
            model: Final = payload.get("model")
            if not isinstance(model, str) or not model.strip() or len(model) > 256:
                raise ValueError("Invalid model")
            async with self.client_factory() as client:
                control: Final = self.control_factory(client)
                session: Final = session_hash(headers, model)
                resolution: Final = await control.resolve(ResolveRequest(card_key=key.strip(), session_hash=session))
                image_tools: Final = payload.get("tools")
                has_images: Final = isinstance(image_tools, list) and any(
                    isinstance(item, dict) and item.get("type") == "image_generation" for item in image_tools
                )
                stream: Final = payload.get("stream") is True
                selected: Final = routes(resolution, model, path, headers, has_images, stream)
                if isinstance(selected, Rejected):
                    await error_response(selected.status, selected.message)(scope, receive, send)
                    return
                await forward(request, payload, key.strip(), session, resolution, selected, control, client, send)
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

    async def models(self, request: Request, key: str, send: Send) -> None:
        try:
            async with self.client_factory() as client:
                resolution: Final = await self.control_factory(client).resolve(ResolveRequest(card_key=key))
            response: Final = JSONResponse(
                {
                    "object": "list",
                    "data": [
                        {"id": name, "object": "model", "created": 0, "owned_by": "account-pool"}
                        for name in model_names(resolution)
                    ],
                },
                headers={"Cache-Control": "no-store"},
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
    session: Final = headers.get("x-session-id") or headers.get("session_id")
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


def error_response(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "account_pool_error"}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )
