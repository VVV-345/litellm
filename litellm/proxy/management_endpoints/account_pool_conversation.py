"""Responses 可见历史的短期加密缓存与安全迁移，不保存上游签名。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, TypeVar, cast

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from litellm._logging import verbose_proxy_logger
from litellm.caching.dual_cache import DualCache
from litellm.exceptions import RateLimitError, ServiceUnavailableError
from litellm.litellm_core_utils.signature_recovery import signature_recovery_reason
from litellm.proxy._types import ProxyException
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value, encrypt_value
from litellm.proxy.management_endpoints.account_pool_failover import exception_header
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity
from litellm.proxy.management_endpoints.account_pool_native_routing import (
    RoutingSnapshot,
    exhausted_snapshot,
    now_utc,
    quota_depleted,
    quota_failover_exclusions,
    snapshot_for,
    unavailable_snapshot,
)
from litellm.proxy.management_endpoints.account_pool_native_routing import snapshots as quota_snapshots
from litellm.proxy.management_endpoints.account_pool_session import has_signed_history
from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator
from litellm.responses.utils import ResponsesAPIRequestUtils
from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
    EncryptedContentAffinityCheck,
    EncryptedContentOriginUnavailableError,
)
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
_Result = TypeVar("_Result")


def _degraded_continuation_enabled() -> bool:
    return os.getenv("ACCOUNT_POOL_DEGRADED_CONTINUATION", "true").strip().lower() not in ("0", "false", "no", "off")


class ConversationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input: tuple[Mapping[str, JsonValue], ...]
    deployment_id: str
    expires_at: float


class ConversationUnavailableError(ProxyException):
    def __init__(self) -> None:
        super().__init__(  # pyright: ignore[reportUnknownMemberType]  # The existing ProxyException constructor has an untyped optional field.
            message="原账号暂不可用，无法安全重建完整对话历史；请重新发送完整历史或开启新会话。",
            type="account_pool_error", param="previous_response_id", code=503,
            openai_code="account_pool_conversation_unavailable",
        )

    def to_dict(self) -> dict[str, object]:
        return {"message": self.message, "type": self.type, "param": self.param, "code": self.openai_code}


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
                verbose_proxy_logger.debug("Account pool conversation unavailable; reason=missing_or_invalid_record")
                return None
            record: Final = ConversationRecord.model_validate_json(
                decrypt_value(base64.b64decode(cached, validate=True), self.secret)
            )
            if record.expires_at <= time.time():
                verbose_proxy_logger.debug("Account pool conversation unavailable; reason=expired_record")
                return None
            return record
        except Exception:
            verbose_proxy_logger.warning("Account pool conversation cache unavailable; recovery disabled")
            return None

    async def save(self, identity: PoolIdentity, model: str, response_id: str, record: ConversationRecord) -> None:
        serialized: Final = record.model_dump_json()
        if len(serialized.encode()) > _MAX_BYTES:
            verbose_proxy_logger.warning("Account pool conversation not retained; reason=history_too_large")
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
        kept
        for item in items
        if (kept := readable_reasoning(item) if item.get("type") == "reasoning" else MappingProxyType({
            key: content for key, content in item.items()
            if key not in ("id", "status", "encrypted_content") and content is not None
        })) is not None
    )


def readable_reasoning(item: Mapping[str, JsonValue]) -> Mapping[str, JsonValue] | None:
    readable: Final = any(
        isinstance(value, str) and bool(value.strip())
        or isinstance(value, list) and any(isinstance(block, dict) and bool(block.get("text")) for block in value)
        for key in ("summary", "content")
        if (value := item.get(key)) is not None
    )
    return MappingProxyType({key: value for key, value in item.items() if key not in ("id", "encrypted_content", "status")}) if readable else None


def compatible_reasoning_input(router: Router, payload: Mapping[str, object], selected: str) -> list[dict[str, JsonValue]] | None:
    raw_input: Final = payload.get("input")
    if not isinstance(raw_input, list) or not any(
        isinstance(item, dict) and item.get("type") == "reasoning" and item.get("encrypted_content")
        for item in raw_input
    ):
        return None
    history: Final = visible_input(raw_input)
    if history is None or payload.get("previous_response_id"):
        return None
    portable: Final = _JSON.validate_python({**{key: value for key, value in payload.items() if key in _FIELDS}, "input": _INPUT.validate_python(history)})
    if signature_recovery_reason(portable) is not None:
        return None
    items: Final = _INPUT.validate_python(payload.get("input"))
    deployment: Final = router.get_deployment(model_id=selected)
    boundary: Final = EncryptedContentAffinityCheck._encryption_boundary_key(deployment.litellm_params) if deployment is not None else None  # pyright: ignore[reportPrivateUsage]  # Share the authoritative encryption-boundary comparison.

    def keep(item: dict[str, JsonValue]) -> Mapping[str, JsonValue] | None:
        if item.get("type") != "reasoning" or not item.get("encrypted_content"):
            return item
        origin: Final = EncryptedContentAffinityCheck._extract_model_id_from_input([item])  # pyright: ignore[reportPrivateUsage]  # Decode both native marker formats.
        source: Final = router.get_deployment(model_id=origin) if origin else None
        same: Final = origin == selected or boundary is not None and source is not None and EncryptedContentAffinityCheck._encryption_boundary_key(source.litellm_params) == boundary  # pyright: ignore[reportPrivateUsage]  # Preserve only matching backend credentials.
        return item if same else readable_reasoning(item)

    return [dict(kept) for item in items if (kept := keep(item)) is not None]


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
        visible: tuple[Mapping[str, JsonValue], ...] | None,
        complete_history: bool,
    ) -> None:
        self.identity: Final = identity
        self.model: Final = model
        self.request: Final = request
        self.history: Final = history
        self.origin: Final = origin
        self.store: Final = store
        self.visible: Final = visible
        self.complete_history: Final = complete_history
        self.relaxed: Final = identity.card_id is None and _degraded_continuation_enabled()

    @classmethod
    async def start(cls, router: Router, payload: Mapping[str, object]) -> Conversation | None:
        identity: Final = pool_identity.get()
        model: Final = payload.get("model")
        secret: Final = os.getenv("ACCOUNT_POOL_MANAGER_TOKEN", "")
        if identity is None or not isinstance(model, str) or len(secret) < 32 or payload.get("extra_body"):
            return None
        relaxed: Final = identity.card_id is None and _degraded_continuation_enabled()
        if not relaxed:
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
        prior: Final = any(
            item.get("role") == "assistant"
            or item.get("type") in ("function_call", "function_call_output", "custom_tool_call", "custom_tool_call_output")
            for item in (current or ())
        )
        history: Final = (
            None
            if current is None
            else (*record.input, *current)
            if record is not None
            else current
            if prior or previous is None
            else None
        )
        return cls(identity, model, request, history, origin, store, current, record is not None or prior)

    def can_replay(self, router: Router, payload: Mapping[str, object]) -> bool:
        if (
            self.history is None
            or payload.get("disable_fallbacks")
            or payload.get("max_fallbacks", router.max_fallbacks) == 0
        ):
            return False
        if not has_signed_history(payload) and not self.relaxed:
            return False
        if self.request.get("truncation") == "auto":
            return False
        restored: Final = _JSON.validate_python(
            MappingProxyType({**self.request, "input": _INPUT.validate_python(self.history)})
        )
        return signature_recovery_reason(restored) is None

    def rebuild(
        self,
        router: Router,
        payload: Mapping[str, object],
        error: Exception,
        *,
        migrated: bool = False,
    ) -> Mapping[str, object] | None:
        deployments: Final = _DEPLOYMENTS.validate_python(router.get_model_list(model_name=self.model) or ())
        failed: Final = getattr(error, "failed_deployment_id", None) or self.origin
        origin: Final = (
            next((item for item in deployments if deployment_id(item) == failed), None)
            if isinstance(failed, str)
            else None
        )
        snapshot: Final = snapshot_for(origin, quota_snapshots.values) if origin is not None else None

        if self.relaxed:
            if payload.get("disable_fallbacks") or payload.get("max_fallbacks", router.max_fallbacks) == 0:
                return None
            if not self._relaxed_trigger(error, snapshot, payload):
                return None
            input_items: Final = self.history if self.complete_history else self.visible
            if input_items is None:
                return None
            excluded: Final = self._excluded(payload, deployments, failed)
            if not any(deployment_id(item) not in excluded for item in deployments):
                return None
            verbose_proxy_logger.warning(
                "Account pool conversation degraded migration; request_id=%s degraded=%s",
                self.identity.request_id,
                not self.complete_history,
            )
            return MappingProxyType(
                {
                    **MappingProxyType({key: value for key, value in payload.items() if key != "previous_response_id"}),
                    "input": _INPUT.validate_python(input_items),
                    "_excluded_deployment_ids": excluded,
                    "disable_fallbacks": True,
                    "num_retries": 0,
                }
            )

        if origin is None or snapshot is None:
            return None
        depleted_before_call: Final = (
            isinstance(error, (RouterRateLimitError, RouterRateLimitErrorBasic, ServiceUnavailableError))
            and getattr(error, "failed_deployment_id", None) is None
            and quota_depleted(snapshot, self.model, now_utc())
        )
        quota_migration: Final = snapshot.quota_switch_project and (
            exhausted_snapshot(error, deployments, self.model) is not None or depleted_before_call
        )
        if not quota_migration and not (
            snapshot.fallback_enabled
            and (
                isinstance(error, EncryptedContentOriginUnavailableError)
                or retry_exhausted(error) or unavailable_snapshot(error, deployments) is not None
            )
        ):
            return None
        if not migrated and not self.can_replay(router, payload):
            verbose_proxy_logger.info("Account pool conversation migration blocked; reason=incomplete_or_unsupported_history")
            if payload.get("previous_response_id") and not payload.get("disable_fallbacks") and payload.get("max_fallbacks", router.max_fallbacks) != 0:
                raise ConversationUnavailableError() from error
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

    def _relaxed_trigger(
        self,
        error: Exception,
        snapshot: RoutingSnapshot | None,
        payload: Mapping[str, object],
    ) -> bool:
        if exception_header(error, "x-account-pool-preflight") == "rejected":
            return payload.get("previous_response_id") is None
        if isinstance(
            error,
            (EncryptedContentOriginUnavailableError, RateLimitError, RouterRateLimitError, RouterRateLimitErrorBasic),
        ):
            return True
        if exception_header(error, "x-account-pool-failure") in ("quota_exhausted", "rate_limited", "budget_exhausted"):
            return True
        return snapshot is not None and quota_depleted(snapshot, self.model, now_utc())

    def _excluded(
        self,
        payload: Mapping[str, object],
        deployments: tuple[Mapping[str, object], ...],
        failed: object,
    ) -> tuple[str, ...]:
        previous_excluded: Final = payload.get("_excluded_deployment_ids")
        base: Final = _EXCLUSIONS.validate_python(previous_excluded) if previous_excluded is not None else ()
        if not isinstance(failed, str):
            return base
        origin: Final = next((item for item in deployments if deployment_id(item) == failed), None)
        if origin is None:
            return (*base, failed) if failed not in base else base
        return quota_failover_exclusions(deployments, failed, base, self.model)

    async def recover(
        self,
        router: Router,
        payload: Mapping[str, object],
        error: Exception,
        invoke: Callable[[Mapping[str, object]], Awaitable[_Result]],
        *,
        remaining: int = 10,
        migrated: bool = False,
    ) -> _Result:
        raw_attempts: Final = exception_header(error, "x-account-pool-attempt")
        used: Final = (
            min(10, max(1, int(raw_attempts))) if raw_attempts and raw_attempts.isdecimal()
            else 0 if isinstance(error, EncryptedContentOriginUnavailableError) else 1
        )
        excluded_before: Final = payload.get("_excluded_deployment_ids")
        failed: Final = getattr(error, "failed_deployment_id", None) or self.origin
        if migrated and isinstance(excluded_before, (tuple, list)) and failed in excluded_before:
            raise error
        budget: Final = remaining - used
        rebuilt: Final = self.rebuild(router, payload, error, migrated=migrated)
        if rebuilt is None or budget <= 0 or time.time() >= self.identity.retry_deadline:
            raise error
        token: Final = pool_identity.set(self.identity.model_copy(update={"attempt_limit": budget}))
        try:
            return await invoke(rebuilt)
        except Exception as next_error:
            return await self.recover(router, rebuilt, next_error, invoke, remaining=budget, migrated=True)
        finally:
            pool_identity.reset(token)

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
