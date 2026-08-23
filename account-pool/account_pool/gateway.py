"""实现 OpenAI 兼容网关，选号后改写模型并可靠结算号池租约。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Final, cast
from uuid import uuid4

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import TypeAdapter, ValidationError

from account_pool.health.service import HealthEventRecorder
from account_pool.health.settlement import parse_retry_after_seconds
from account_pool.models import AcquireRequest, AcquireSuccess, Lease, SettleRequest
from account_pool.scheduler import Scheduler
from account_pool.store import StateStore

_REQUEST_HEADER_ALLOWLIST: Final = frozenset(
    {
        "accept",
        "authorization",
        "content-type",
        "user-agent",
        "x-litellm-api-key",
        "x-request-id",
    }
)
_RESPONSE_HEADER_DENYLIST: Final = frozenset(
    {"connection", "content-encoding", "content-length", "keep-alive", "transfer-encoding"}
)
_JSON_OBJECT: Final = TypeAdapter(dict[str, object])
_PRE_AUTH_CACHE_MAX_ENTRIES: Final = 10_000


class Gateway:
    def __init__(
        self,
        scheduler: Scheduler,
        store: StateStore,
        client: httpx.AsyncClient,
        litellm_url: str,
        lease_ttl_seconds: int,
        health_recorder: HealthEventRecorder | None = None,
        pre_auth: bool = False,
        pre_auth_cache_seconds: int = 30,
    ) -> None:
        self._scheduler = scheduler
        self._store = store
        self._client = client
        self._litellm_url = litellm_url
        self._lease_ttl_seconds: Final = lease_ttl_seconds
        self._health_recorder = health_recorder
        self._pre_auth: Final = pre_auth
        self._pre_auth_cache_seconds: Final = pre_auth_cache_seconds
        self._pre_auth_cache: Final[dict[str, float]] = {}
        self._pre_auth_lock: Final = asyncio.Lock()

    async def forward(self, path: str, request: Request) -> Response:
        if self._pre_auth:
            authorized: Final = await self._pre_authorized(request)
            if authorized is not None:
                return authorized
        parsed: Final = await _request_json(request)
        if isinstance(parsed, JSONResponse):
            return parsed
        public_model: Final = parsed.get("model")
        if not isinstance(public_model, str) or not public_model:
            return _error_response(status_code=400, message="Request body must contain a model")

        request_id: Final = request.headers.get("x-request-id") or uuid4().hex
        estimate: Final = _estimated_tokens(parsed)
        acquired: Final = await self._scheduler.acquire(
            AcquireRequest(request_id=request_id, model=public_model, estimated_tokens=estimate)
        )
        if not isinstance(acquired, AcquireSuccess):
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": "No account capacity is available for this model",
                        "type": "account_pool_unavailable",
                        "details": {**acquired.model_dump(mode="json"), "reasons": acquired.reasons},
                    }
                },
            )

        lease: Final = acquired.lease
        await self._record_request_activity(lease)
        metadata_value: Final = parsed.get("metadata")
        metadata: Final[dict[str, object]] = (
            _JSON_OBJECT.validate_python(metadata_value) if isinstance(metadata_value, dict) else {}
        )
        forwarded_body: Final = _with_stream_usage(parsed, lease, request_id, metadata)
        headers: Final = {
            name: value for name, value in request.headers.items() if name.lower() in _REQUEST_HEADER_ALLOWLIST
        }
        upstream_request: Final = self._client.build_request(
            method="POST",
            url=f"{self._litellm_url}/v1/{path}",
            headers=headers,
            json=forwarded_body,
        )
        started: Final = time.perf_counter()
        try:
            async with asyncio.timeout(max(0, lease.absolute_expires_at - time.time())):
                upstream: Final = await self._client.send(upstream_request, stream=bool(parsed.get("stream")))
        except (httpx.HTTPError, TimeoutError) as exc:
            await self._settle(
                lease,
                SettleRequest(
                    lease_id=lease.lease_id,
                    success=False,
                    error_type=type(exc).__name__,
                    latency_ms=(time.perf_counter() - started) * 1000,
                ),
            )
            await self._store.release(lease.lease_id)
            return _error_response(status_code=502, message="LiteLLM Proxy is unavailable")

        response_headers: Final = {
            name: value for name, value in upstream.headers.items() if name.lower() not in _RESPONSE_HEADER_DENYLIST
        }
        if bool(parsed.get("stream")):
            return StreamingResponse(
                self._stream_response(upstream=upstream, lease=lease, started=started),
                status_code=upstream.status_code,
                headers=response_headers,
                media_type=_content_type(upstream),
            )

        content: Final = await upstream.aread()
        usage: Final = _usage_from_content(content)
        await upstream.aclose()
        await self._settle(
            lease,
            SettleRequest(
                lease_id=lease.lease_id,
                success=upstream.status_code < 400,
                status_code=upstream.status_code,
                input_tokens=usage[0],
                output_tokens=usage[1],
                latency_ms=(time.perf_counter() - started) * 1000,
                error_type=None if upstream.status_code < 400 else "proxy_http_status",
                provider_error_code=None if upstream.status_code < 400 else _provider_error_code(content),
                retry_after_seconds=_retry_after_from_response(upstream),
            ),
        )
        await self._store.release(lease.lease_id)
        return Response(
            content=content,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=_content_type(upstream),
        )

    async def _stream_response(
        self,
        upstream: httpx.Response,
        lease: Lease,
        started: float,
    ) -> AsyncIterator[bytes]:
        completed = False
        usage = (0, 0)
        heartbeat_failed = asyncio.Event()
        heartbeat_task: Final = asyncio.create_task(self._maintain_stream_lease(upstream, lease, heartbeat_failed))
        try:
            async with asyncio.timeout(max(0, lease.absolute_expires_at - time.time())):
                async for chunk in upstream.aiter_bytes():
                    usage = _stream_usage_from_chunk(chunk, usage)
                    yield chunk
                completed = not heartbeat_failed.is_set()
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            await upstream.aclose()
            await self._settle(
                lease,
                SettleRequest(
                    lease_id=lease.lease_id,
                    success=completed and upstream.status_code < 400,
                    status_code=upstream.status_code,
                    input_tokens=usage[0],
                    output_tokens=usage[1],
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error_type=None if completed and upstream.status_code < 400 else "stream_interrupted",
                    retry_after_seconds=_retry_after_from_response(upstream),
                ),
            )
            await self._store.release(lease.lease_id)

    async def _maintain_stream_lease(
        self,
        upstream: httpx.Response,
        lease: Lease,
        failed: asyncio.Event,
    ) -> None:
        interval: Final = max(0.1, self._lease_ttl_seconds / 2)
        while True:
            remaining = lease.absolute_expires_at - time.time()  # rebind-ok: 每个心跳周期都要重新计算剩余时间
            if remaining <= 0 or not await self._store.heartbeat(
                lease.lease_id,
                self._lease_ttl_seconds,
            ):
                failed.set()
                await upstream.aclose()
                return
            await asyncio.sleep(min(interval, remaining))

    async def _pre_authorized(self, request: Request) -> JSONResponse | None:
        key: Final = request.headers.get("x-litellm-api-key") or request.headers.get("authorization") or ""
        if not key:
            return _error_response(status_code=401, message="Missing API key")
        now: Final = time.monotonic()
        cache_key: Final = sha256(key.encode("utf-8")).hexdigest()
        async with self._pre_auth_lock:
            cached_until: Final = self._pre_auth_cache.get(cache_key)
            if cached_until is not None and cached_until > now:
                return None
        probe: Final = self._client.build_request(
            method="GET",
            url=f"{self._litellm_url}/key/info",
            headers={"authorization": key},
        )
        try:
            response: Final = await self._client.send(probe)
        except httpx.HTTPError:
            return _error_response(status_code=502, message="LiteLLM Proxy is unavailable")
        if response.status_code in (200, 403):
            async with self._pre_auth_lock:
                if len(self._pre_auth_cache) >= _PRE_AUTH_CACHE_MAX_ENTRIES:
                    expired: Final = tuple(digest for digest, until in self._pre_auth_cache.items() if until <= now)
                    for digest in expired:
                        del self._pre_auth_cache[digest]
                if len(self._pre_auth_cache) < _PRE_AUTH_CACHE_MAX_ENTRIES:
                    self._pre_auth_cache[cache_key] = now + self._pre_auth_cache_seconds
            return None
        return _error_response(status_code=401, message="Invalid API key")

    async def _record_request_activity(self, lease: Lease) -> None:
        if self._health_recorder is not None:
            await self._health_recorder.record_request(lease)

    async def _settle(self, lease: Lease, request: SettleRequest) -> bool:
        settled: Final = await self._store.settle(request)
        if self._health_recorder is not None:
            await self._health_recorder.record_passive(lease, request)
        return settled


async def _request_json(request: Request) -> dict[str, object] | JSONResponse:
    try:
        value: Final = _JSON_OBJECT.validate_python(cast(object, await request.json()))
    except (json.JSONDecodeError, ValidationError):
        return _error_response(status_code=400, message="Request body must be valid JSON")
    return value


def _estimated_tokens(body: Mapping[str, object]) -> int:
    max_tokens: Final = body.get("max_tokens") or body.get("max_completion_tokens")
    return max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else 0


def _with_stream_usage(
    body: Mapping[str, object],
    lease: Lease,
    request_id: str,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    forwarded: Final[dict[str, object]] = {
        **body,
        "model": lease.deployment_id,
        "metadata": {
            **metadata,
            "account_pool_lease_id": lease.lease_id,
            "account_pool_request_id": request_id,
            "account_pool_public_model": lease.public_model,
        },
    }
    if body.get("stream") is True:
        stream_options: Final = body.get("stream_options")
        if isinstance(stream_options, dict) and stream_options.get("include_usage") is True:
            return forwarded
        forwarded["stream_options"] = {
            **(stream_options if isinstance(stream_options, dict) else {}),
            "include_usage": True,
        }
    return forwarded


def _usage_fields(usage: Mapping[str, object]) -> tuple[int, int]:
    input_tokens: Final = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    output_tokens: Final = usage.get("completion_tokens", usage.get("output_tokens", 0))
    return (
        input_tokens if isinstance(input_tokens, int) else 0,
        output_tokens if isinstance(output_tokens, int) else 0,
    )


def _usage_from_content(content: bytes) -> tuple[int, int]:
    try:
        value: Final = _JSON_OBJECT.validate_python(cast(object, json.loads(content)))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
        return 0, 0
    usage: Final = value.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    return _usage_fields(_JSON_OBJECT.validate_python(usage))


def _stream_usage_from_chunk(chunk: bytes, previous: tuple[int, int]) -> tuple[int, int]:
    for line in chunk.splitlines():
        payload = line.removeprefix(b"data: ").strip()
        if not payload or payload == b"[DONE]":
            continue
        try:
            value = _JSON_OBJECT.validate_python(cast(object, json.loads(payload)))
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            continue
        usage = value.get("usage")
        if isinstance(usage, dict):
            extracted = _usage_fields(_JSON_OBJECT.validate_python(usage))
            if extracted != (0, 0):
                return extracted
    return previous


def _provider_error_code(content: bytes) -> str | None:
    try:
        value: Final = _JSON_OBJECT.validate_python(cast(object, json.loads(content)))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
        return None
    error: Final = value.get("error")
    if not isinstance(error, dict):
        return None
    typed_error: Final = _JSON_OBJECT.validate_python(error)
    candidates: Final = (typed_error.get("code"), typed_error.get("type"))
    return next(
        (candidate for candidate in candidates if isinstance(candidate, str) and candidate and len(candidate) <= 128),
        None,
    )


def _retry_after_from_response(response: httpx.Response) -> float | None:
    value: Final = cast(str | None, response.headers.get("retry-after"))
    return parse_retry_after_seconds(value, datetime.now(UTC))


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"message": message, "type": "gateway_error"}})


def _content_type(response: httpx.Response) -> str | None:
    return cast(str | None, response.headers.get("content-type"))
