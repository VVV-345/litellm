"""本模块处理单次模型请求的转发、流式释放和有限重试，不记录请求正文。"""

from __future__ import annotations

import asyncio
import io
import json
import math
import re
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from functools import cache, reduce
from types import MappingProxyType
from typing import Final, Protocol, TypedDict, cast
from uuid import UUID, uuid4

import httpx
from openai.types.responses.response_create_params import ResponseInputParam
from pydantic import JsonValue, TypeAdapter
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.types import Message, Send
from typing_extensions import ReadOnly

from litellm._logging import redact_secrets, verbose_proxy_logger
from litellm.litellm_core_utils import token_counter as token_counter_module
from litellm.litellm_core_utils.url_utils import validate_url
from litellm.proxy.management_endpoints.account_pool_gateway_client import GatewayControl
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import (
    AcquireRejected,
    AcquireRejectionReason,
    AcquireRequest,
    FinishRequest,
    GatewayCredential,
    Lease,
    Resolution,
    RoutingReason,
)
from litellm.proxy.management_endpoints.account_pool_routing import Route, upstream_url
from litellm.proxy.management_endpoints.account_pool_stream import EventStream, cache_usage_tokens, usage_tokens
from litellm.responses.litellm_completion_transformation.transformation import LiteLLMCompletionResponsesConfig
from litellm.types.llms.openai import (
    AllMessageValues,
    ChatCompletionNamedToolChoiceParam,
    ChatCompletionToolParam,
    ResponsesAPIOptionalRequestParams,
)
from litellm.types.utils import Message as LiteLLMMessage


class _TokenCounter(Protocol):
    def __call__(
        self,
        *,
        model: str,
        messages: Sequence[AllMessageValues | LiteLLMMessage],
        tools: Sequence[ChatCompletionToolParam] | None,
        tool_choice: ChatCompletionNamedToolChoiceParam | None,
        use_default_image_token_count: bool,
    ) -> int: ...


class _ResponsesRequestTransformer(Protocol):
    def __call__(
        self,
        model: str,
        input: str | ResponseInputParam,
        responses_api_request: ResponsesAPIOptionalRequestParams,
    ) -> Mapping[str, object]: ...


_JSON: Final = TypeAdapter(dict[str, JsonValue])
_HEADERS: Final = frozenset(
    (
        "content-type",
        "accept",
        "user-agent",
        "originator",
        "openai-beta",
        "anthropic-version",
        "anthropic-beta",
        "x-app",
        "x-client-request-id",
        "x-claude-code-session-id",
        "x-claude-remote-session-id",
        "x-session-id",
        "x-session-affinity",
        "session-id",
        "session_id",
        "conversation_id",
        "x-codex-turn-metadata",
        "x-stainless-arch",
        "x-stainless-async",
        "x-stainless-lang",
        "x-stainless-os",
        "x-stainless-package-version",
        "x-stainless-read-timeout",
        "x-stainless-retry-count",
        "x-stainless-runtime",
        "x-stainless-runtime-version",
        "x-stainless-timeout",
    )
)
_RESPONSE_HEADERS: Final = frozenset(
    (
        "cache-control",
        "retry-after",
        "retry-after-ms",
        "request-id",
        "x-request-id",
        "openai-request-id",
        "anthropic-request-id",
        "openai-processing-ms",
        "openai-version",
        "x-should-retry",
    )
)
_RESPONSE_HEADER_PREFIXES: Final = ("x-ratelimit-", "ratelimit-", "anthropic-ratelimit-")
_SENSITIVE_ERROR_FIELDS: Final = frozenset(
    ("authorization", "api-key", "api_key", "token", "access_token", "refresh_token", "secret", "cookie")
)
_SAFE_CONNECT_FAILURES: Final = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_MESSAGES: Final = TypeAdapter(list[AllMessageValues])
_CHAT_TOOLS: Final = TypeAdapter(list[ChatCompletionToolParam])
_TOOL_CHOICE: Final = TypeAdapter(ChatCompletionNamedToolChoiceParam)
_TOKEN_COUNTER: Final[_TokenCounter] = cast(
    _TokenCounter, token_counter_module.token_counter
)  # cast-ok: legacy token counter annotations expose broader optional inputs
_RESPONSES_TO_CHAT: Final[_ResponsesRequestTransformer] = cast(
    _ResponsesRequestTransformer,
    LiteLLMCompletionResponsesConfig.transform_responses_api_request_to_chat_completion_request,
)  # cast-ok: shared transformer returns a legacy dict with the validated fields read here
_CONFIGURATION_REJECTIONS: Final[frozenset[AcquireRejectionReason]] = frozenset(("configuration", "session"))
_TOKEN_BUDGET_REJECTIONS: Final[frozenset[AcquireRejectionReason]] = frozenset(("token_budget",))


class _DebugDetail(TypedDict):
    transport: ReadOnly[str]
    account_id: ReadOnly[str]
    supplier: ReadOnly[str]
    query_fields: ReadOnly[tuple[str, ...]]
    request_fields: ReadOnly[tuple[str, ...]]
    stream: ReadOnly[bool]


class _GatewayErrorDetail(TypedDict):
    message: ReadOnly[str]
    type: ReadOnly[str]


class _GatewayErrorEnvelope(TypedDict):
    error: ReadOnly[_GatewayErrorDetail]


@cache
def _responses_input_adapter() -> TypeAdapter[str | ResponseInputParam]:
    return TypeAdapter(str | ResponseInputParam)


@cache
def _responses_request_adapter() -> TypeAdapter[ResponsesAPIOptionalRequestParams]:
    return TypeAdapter(ResponsesAPIOptionalRequestParams)


class Attempt:
    def __init__(self, lease: Lease, endpoint: str, send: Send, detail: str | None = None) -> None:
        self.result = FinishRequest(
            lease_id=lease.lease_id,
            endpoint=endpoint,
            http_status=499,
            stage="response",
            message="Downstream disconnected",
            detail=detail,
        )
        self.started = False
        self.send: Final = send
        self.request_id: Final = lease.request_id

    async def emit(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.started = True
            response_headers: Final = cast(Sequence[tuple[bytes, bytes]], message.get("headers", ()))
            header_names: Final = frozenset(name.lower() for name, _ in response_headers)
            request_id: Final = str(self.request_id).encode()
            outbound: Final[Message] = {
                **message,
                "headers": (
                    *response_headers,
                    *(((b"x-request-id", request_id),) if b"x-request-id" not in header_names else ()),
                    (b"x-account-pool-request-id", request_id),
                ),
            }
            await self.send(outbound)
            return
        await self.send(message)

    def outcome(self, status: int, message: str, **values: JsonValue) -> None:
        updated: Final = MappingProxyType(
            {
                **self.result.model_dump(mode="json"),
                "http_status": status,
                "message": message,
                **values,
            }
        )
        self.result = FinishRequest.model_validate(updated)

    def record_cost(self, cost_usd: float | None) -> None:
        if cost_usd is not None:
            self.result = self.result.model_copy(update=MappingProxyType({"cost_usd": cost_usd}))


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
    completed, rejections = await forward_candidate(
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
        rejection_reasons=frozenset[AcquireRejectionReason](),
    )
    if completed:
        return
    budget_only: Final = rejections == _TOKEN_BUDGET_REJECTIONS
    status: Final = 409 if rejections & _CONFIGURATION_REJECTIONS else 429 if budget_only else 503
    message: Final = (
        "Account pool configuration changed"
        if "configuration" in rejections
        else "The session-bound account is unavailable"
        if "session" in rejections
        else "No bound account has available local token budget"
        if budget_only
        else "No bound account is currently available"
    )
    content: Final[_GatewayErrorEnvelope] = {
        "error": {"message": message, "type": "account_pool_error"},
    }
    response_headers: Final = MappingProxyType({"x-request-id": str(request_id)})
    await JSONResponse(
        content,
        status_code=status,
        headers=response_headers,
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
    rejection_reasons: frozenset[AcquireRejectionReason],
) -> tuple[bool, frozenset[AcquireRejectionReason]]:
    if route_index >= len(selected):
        return False, rejection_reasons
    route: Final = selected[route_index]
    seconds: Final = min(
        resolution.policy.transport.request_timeout_seconds, route.account.policy.transport.request_timeout_seconds
    )
    sticky_unavailable: Final = resolution.sticky_account_id is not None and all(
        item.account.id != resolution.sticky_account_id for item in selected
    )
    estimated_tokens: Final = (
        estimate_request_tokens(request.url.path, payload, route.model)
        if route.account.policy.routing.token_budget_limit is not None
        else 0
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
        estimated_tokens=estimated_tokens,
        attempt=attempt_number,
        routing_reason=attempt_routing_reason(
            route, route_index, attempt_number, sticky_unavailable, "token_budget" in rejection_reasons
        ),
        allow_session_rebind=route_index > 0 or sticky_unavailable,
    )
    lease: Final = await control.acquire(acquisition)
    if isinstance(lease, AcquireRejected):
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
            rejection_reasons | frozenset((lease.reason,)),
        )
    next_id: Final = (
        selected[route_index + 1].account.id
        if attempt_number < max_attempts and route_index + 1 < len(selected)
        else None
    )
    debug_detail: Final = (
        safe_debug_detail(request, payload, route) if resolution.policy.transport.debug_log_enabled else None
    )
    attempt: Final = Attempt(lease, request.url.path, send, debug_detail)
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
            frozenset[AcquireRejectionReason](),
        )
    return True, frozenset[AcquireRejectionReason]()


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
    provider_header_names: Final = frozenset(name.lower() for name, _ in route.account.headers)
    client_headers: Final = tuple(
        (name, value)
        for name, value in upstream_request_headers(request.headers)
        if name.lower() not in provider_header_names
    )
    upstream_model: Final = (
        route.model.removeprefix(route.account.model_prefix)
        if route.account.model_prefix and route.model.startswith(route.account.model_prefix)
        else route.model
    )
    body: Final = {**payload, "model": upstream_model}  # mutable-ok: JSON encoding requires a concrete object
    credential: Final = select_gateway_credential(route.account.credentials, attempt.request_id, route.account.api_key)
    selected_client: Final = (
        client
        if credential.proxy_url is None
        else httpx.AsyncClient(
            proxy=credential.proxy_url,
            timeout=seconds,
            trust_env=False,
            follow_redirects=False,
        )
    )
    try:
        destination: Final = upstream_url(route.account, request.url.path)
        validated_destination, host_header = (
            await asyncio.to_thread(validate_url, destination)
            if route.account.supplier == "openai_compatible"
            else (destination, None)
        )
        provider_headers: Final = tuple(
            (name, value)
            for name, value in route.account.headers
            if name.lower() not in ("authorization", "host", "accept-encoding")
        )
        host_headers: Final = (("host", host_header),) if host_header is not None else ()
        headers: Final = (
            *client_headers,
            *provider_headers,
            *host_headers,
            ("authorization", f"Bearer {credential.api_key}"),
            ("accept-encoding", "identity"),
        )
        async with asyncio.timeout(seconds):
            upstream: Final = selected_client.build_request(
                "POST",
                validated_destination,
                params=tuple(request.query_params.multi_items()),
                headers=headers,
                content=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(),
                timeout=seconds,
            )
            response: Final = await selected_client.send(upstream, stream=True)
            try:
                cost_usd: Final = upstream_response_cost(response.headers)
                attempt.record_cost(cost_usd)
                if response.status_code >= 300:
                    error_payload, code = await upstream_error_payload(response, (credential.api_key,))
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
                        error_payload,
                        status_code=public_status,
                        headers=public_response_headers(response.headers),
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
                    cache_read, cache_created = cache_usage_tokens(parsed)
                    attempt.outcome(
                        response.status_code,
                        "Request completed",
                        input_tokens=usage_in,
                        output_tokens=usage_out,
                        cache_read_input_tokens=cache_read,
                        cache_creation_input_tokens=cache_created,
                        cost_usd=cost_usd,
                    )
                    public: Final = (
                        {**parsed, "model": payload["model"]}  # mutable-ok: JSON response requires a concrete object
                        if "model" in parsed
                        else parsed
                    )
                    await JSONResponse(
                        public,
                        status_code=response.status_code,
                        headers=public_response_headers(response.headers),
                    )(request.scope, request.receive, attempt.emit)
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
    finally:
        if selected_client is not client:
            await selected_client.aclose()


def select_gateway_credential(
    credentials: tuple[GatewayCredential, ...],
    request_id: UUID,
    fallback_api_key: str,
) -> GatewayCredential:
    if not credentials:
        return GatewayCredential(api_key=fallback_api_key)
    total_weight: Final = sum(credential.weight for credential in credentials)
    selected: Final = request_id.int % total_weight
    boundary: int = 0
    for credential in credentials:
        boundary += credential.weight
        if selected < boundary:
            return credential
    return credentials[-1]


def upstream_request_headers(headers: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple((name, value) for name, value in headers.items() if name.lower() in _HEADERS)


def safe_debug_detail(request: Request, payload: Mapping[str, JsonValue], route: Route) -> str:
    def safe_names(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(
            sorted(
                frozenset(
                    name[:80]
                    for name in values
                    if name.lower() not in _SENSITIVE_ERROR_FIELDS and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name)
                )
            )
        )

    detail: Final[_DebugDetail] = {
        "transport": "http",
        "account_id": str(route.account.id),
        "supplier": route.account.supplier,
        "query_fields": safe_names(request.query_params),
        "request_fields": safe_names(payload),
        "stream": payload.get("stream") is True,
    }
    return json.dumps(
        detail,
        separators=(",", ":"),
        sort_keys=True,
    )


async def bounded_body(response: httpx.Response, limit: int) -> bytes:
    buffer: Final = io.BytesIO()
    async for chunk in response.aiter_bytes():
        if buffer.tell() + len(chunk) > limit:
            raise ValueError("Upstream response is too large")
        buffer.write(chunk)
    return buffer.getvalue()


async def upstream_error_payload(
    response: httpx.Response,
    secrets: tuple[str, ...],
) -> tuple[dict[str, JsonValue], str | None]:
    try:
        data: Final = _JSON.validate_json(await bounded_body(response, 65536))
    except ValueError:
        return {"error": {"message": f"Upstream HTTP {response.status_code}", "type": "upstream_error"}}, None
    safe: Final = _safe_error_value(data, secrets)
    if not isinstance(safe, dict):
        return {"error": {"message": f"Upstream HTTP {response.status_code}", "type": "upstream_error"}}, None
    error: Final = safe.get("error")
    code: Final = error.get("code") if isinstance(error, dict) else None
    normalized_code: Final = code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) else None
    return safe, normalized_code


def _safe_error_value(value: JsonValue, secrets: tuple[str, ...], key: str | None = None) -> JsonValue:
    if key is not None and key.lower() in _SENSITIVE_ERROR_FIELDS:
        return "[REDACTED]"
    if isinstance(value, str):
        return reduce(
            lambda redacted, secret: redacted.replace(secret, "[REDACTED]") if secret else redacted,
            secrets,
            redact_secrets(value),
        )
    if isinstance(value, list):
        return [_safe_error_value(item, secrets) for item in value]
    if isinstance(value, dict):
        return {name: _safe_error_value(item, secrets, name) for name, item in value.items()}
    return value


def public_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.multi_items()
        if name in _RESPONSE_HEADERS or name.startswith(_RESPONSE_HEADER_PREFIXES)
    }


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
    budget_fallback: bool,
) -> RoutingReason:
    if attempt_number > 1:
        return "retry_failover"
    if route_index > 0:
        return "token_budget_fallback" if budget_fallback else "concurrency_fallback"
    if sticky_unavailable:
        return "session_rebind"
    return route.reason


def estimate_request_tokens(path: str, payload: Mapping[str, JsonValue], model: str) -> int:
    output_tokens: Final = requested_output_tokens(path, payload)
    if path == "/v1/images/generations":
        return output_tokens
    try:
        input_tokens: Final = (
            estimate_responses_input_tokens(payload, model)
            if path.startswith("/v1/responses")
            else estimate_chat_input_tokens(payload, model)
        )
    except (TypeError, ValueError):
        serialized: Final = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        return max(1, len(serialized)) + output_tokens
    return input_tokens + output_tokens


def estimate_chat_input_tokens(payload: Mapping[str, JsonValue], model: str) -> int:
    messages: Final = _MESSAGES.validate_python(payload.get("messages", ()))
    tools: Final = _CHAT_TOOLS.validate_python(payload.get("tools")) if payload.get("tools") is not None else None
    tool_choice_value: Final = payload.get("tool_choice")
    tool_choice: Final = (
        _TOOL_CHOICE.validate_python(tool_choice_value) if isinstance(tool_choice_value, dict) else None
    )
    return _TOKEN_COUNTER(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        use_default_image_token_count=True,
    )


def estimate_responses_input_tokens(payload: Mapping[str, JsonValue], model: str) -> int:
    request: Final = _responses_request_adapter().validate_python(payload)
    request_input: Final = _responses_input_adapter().validate_python(payload.get("input", ""))
    chat_request: Final = _RESPONSES_TO_CHAT(
        model,
        request_input,
        request,
    )
    messages: Final = _MESSAGES.validate_python(chat_request.get("messages", ()))
    tools_value: Final = chat_request.get("tools")
    tools: Final = _CHAT_TOOLS.validate_python(tools_value) if tools_value is not None else None
    tool_choice_value: Final = chat_request.get("tool_choice")
    tool_choice: Final = (
        _TOOL_CHOICE.validate_python(tool_choice_value) if isinstance(tool_choice_value, dict) else None
    )
    return _TOKEN_COUNTER(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        use_default_image_token_count=True,
    )


def requested_output_tokens(path: str, payload: Mapping[str, JsonValue]) -> int:
    fields: Final = (
        ("max_output_tokens",)
        if path.startswith("/v1/responses")
        else (
            "max_completion_tokens",
            "max_tokens",
        )
    )
    explicit: Final = tuple(
        value for field in fields for value in (payload.get(field),) if type(value) is int and value >= 0
    )
    if explicit:
        return max(explicit)
    return 0 if path == "/v1/images/generations" else 1024


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
                cache_read_input_tokens=state.cache_read_input_tokens,
                cache_creation_input_tokens=state.cache_creation_input_tokens,
                cost_usd=cost_usd,
            )

    await StreamingResponse(
        chunks(),
        status_code=response.status_code,
        media_type="text/event-stream",
        headers={
            **public_response_headers(response.headers),
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )(request.scope, request.receive, attempt.emit)
