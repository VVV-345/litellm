"""本模块转发号池 WebSocket 帧并确保租约在所有断线路径释放。"""

from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Literal, Protocol, TypedDict, cast
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from typing_extensions import ReadOnly
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.typing import Subprotocol

from litellm.litellm_core_utils.url_utils import validate_url
from litellm.proxy.management_endpoints.account_pool_gateway_client import GatewayControl
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import (
    AcquireRejected,
    AcquireRejectionReason,
    AcquireRequest,
    FinishRequest,
    Resolution,
)
from litellm.proxy.management_endpoints.account_pool_gateway_forwarder import (
    attempt_routing_reason,
    report,
    select_gateway_credential,
    upstream_request_headers,
)
from litellm.proxy.management_endpoints.account_pool_request_log import RequestLog
from litellm.proxy.management_endpoints.account_pool_routing import Route, upstream_url

_FRAME: Final = TypeAdapter(dict[str, JsonValue])
_SUBPROTOCOLS: Final = TypeAdapter(tuple[str, ...])
_MAX_MESSAGE: Final = 16 * 1024 * 1024
_SENSITIVE_QUERY_FIELDS: Final = frozenset(("api_key", "key", "token", "access_token"))


class _WebSocketDebugDetail(TypedDict):
    transport: ReadOnly[str]
    account_id: ReadOnly[str]
    supplier: ReadOnly[str]
    query_fields: ReadOnly[tuple[str, ...]]


class _ModelUpdate(TypedDict, total=False):
    model: ReadOnly[JsonValue]


class _ResponseUpdate(TypedDict, total=False):
    response: ReadOnly[JsonValue]


class _SessionUpdate(TypedDict, total=False):
    session: ReadOnly[JsonValue]


class _ClientMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    type: str
    code: int = 1000
    text: str | None = None
    binary: bytes | None = Field(default=None, validation_alias="bytes")


_EMPTY_MODEL_UPDATE: Final[_ModelUpdate] = {}
_EMPTY_RESPONSE_UPDATE: Final[_ResponseUpdate] = {}
_EMPTY_SESSION_UPDATE: Final[_SessionUpdate] = {}


class UpstreamWebSocket(Protocol):
    subprotocol: str | None

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


@dataclass(frozen=True, slots=True)
class WebSocketDialRequest:
    url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    subprotocols: tuple[Subprotocol, ...] = field(repr=False)
    timeout_seconds: int
    proxy_url: str | None = field(repr=False)


class WebSocketDialer(Protocol):
    def connect(self, request: WebSocketDialRequest) -> AbstractAsyncContextManager[UpstreamWebSocket]: ...


class DefaultWebSocketDialer:
    def connect(self, request: WebSocketDialRequest) -> AbstractAsyncContextManager[UpstreamWebSocket]:
        connection: Final = connect(
            request.url,
            additional_headers=request.headers,
            subprotocols=request.subprotocols or None,
            open_timeout=request.timeout_seconds,
            close_timeout=min(request.timeout_seconds, 10),
            max_size=_MAX_MESSAGE,
            max_queue=16,
            proxy=request.proxy_url,
        )
        return cast(  # cast-ok: websockets connect structurally implements this context-manager protocol
            AbstractAsyncContextManager[UpstreamWebSocket],
            connection,
        )


@dataclass(frozen=True, slots=True)
class _RelayResult:
    http_status: int
    message: str


class _Attempt:
    def __init__(self, lease_id: UUID, endpoint: str, detail: str | None) -> None:
        self.result = FinishRequest(
            lease_id=lease_id,
            endpoint=endpoint,
            method="GET",
            http_status=499,
            stage="response",
            message="WebSocket downstream disconnected",
            detail=detail,
        )

    def outcome(
        self,
        status: int,
        message: str,
        *,
        stage: Literal["connection", "upstream", "response"],
        retryable: bool = False,
        switched_account: bool = False,
        next_account_id: UUID | None = None,
    ) -> None:
        self.result = self.result.model_copy(
            update=MappingProxyType(
                {
                    "http_status": status,
                    "message": message,
                    "stage": stage,
                    "retryable": retryable,
                    "switched_account": switched_account,
                    "next_account_id": next_account_id,
                }
            )
        )


async def forward_websocket(
    websocket: WebSocket,
    key: str,
    public_model: str,
    session: str | None,
    resolution: Resolution,
    selected: tuple[Route, ...],
    control: GatewayControl,
    dialer: WebSocketDialer,
) -> None:
    request_id: Final = uuid4()
    completed, rejections = await _forward_candidate(
        websocket,
        key,
        public_model,
        session,
        resolution,
        selected,
        control,
        dialer,
        request_id,
        route_index=0,
        attempt_number=1,
        rejection_reasons=frozenset[AcquireRejectionReason](),
    )
    if completed:
        return
    code: Final = 1008 if rejections & frozenset(("configuration", "session")) else 1013
    await websocket.close(code=code, reason="No bound account is currently available")


async def _forward_candidate(
    websocket: WebSocket,
    key: str,
    public_model: str,
    session: str | None,
    resolution: Resolution,
    selected: tuple[Route, ...],
    control: GatewayControl,
    dialer: WebSocketDialer,
    request_id: UUID,
    route_index: int,
    attempt_number: int,
    rejection_reasons: frozenset[AcquireRejectionReason],
) -> tuple[bool, frozenset[AcquireRejectionReason]]:
    if route_index >= len(selected):
        return False, rejection_reasons
    route: Final = selected[route_index]
    seconds: Final = min(
        resolution.policy.transport.request_timeout_seconds,
        route.account.policy.transport.request_timeout_seconds,
    )
    sticky_unavailable: Final = resolution.sticky_account_id is not None and all(
        item.account.id != resolution.sticky_account_id for item in selected
    )
    acquisition: Final = AcquireRequest(
        card_key=key,
        session_hash=session,
        account_id=route.account.id,
        request_id=request_id,
        model=route.model,
        card_version=resolution.card_version,
        policy_version=resolution.policy_version,
        account_version=route.account.environment_version,
        account_policy_version=route.account.policy_version,
        timeout_seconds=seconds,
        attempt=attempt_number,
        routing_reason=attempt_routing_reason(
            route,
            route_index,
            attempt_number,
            sticky_unavailable,
            "token_budget" in rejection_reasons,
        ),
        allow_session_rebind=route_index > 0 or sticky_unavailable,
    )
    debug_detail: Final = (
        _safe_debug_detail(websocket, route) if resolution.policy.transport.debug_log_enabled else None
    )
    lease: Final = await control.acquire(acquisition)
    if isinstance(lease, AcquireRejected):
        return await _forward_candidate(
            websocket,
            key,
            public_model,
            session,
            resolution,
            selected,
            control,
            dialer,
            request_id,
            route_index + 1,
            attempt_number,
            rejection_reasons | frozenset((lease.reason,)),
        )
    log: Final = RequestLog(lease, route, resolution, websocket.headers, {"model": public_model}, key, "websocket")
    attempt: Final = _Attempt(lease.lease_id, websocket.url.path, debug_detail)
    fallback: Final = (
        resolution.policy.routing.fallback_enabled
        and attempt_number < resolution.policy.routing.max_attempts
        and route_index + 1 < len(selected)
    )
    try:
        completed: Final = await _connect_and_relay(
            websocket,
            public_model,
            resolution,
            selected,
            dialer,
            request_id,
            route_index,
            seconds,
            route,
            fallback,
            attempt,
            log,
        )
    except asyncio.CancelledError:
        attempt.outcome(499, "WebSocket request cancelled", stage="response")
        raise
    except WebSocketDisconnect:
        attempt.outcome(499, "WebSocket downstream disconnected", stage="response")
        return True, frozenset[AcquireRejectionReason]()
    except Exception:
        attempt.outcome(502, "WebSocket forwarding failed", stage="connection")
        raise
    finally:
        await asyncio.shield(_finish_attempt(control, attempt, log))
    if completed:
        return True, frozenset[AcquireRejectionReason]()
    return await _forward_candidate(
        websocket,
        key,
        public_model,
        session,
        resolution,
        selected,
        control,
        dialer,
        request_id,
        route_index + 1,
        attempt_number + 1,
        frozenset[AcquireRejectionReason](),
    )


async def _finish_attempt(control: GatewayControl, attempt: _Attempt, log: RequestLog) -> None:
    try:
        attempt.result = await log.finish(attempt.result)
    finally:
        await report(control, attempt.result)


async def _connect_and_relay(
    websocket: WebSocket,
    public_model: str,
    resolution: Resolution,
    selected: tuple[Route, ...],
    dialer: WebSocketDialer,
    request_id: UUID,
    route_index: int,
    seconds: int,
    route: Route,
    fallback: bool,
    attempt: _Attempt,
    log: RequestLog,
) -> bool:
    credential: Final = select_gateway_credential(route.account.credentials, request_id, route.account.api_key)
    upstream_model: Final = (
        route.model.removeprefix(route.account.model_prefix)
        if route.account.model_prefix and route.model.startswith(route.account.model_prefix)
        else route.model
    )
    raw_subprotocols: Final[object] = websocket.scope.get("subprotocols", ())
    request: Final = WebSocketDialRequest(
        url=await _websocket_url(websocket, route, upstream_model),
        headers=_websocket_request_headers(
            websocket,
            route,
            credential.api_key,
        ),
        subprotocols=tuple(Subprotocol(value) for value in _SUBPROTOCOLS.validate_python(raw_subprotocols)),
        timeout_seconds=seconds,
        proxy_url=credential.proxy_url,
    )
    try:
        async with dialer.connect(request) as upstream:
            await websocket.accept(
                subprotocol=upstream.subprotocol if upstream.subprotocol in request.subprotocols else None,
                headers=((b"x-account-pool-request-id", str(request_id).encode()),),
            )
            outcome: Final = await _relay(websocket, upstream, public_model, upstream_model, log)
            attempt.outcome(outcome.http_status, outcome.message, stage="response")
            return True
    except (OSError, TimeoutError, WebSocketException):
        attempt.outcome(
            502,
            "WebSocket upstream connection failed",
            stage="connection",
            retryable=fallback,
            switched_account=fallback,
            next_account_id=selected[route_index + 1].account.id if fallback else None,
        )
        if fallback:
            return False
        await websocket.close(code=1011, reason="WebSocket upstream connection failed")
        return True


async def _websocket_url(websocket: WebSocket, route: Route, upstream_model: str) -> str:
    destination: Final = upstream_url(route.account, websocket.url.path)
    validated: Final = (
        (await asyncio.to_thread(validate_url, destination))[0]
        if route.account.supplier == "openai_compatible"
        else destination
    )
    parsed: Final = urlsplit(validated)
    query: Final = tuple(
        (name, upstream_model if name == "model" else value) for name, value in websocket.query_params.multi_items()
    )
    effective_query: Final = query if any(name == "model" for name, _ in query) else (*query, ("model", upstream_model))
    scheme: Final = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, parsed.path, urlencode(effective_query), ""))


async def _relay(
    websocket: WebSocket,
    upstream: UpstreamWebSocket,
    public_model: str,
    upstream_model: str,
    log: RequestLog | None = None,
) -> _RelayResult:
    downstream_task: Final = asyncio.create_task(
        _downstream_to_upstream(websocket, upstream, public_model, upstream_model, log)
    )
    upstream_task: Final = asyncio.create_task(
        _upstream_to_downstream(websocket, upstream, public_model, upstream_model, log)
    )
    done, pending = await asyncio.wait((downstream_task, upstream_task), return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    results: Final = await asyncio.gather(*done, return_exceptions=True)
    error: Final = next((item for item in results if isinstance(item, BaseException)), None)
    if error is not None:
        raise error
    result: Final = results[0]
    if not isinstance(result, _RelayResult):
        raise RuntimeError("WebSocket relay returned an invalid result")
    return result


async def _downstream_to_upstream(
    websocket: WebSocket,
    upstream: UpstreamWebSocket,
    public_model: str,
    upstream_model: str,
    log: RequestLog | None = None,
) -> _RelayResult:
    try:
        while True:
            message: Final = _ClientMessage.model_validate(  # pyright: ignore[reportGeneralTypeIssues]  # loop-local frame
                await websocket.receive()
            )
            if message.type == "websocket.disconnect":
                code: Final = (  # pyright: ignore[reportGeneralTypeIssues]  # loop-local close code
                    message.code
                )
                await upstream.close(code=_safe_close_code(code))
                return _RelayResult(200 if code in (1000, 1001) else 499, "WebSocket downstream disconnected")
            data: Final = (  # pyright: ignore[reportGeneralTypeIssues]  # loop-local payload
                message.text if message.text is not None else message.binary
            )
            if not isinstance(data, (str, bytes)):
                continue
            if _message_size(data) > _MAX_MESSAGE:
                await upstream.close(code=1009, reason="Message too large")
                await websocket.close(code=1009, reason="Message too large")
                return _RelayResult(413, "WebSocket message exceeds 16 MiB")
            try:
                rewritten: Final = _rewrite_model(  # pyright: ignore[reportGeneralTypeIssues]  # loop-local frame
                    data, public_model, upstream_model, strict=True
                )
            except ValueError:
                await upstream.close(code=1008, reason="Routed model cannot be changed")
                if websocket.client_state is not WebSocketState.DISCONNECTED:
                    await websocket.close(code=1008, reason="Routed model cannot be changed")
                return _RelayResult(400, "WebSocket frame changes the routed model")
            if log is not None:
                log.observe_websocket(rewritten, incoming=True)
            await upstream.send(rewritten)
    except WebSocketDisconnect as error:
        await upstream.close(code=_safe_close_code(error.code))
        return _RelayResult(200 if error.code in (1000, 1001) else 499, "WebSocket downstream disconnected")


async def _upstream_to_downstream(
    websocket: WebSocket,
    upstream: UpstreamWebSocket,
    public_model: str,
    upstream_model: str,
    log: RequestLog | None = None,
) -> _RelayResult:
    try:
        while True:
            data: Final = await upstream.recv()  # pyright: ignore[reportGeneralTypeIssues]  # loop-local frame
            if _message_size(data) > _MAX_MESSAGE:
                await websocket.close(code=1009, reason="Message too large")
                return _RelayResult(502, "WebSocket upstream message exceeds 16 MiB")
            if log is not None:
                log.observe_websocket(data)
            public: Final = _rewrite_model(  # pyright: ignore[reportGeneralTypeIssues]  # loop-local frame
                data, upstream_model, public_model, strict=False
            )
            if isinstance(public, str):
                await websocket.send_text(public)
            else:
                await websocket.send_bytes(public)
    except ConnectionClosed as error:
        code: Final = _safe_close_code(error.code)
        if websocket.client_state is not WebSocketState.DISCONNECTED:
            await websocket.close(code=code)
        return _RelayResult(200 if code in (1000, 1001) else 502, "WebSocket upstream disconnected")


def _rewrite_model(message: str | bytes, expected: str, replacement: str, *, strict: bool) -> str | bytes:
    raw: Final = message.encode() if isinstance(message, str) else message
    try:
        payload: Final = _FRAME.validate_json(raw)
    except ValueError:
        return message
    direct_model: Final = payload.get("model")
    response: Final = payload.get("response")
    session: Final = payload.get("session")
    nested_model: Final = response.get("model") if isinstance(response, dict) else None
    session_model: Final = session.get("model") if isinstance(session, dict) else None
    if strict and any(
        value not in (None, expected, replacement) for value in (direct_model, nested_model, session_model)
    ):
        raise ValueError("WebSocket frame changes the routed model")
    rewritten_response: Final = _rewrite_nested_model(response, expected, replacement)
    rewritten_session: Final = _rewrite_nested_model(session, expected, replacement)
    replacement_model_update: Final[_ModelUpdate] = {"model": replacement}
    model_update: Final = replacement_model_update if direct_model == expected else _EMPTY_MODEL_UPDATE
    replacement_response_update: Final[_ResponseUpdate] = {"response": rewritten_response}
    response_update: Final[_ResponseUpdate] = (
        replacement_response_update if rewritten_response is not response else _EMPTY_RESPONSE_UPDATE
    )
    replacement_session_update: Final[_SessionUpdate] = {"session": rewritten_session}
    session_update: Final[_SessionUpdate] = (
        replacement_session_update if rewritten_session is not session else _EMPTY_SESSION_UPDATE
    )
    rewritten: Final = {  # mutable-ok: protocol frames must preserve arbitrary provider-defined JSON fields
        **payload,
        **model_update,
        **response_update,
        **session_update,
    }
    if rewritten == payload:
        return message
    encoded: Final = json.dumps(rewritten, separators=(",", ":"), ensure_ascii=False)
    return encoded if isinstance(message, str) else encoded.encode()


def _rewrite_nested_model(value: JsonValue, expected: str, replacement: str) -> JsonValue:
    if not isinstance(value, dict) or value.get("model") != expected:
        return value
    return {**value, "model": replacement}  # mutable-ok: JSON serialization requires a concrete mapping


def _message_size(message: str | bytes) -> int:
    return len(message.encode()) if isinstance(message, str) else len(message)


def _safe_debug_detail(websocket: WebSocket, route: Route) -> str:
    query_fields: Final = tuple(
        sorted(
            frozenset(
                name[:80]
                for name in websocket.query_params
                if name.lower() not in _SENSITIVE_QUERY_FIELDS
                and name.replace("-", "").replace("_", "").replace(".", "").isalnum()
            )
        )
    )
    detail: Final[_WebSocketDebugDetail] = {
        "transport": "websocket",
        "account_id": str(route.account.id),
        "supplier": route.account.supplier,
        "query_fields": query_fields,
    }
    return json.dumps(
        detail,
        separators=(",", ":"),
        sort_keys=True,
    )


def _websocket_request_headers(websocket: WebSocket, route: Route, api_key: str) -> tuple[tuple[str, str], ...]:
    provider_header_names: Final = frozenset(name.lower() for name, _ in route.account.headers)
    client_headers: Final = tuple(
        (name, value)
        for name, value in upstream_request_headers(websocket.headers)
        if name.lower() not in provider_header_names
    )
    provider_headers: Final = tuple(
        (name, value) for name, value in route.account.headers if name.lower() != "authorization"
    )
    return (*client_headers, *provider_headers, ("authorization", f"Bearer {api_key}"))


def _safe_close_code(code: int) -> int:
    return code if code == 1000 or 1001 <= code <= 1014 else 1011


__all__ = (
    "DefaultWebSocketDialer",
    "UpstreamWebSocket",
    "WebSocketDialRequest",
    "WebSocketDialer",
    "forward_websocket",
)
