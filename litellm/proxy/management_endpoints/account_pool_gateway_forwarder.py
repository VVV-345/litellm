"""本模块处理单次模型请求的转发、流式释放和有限重试，不记录请求正文。"""

from __future__ import annotations

import asyncio
import io
import json
import math
import re
from collections.abc import AsyncIterator
from typing import Final
from uuid import UUID, uuid4

import httpx
from pydantic import JsonValue, TypeAdapter
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.types import Message, Send

from litellm._logging import verbose_proxy_logger
from litellm.proxy.management_endpoints.account_pool_gateway_client import GatewayControl
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import (
    AcquireRequest,
    FinishRequest,
    Lease,
    Resolution,
    RoutingReason,
)
from litellm.proxy.management_endpoints.account_pool_routing import Route, upstream_url
from litellm.proxy.management_endpoints.account_pool_stream import EventStream, usage_tokens

_JSON: Final = TypeAdapter(dict[str, JsonValue])
_HEADERS: Final = frozenset(("content-type", "accept", "user-agent", "originator", "openai-beta", "anthropic-version"))
_SAFE_CONNECT_FAILURES: Final = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class Attempt:
    def __init__(self, lease: Lease, endpoint: str, send: Send) -> None:
        self.result = FinishRequest(
            lease_id=lease.lease_id,
            endpoint=endpoint,
            http_status=499,
            stage="response",
            message="Downstream disconnected",
        )
        self.started = False
        self.send: Final = send
        self.request_id: Final = lease.request_id

    async def emit(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.started = True
            await self.send(
                {**message, "headers": [*message.get("headers", []), (b"x-request-id", str(self.request_id).encode())]}
            )
            return
        await self.send(message)

    def outcome(self, status: int, message: str, **values: JsonValue) -> None:
        self.result = FinishRequest.model_validate(
            {
                **self.result.model_dump(mode="json"),
                "http_status": status,
                "message": message,
                **values,
            }
        )

    def record_cost(self, cost_usd: float | None) -> None:
        if cost_usd is not None:
            self.result = self.result.model_copy(update={"cost_usd": cost_usd})


async def report(control: GatewayControl, result: FinishRequest) -> None:
    try:
        async with asyncio.timeout(10):
            await control.finish(result)
    except Exception as error:
        verbose_proxy_logger.warning(
            "Account pool completion event failed: request lease=%s type=%s", result.lease_id, error.__class__.__name__
        )


async def forward(
    request: Request,
    payload: dict[str, JsonValue],
    key: str,
    session: str | None,
    resolution: Resolution,
    selected: tuple[Route, ...],
    control: GatewayControl,
    client: httpx.AsyncClient,
    send: Send,
) -> None:
    routing: Final = resolution.policy.routing
    allow_retry: Final = routing.fallback_enabled and not payload.get("previous_response_id")
    max_attempts: Final = routing.max_attempts if allow_retry else 1
    request_id: Final = uuid4()
    completed: Final = await forward_candidate(
        request,
        payload,
        key,
        session,
        resolution,
        selected,
        control,
        client,
        send,
        request_id,
        route_index=0,
        attempt_number=1,
        max_attempts=max_attempts,
    )
    if completed:
        return
    await JSONResponse(
        {"error": {"message": "No bound account has available concurrency", "type": "account_pool_error"}},
        status_code=503,
        headers={"x-request-id": str(request_id)},
    )(request.scope, request.receive, send)


async def forward_candidate(
    request: Request,
    payload: dict[str, JsonValue],
    key: str,
    session: str | None,
    resolution: Resolution,
    selected: tuple[Route, ...],
    control: GatewayControl,
    client: httpx.AsyncClient,
    send: Send,
    request_id: UUID,
    route_index: int,
    attempt_number: int,
    max_attempts: int,
) -> bool:
    if route_index >= len(selected):
        return False
    route: Final = selected[route_index]
    seconds: Final = min(
        resolution.policy.transport.request_timeout_seconds, route.account.policy.transport.request_timeout_seconds
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
        routing_reason=attempt_routing_reason(route, route_index, attempt_number, sticky_unavailable),
        allow_session_rebind=route_index > 0 or sticky_unavailable,
    )
    lease: Final = await control.acquire(acquisition)
    if lease is None:
        return await forward_candidate(
            request,
            payload,
            key,
            session,
            resolution,
            selected,
            control,
            client,
            send,
            request_id,
            route_index + 1,
            attempt_number,
            max_attempts,
        )
    next_id: Final = (
        selected[route_index + 1].account.id
        if attempt_number < max_attempts and route_index + 1 < len(selected)
        else None
    )
    attempt: Final = Attempt(lease, request.url.path, send)
    try:
        completed: Final = await execute(request, payload, route, resolution, client, attempt, next_id, seconds)
    finally:
        await asyncio.shield(report(control, attempt.result))
    if not completed:
        await asyncio.sleep(min(resolution.policy.routing.backoff_ms, 60000) / 1000)
        return await forward_candidate(
            request,
            payload,
            key,
            session,
            resolution,
            selected,
            control,
            client,
            send,
            request_id,
            route_index + 1,
            attempt_number + 1,
            max_attempts,
        )
    return True


async def execute(
    request: Request,
    payload: dict[str, JsonValue],
    route: Route,
    resolution: Resolution,
    client: httpx.AsyncClient,
    attempt: Attempt,
    next_id: UUID | None,
    seconds: int,
) -> bool:
    headers: Final = {name: value for name, value in request.headers.items() if name in _HEADERS}
    body: Final = {**payload, "model": route.model}
    try:
        async with asyncio.timeout(seconds):
            upstream: Final = client.build_request(
                "POST",
                upstream_url(route.account, request.url.path),
                headers={**headers, "authorization": f"Bearer {route.account.api_key}", "accept-encoding": "identity"},
                content=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(),
                timeout=seconds,
            )
            response: Final = await client.send(upstream, stream=True)
            try:
                cost_usd: Final = upstream_response_cost(response.headers)
                attempt.record_cost(cost_usd)
                if response.status_code >= 300:
                    code: Final = await error_code(response)
                    public_status: Final = response.status_code if response.status_code >= 400 else 502
                    retry: Final = (
                        next_id is not None and response.status_code in resolution.policy.routing.retryable_statuses
                    )
                    attempt.outcome(
                        public_status,
                        f"Upstream HTTP {response.status_code}",
                        stage="upstream",
                        upstream_code=code,
                        retryable=retry,
                        switched_account=retry,
                        next_account_id=str(next_id) if retry else None,
                        cost_usd=cost_usd,
                    )
                    if retry:
                        return False
                    await JSONResponse(
                        {"error": {"message": attempt.result.message, "code": code, "type": "upstream_error"}},
                        status_code=public_status,
                    )(request.scope, request.receive, attempt.emit)
                    return True
                if payload.get("stream") is True:
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        raise ValueError("Upstream did not return an event stream")
                    await stream_response(request, response, attempt, cost_usd)
                else:
                    data: Final = await bounded_body(response, 32 * 1024 * 1024)
                    parsed: Final = _JSON.validate_json(data)
                    usage_in, usage_out = usage_tokens(parsed)
                    attempt.outcome(
                        response.status_code,
                        "Request completed",
                        input_tokens=usage_in,
                        output_tokens=usage_out,
                        cost_usd=cost_usd,
                    )
                    public: Final = {**parsed, **({"model": payload["model"]} if "model" in parsed else {})}
                    await JSONResponse(public, status_code=response.status_code)(
                        request.scope, request.receive, attempt.emit
                    )
                return True
            finally:
                await response.aclose()
    except asyncio.CancelledError:
        attempt.outcome(499, "Downstream disconnected", stage="response")
        raise
    except (httpx.HTTPError, TimeoutError, ValueError, ClientDisconnect, OSError) as error:
        disconnected: Final = isinstance(error, (ClientDisconnect, OSError)) and not isinstance(error, httpx.HTTPError)
        status: Final = (
            499 if disconnected else 504 if isinstance(error, (TimeoutError, httpx.TimeoutException)) else 502
        )
        retry_connection: Final = (
            next_id is not None and isinstance(error, _SAFE_CONNECT_FAILURES) and not attempt.started
        )
        attempt.outcome(
            status,
            "Downstream disconnected" if disconnected else "Upstream connection or response failed",
            stage="response" if attempt.started else "connection",
            retryable=retry_connection,
            switched_account=retry_connection,
            next_account_id=str(next_id) if retry_connection else None,
        )
        if retry_connection:
            return False
        if not attempt.started and not disconnected:
            await JSONResponse({"error": {"message": attempt.result.message}}, status_code=status)(
                request.scope,
                request.receive,
                attempt.emit,
            )
        elif attempt.started and not disconnected:
            await attempt.emit(
                {
                    "type": "http.response.body",
                    "more_body": False,
                    "body": b'data: {"error":{"message":"Upstream stream interrupted"}}\n\n',
                }
            )
        return True


async def bounded_body(response: httpx.Response, limit: int) -> bytes:
    buffer: Final = io.BytesIO()
    async for chunk in response.aiter_bytes():
        if buffer.tell() + len(chunk) > limit:
            raise ValueError("Upstream response is too large")
        buffer.write(chunk)
    return buffer.getvalue()


async def error_code(response: httpx.Response) -> str | None:
    try:
        data: Final = _JSON.validate_json(await bounded_body(response, 65536))
    except ValueError:
        return None
    error: Final = data.get("error")
    code: Final = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) else None


def upstream_response_cost(headers: httpx.Headers) -> float | None:
    raw: Final = next((value for name, value in headers.multi_items() if name == "x-litellm-response-cost"), None)
    if raw is None or re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw) is None:
        return None
    try:
        value: Final = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def attempt_routing_reason(
    route: Route,
    route_index: int,
    attempt_number: int,
    sticky_unavailable: bool,
) -> RoutingReason:
    if attempt_number > 1:
        return "retry_failover"
    if route_index > 0:
        return "concurrency_fallback"
    if sticky_unavailable:
        return "session_rebind"
    return route.reason


async def stream_response(
    request: Request,
    response: httpx.Response,
    attempt: Attempt,
    cost_usd: float | None,
) -> None:
    state: Final = EventStream()

    async def chunks() -> AsyncIterator[bytes]:
        async for chunk in response.aiter_bytes():
            for frame in state.feed(chunk):
                yield state.observe(frame)
                if state.terminal:
                    break
            if state.terminal:
                break
        final: Final = state.finish()
        if final is not None:
            yield state.observe(final)
        if not state.terminal:
            raise ValueError("Upstream event stream ended before completion")
        if state.failed:
            attempt.outcome(502, "Upstream stream reported an error", stage="response")
        else:
            attempt.outcome(
                response.status_code,
                "Request completed",
                stage="response",
                input_tokens=state.input_tokens,
                output_tokens=state.output_tokens,
                cost_usd=cost_usd,
            )

    await StreamingResponse(
        chunks(),
        status_code=response.status_code,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )(request.scope, request.receive, attempt.emit)
