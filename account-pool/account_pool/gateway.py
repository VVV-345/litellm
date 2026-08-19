"""实现 OpenAI 兼容网关，选号后改写模型并可靠结算号池租约。"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Final, cast
from uuid import uuid4

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import TypeAdapter, ValidationError

from account_pool.health.settlement import parse_retry_after_seconds
from account_pool.models import AcquireRequest, AcquireSuccess, SettleRequest
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


class Gateway:
    def __init__(self, scheduler: Scheduler, store: StateStore, client: httpx.AsyncClient, litellm_url: str) -> None:
        self._scheduler = scheduler
        self._store = store
        self._client = client
        self._litellm_url = litellm_url

    async def forward(self, path: str, request: Request) -> Response:
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
                        "details": acquired.reasons,
                    }
                },
            )

        lease: Final = acquired.lease
        metadata_value: Final = parsed.get("metadata")
        metadata: Final[dict[str, object]] = (
            _JSON_OBJECT.validate_python(metadata_value) if isinstance(metadata_value, dict) else {}
        )
        forwarded_body: Final = {
            **parsed,
            "model": lease.deployment_id,
            "metadata": {
                **metadata,
                "account_pool_lease_id": lease.lease_id,
                "account_pool_request_id": request_id,
                "account_pool_public_model": public_model,
            },
        }
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
            upstream: Final = await self._client.send(upstream_request, stream=bool(parsed.get("stream")))
        except httpx.HTTPError as exc:
            await self._store.settle(
                SettleRequest(
                    lease_id=lease.lease_id,
                    success=False,
                    error_type=type(exc).__name__,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            )
            await self._store.release(lease.lease_id)
            return _error_response(status_code=502, message="LiteLLM Proxy is unavailable")

        response_headers: Final = {
            name: value for name, value in upstream.headers.items() if name.lower() not in _RESPONSE_HEADER_DENYLIST
        }
        if bool(parsed.get("stream")):
            return StreamingResponse(
                self._stream_response(upstream=upstream, lease_id=lease.lease_id, started=started),
                status_code=upstream.status_code,
                headers=response_headers,
                media_type=_content_type(upstream),
            )

        content: Final = await upstream.aread()
        usage: Final = _usage_from_content(content)
        await upstream.aclose()
        await self._store.settle(
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
            )
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
        lease_id: str,
        started: float,
    ) -> AsyncIterator[bytes]:
        completed = False
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
            completed = True
        finally:
            await upstream.aclose()
            await self._store.settle(
                SettleRequest(
                    lease_id=lease_id,
                    success=completed and upstream.status_code < 400,
                    status_code=upstream.status_code,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error_type=None if completed and upstream.status_code < 400 else "stream_interrupted",
                    retry_after_seconds=_retry_after_from_response(upstream),
                )
            )
            await self._store.release(lease_id)


async def _request_json(request: Request) -> dict[str, object] | JSONResponse:
    try:
        value: Final = _JSON_OBJECT.validate_python(cast(object, await request.json()))
    except (json.JSONDecodeError, ValidationError):
        return _error_response(status_code=400, message="Request body must be valid JSON")
    return value


def _estimated_tokens(body: Mapping[str, object]) -> int:
    max_tokens: Final = body.get("max_tokens") or body.get("max_completion_tokens")
    return max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else 0


def _usage_from_content(content: bytes) -> tuple[int, int]:
    try:
        value: Final = _JSON_OBJECT.validate_python(cast(object, json.loads(content)))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
        return 0, 0
    usage: Final = value.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    typed_usage: Final = _JSON_OBJECT.validate_python(usage)
    input_tokens: Final = typed_usage.get("prompt_tokens", typed_usage.get("input_tokens", 0))
    output_tokens: Final = typed_usage.get("completion_tokens", typed_usage.get("output_tokens", 0))
    return (
        input_tokens if isinstance(input_tokens, int) else 0,
        output_tokens if isinstance(output_tokens, int) else 0,
    )


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
        (
            candidate
            for candidate in candidates
            if isinstance(candidate, str) and candidate and len(candidate) <= 128
        ),
        None,
    )


def _retry_after_from_response(response: httpx.Response) -> float | None:
    value: Final = cast(str | None, response.headers.get("retry-after"))
    return parse_retry_after_seconds(value, datetime.now(UTC))


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"message": message, "type": "gateway_error"}})


def _content_type(response: httpx.Response) -> str | None:
    return cast(str | None, response.headers.get("content-type"))
