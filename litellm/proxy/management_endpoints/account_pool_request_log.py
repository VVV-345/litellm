"""本模块在网关采集有界对话与用量，独立保存完整正文并生成日常日志字段。"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from datetime import datetime, timezone
from functools import reduce
from typing import Final, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import JsonValue, TypeAdapter
from starlette.datastructures import Headers

from litellm._logging import redact_secrets, verbose_proxy_logger
from litellm.proxy.management_endpoints.account_pool_accounting import (
    PriceSnapshot,
    estimate_cost,
    price_snapshot,
    sync_spend,
)
from litellm.proxy.management_endpoints.account_pool_full_logs import FullLogRecord, full_log_store
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import FinishRequest, Lease, Resolution
from litellm.proxy.management_endpoints.account_pool_routing import Route
from litellm.proxy.management_endpoints.account_pool_stream import cache_usage_tokens, usage_tokens

_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_JSON: Final = TypeAdapter(dict[str, JsonValue])
_MAX_CONTENT: Final = 16 * 1024 * 1024
_SECRET_FIELDS: Final = frozenset(
    (
        "authorization",
        "cookie",
        "set-cookie",
        "api_key",
        "api-key",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "password",
        "secret",
        "proxy-authorization",
    )
)
_SESSION_HEADERS: Final = (
    "x-litellm-session-id",
    "x-claude-code-session-id",
    "x-session-id",
    "session-id",
    "session_id",
    "thread-id",
    "conversation_id",
    "x-session-affinity",
)


def conversation_id(headers: Headers, key_id: str) -> str | None:
    session: Final = next((headers[name] for name in _SESSION_HEADERS if headers.get(name)), None)
    if not session or len(session) > 512:
        return None
    return "pool-" + hashlib.sha256(f"{key_id}:{session}".encode()).hexdigest()


def clean_content(value: JsonValue, secrets: tuple[str, ...]) -> JsonValue:
    if isinstance(value, dict):
        return {
            name: "[REDACTED]" if name.lower() in _SECRET_FIELDS else clean_content(item, secrets)
            for name, item in value.items()
        }
    if isinstance(value, list):
        return [clean_content(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    scrubbed: Final = redact_secrets(reduce(lambda text, secret: text.replace(secret, "[REDACTED]"), secrets, value))
    if scrubbed.startswith("data:") and ";base64," in scrubbed[:200]:
        return "[附件内联数据未保存]"
    if scrubbed.startswith(("https://", "http://")):
        try:
            url: Final = urlsplit(scrubbed)
        except ValueError:
            return "[无效链接]"
        return urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
    return re.sub(
        r'(?i)(["\']?(?:access_token|refresh_token|id_token|api_key|authorization|cookie|password|client_secret)["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+',
        r"\1[REDACTED]",
        re.sub(r"\b(?:cpk_|sk-)[A-Za-z0-9_-]{12,}\b", "[REDACTED]", scrubbed),
    )


class RequestLog:
    def __init__(
        self,
        lease: Lease,
        route: Route,
        resolution: Resolution,
        headers: Headers,
        payload: dict[str, JsonValue],
        key: str,
        transport: Literal["http", "sse", "websocket"],
        standard_accounting: bool = False,
    ) -> None:
        self.lease: Final = lease
        self.key: Final = key
        self.transport: Final = transport
        self.standard_accounting: Final = standard_accounting
        self.enabled: Final = resolution.full_logging_enabled
        self.skip_failed: Final = resolution.full_log_skip_failed
        self.session_id: Final = conversation_id(headers, str(lease.key_id))
        self.proxy_endpoint: Final = route.account.proxy_endpoint
        self.requested_model: Final = str(payload.get("model", lease.model))
        self.secrets: Final = tuple(
            value
            for value in (
                key,
                route.account.api_key,
                *(item.api_key for item in route.account.credentials),
                *(value for name, value in route.account.headers if name.lower() in _SECRET_FIELDS),
            )
            if value
        )
        try:
            requested_price: Final = price_snapshot(str(lease.account_id), self.requested_model)
            self.price = (
                requested_price
                if requested_price.source != "unknown"
                else price_snapshot(str(lease.account_id), lease.model)
            )
        except Exception:  # noqa: BLE001  # 计价组件故障不能阻止转发已取得租约的请求。
            self.price = PriceSnapshot(model=lease.model, model_id="")
        self.request: Final = clean_content(payload, self.secrets) if self.enabled else None
        self.response = io.BytesIO() if self.enabled else None
        self.client_frames = io.BytesIO() if self.enabled and transport == "websocket" else None
        self.truncated = False
        self.usage: dict[str, JsonValue] | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cache_read: int | None = None
        self.cache_creation: int | None = None
        self.responses: frozenset[str] = frozenset()
        self.failure = False
        self.finished = False
        requested_tier: Final = payload.get("service_tier")
        self.service_tier: str | None = requested_tier if isinstance(requested_tier, str) else None
        self.websocket_cost = 0.0
        self.websocket_cost_known = True
        self.websocket_pending = False

    def capture(self, data: bytes, *, incoming: bool = False) -> None:
        buffer: Final = self.client_frames if incoming else self.response
        if buffer is None:
            return
        room: Final = _MAX_CONTENT - buffer.tell()
        if len(data) > room:
            self.truncated = True
        buffer.write(data[:room])

    def observe(self, event: dict[str, JsonValue]) -> None:
        response: Final = event.get("response")
        message: Final = event.get("message")
        source: Final = response if isinstance(response, dict) else message if isinstance(message, dict) else event
        usage: Final = source.get("usage")
        tier: Final = source.get("service_tier")
        if isinstance(tier, str):
            self.service_tier = tier
        if event.get("type") in ("error", "response.failed", "response.incomplete") or event.get("error"):
            self.failure = True
        if event.get("type") in ("response.completed", "response.done", "message_stop"):
            self.finished = True
        if self.transport == "websocket":
            if event.get("type") not in (
                "response.done",
                "response.completed",
                "response.failed",
                "response.incomplete",
            ):
                return
            self.websocket_pending = False
            identifier: Final = source.get("id")
            if not isinstance(identifier, str) or identifier in self.responses:
                return
            self.responses = self.responses | frozenset((identifier,))
            if not isinstance(usage, dict):
                self.websocket_cost_known = False
                return
        if not isinstance(usage, dict):
            return
        self.usage = {**(self.usage or {}), **usage}
        inputs, outputs = usage_tokens({"usage": usage})
        read, created = cache_usage_tokens({"usage": usage})
        if self.transport == "websocket":
            priced: Final = estimate_cost(
                FinishRequest(
                    lease_id=self.lease.lease_id,
                    endpoint="/v1/responses",
                    http_status=200,
                    message="done",
                    input_tokens=inputs,
                    output_tokens=outputs,
                    cache_read_input_tokens=read,
                    cache_creation_input_tokens=created,
                ),
                self.price,
                usage,
                tier if isinstance(tier, str) else None,
            )
            self.websocket_cost += priced.cost_usd or 0.0
            self.websocket_cost_known = self.websocket_cost_known and priced.cost_usd is not None
        self.input_tokens = self._counter(self.input_tokens, inputs)
        self.output_tokens = self._counter(self.output_tokens, outputs)
        self.cache_read = self._counter(self.cache_read, read)
        self.cache_creation = self._counter(self.cache_creation, created)

    def _counter(self, previous: int | None, value: int | None) -> int | None:
        if value is None:
            return previous
        return (previous or 0) + value if self.transport == "websocket" else value

    def observe_frame(self, frame: bytes) -> None:
        payload: Final = b"\n".join(line[5:].lstrip(b" ") for line in frame.splitlines() if line.startswith(b"data:"))
        if payload == b"[DONE]":
            self.finished = True
            return
        if payload:
            self.observe(_JSON.validate_json(payload))

    def observe_websocket(self, data: str | bytes, *, incoming: bool = False) -> None:
        raw: Final = data.encode() if isinstance(data, str) else data
        self.capture(raw + b"\n", incoming=incoming)
        try:
            if incoming:
                if _JSON.validate_json(raw).get("type") == "response.create":
                    self.websocket_pending = True
            else:
                self.observe(_JSON.validate_json(raw))
        except ValueError:
            return

    def _body(self, buffer: io.BytesIO | None) -> JsonValue:
        if buffer is None:
            return None
        raw: Final = buffer.getvalue().decode("utf-8", errors="replace")
        try:
            return clean_content(_VALUE.validate_json(raw), self.secrets)
        except ValueError:
            events: Final = tuple(
                line[5:].strip() if line.startswith("data:") else line.strip()
                for line in raw.splitlines()
                if (line.startswith("data:") or self.transport == "websocket") and line.strip() != "data: [DONE]"
            )
            return (
                [self._clean_event(event) for event in events if event] if events else clean_content(raw, self.secrets)
            )

    def _clean_event(self, raw: str) -> JsonValue:
        try:
            return clean_content(_VALUE.validate_json(raw), self.secrets)
        except ValueError:
            return clean_content(raw, self.secrets)

    async def finish(self, result: FinishRequest) -> FinishRequest:
        enriched: Final = result.model_copy(
            update={
                "cost_usd": None if self.standard_accounting else result.cost_usd,
                "session_id": self.session_id,
                "proxy_endpoint": self.proxy_endpoint,
                "input_tokens": self.input_tokens if self.input_tokens is not None else result.input_tokens,
                "output_tokens": self.output_tokens if self.output_tokens is not None else result.output_tokens,
                "cache_read_input_tokens": self.cache_read
                if self.cache_read is not None
                else result.cache_read_input_tokens,
                "cache_creation_input_tokens": self.cache_creation
                if self.cache_creation is not None
                else result.cache_creation_input_tokens,
            }
        )
        estimate: Final = estimate_cost(enriched, self.price, self.usage, self.service_tier)
        priced: Final = (
            estimate.model_copy(
                update={
                    "cost_usd": self.websocket_cost
                    if self.websocket_cost_known and self.responses and not self.websocket_pending
                    else None,
                    "cost_source": self.price.source
                    if self.websocket_cost_known and self.responses and not self.websocket_pending
                    else "unknown",
                }
            )
            if self.transport == "websocket"
            else estimate
        )
        synced: Final = await self._sync(priced)
        keep_full: Final = self.enabled and not (self.skip_failed and self.incomplete(synced))
        return await self._save_full(synced) if keep_full else synced

    def incomplete(self, result: FinishRequest) -> bool:
        return (
            result.http_status >= 400
            or self.failure
            or self.websocket_pending
            or (self.transport != "http" and not self.finished)
        )

    async def _sync(self, result: FinishRequest, retries: int = 2) -> FinishRequest:
        if self.standard_accounting:
            return result.model_copy(update={"spend_sync_state": "standard"})
        try:
            state: Final = await asyncio.wait_for(
                sync_spend(self.lease, result, self.requested_model, self.key), timeout=10
            )
            return FinishRequest.model_validate({**result.model_dump(), "spend_sync_state": state})
        except Exception:  # noqa: BLE001  # 隔离数据库驱动故障，仍须保存日常摘要并释放租约。
            if retries:
                await asyncio.sleep(0.2)
                return await self._sync(result, retries - 1)
            verbose_proxy_logger.warning("Account pool Usage sync failed: event=%s", self.lease.lease_id)
            return result.model_copy(update={"spend_sync_state": "failed"})

    async def _save_full(self, result: FinishRequest) -> FinishRequest:
        saved: Final = result.model_copy(update={"full_log_state": "truncated" if self.truncated else "stored"})
        record: Final = FullLogRecord(
            event_id=self.lease.lease_id,
            request_id=self.lease.request_id,
            card_id=self.lease.card_id,
            account_id=self.lease.account_id,
            key_id=self.lease.key_id,
            session_id=self.session_id,
            started_at=self.lease.started_at,
            finished_at=datetime.now(timezone.utc),
            model=self.lease.model,
            requested_model=self.requested_model,
            attempt=self.lease.attempt,
            result=saved,
            transport=self.transport,
            incomplete=self.incomplete(result),
            truncated=self.truncated,
            skip_failed=self.skip_failed,
            request=self._body(self.client_frames) if self.client_frames else self.request,
            response=self._body(self.response),
        )
        try:
            await asyncio.to_thread(full_log_store().append, record)
        except Exception:  # noqa: BLE001  # 正文持久化故障不能改变已经发送的上游结果。
            verbose_proxy_logger.warning("Account pool full log persistence failed: event=%s", self.lease.lease_id)
            return result.model_copy(update={"full_log_state": "failed"})
        return saved
