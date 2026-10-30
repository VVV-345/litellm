"""本模块转发号池 WebSocket 帧并确保租约在所有断线路径释放。"""

from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager, AsyncExitStack
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

from litellm.litellm_core_utils.signature_recovery import merge_visible_history, recover_signature_history
from litellm.litellm_core_utils.url_utils import validate_url
from litellm.proxy.management_endpoints.account_pool_failover import ACCOUNT_POOL_QUOTA_CODES, QUOTA_CODES
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
    estimate_request_tokens,
    report,
    select_gateway_credential,
    upstream_request_headers,
)
from litellm.proxy.management_endpoints.account_pool_request_log import RequestLog
from litellm.proxy.management_endpoints.account_pool_retry import failover_rebuildable, has_server_reference
from litellm.proxy.management_endpoints.account_pool_routing import Route, upstream_url

_FRAME: Final = TypeAdapter(dict[str, JsonValue])
_SUBPROTOCOLS: Final = TypeAdapter(tuple[str, ...])
_MAX_MESSAGE: Final = 16 * 1024 * 1024
_MAX_HISTORY: Final = 256 * 1024
_HISTORY_ITEMS: Final = TypeAdapter(tuple[dict[str, JsonValue], ...])
_SENSITIVE_QUERY_FIELDS: Final = frozenset(("api_key", "key", "token", "access_token"))
_PRE_OUTPUT_EVENTS: Final = frozenset(("response.created", "response.in_progress", "response.queued", "ping"))


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


@dataclass(frozen=True, slots=True)
class _UpstreamEvent:
    data: str | bytes


@dataclass(frozen=True, slots=True)
class _ResponseCancellation:
    data: str | bytes


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
        self.finished = False
        self.log: RequestLog | None = None
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
    request_id: Final = getattr(websocket.state, "account_pool_request_id", None) or uuid4()
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
    deadline: Final = asyncio.get_running_loop().time() + seconds
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
    log: Final = RequestLog(
        lease,
        route,
        resolution,
        websocket.headers,
        {"model": public_model},
        key,
        "websocket",
        standard_accounting=getattr(websocket.state, "account_pool_standard_accounting", False),
    )
    attempt: Final = _Attempt(lease.lease_id, websocket.url.path, debug_detail)
    fallback: Final = (
        resolution.policy.routing.fallback_enabled
        and attempt_number < resolution.policy.routing.max_attempts
        and route_index + 1 < len(selected)
    )
    try:
        # 总时限覆盖建连与转发，必须在 Manager 的租约到期前停止占用上游。
        async with asyncio.timeout_at(deadline):
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
                key,
                session,
                control,
            )
    except TimeoutError:
        attempt.outcome(504, "WebSocket request timed out", stage="response")
        await websocket.close(code=1001, reason="WebSocket request timed out")
        return True, frozenset[AcquireRejectionReason]()
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
    if attempt.finished:
        return
    attempt.finished = True
    try:
        attempt.result = await (attempt.log or log).finish(attempt.result)
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
    key: str,
    session: str | None,
    control: GatewayControl,
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
            outcome: Final = (
                await _ResponsesRelay(
                    websocket, upstream, public_model, upstream_model, key, session,
                    resolution, selected, control, dialer, request_id, route_index, attempt, log,
                ).run()
                if websocket.url.path.rstrip("/").endswith("/responses")
                else await _relay(websocket, upstream, public_model, upstream_model, log)
            )
            attempt.outcome(outcome.http_status, outcome.message, stage="response")
            return True
    except (OSError, TimeoutError, WebSocketException):
        if websocket.application_state is WebSocketState.CONNECTED:
            attempt.outcome(502, "WebSocket upstream connection failed", stage="connection")
            await websocket.close(code=1011, reason="WebSocket upstream connection failed")
            return True
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


def _history_items(value: object) -> tuple[dict[str, JsonValue], ...] | None:
    try:
        return _HISTORY_ITEMS.validate_python(value)
    except ValueError:
        return None


def _event_fields(value: str | bytes) -> dict[str, JsonValue]:
    try:
        return _FRAME.validate_json(value)
    except ValueError:
        return {}


class _ResponsesRelay:
    def __init__(
        self,
        websocket: WebSocket,
        upstream: UpstreamWebSocket,
        public_model: str,
        upstream_model: str,
        key: str,
        session: str | None,
        resolution: Resolution,
        selected: tuple[Route, ...],
        control: GatewayControl,
        dialer: WebSocketDialer,
        request_id: UUID,
        route_index: int,
        attempt: _Attempt,
        log: RequestLog,
    ) -> None:
        self.websocket = websocket
        self.upstream = upstream
        self.public_model = public_model
        self.upstream_model = upstream_model
        self.subprotocol = upstream.subprotocol
        self.key = key
        self.session = session
        self.resolution = resolution
        self.selected = selected
        self.control = control
        self.dialer = dialer
        self.request_id = request_id
        self.route_index = route_index
        self.attempt = attempt
        self.log = log
        self.history: tuple[str, dict[str, JsonValue]] | None = None
        self.frames: asyncio.Queue[_ClientMessage] = asyncio.Queue(maxsize=16)
        self.cancellations: asyncio.Queue[str | bytes] = asyncio.Queue(maxsize=16)
        self.turn_active = False
        self.cancelled = False
        self.output_visible = False
        self.lifecycle_frames: tuple[str | bytes, ...] = ()
        self.turn_failure_reasons: tuple[str, ...] = ()
        self.turn_retry_after_seconds: tuple[int, ...] = ()
        self.disconnected = asyncio.Event()
        self.stack = AsyncExitStack()
        self.pending = asyncio.create_task(upstream.recv())

    async def _read(self) -> None:
        try:
            while await self._read_frame():
                pass
        finally:
            self.disconnected.set()

    async def _read_frame(self) -> bool:
        message: Final = _ClientMessage.model_validate(await self.websocket.receive())
        if message.type == "websocket.disconnect":
            self.disconnected.set()
        data: Final = message.text if message.text is not None else message.binary
        if (
            self.turn_active
            and isinstance(data, (str, bytes))
            and _event_fields(data).get("type") == "response.cancel"
        ):
            await self.cancellations.put(data)
            return True
        await self.frames.put(message)
        return not self.disconnected.is_set()

    async def _recv(self) -> str | bytes:
        while True:
            match await self._wait_for_event():
                case _ResponseCancellation(data):
                    await self._forward_cancel(data)
                case _UpstreamEvent(data):
                    return data

    async def _forward_cancel(self, data: str | bytes) -> None:
        rewritten: Final = _rewrite_model(data, self.public_model, self.upstream_model, strict=True)
        self.cancelled = True
        self.log.observe_websocket(rewritten, incoming=True)
        await self.upstream.send(rewritten)

    async def _wait_for_event(self) -> _UpstreamEvent | _ResponseCancellation:
        cancellation: Final = asyncio.create_task(self.cancellations.get())
        disconnected: Final = asyncio.create_task(self.disconnected.wait())
        try:
            done, _ = await asyncio.wait(
                (self.pending, cancellation, disconnected), return_when=asyncio.FIRST_COMPLETED
            )
            if disconnected in done:
                raise WebSocketDisconnect(1000)
            if cancellation in done:
                return _ResponseCancellation(cancellation.result())
            result: Final = self.pending.result()
            self.pending = asyncio.create_task(self.upstream.recv())
            return _UpstreamEvent(result)
        finally:
            cancellation.cancel()
            disconnected.cancel()
            await asyncio.gather(cancellation, disconnected, return_exceptions=True)

    async def _next_frame(self) -> _ClientMessage:
        frame: Final = asyncio.create_task(self.frames.get())
        try:
            while True:
                done, _ = await asyncio.wait((frame, self.pending), return_when=asyncio.FIRST_COMPLETED)
                if frame in done:
                    return frame.result()
                await self._forward_pending()
        finally:
            frame.cancel()
            await asyncio.gather(frame, return_exceptions=True)

    async def _forward_pending(self) -> None:
        data: Final = self.pending.result()
        if _message_size(data) > _MAX_MESSAGE:
            raise WebSocketException("WebSocket upstream message exceeds 16 MiB")
        self.log.observe_websocket(data)
        public: Final = _rewrite_model(data, self.upstream_model, self.public_model, strict=False)
        if isinstance(public, str):
            await self.websocket.send_text(public)
        else:
            await self.websocket.send_bytes(public)
        self.pending = asyncio.create_task(self.upstream.recv())

    async def run(self) -> _RelayResult:
        reader: Final = asyncio.create_task(self._read())
        try:
            async with self.stack:
                while True:
                    if (result := await self._frame()) is not None:
                        return result
        except WebSocketDisconnect:
            return _RelayResult(200, "WebSocket downstream disconnected")
        except ConnectionClosed as error:
            code: Final = _safe_close_code(error.code)
            if self.websocket.client_state is not WebSocketState.DISCONNECTED:
                await self.websocket.close(code=code)
            return _RelayResult(200 if code in (1000, 1001) else 502, "WebSocket upstream disconnected")
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)

    async def _frame(self) -> _RelayResult | None:
        frame: Final = await self._next_frame()
        if frame.type == "websocket.disconnect":
            return _RelayResult(200, "WebSocket downstream disconnected")
        data: Final = frame.text if frame.text is not None else frame.binary
        if not isinstance(data, (str, bytes)):
            return None
        if _message_size(data) > _MAX_MESSAGE:
            await self.websocket.close(code=1009, reason="Message too large")
            self.attempt.outcome(413, "WebSocket message exceeds 16 MiB", stage="response")
            return _RelayResult(413, "WebSocket message exceeds 16 MiB")
        try:
            rewritten: Final = _rewrite_model(data, self.public_model, self.upstream_model, strict=True)
            payload: Final = _FRAME.validate_json(rewritten)
        except ValueError:
            await self.websocket.close(code=1008, reason="Invalid response frame or routed model")
            self.attempt.outcome(400, "WebSocket frame changes the routed model", stage="response")
            return _RelayResult(400, "WebSocket frame changes the routed model")
        if payload.get("type") != "response.create":
            await self.upstream.send(rewritten)
            return None
        nested: Final = payload.get("response")
        params: Final = nested if isinstance(nested, dict) else payload
        raw_input: Final = params.get("input", [])
        current: Final = _history_items(
            [{"role": "user", "content": raw_input}] if isinstance(raw_input, str) else raw_input
        )
        if current is None:
            await self._error("invalid_request_error", "Response input must contain only JSON objects")
            self.attempt.outcome(400, "Invalid response input", stage="response")
            return _RelayResult(400, "Invalid response input")
        previous: Final = params.get("previous_response_id")
        record: Final = self.history[1] if self.history is not None and self.history[0] == previous else {}
        cached: Final = _HISTORY_ITEMS.validate_python(record.get("input", []))
        history: Final = merge_visible_history(cached, current)
        context: Final = {key: value for key, value in record.items() if key != "input"}
        portable: Final = {
            **context,
            **{key: value for key, value in params.items() if key not in ("previous_response_id", "type")},
        }
        portable_payload: Final[dict[str, JsonValue]] = {**portable, "input": [dict(item) for item in history]}
        complete: Final = not previous or bool(cached)
        self.turn_active = True
        try:
            recovered: Final = await self._turn(payload, portable_payload, complete)
        finally:
            self.turn_active = False
            while not self.cancellations.empty():
                self.cancellations.get_nowait()
        if not recovered:
            return _RelayResult(502, "WebSocket turn cannot be recovered safely")

    async def _acquire(self, index: int, attempt_number: int, estimated_tokens: int) -> AcquireRejected | None:
        route: Final = self.selected[index]
        seconds: Final = min(
            self.resolution.policy.transport.request_timeout_seconds,
            route.account.policy.transport.request_timeout_seconds,
        )
        lease: Final = await self.control.acquire(
            AcquireRequest(
                card_key=self.key,
                session_hash=self.session,
                account_id=route.account.id,
                request_id=self.request_id,
                model=route.model,
                card_version=self.resolution.card_version,
                policy_version=self.resolution.policy_version,
                account_version=route.account.environment_version,
                account_policy_version=route.account.policy_version,
                timeout_seconds=seconds,
                attempt=attempt_number,
                estimated_tokens=estimated_tokens,
                allow_session_rebind=index != self.route_index,
            )
        )
        if isinstance(lease, AcquireRejected):
            return lease
        self.attempt.result = _Attempt(
            lease.lease_id,
            self.websocket.url.path,
            _safe_debug_detail(self.websocket, route) if self.resolution.policy.transport.debug_log_enabled else None,
        ).result
        self.attempt.finished = False
        self.log = RequestLog(
            lease,
            route,
            self.resolution,
            self.websocket.headers,
            {"model": self.public_model},
            self.key,
            "websocket",
            standard_accounting=getattr(self.websocket.state, "account_pool_standard_accounting", False),
        )
        self.attempt.log = self.log
        if index == self.route_index:
            return None
        credential: Final = select_gateway_credential(route.account.credentials, self.request_id, route.account.api_key)
        model: Final = (
            route.model.removeprefix(route.account.model_prefix) if route.account.model_prefix else route.model
        )
        request: Final = WebSocketDialRequest(
            url=await _websocket_url(self.websocket, route, model),
            headers=_websocket_request_headers(self.websocket, route, credential.api_key),
            subprotocols=tuple(
                Subprotocol(value)
                for value in _SUBPROTOCOLS.validate_python(self.websocket.scope.get("subprotocols", ()))
            ),
            timeout_seconds=seconds,
            proxy_url=credential.proxy_url,
        )
        self.pending.cancel()
        await asyncio.gather(self.pending, return_exceptions=True)
        await self.upstream.close()
        self.upstream = await self.stack.enter_async_context(self.dialer.connect(request))
        if self.upstream.subprotocol != self.subprotocol:
            await self.upstream.close(code=1002, reason="Incompatible upstream subprotocol")
            raise WebSocketException("Incompatible upstream subprotocol")
        self.pending = asyncio.create_task(self.upstream.recv())
        self.route_index = index
        self.upstream_model = model
        return None

    async def _turn(self, original: dict[str, JsonValue], portable: dict[str, JsonValue], complete: bool) -> bool:
        self.turn_failure_reasons = ()
        self.turn_retry_after_seconds = ()
        initial_index: Final = self.route_index
        allowed: Final = self.resolution.policy.routing.fallback_enabled
        limit: Final = self.resolution.policy.routing.max_attempts if allowed else 1
        indices: Final = (initial_index, *(index for index in range(len(self.selected)) if index != initial_index))[
            :limit
        ]
        recovered: Final = recover_signature_history(portable) or portable
        safe: Final = complete and not has_server_reference(portable) and failover_rebuildable(recovered)
        for number, index in enumerate(indices, 1):
            if (result := await self._candidate(original, portable, recovered, safe, initial_index, indices, number, index)) is not None:
                return result
        reasons: Final = tuple(dict.fromkeys(self.turn_failure_reasons))
        if reasons == ("quota_exhausted",):
            await self._error(
                "account_pool_quota_exhausted", "No bound account is currently available", reasons=reasons
            )
            return False
        retry_after_seconds: Final = min(
            (seconds for seconds in self.turn_retry_after_seconds if seconds > 0), default=None
        )
        await self._error(
            "account_pool_no_eligible_accounts",
            "No eligible account is currently available",
            reasons=reasons,
            retry_after_seconds=retry_after_seconds,
        )
        return False

    async def _candidate(
        self, original: dict[str, JsonValue], portable: dict[str, JsonValue], recovered: dict[str, JsonValue],
        safe: bool, initial_index: int, indices: tuple[int, ...], number: int, index: int,
    ) -> bool | None:
        migrated: Final = index != initial_index
        if migrated and not safe:
            await self._error(
                "account_pool_history_unavailable", "Complete portable history is required to switch accounts"
            )
            return False
        if self.attempt.finished or migrated:
            try:
                rejection: Final = await self._acquire(
                    index, number, estimate_request_tokens("/v1/responses", portable, self.selected[index].model)
                )
                if rejection is not None:
                    self.turn_failure_reasons = (*self.turn_failure_reasons, rejection.reason)
                    self.turn_retry_after_seconds = (*self.turn_retry_after_seconds, rejection.retry_after_seconds)
                    return None
            except (OSError, TimeoutError, WebSocketException, ConnectionClosed):
                self.turn_failure_reasons = (*self.turn_failure_reasons, "upstream_connection_error")
                connection_next_account: Final = (
                    self.selected[indices[number]].account.id if number < len(indices) else None
                )
                self.attempt.outcome(
                    502,
                    "WebSocket upstream connection failed",
                    stage="connection",
                    retryable=connection_next_account is not None,
                    switched_account=connection_next_account is not None,
                    next_account_id=connection_next_account,
                )
                await _finish_attempt(self.control, self.attempt, self.log)
                if connection_next_account is None:
                    await self._error("account_pool_account_unavailable", "WebSocket upstream connection failed")
                    return False
                return None
        nested: Final = original.get("response")
        source: Final = (
            {**recovered, "model": self.upstream_model}
            if migrated
            else (nested if isinstance(nested, dict) else original)
        )
        outgoing: Final = (
            {**original, "response": source} if isinstance(nested, dict) else {**source, "type": "response.create"}
        )
        wire: Final = _rewrite_model(json.dumps(outgoing), self.public_model, self.upstream_model, strict=True)
        self.log.observe_websocket(wire, incoming=True)
        seconds: Final = min(
            self.resolution.policy.transport.request_timeout_seconds,
            self.selected[index].account.policy.transport.request_timeout_seconds,
        )
        next_account: Final = self.selected[indices[number]].account.id if safe and number < len(indices) else None
        async with asyncio.timeout(seconds):
            await self.upstream.send(wire)
            result: Final = await self._events(portable, next_account)
        if result:
            return True
        return None if self.attempt.result.failure_reason == "quota_exhausted" else False

    async def _events(self, portable: dict[str, JsonValue], next_account: UUID | None) -> bool:
        self.output_visible = False
        self.cancelled = False
        self.lifecycle_frames = ()
        while (result := await self._event(portable, next_account)) is None:
            pass
        return result

    async def _event(self, portable: dict[str, JsonValue], next_account: UUID | None) -> bool | None:
        retry: Final = next_account is not None and not self.cancelled
        data: Final = await self._recv()
        if _message_size(data) > _MAX_MESSAGE:
            await self.websocket.close(code=1009, reason="Message too large")
            return False
        event: Final = _event_fields(data)
        response: Final = event.get("response")
        response_fields: Final = response if isinstance(response, dict) else {}
        error: Final = event.get("error") if event.get("type") == "error" else response_fields.get("error")
        quota: Final = isinstance(error, dict) and any(
            isinstance(error.get(key), str) and error.get(key) in QUOTA_CODES | ACCOUNT_POOL_QUOTA_CODES
            for key in ("code", "type")
        )
        self.log.observe_websocket(data)
        if quota and isinstance(error, dict):
            self.turn_failure_reasons = (*self.turn_failure_reasons, "quota_exhausted")
            self.attempt.result = self.attempt.result.model_copy(
                update={
                    "http_status": 429,
                    "failure_reason": "quota_exhausted",
                    "upstream_code": str(error.get("code") or error.get("type")),
                    "retryable": retry and not self.output_visible,
                    "switched_account": retry and not self.output_visible,
                    "next_account_id": next_account if not self.output_visible else None,
                }
            )
            await _finish_attempt(self.control, self.attempt, self.log)
            if retry and not self.output_visible:
                return False
        if retry and not self.output_visible and event.get("type") in _PRE_OUTPUT_EVENTS:
            self.lifecycle_frames = (*self.lifecycle_frames, data)
            return None
        frames: Final = (*self.lifecycle_frames, data)
        self.lifecycle_frames = ()
        for frame in frames:
            await self._send_upstream_event(frame)
        self.output_visible = True
        if event.get("type") == "response.completed":
            identifier: Final = response_fields.get("id")
            output: Final = response_fields.get("output")
            inputs: Final = portable.get("input")
            self.history = None
            if isinstance(identifier, str) and isinstance(output, list) and isinstance(inputs, list):
                items: Final = _history_items((*inputs, *output))
                record: Final[dict[str, JsonValue]] = {
                    **{
                        key: value
                        for key, value in portable.items()
                        if key not in ("generate", "client_metadata", "stream_options")
                    },
                    "input": [dict(item) for item in items] if items is not None else [],
                }
                if items and len(json.dumps(record).encode()) <= _MAX_HISTORY:
                    self.history = (identifier, record)
            self.attempt.outcome(200, "WebSocket response completed", stage="response")
            await _finish_attempt(self.control, self.attempt, self.log)
            return True
        if event.get("type") in ("error", "response.failed", "response.incomplete"):
            if event.get("type") == "response.incomplete" and self.cancelled:
                self.attempt.outcome(499, "WebSocket response cancelled", stage="response")
                await _finish_attempt(self.control, self.attempt, self.log)
            elif not quota:
                self.attempt.outcome(502, "WebSocket response failed", stage="response")
                await _finish_attempt(self.control, self.attempt, self.log)
            return True
        return None

    async def _send_upstream_event(self, data: str | bytes) -> None:
        public: Final = _rewrite_model(data, self.upstream_model, self.public_model, strict=False)
        if isinstance(public, str):
            await self.websocket.send_text(public)
        else:
            await self.websocket.send_bytes(public)

    async def _error(
        self,
        code: str,
        message: str,
        *,
        reasons: tuple[str, ...] = (),
        retry_after_seconds: int | None = None,
    ) -> None:
        detail: Final[dict[str, JsonValue]] = {
            "code": code,
            "message": message,
            **({"reasons": list(reasons)} if reasons else {}),
            **({"retry_after_seconds": retry_after_seconds} if retry_after_seconds is not None else {}),
        }
        await self.websocket.send_text(json.dumps({"type": "error", "error": detail}))


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
    try:
        done, _ = await asyncio.wait((downstream_task, upstream_task), return_when=asyncio.FIRST_COMPLETED)
    finally:
        # 超时和取消同样要终止两个方向，不能在归还租约后继续转发。
        downstream_task.cancel()
        upstream_task.cancel()
        await asyncio.gather(downstream_task, upstream_task, return_exceptions=True)
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
