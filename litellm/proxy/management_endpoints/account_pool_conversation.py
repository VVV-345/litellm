"""Responses 可见历史的短期加密缓存与安全迁移，不保存上游签名。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, cast

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from litellm._logging import verbose_proxy_logger
from litellm.caching.dual_cache import DualCache
from litellm.exceptions import ServiceUnavailableError
from litellm.litellm_core_utils.signature_recovery import signature_recovery_reason
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value, encrypt_value
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity
from litellm.proxy.management_endpoints.account_pool_native_routing import (
    exhausted_snapshot,
    now_utc,
    quota_failover_exclusions,
    snapshot_for,
)
from litellm.proxy.management_endpoints.account_pool_native_routing import snapshots as quota_snapshots
from litellm.proxy.management_endpoints.account_pool_session import has_signed_history
from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator
from litellm.responses.utils import ResponsesAPIRequestUtils
from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import EncryptedContentAffinityCheck
from litellm.types.llms.openai import ResponseCompletedEvent, ResponsesAPIResponse, ResponsesAPIStreamingResponse
from litellm.types.router import RouterRateLimitError, RouterRateLimitErrorBasic

if TYPE_CHECKING:
    from litellm import Router

_JSON: Final = TypeAdapter(dict[str, JsonValue])
_INPUT: Final = TypeAdapter(list[dict[str, JsonValue]])
_EXCLUSIONS: Final = TypeAdapter(tuple[str, ...])
_DEPLOYMENTS: Final = TypeAdapter(tuple[Mapping[str, object], ...])
_FIELDS: Final = frozenset(("input", "tools", "functions", "background", "conversation", "store", "truncation"))
_MAX_BYTES: Final = 256 * 1024
_TTL: Final = 3600


class ConversationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input: tuple[Mapping[str, JsonValue], ...]
    deployment_id: str
    expires_at: float


class ConversationStore:
    def __init__(self, cache: DualCache, secret: str) -> None:
        self.cache: Final = cache
        self.secret: Final = secret

    def key(self, identity: PoolIdentity, model: str, response_id: str) -> str:
        material: Final = _JSON.dump_json(
            _JSON.validate_python(MappingProxyType({"key": identity.key_hash, "model": model, "response": response_id}))
        )
        return "account-pool-conversation:v1:" + hmac.new(self.secret.encode(), material, hashlib.sha256).hexdigest()

    async def load(self, identity: PoolIdentity, model: str, response_id: str) -> ConversationRecord | None:
        try:
            cached: Final = cast(  # cast-ok: Narrow untyped cache values before validation.
                object,
                await self.cache.async_get_cache(self.key(identity, model, response_id)),  # pyright: ignore[reportUnknownMemberType]  # Validate untyped DualCache output below.
            )
            if not isinstance(cached, str) or len(cached) > _MAX_BYTES * 2:
                return None
            record: Final = ConversationRecord.model_validate_json(
                decrypt_value(base64.b64decode(cached, validate=True), self.secret)
            )
            return record if record.expires_at > time.time() else None
        except Exception:
            verbose_proxy_logger.warning("Account pool conversation cache unavailable; recovery disabled")
            return None

    async def save(self, identity: PoolIdentity, model: str, response_id: str, record: ConversationRecord) -> None:
        serialized: Final = record.model_dump_json()
        if len(serialized.encode()) > _MAX_BYTES:
            return
        try:
            encrypted: Final = base64.b64encode(encrypt_value(serialized, self.secret)).decode()
            await self.cache.async_set_cache(self.key(identity, model, response_id), encrypted, ttl=_TTL)  # pyright: ignore[reportUnknownMemberType]  # Existing cache accepts typed string values and TTL.
        except Exception:
            verbose_proxy_logger.warning("Account pool conversation cache write failed; response not retained")


def visible_input(value: object) -> tuple[Mapping[str, JsonValue], ...] | None:
    if isinstance(value, str):
        return (MappingProxyType({"role": "user", "content": value}),) if value else None
    try:
        items: Final = _INPUT.validate_python(value)
    except ValidationError:
        return None
    if any(not portable_item(item) for item in items):
        return None
    return tuple(
        MappingProxyType(
            {
                key: content
                for key, content in item.items()
                if key not in ("id", "status", "encrypted_content") and content is not None
            }
        )
        for item in items
        if item.get("type") != "reasoning"
    )


def portable_item(item: Mapping[str, JsonValue]) -> bool:
    kind: Final = item.get("type")
    if kind == "reasoning":
        return True
    if kind in ("function_call", "custom_tool_call"):
        return isinstance(item.get("call_id"), str) and isinstance(item.get("name"), str)
    if kind in ("function_call_output", "custom_tool_call_output"):
        return isinstance(item.get("call_id"), str) and isinstance(item.get("output"), str)
    if kind not in (None, "message") or item.get("role") not in ("user", "assistant", "system", "developer"):
        return False
    content: Final = item.get("content")
    return (
        isinstance(content, str)
        or isinstance(content, list)
        and bool(content)
        and all(
            isinstance(block, dict)
            and (
                block.get("type") in ("input_text", "output_text", "text")
                and isinstance(block.get("text"), str)
                or block.get("type") == "refusal"
                and isinstance(block.get("refusal"), str)
            )
            and not block.get("annotations")
            for block in content
        )
    )


def retry_exhausted(error: BaseException, depth: int = 0) -> bool:
    headers: Final = getattr(error, "headers", None)
    response: Final = getattr(error, "response", None)
    if (
        isinstance(headers, (Mapping, httpx.Headers))
        and cast(Mapping[str, object], headers).get("x-account-pool-rebuild") == "retry-exhausted"  # cast-ok: Only compare the opaque exception header value after checking the mapping protocol.
        or isinstance(response, httpx.Response)
        and response.headers.get("x-account-pool-rebuild") == "retry-exhausted"
    ):
        return True
    previous: Final = error.__cause__ or error.__context__
    return depth < 5 and previous is not None and retry_exhausted(previous, depth + 1)


class Conversation:
    def __init__(
        self,
        identity: PoolIdentity,
        model: str,
        request: Mapping[str, JsonValue],
        history: tuple[Mapping[str, JsonValue], ...] | None,
        origin: str | None,
        store: ConversationStore,
    ) -> None:
        self.identity: Final = identity
        self.model: Final = model
        self.request: Final = request
        self.history: Final = history
        self.origin: Final = origin
        self.store: Final = store

    @classmethod
    async def start(cls, router: Router, payload: Mapping[str, object]) -> Conversation | None:
        identity: Final = pool_identity.get()
        model: Final = payload.get("model")
        secret: Final = os.getenv("ACCOUNT_POOL_MANAGER_TOKEN", "")
        if identity is None or not isinstance(model, str) or len(secret) < 32 or payload.get("extra_body"):
            return None
        deployments: Final = router.get_model_list(model_name=model) or ()
        if not any(
            (snapshot := snapshot_for(item, quota_snapshots.values)) is not None
            and (snapshot.quota_switch_project or snapshot.fallback_enabled)
            for item in deployments
        ):
            return None
        try:
            request: Final = _JSON.validate_python(
                MappingProxyType({key: value for key, value in payload.items() if key in _FIELDS})
            )
        except ValidationError:
            return None
        current: Final = visible_input(request.get("input"))
        store: Final = ConversationStore(router.cache, secret)
        previous: Final = payload.get("previous_response_id")
        record: Final = await store.load(identity, model, previous) if isinstance(previous, str) else None
        origin: Final = (
            record.deployment_id
            if record is not None
            else ResponsesAPIRequestUtils.get_model_id_from_response_id(previous)
            if isinstance(previous, str)
            else EncryptedContentAffinityCheck._extract_model_id_from_input(payload.get("input"))  # pyright: ignore[reportPrivateUsage]  # Share the native affinity marker parser.
        )
        history: Final = (
            None
            if current is None or previous and record is None
            else (*(record.input if record is not None else ()), *current)
        )
        return cls(identity, model, request, history, origin, store)

    def can_replay(self, router: Router, payload: Mapping[str, object]) -> bool:
        if (
            self.history is None
            or payload.get("disable_fallbacks")
            or payload.get("max_fallbacks", router.max_fallbacks) == 0
        ):
            return False
        if not has_signed_history(payload):
            return False
        if self.request.get("truncation") == "auto":
            return False
        restored: Final = _JSON.validate_python(
            MappingProxyType({**self.request, "input": _INPUT.validate_python(self.history)})
        )
        return signature_recovery_reason(restored) is None

    def rebuild(self, router: Router, payload: Mapping[str, object], error: Exception) -> Mapping[str, object] | None:
        if not self.can_replay(router, payload):
            return None
        deployments: Final = _DEPLOYMENTS.validate_python(router.get_model_list(model_name=self.model) or ())
        failed: Final = getattr(error, "failed_deployment_id", None) or self.origin
        if not isinstance(failed, str):
            return None
        origin: Final = next((item for item in deployments if deployment_id(item) == failed), None)
        if origin is None:
            return None
        snapshot: Final = snapshot_for(origin, quota_snapshots.values)
        if snapshot is None:
            return None
        quota: Final = snapshot.model_quotas.get(snapshot.model_aliases.get(self.model, self.model), snapshot.quota)
        remaining: Final = quota.effective(now_utc())[0]
        depleted_before_call: Final = (
            isinstance(error, (RouterRateLimitError, RouterRateLimitErrorBasic, ServiceUnavailableError))
            and getattr(error, "failed_deployment_id", None) is None
            and remaining is not None
            and remaining <= snapshot.quota_reserve_percent
        )
        quota_migration: Final = snapshot.quota_switch_project and (
            exhausted_snapshot(error, deployments, self.model) is not None or depleted_before_call
        )
        if not quota_migration and not (snapshot.fallback_enabled and retry_exhausted(error)):
            return None
        previous_excluded: Final = payload.get("_excluded_deployment_ids")
        excluded: Final = quota_failover_exclusions(
            deployments,
            failed,
            _EXCLUSIONS.validate_python(previous_excluded) if previous_excluded is not None else (),
            self.model,
        )
        if not any(deployment_id(item) not in excluded for item in deployments):
            return None
        verbose_proxy_logger.info("Account pool conversation migration; request_id=%s", self.identity.request_id)
        return MappingProxyType(
            {
                **MappingProxyType({key: value for key, value in payload.items() if key != "previous_response_id"}),
                "input": _INPUT.validate_python(self.history),
                "_excluded_deployment_ids": excluded,
                "disable_fallbacks": True,
                "num_retries": 0,
            }
        )

    async def complete(self, response: ResponsesAPIResponse) -> None:
        if self.request.get("store") is False or self.history is None or response.status != "completed":
            return
        if (
            self.request.get("background")
            or self.request.get("conversation")
            or self.request.get("truncation") == "auto"
        ):
            return
        deployment_id: Final = ResponsesAPIRequestUtils.get_model_id_from_response_id(response.id)
        output: Final = visible_input(response.model_dump(mode="json").get("output"))
        if deployment_id is None or output is None:
            return
        await self.store.save(
            self.identity,
            self.model,
            response.id,
            ConversationRecord(
                input=(*self.history, *output), deployment_id=deployment_id, expires_at=time.time() + _TTL
            ),
        )


class ConversationStream(BaseResponsesAPIStreamingIterator):
    def __init__(self, source: BaseResponsesAPIStreamingIterator, conversation: Conversation) -> None:
        self.source: Final = source
        self.conversation: Final = conversation

    def __getattr__(self, name: str) -> object:
        value: Final = cast(  # cast-ok: Delegate native stream attributes as opaque objects.
            object, getattr(self.source, name)
        )
        return value

    def __aiter__(self) -> ConversationStream:
        return self

    async def __anext__(self) -> ResponsesAPIStreamingResponse:
        event: Final = await anext(
            cast(  # cast-ok: Native streams implement iteration, absent on base annotations.
                AsyncIterator[ResponsesAPIStreamingResponse], self.source
            )
        )
        if isinstance(event, ResponseCompletedEvent):
            await self.conversation.complete(event.response)
        return event

    async def aclose(self) -> None:
        close: Final = cast(  # cast-ok: Optional native stream cleanup protocol.
            Callable[[], Awaitable[None]] | None,  # mutable-ok: Callable syntax needs brackets.
            getattr(self.source, "aclose", None),
        )
        if close is not None:
            await close()
        elif getattr(self.source, "response", None) is not None:
            await self.source.response.aclose()


def deployment_id(deployment: Mapping[str, object]) -> str | None:
    info: Final = deployment.get("model_info")
    value: Final = _JSON.validate_python(info).get("id") if isinstance(info, Mapping) else None
    return value if isinstance(value, str) else None
