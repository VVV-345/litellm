"""验证 Cockpit 主体路由只选择一个 owner，默认关闭且单次直连不重试。"""

from __future__ import annotations

import asyncio
import json
from typing import Final
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

import litellm
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.management_endpoints.account_pool_cockpit_route import (
    CockpitRoute,
    CockpitRouteResolver,
    CockpitRouteUnavailable,
    ManagerRouteLookup,
    RouteDecision,
    cockpit_route_for_request,
    dispatch_cockpit_route,
    route_ttl_seconds,
)
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity
from litellm.proxy.route_llm_request import _route_request_single_attempt

SUBJECT: Final = "a" * 64
CARD: Final = UUID("00000000-0000-0000-0000-000000000001")
REVISION: Final = '"sha256-' + "b" * 64 + '"'
TOKEN: Final = "m" * 32


def _route(models: tuple[str, ...] = ("fixture-model",), origin: str = "http://cockpit.test:8080") -> CockpitRoute:
    return CockpitRoute(
        subject_hash=SUBJECT,
        origin=origin,
        api_key=SecretStr("cockpit-internal-key"),
        account_ids=(str(CARD),),
        models=models,
        manifest_revision=REVISION,
    )


class _Router:
    model_names: Final[tuple[str, ...]] = ()

    def aresponses(self, **_kwargs: object) -> object:
        return "router-path"


def _identity() -> PoolIdentity:
    return PoolIdentity(key_hash=SUBJECT, card_id=CARD, request_id=uuid4())


def _user() -> UserAPIKeyAuth:
    return UserAPIKeyAuth(token=SUBJECT)


def _resolver(decision: RouteDecision | Exception) -> CockpitRouteResolver:
    async def lookup(_subject: str) -> RouteDecision:
        if isinstance(decision, Exception):
            raise decision
        return decision

    return CockpitRouteResolver(lookup=lookup)


def test_gate_off_never_calls_the_route_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED", raising=False)
    calls: Final[list[str]] = []

    async def lookup(subject: str) -> RouteDecision:
        calls.append(subject)
        return RouteDecision("active", _route())

    local: Final = CockpitRouteResolver(lookup=lookup)
    result: Final = asyncio.run(
        cockpit_route_for_request(_identity(), SUBJECT, {"model": "fixture-model"}, "aresponses", resolver=local)
    )
    assert result is None and calls == []


@pytest.mark.parametrize("state", ["absent", "staged"])
def test_absent_or_staged_route_keeps_the_existing_path(state: str, monkeypatch: pytest.MonkeyPatch) -> None:
    decisions: Final = {"absent": RouteDecision("absent"), "staged": RouteDecision("staged")}
    monkeypatch.setenv("ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED", "true")
    local: Final = _resolver(decisions[state])
    result: Final = asyncio.run(
        cockpit_route_for_request(_identity(), SUBJECT, {"model": "fixture-model"}, "aresponses", resolver=local)
    )
    assert result is None


def test_active_route_requires_published_model_responses_protocol_and_safe_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED", "true")
    local: Final = _resolver(RouteDecision("active", _route(models=("fixture-model",))))
    accepted: Final = asyncio.run(
        cockpit_route_for_request(_identity(), SUBJECT, {"model": "Fixture-Model"}, "aresponses", resolver=local)
    )
    with pytest.raises(HTTPException) as model_error:
        asyncio.run(
            cockpit_route_for_request(_identity(), SUBJECT, {"model": "other-model"}, "aresponses", resolver=local)
        )
    with pytest.raises(HTTPException) as protocol_error:
        asyncio.run(
            cockpit_route_for_request(_identity(), SUBJECT, {"model": "fixture-model"}, "acompletion", resolver=local)
        )
    with pytest.raises(HTTPException) as history_error:
        asyncio.run(
            cockpit_route_for_request(
                _identity(),
                SUBJECT,
                {"model": "fixture-model", "previous_response_id": "resp_1"},
                "aresponses",
                resolver=local,
            )
        )
    assert accepted is not None and accepted.subject_hash == SUBJECT
    assert model_error.value.status_code == 403
    assert protocol_error.value.status_code == 422
    assert history_error.value.status_code == 422
    assert history_error.value.detail == "cockpit_continuation_unsupported"


@pytest.mark.parametrize("decision", [RouteDecision("blocked"), CockpitRouteUnavailable()])
def test_blocked_or_unavailable_active_state_fails_closed(
    decision: RouteDecision | Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED", "true")
    local: Final = _resolver(decision)
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            cockpit_route_for_request(_identity(), SUBJECT, {"model": "fixture-model"}, "aresponses", resolver=local)
        )
    assert error.value.status_code == 503


def test_dispatch_is_one_direct_call_without_router_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: Final[list[dict[str, object]]] = []

    async def fake_aresponses(**kwargs: object) -> object:
        calls.append(kwargs)
        return "cockpit-response"

    monkeypatch.setattr(litellm, "aresponses", fake_aresponses)
    result: Final = asyncio.run(
        dispatch_cockpit_route(
            _route(),
            {
                "model": "fixture-model",
                "input": "hi",
                "litellm_metadata": {
                    "user_api_key": "hashed",
                    "user_api_key_hash": SUBJECT,
                    "user_api_key_user_id": "user-1",
                    "spend_logs_metadata": {"user_api_key_alias": "fixture-key"},
                },
            },
            "aresponses",
        )
    )
    assert result == "cockpit-response" and len(calls) == 1
    kwargs: Final = calls[0]
    assert kwargs["api_base"] == "http://cockpit.test:8080/v1"
    assert kwargs["api_key"] == "cockpit-internal-key"
    assert kwargs["custom_llm_provider"] == "openai"
    assert kwargs["num_retries"] == 0 and kwargs["max_retries"] == 0
    assert kwargs["fallbacks"] == [] and kwargs["context_window_fallbacks"] == []
    assert kwargs["caching"] is False and kwargs["cache"] == {"no-cache": True, "no-store": True}
    metadata: Final = kwargs["litellm_metadata"]
    assert metadata["user_api_key"] == "hashed"
    assert metadata["user_api_key_hash"] == SUBJECT
    assert metadata["user_api_key_user_id"] == "user-1"
    assert metadata["spend_logs_metadata"] == {"user_api_key_alias": "fixture-key"}
    assert metadata["account_pool_route_owner"] == "cockpit"
    assert metadata["account_pool_subject_hash"] == SUBJECT
    assert "cockpit-internal-key" not in json.dumps(metadata)


def test_request_routing_uses_cockpit_once_and_never_touches_the_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED", "true")
    local: Final = _resolver(RouteDecision("active", _route()))
    calls: Final[list[dict[str, object]]] = []

    async def fake_aresponses(**kwargs: object) -> object:
        calls.append(kwargs)
        return "cockpit-response"

    monkeypatch.setattr(litellm, "aresponses", fake_aresponses)

    class RejectingRouter(_Router):
        def aresponses(self, **_kwargs: object) -> object:
            raise AssertionError("the LiteLLM router must not own a cockpit request")

    context = pool_identity.set(_identity())
    try:
        result: Final = asyncio.run(
            _route_request_single_attempt(
                {"model": "fixture-model", "input": "hi"},
                RejectingRouter(),
                None,
                "aresponses",
                _user(),
                cockpit_resolver=local,
            )
        )
    finally:
        pool_identity.reset(context)
    assert result == "cockpit-response" and len(calls) == 1


def test_gate_off_keeps_the_existing_router_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED", raising=False)

    async def forbidden_lookup(_subject: str) -> RouteDecision:
        raise AssertionError("gate off must not read cockpit routes")

    local: Final = CockpitRouteResolver(lookup=forbidden_lookup)
    context = pool_identity.set(_identity())
    try:
        result: Final = asyncio.run(
            _route_request_single_attempt(
                {"model": "fixture-model", "input": "hi"},
                _Router(),
                None,
                "aresponses",
                _user(),
                cockpit_resolver=local,
            )
        )
    finally:
        pool_identity.reset(context)
    assert result == "router-path"


def test_manager_route_lookup_parses_active_scope_and_rejects_bad_origin() -> None:
    seen: Final[list[httpx.Request]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "subject_hash": SUBJECT,
                "state": "active",
                "account_ids": [str(CARD)],
                "model_scope": "restricted",
                "models": ["fixture-model"],
                "origin": "http://cockpit.test:8080",
                "api_key": "cockpit-internal-key",
                "persisted_manifest_revision": REVISION,
                "runtime_activation": "restart_required_or_unverified",
            },
        )

    lookup: Final = ManagerRouteLookup(
        "http://manager.test",
        TOKEN,
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    decision: Final = asyncio.run(lookup.read(SUBJECT))
    assert decision.state == "active" and decision.route is not None
    assert decision.route.origin == "http://cockpit.test:8080"
    assert decision.route.api_key.get_secret_value() == "cockpit-internal-key"
    assert "cockpit-internal-key" not in repr(decision.route)
    assert seen[0].url.path == "/internal/cockpit/route/" + SUBJECT
    assert seen[0].headers["authorization"] == "Bearer " + TOKEN

    def bad_origin(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "subject_hash": SUBJECT,
                "state": "active",
                "account_ids": [str(CARD)],
                "model_scope": "restricted",
                "models": ["fixture-model"],
                "origin": "http://user:pass@cockpit.test:8080",
                "api_key": "cockpit-internal-key",
                "persisted_manifest_revision": REVISION,
            },
        )

    bad: Final = ManagerRouteLookup(
        "http://manager.test",
        TOKEN,
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(bad_origin)),
    )
    with pytest.raises(CockpitRouteUnavailable):
        asyncio.run(bad.read(SUBJECT))


def test_route_resolver_caches_successes_but_not_failures() -> None:
    now: Final[list[float]] = [0.0]
    calls: Final[list[str]] = []

    async def lookup(_subject: str) -> RouteDecision:
        calls.append(_subject)
        return RouteDecision("staged")

    local: Final = CockpitRouteResolver(clock=lambda: now[0], lookup=lookup)
    assert asyncio.run(local.decision(SUBJECT)).state == "staged"
    assert asyncio.run(local.decision(SUBJECT)).state == "staged"
    assert calls == [SUBJECT]
    now[0] = 31.0
    assert asyncio.run(local.decision(SUBJECT)).state == "staged"
    assert calls == [SUBJECT, SUBJECT]

    failures: Final[list[str]] = []

    async def failing(subject: str) -> RouteDecision:
        failures.append(subject)
        raise CockpitRouteUnavailable

    failing_resolver: Final = CockpitRouteResolver(clock=lambda: now[0], lookup=failing)
    for _ in range(2):
        with pytest.raises(CockpitRouteUnavailable):
            asyncio.run(failing_resolver.decision(SUBJECT))
    assert failures == [SUBJECT, SUBJECT]


def test_route_ttl_is_configurable_and_invalidation_refreshes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert route_ttl_seconds("bad") == 30.0
    assert route_ttl_seconds("0") == 1.0
    assert route_ttl_seconds("9999") == 300.0
    monkeypatch.setenv("ACCOUNT_POOL_COCKPIT_ROUTE_TTL_SECONDS", "2")
    now: Final[list[float]] = [0.0]
    calls: Final[list[str]] = []

    async def lookup(_subject: str) -> RouteDecision:
        calls.append(_subject)
        return RouteDecision("staged")

    local: Final = CockpitRouteResolver(clock=lambda: now[0], lookup=lookup)
    assert asyncio.run(local.decision(SUBJECT)).state == "staged"
    now[0] = 1.9
    assert asyncio.run(local.decision(SUBJECT)).state == "staged"
    assert calls == [SUBJECT]
    now[0] = 2.1
    assert asyncio.run(local.decision(SUBJECT)).state == "staged"
    assert calls == [SUBJECT, SUBJECT]
    local.invalidate(SUBJECT)
    assert asyncio.run(local.decision(SUBJECT)).state == "staged"
    assert calls == [SUBJECT, SUBJECT, SUBJECT]


def test_dispatch_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: Final[list[dict[str, object]]] = []

    async def failing(**_kwargs: object) -> object:
        calls.append(_kwargs)
        raise HTTPException(429, "upstream quota exhausted")

    monkeypatch.setattr(litellm, "aresponses", failing)
    with pytest.raises(HTTPException):
        asyncio.run(dispatch_cockpit_route(_route(), {"model": "fixture-model", "input": "hi"}, "aresponses"))
    assert len(calls) == 1


def test_active_route_failure_never_falls_back_to_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED", "true")
    local: Final = _resolver(RouteDecision("active", _route()))
    calls: Final[list[dict[str, object]]] = []

    async def failing(**kwargs: object) -> object:
        calls.append(kwargs)
        raise HTTPException(429, "upstream quota exhausted")

    monkeypatch.setattr(litellm, "aresponses", failing)

    class RejectingRouter(_Router):
        def aresponses(self, **_kwargs: object) -> object:
            raise AssertionError("failed cockpit request must not fall back")

    context = pool_identity.set(_identity())
    try:
        with pytest.raises(HTTPException):
            asyncio.run(
                _route_request_single_attempt(
                    {"model": "fixture-model", "input": "hi"},
                    RejectingRouter(),
                    None,
                    "aresponses",
                    _user(),
                    cockpit_resolver=local,
                )
            )
    finally:
        pool_identity.reset(context)
    assert len(calls) == 1
