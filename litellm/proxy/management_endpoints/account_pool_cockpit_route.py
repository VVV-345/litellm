"""按已发布 Cockpit 主体范围选择唯一请求 owner；默认关闭且不重试或回放。"""

from __future__ import annotations

import asyncio
import math
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, ValidationError

from litellm._logging import verbose_proxy_logger
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity
from litellm.proxy.management_endpoints.account_pool_session import has_signed_history

_HASH: Final = re.compile(r"^[0-9a-f]{64}$")
_ETAG: Final = re.compile(r'^"sha256-[0-9a-f]{64}"$')
_GATE: Final = "ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED"
_TTL_SECONDS: Final = 30.0
_TTL_ENV: Final = "ACCOUNT_POOL_COCKPIT_ROUTE_TTL_SECONDS"
_MIN_TTL_SECONDS: Final = 1.0
_MAX_TTL_SECONDS: Final = 300.0
_PATH: Final = "/internal/cockpit/route/"
_MAX_RESPONSE: Final = 1 << 20
_DATA: Final = TypeAdapter(dict[str, object])


class CockpitRouteState(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["absent", "staged", "active", "blocked"]
    account_ids: list[str] = Field(default_factory=list)
    model_scope: Literal["none", "restricted"] = "none"
    models: list[str] = Field(default_factory=list)
    origin: str | None = None
    api_key: SecretStr | None = Field(default=None, repr=False)
    persisted_manifest_revision: str | None = Field(default=None, pattern=r'^"sha256-[0-9a-f]{64}"$')


@dataclass(frozen=True, slots=True)
class CockpitRoute:
    subject_hash: str
    origin: str
    api_key: SecretStr
    account_ids: tuple[str, ...]
    models: tuple[str, ...]
    manifest_revision: str


@dataclass(frozen=True, slots=True)
class RouteDecision:
    state: Literal["absent", "staged", "active", "blocked"]
    route: CockpitRoute | None = None


class CockpitRouteUnavailable(Exception):
    pass


RouteLookup = Callable[[str], Awaitable[RouteDecision]]


def routing_enabled() -> bool:
    return os.getenv(_GATE, "").strip().lower() in {"1", "true", "yes", "on"}


def route_ttl_seconds(raw: str | None = None) -> float:
    value: Final = os.getenv(_TTL_ENV, "") if raw is None else raw
    try:
        parsed: Final = float(value)
    except ValueError:
        return _TTL_SECONDS
    if not math.isfinite(parsed):
        return _TTL_SECONDS
    return max(_MIN_TTL_SECONDS, min(parsed, _MAX_TTL_SECONDS))


def _valid_origin(value: str) -> bool:
    try:
        parsed: Final = urlsplit(value)
        port: Final = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and (port is None or 1 <= port <= 65535)
        and all(32 < ord(char) < 127 for char in value)
    )


def _active_route(state: CockpitRouteState) -> CockpitRoute:
    if (
        state.state != "active"
        or state.origin is None
        or not _valid_origin(state.origin)
        or state.api_key is None
        or not state.account_ids
        or not state.models
        or state.persisted_manifest_revision is None
        or _ETAG.fullmatch(state.persisted_manifest_revision) is None
    ):
        raise CockpitRouteUnavailable
    return CockpitRoute(
        subject_hash=state.subject_hash,
        origin=state.origin.rstrip("/"),
        api_key=state.api_key,
        account_ids=tuple(state.account_ids),
        models=tuple(state.models),
        manifest_revision=state.persisted_manifest_revision,
    )


def _decision(payload: bytes, subject_hash: str) -> RouteDecision:
    try:
        state: Final = CockpitRouteState.model_validate_json(payload)
    except ValidationError:
        raise CockpitRouteUnavailable from None
    if state.subject_hash != subject_hash:
        raise CockpitRouteUnavailable
    if state.state == "active":
        return RouteDecision("active", _active_route(state))
    return RouteDecision(state.state)


class ManagerRouteLookup:
    def __init__(
        self,
        base_url: str,
        token: str,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._base_url: Final = base_url.rstrip("/")
        self._token: Final = token
        self._client_factory: Final = client_factory or (
            lambda: httpx.AsyncClient(timeout=5.0, trust_env=False, follow_redirects=False)
        )

    async def read(self, subject_hash: str) -> RouteDecision:
        if len(self._token) < 32:
            raise CockpitRouteUnavailable
        try:
            async with self._client_factory() as client:
                response: Final = await client.get(
                    self._base_url + _PATH + subject_hash,
                    headers={"Authorization": "Bearer " + self._token},
                )
        except httpx.HTTPError:
            raise CockpitRouteUnavailable from None
        if response.status_code != 200 or len(response.content) > _MAX_RESPONSE:
            raise CockpitRouteUnavailable
        return _decision(response.content, subject_hash)


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    expires: float
    decision: RouteDecision


class CockpitRouteResolver:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        lookup: RouteLookup | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        self._clock: Final = clock
        self._lookup: RouteLookup | None = lookup
        self._ttl: Final = route_ttl_seconds() if ttl_seconds is None else ttl_seconds
        self._entries: Mapping[str, _CacheEntry] = MappingProxyType({})
        self._lock = asyncio.Lock()

    def invalidate(self, subject_hash: str) -> None:
        self._entries = MappingProxyType({key: entry for key, entry in self._entries.items() if key != subject_hash})

    def clear(self) -> None:
        self._entries = MappingProxyType({})

    async def decision(self, subject_hash: str) -> RouteDecision:
        now = self._clock()
        cached = self._entries.get(subject_hash)
        if cached is not None and cached.expires > now:
            return cached.decision
        async with self._lock:
            now = self._clock()
            cached = self._entries.get(subject_hash)
            if cached is not None and cached.expires > now:
                return cached.decision
            lookup: Final = self._lookup or self._default_lookup
            decision: Final = await lookup(subject_hash)
            kept: Final = {key: entry for key, entry in self._entries.items() if entry.expires > now}
            kept[subject_hash] = _CacheEntry(now + self._ttl, decision)
            self._entries = MappingProxyType(kept)
            return decision

    @staticmethod
    async def _default_lookup(subject_hash: str) -> RouteDecision:
        token: Final = os.getenv("ACCOUNT_POOL_MANAGER_TOKEN", "")
        base_url: Final = os.getenv("ACCOUNT_POOL_MANAGER_URL", "http://account-pool:8091")
        return await ManagerRouteLookup(base_url, token).read(subject_hash)


default_resolver: Final = CockpitRouteResolver()


async def cockpit_route_for_request(
    identity: PoolIdentity | None,
    token: str | None,
    data: object,
    route_type: str,
    *,
    resolver: CockpitRouteResolver | None = None,
) -> CockpitRoute | None:
    if not routing_enabled() or identity is None or identity.card_id is None:
        return None
    if not isinstance(token, str) or _HASH.fullmatch(token) is None:
        return None
    try:
        request: Final = _DATA.validate_python(data)
    except ValidationError:
        raise HTTPException(400, "Invalid account pool request body") from None
    active_resolver: Final = resolver or default_resolver
    try:
        decision: Final = await active_resolver.decision(token)
    except CockpitRouteUnavailable:
        raise HTTPException(503, "cockpit_route_unavailable") from None
    if decision.state in {"absent", "staged"}:
        return None
    if decision.state == "blocked" or decision.route is None:
        raise HTTPException(503, "cockpit_route_blocked")
    route: Final = decision.route
    model: Final = request.get("model")
    if not isinstance(model, str) or not any(model.casefold() == allowed.casefold() for allowed in route.models):
        raise HTTPException(403, "model_outside_cockpit_scope")
    if route_type != "aresponses":
        raise HTTPException(422, "cockpit_protocol_unsupported")
    if has_signed_history(request):
        raise HTTPException(422, "cockpit_continuation_unsupported")
    return route


async def dispatch_cockpit_route(
    route: CockpitRoute,
    data: object,
    route_type: str,
) -> object:
    import litellm

    if route_type != "aresponses":
        raise HTTPException(422, "cockpit_protocol_unsupported")
    try:
        request: Final = _DATA.validate_python(data)
    except ValidationError:
        raise HTTPException(400, "Invalid account pool request body") from None

    outbound: Final[dict[str, object]] = {
        **request,
        "api_base": route.origin + "/v1",
        "api_key": route.api_key.get_secret_value(),
        "custom_llm_provider": "openai",
        "num_retries": 0,
        "max_retries": 0,
        "fallbacks": [],
        "context_window_fallbacks": [],
        "content_policy_fallbacks": [],
        "caching": False,
        "cache": {"no-cache": True, "no-store": True},
    }
    metadata_name: Final = "litellm_metadata" if "litellm_metadata" in outbound else "metadata"
    metadata: Final = outbound.get(metadata_name)
    merged: Final[dict[str, object]] = {}
    if isinstance(metadata, Mapping):
        try:
            merged.update(_DATA.validate_python(metadata))
        except ValidationError:
            pass
    merged.update(
        {
            "account_pool_route_owner": "cockpit",
            "account_pool_subject_hash": route.subject_hash,
            "account_pool_cockpit_manifest_revision": route.manifest_revision,
        }
    )
    outbound[metadata_name] = merged
    verbose_proxy_logger.info(
        "account pool cockpit route selected for subject %s revision %s",
        route.subject_hash[:12],
        route.manifest_revision,
    )
    return await litellm.aresponses(**outbound)  # pyright: ignore[reportArgumentType, reportUnknownMemberType, reportAny]  # request kwargs are validated JSON; a typed Responses model would duplicate the proxy schema
