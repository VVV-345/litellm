"""本文件验证卡片网关的鉴权隔离、重试边界、流式转发和租约释放。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from typing import Final, Literal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from litellm.proxy.management_endpoints.account_pool_gateway import AccountPoolGatewayMiddleware, session_hash
from litellm.proxy.management_endpoints.account_pool_gateway_client import ControlError
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import (
    AcquireRejected,
    AcquireRequest,
    Candidate,
    CandidateModelQuota,
    FinishRequest,
    GatewayCredential,
    Lease,
    Resolution,
    ResolveRequest,
)
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPolicy,
    CodexPolicy,
    ModelAlias,
    RoutingPolicy,
    TransportPolicy,
)
from litellm.proxy.management_endpoints.account_pool_routing import Rejected, plan_rank, routes
from litellm.proxy.management_endpoints.account_pool_websocket import (
    UpstreamWebSocket,
    WebSocketDialRequest,
)

_KEY: Final = "cpk_" + "test-only-" * 5


@pytest.mark.asyncio
async def test_stream_bootstrap_bounds_buffer_and_replays_every_byte() -> None:
    from litellm.proxy.management_endpoints.account_pool_retry import StreamBootstrap

    oversized: Final = b":" + b" " * 70000 + b"\n\ndata: [DONE]\n\n"

    async def chunks():
        yield oversized

    bootstrap: Final = StreamBootstrap(chunks())
    await bootstrap.prepare()
    assert bootstrap.buffer.tell() == 65536
    assert b"".join([chunk async for chunk in bootstrap.replay()]) == oversized
    await bootstrap.close()


class Control:
    def __init__(self, resolution: Resolution) -> None:
        self.resolution: Final = resolution
        self.acquisitions: list[AcquireRequest] = []
        self.finished: list[FinishRequest] = []
        self.revoked = False
        self.exhausted = False
        self.budget_exhausted_account_ids: frozenset[UUID] = frozenset()
        self.unavailable_account_ids: frozenset[UUID] = frozenset()

    async def resolve(self, request: ResolveRequest) -> Resolution:
        if request.card_key != _KEY:
            raise ControlError(401)
        return self.resolution

    async def acquire(self, request: AcquireRequest) -> Lease | AcquireRejected:
        self.acquisitions.append(request)
        if self.revoked:
            raise ControlError(401)
        if self.exhausted:
            return AcquireRejected(reason="concurrency")
        if request.account_id in self.budget_exhausted_account_ids:
            return AcquireRejected(reason="token_budget")
        if request.account_id in self.unavailable_account_ids:
            return AcquireRejected(reason="concurrency")
        return Lease(
            lease_id=uuid4(),
            card_id=self.resolution.card_id,
            key_id=self.resolution.key_id,
            account_id=request.account_id,
            request_id=request.request_id,
            model=request.model,
            channel="cliproxyapi",
            supplier="openai_codex",
            started_at=datetime.now(timezone.utc),
            attempt=request.attempt,
            routing_reason=request.routing_reason,
        )

    async def finish(self, request: FinishRequest) -> None:
        self.finished.append(request)


class FakeUpstreamWebSocket:
    def __init__(self) -> None:
        self.subprotocol: str | None = None
        self.sent: list[str | bytes] = []
        self.responses: asyncio.Queue[str | bytes] = asyncio.Queue()

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)
        payload: Final = json.loads(message)
        await self.responses.put(
            json.dumps(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_1", "model": payload["model"]},
                }
            )
        )

    async def recv(self) -> str | bytes:
        return await self.responses.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


class FakeUpstreamContext(AbstractAsyncContextManager[UpstreamWebSocket]):
    def __init__(self, upstream: FakeUpstreamWebSocket) -> None:
        self.upstream: Final = upstream

    async def __aenter__(self) -> UpstreamWebSocket:
        return self.upstream

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None


class FakeWebSocketDialer:
    def __init__(self) -> None:
        self.upstream: Final = FakeUpstreamWebSocket()
        self.requests: list[WebSocketDialRequest] = []

    def connect(self, request: WebSocketDialRequest) -> AbstractAsyncContextManager[UpstreamWebSocket]:
        self.requests.append(request)
        return FakeUpstreamContext(self.upstream)


def candidate(
    identifier: UUID,
    priority: int = 0,
    *,
    plan_type: str | None = None,
    auth_file_plan_type: str | None = None,
    subscription_active_until: datetime | None = None,
) -> Candidate:
    return Candidate(
        id=identifier,
        channel="cliproxyapi",
        supplier="openai_codex",
        environment_version=2,
        policy_version=1,
        enabled_models=("model-a",),
        api_base=f"http://cliproxy-{identifier.hex}:8317/v1",
        api_key="internal-secret",
        concurrency_limit=1,
        policy=AccountPolicy(routing=RoutingPolicy(priority=priority)),
        plan_type=plan_type,
        auth_file_plan_type=auth_file_plan_type,
        subscription_active_until=subscription_active_until,
    )


def setup_gateway(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    retry: bool = False,
    max_attempts: int = 2,
    websocket_dialer: FakeWebSocketDialer | None = None,
):
    card: Final = uuid4()
    policy: Final = AccountPolicy(
        model_aliases=(ModelAlias(alias="public-model", target="model-a"),),
        routing=RoutingPolicy(fallback_enabled=retry, max_attempts=max_attempts, backoff_ms=0),
    )
    control: Final = Control(
        Resolution(
            card_id=card,
            key_id=uuid4(),
            card_version=1,
            policy_version=1,
            policy=policy,
            candidates=(candidate(card, 10), candidate(uuid4())),
        )
    )
    app: Final = FastAPI()

    @app.post("/v1/chat/completions")
    async def ordinary() -> dict[str, str]:
        return {"route": "ordinary"}

    app.add_middleware(
        lambda app, **kwargs: AccountPoolGatewayMiddleware(app, **kwargs).dispatch_card,
        control_factory=lambda _: control,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        websocket_dialer=websocket_dialer,
    )
    return TestClient(app), control


@pytest.mark.parametrize(
    "policy,expected",
    [
        (RoutingPolicy(), "automatic"),
        (RoutingPolicy(strategy="priority"), "priority"),
        (RoutingPolicy(strategy="quota"), "quota"),
        (RoutingPolicy(strategy="plan"), "plan"),
        (RoutingPolicy(strategy="expiry"), "expiry"),
        (RoutingPolicy(strategy="custom"), "custom_order"),
    ],
)
def test_route_reason_matches_the_active_selection_strategy(policy: RoutingPolicy, expected: str) -> None:
    first: Final = candidate(uuid4(), 10).model_copy(update={"remaining_percent": 80})
    second: Final = candidate(uuid4()).model_copy(update={"remaining_percent": 20})
    resolution: Final = Resolution(
        card_id=uuid4(),
        key_id=uuid4(),
        card_version=1,
        policy_version=1,
        policy=AccountPolicy(routing=policy),
        candidates=(first, second),
    )

    selected: Final = routes(resolution, "model-a", "/v1/responses", Headers())

    assert not isinstance(selected, Rejected)
    assert selected[0].reason == expected


@pytest.mark.parametrize(
    "routing",
    [
        RoutingPolicy(),
        RoutingPolicy(strategy="random"),
        RoutingPolicy(strategy="priority"),
        RoutingPolicy(strategy="quota"),
        RoutingPolicy(strategy="plan"),
        RoutingPolicy(strategy="expiry"),
        RoutingPolicy(strategy="custom"),
    ],
)
def test_single_account_route_reason_is_explicit(routing: RoutingPolicy) -> None:
    account: Final = candidate(uuid4())
    resolution: Final = Resolution(
        card_id=uuid4(),
        key_id=uuid4(),
        card_version=1,
        policy_version=1,
        policy=AccountPolicy(routing=routing),
        candidates=(account,),
    )

    selected: Final = routes(resolution, "model-a", "/v1/responses", Headers())

    assert not isinstance(selected, Rejected)
    assert selected[0].reason == "single_account"


def test_plan_routing_prefers_verified_higher_tiers_and_uses_quota_as_a_tiebreaker() -> None:
    observed_at: Final = datetime.now(timezone.utc)
    plus: Final = candidate(uuid4(), plan_type="plus").model_copy(
        update={"remaining_percent": 90, "quota_observed_at": observed_at}
    )
    pro_lite: Final = candidate(uuid4(), plan_type="pro", auth_file_plan_type="prolite").model_copy(
        update={"remaining_percent": 30, "quota_observed_at": observed_at}
    )
    pro_max: Final = candidate(uuid4(), plan_type="pro", auth_file_plan_type="promax").model_copy(
        update={"remaining_percent": 10, "quota_observed_at": observed_at}
    )
    unknown: Final = candidate(uuid4())
    resolution: Final = Resolution(
        card_id=uuid4(),
        key_id=uuid4(),
        card_version=1,
        policy_version=1,
        policy=AccountPolicy(routing=RoutingPolicy(strategy="plan")),
        candidates=(unknown, plus, pro_lite, pro_max),
    )

    selected: Final = routes(resolution, "model-a", "/v1/responses", Headers())

    assert not isinstance(selected, Rejected)
    assert tuple(route.account.id for route in selected) == (pro_max.id, pro_lite.id, plus.id, unknown.id)


@pytest.mark.parametrize(
    ("plan_type", "expected"),
    (("enterprise", 700), ("ultra-tier", 650), ("supergrok-heavy", 650), ("max", 600), ("supergrok", 500)),
)
def test_plan_rank_supports_verified_non_codex_tiers(plan_type: str, expected: int) -> None:
    assert plan_rank(candidate(uuid4(), plan_type=plan_type)) == expected


def test_model_quota_applies_only_to_the_routed_model() -> None:
    observed_at: Final = datetime.now(timezone.utc)
    account: Final = candidate(uuid4()).model_copy(
        update={
            "enabled_models": ("model-a", "model-b"),
            "remaining_percent": 90,
            "quota_observed_at": observed_at,
            "model_quotas": (CandidateModelQuota(model="model-a", remaining_percent=0, observed_at=observed_at),),
        }
    )
    resolution: Final = Resolution(
        card_id=uuid4(),
        key_id=uuid4(),
        card_version=1,
        policy_version=1,
        policy=AccountPolicy(routing=RoutingPolicy(strategy="quota")),
        candidates=(account,),
    )

    exhausted: Final = routes(resolution, "model-a", "/v1/responses", Headers())
    available: Final = routes(resolution, "model-b", "/v1/responses", Headers())

    assert exhausted == Rejected(503, "No bound account currently supports this model and policy")
    assert not isinstance(available, Rejected)
    assert available[0].account.id == account.id


def test_expiry_routing_prefers_soonest_active_subscription_and_skips_expired_accounts() -> None:
    now: Final = datetime.now(timezone.utc)
    expired: Final = candidate(uuid4(), plan_type="enterprise", subscription_active_until=now - timedelta(days=1))
    sooner: Final = candidate(uuid4(), plan_type="plus", subscription_active_until=now + timedelta(days=30))
    later: Final = candidate(uuid4(), plan_type="pro", subscription_active_until=now + timedelta(days=60))
    unknown: Final = candidate(uuid4(), plan_type="enterprise")
    resolution: Final = Resolution(
        card_id=uuid4(),
        key_id=uuid4(),
        card_version=1,
        policy_version=1,
        policy=AccountPolicy(routing=RoutingPolicy(strategy="expiry")),
        candidates=(expired, unknown, later, sooner),
    )

    selected: Final = routes(resolution, "model-a", "/v1/responses", Headers())

    assert not isinstance(selected, Rejected)
    assert tuple(route.account.id for route in selected) == (sooner.id, later.id, unknown.id)


@pytest.mark.parametrize("strategy", ("auto", "random", "priority", "quota", "plan", "expiry", "custom"))
def test_expired_subscription_is_never_routable_for_any_strategy(
    strategy: Literal["auto", "random", "priority", "quota", "plan", "expiry", "custom"],
) -> None:
    now: Final = datetime.now(timezone.utc)
    expired: Final = candidate(
        uuid4(),
        priority=100,
        plan_type="enterprise",
        subscription_active_until=now - timedelta(seconds=1),
    ).model_copy(update={"remaining_percent": 100, "quota_observed_at": now})
    active: Final = candidate(
        uuid4(),
        priority=-100,
        plan_type="free",
        subscription_active_until=now + timedelta(days=1),
    ).model_copy(update={"remaining_percent": 1, "quota_observed_at": now})
    resolution: Final = Resolution(
        card_id=uuid4(),
        key_id=uuid4(),
        card_version=1,
        policy_version=1,
        policy=AccountPolicy(routing=RoutingPolicy(strategy=strategy)),
        candidates=(expired, active),
    )

    selected: Final = routes(resolution, "model-a", "/v1/responses", Headers())

    assert not isinstance(selected, Rejected)
    assert tuple(route.account.id for route in selected) == (active.id,)


def test_streaming_rule_can_disable_stream_requests() -> None:
    account: Final = candidate(uuid4())
    resolution: Final = Resolution(
        card_id=uuid4(),
        key_id=uuid4(),
        card_version=1,
        policy_version=1,
        policy=AccountPolicy(),
        candidates=(account,),
        streaming_mode="disabled",
    )

    selected: Final = routes(resolution, "model-a", "/v1/chat/completions", Headers(), stream=True)

    assert selected == Rejected(403, "Streaming is disabled for this account")


def test_websocket_route_requires_card_and_candidate_enablement() -> None:
    account: Final = candidate(uuid4())
    resolution: Final = Resolution(
        card_id=uuid4(),
        key_id=uuid4(),
        card_version=1,
        policy_version=1,
        policy=AccountPolicy(),
        candidates=(account,),
    )

    disabled: Final = routes(resolution, "model-a", "/v1/responses", Headers(), websocket=True)
    card_enabled: Final = resolution.model_copy(update={"websocket_enabled": True})
    no_candidate: Final = routes(card_enabled, "model-a", "/v1/responses", Headers(), websocket=True)

    assert disabled == Rejected(403, "WebSocket transport is disabled for this card")
    assert no_candidate == Rejected(503, "No bound account currently supports this model and policy")


def test_card_websocket_forwards_frames_with_internal_auth_and_releases_the_lease() -> None:
    dialer: Final = FakeWebSocketDialer()
    client, control = setup_gateway(lambda _: httpx.Response(500), websocket_dialer=dialer)
    enabled_policy: Final = control.resolution.policy.model_copy(
        update={"transport": TransportPolicy(websocket="enabled", debug_log_enabled=True)}
    )
    enabled_candidate: Final = control.resolution.candidates[0].model_copy(
        update={
            "policy": control.resolution.candidates[0].policy.model_copy(
                update={"transport": TransportPolicy(websocket="enabled")}
            ),
            "websocket_enabled": True,
            "credentials": (
                GatewayCredential(
                    api_key="internal-secret",
                    proxy_url="http://proxy-user:proxy-secret@proxy.test:8080",
                ),
            ),
        }
    )
    control.resolution = control.resolution.model_copy(
        update={
            "policy": enabled_policy,
            "candidates": (enabled_candidate,),
            "websocket_enabled": True,
        }
    )

    with client.websocket_connect(
        "/v1/responses?model=public-model&trace=query-secret&token=query-token-secret",
        headers={"Authorization": f"Bearer {_KEY}"},
    ) as socket:
        socket.send_json({"type": "response.create", "model": "public-model", "input": "hello"})
        response: Final = socket.receive_json()

    assert response["response"]["model"] == "public-model"
    assert json.loads(dialer.upstream.sent[0])["model"] == "model-a"
    assert dialer.requests[0].url.endswith("/v1/responses?model=model-a&trace=query-secret&token=query-token-secret")
    headers: Final = dict(dialer.requests[0].headers)
    assert headers["authorization"] == "Bearer internal-secret"
    request_repr: Final = str(dialer.requests)
    assert _KEY not in request_repr
    assert "internal-secret" not in request_repr
    assert "proxy-secret" not in request_repr
    assert len(control.acquisitions) == 1
    assert len(control.finished) == 1
    assert control.finished[0].method == "GET"
    assert json.loads(control.finished[0].detail or "{}") == {
        "account_id": str(enabled_candidate.id),
        "query_fields": ["model", "trace"],
        "supplier": "openai_codex",
        "transport": "websocket",
    }
    assert all(
        secret not in (control.finished[0].detail or "")
        for secret in (_KEY, "internal-secret", "proxy-secret", "query-secret", "query-token-secret")
    )


def test_websocket_model_change_releases_the_lease_and_closes_the_connection() -> None:
    dialer: Final = FakeWebSocketDialer()
    client, control = setup_gateway(lambda _: httpx.Response(500), websocket_dialer=dialer)
    enabled_candidate: Final = control.resolution.candidates[0].model_copy(
        update={
            "policy": control.resolution.candidates[0].policy.model_copy(
                update={"transport": TransportPolicy(websocket="enabled")}
            ),
            "websocket_enabled": True,
        }
    )
    control.resolution = control.resolution.model_copy(
        update={
            "policy": control.resolution.policy.model_copy(update={"transport": TransportPolicy(websocket="enabled")}),
            "candidates": (enabled_candidate,),
            "websocket_enabled": True,
        }
    )

    with client.websocket_connect(
        "/v1/responses?model=public-model",
        headers={"Authorization": f"Bearer {_KEY}"},
    ) as socket:
        socket.send_json({"type": "response.create", "model": "different-model", "input": "hello"})
        closed: Final = socket.receive()

    assert closed["type"] == "websocket.close"
    assert closed["code"] == 1008
    assert len(control.finished) == 1
    assert control.finished[0].http_status == 400
    assert control.finished[0].message == "WebSocket frame changes the routed model"


def test_http_debug_detail_records_structure_without_secret_values() -> None:
    client, control = setup_gateway(lambda _: httpx.Response(200, json={"output": []}))
    control.resolution = control.resolution.model_copy(
        update={
            "policy": control.resolution.policy.model_copy(
                update={"transport": TransportPolicy(debug_log_enabled=True)}
            )
        }
    )

    with client:
        response: Final = client.post(
            "/v1/responses?trace=enabled&token=query-secret",
            json={"model": "public-model", "input": "body-secret", "api_key": "body-key-secret"},
            headers={"Authorization": f"Bearer {_KEY}"},
        )

    assert response.status_code == 200
    detail: Final = control.finished[0].detail or ""
    assert json.loads(detail) == {
        "account_id": str(control.resolution.candidates[0].id),
        "query_fields": ["trace"],
        "request_fields": ["input", "model"],
        "stream": False,
        "supplier": "openai_codex",
        "transport": "http",
    }
    assert all(
        secret not in detail
        for secret in (_KEY, "internal-secret", "query-secret", "body-secret", "body-key-secret", "enabled")
    )


def test_card_key_forwards_only_to_bound_target_with_internal_credentials() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "Bearer internal-secret"
        assert "x-api-key" not in request.headers and "cookie" not in request.headers
        assert "x-account-pool-card-id" not in request.headers
        assert request.headers["session-id"] == "codex-session"
        assert request.headers["x-claude-code-session-id"] == "claude-session"
        assert request.headers["anthropic-beta"] == "context-1m"
        assert request.headers["x-codex-turn-metadata"] == '{"turn_id":"turn-1"}'
        assert request.url.query == b"api-version=2026-09-01&feature=one&feature=two"
        assert json.loads(request.content)["model"] == "model-a"
        return httpx.Response(
            200,
            headers={
                "x-litellm-response-cost": "0.00042",
                "x-request-id": "upstream-request",
                "x-ratelimit-remaining-requests": "7",
            },
            json={"model": "model-a", "choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}},
        )

    client, control = setup_gateway(upstream)
    with client:
        response: Final = client.post(
            "/v1/chat/completions?api-version=2026-09-01&feature=one&feature=two",
            json={"model": "public-model", "messages": []},
            headers={
                "Authorization": f"Bearer {_KEY}",
                "Cookie": "token=private",
                "x-api-key": "downstream-key",
                "x-account-pool-card-id": str(uuid4()),
                "Session-Id": "codex-session",
                "X-Claude-Code-Session-Id": "claude-session",
                "Anthropic-Beta": "context-1m",
                "X-Codex-Turn-Metadata": '{"turn_id":"turn-1"}',
            },
        )
    assert response.status_code == 200 and response.json()["model"] == "public-model"
    assert seen[0].url.host == f"cliproxy-{control.resolution.card_id.hex}"
    assert len(control.finished) == 1 and control.finished[0].input_tokens == 4
    assert control.finished[0].cost_usd == 0.00042
    assert control.acquisitions[0].routing_reason == "automatic"
    assert control.acquisitions[0].estimated_tokens == 0
    assert response.headers["x-request-id"] == "upstream-request"
    assert response.headers["x-ratelimit-remaining-requests"] == "7"
    assert response.headers["x-account-pool-request-id"]
    assert _KEY not in response.text and "internal-secret" not in response.text


def test_session_hash_recognizes_codex_and_claude_session_headers() -> None:
    codex: Final = session_hash(Headers({"Session-Id": "codex-session"}), "gpt-5")
    claude: Final = session_hash(Headers({"X-Claude-Code-Session-Id": "claude-session"}), "claude")

    assert codex is not None
    assert claude is not None
    assert codex != claude


def test_upstream_error_preserves_safe_json_and_rate_limit_headers() -> None:
    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "12", "x-request-id": "provider-request"},
            json={
                "error": {
                    "message": "quota exceeded for Bearer internal-secret",
                    "type": "rate_limit_error",
                    "code": "rate_limit_exceeded",
                    "authorization": "Bearer internal-secret",
                }
            },
        )

    client, _ = setup_gateway(upstream)
    with client:
        response: Final = client.post(
            "/v1/responses",
            json={"model": "model-a"},
            headers={"Authorization": f"Bearer {_KEY}"},
        )

    assert response.status_code == 429
    assert response.json()["error"] == {
        "message": "quota exceeded for REDACTED",
        "type": "rate_limit_error",
        "code": "rate_limit_exceeded",
        "authorization": "[REDACTED]",
    }
    assert response.headers["retry-after"] == "12"
    assert response.headers["x-request-id"] == "provider-request"
    assert response.headers["x-account-pool-request-id"]


def test_openai_compatible_route_uses_prefixed_model_custom_headers_and_weighted_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "litellm.proxy.management_endpoints.account_pool_gateway_forwarder.validate_url",
        lambda url: (url, "api.example.com"),
    )
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["host"] == "api.example.com"
        assert request.headers["x-provider-feature"] == "enabled"
        assert request.headers.get_list("authorization") in (["Bearer first-key"], ["Bearer second-key"])
        assert request.headers["accept-encoding"] == "identity"
        assert json.loads(request.content)["model"] == "chat-model"
        return httpx.Response(200, json={"model": "chat-model", "choices": []})

    client, control = setup_gateway(upstream)
    compatible: Final = control.resolution.candidates[0].model_copy(
        update={
            "channel": "openai_compatible",
            "supplier": "openai_compatible",
            "enabled_models": ("vendor/chat-model",),
            "api_base": "https://api.example.com/v1",
            "api_key": "first-key",
            "credentials": (
                GatewayCredential(api_key="first-key", weight=2),
                GatewayCredential(api_key="second-key", weight=1),
            ),
            "headers": (
                ("x-provider-feature", "enabled"),
                ("Authorization", "Bearer provider-override"),
                ("Host", "attacker.example"),
                ("Accept-Encoding", "gzip"),
            ),
            "model_prefix": "vendor/",
        }
    )
    control.resolution = control.resolution.model_copy(update={"candidates": (compatible,)})
    with client:
        response: Final = client.post(
            "/v1/chat/completions",
            json={"model": "vendor/chat-model", "messages": []},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 200
    assert response.json()["model"] == "vendor/chat-model"
    assert seen[0].url == "https://api.example.com/v1/chat/completions"


def test_explicit_output_cap_is_included_in_token_reservation() -> None:
    client, control = setup_gateway(lambda _: httpx.Response(200, json={"output": []}))
    budget_policy: Final = AccountPolicy(routing=RoutingPolicy(token_budget_limit=10000))
    control.resolution = control.resolution.model_copy(
        update={
            "candidates": tuple(
                item.model_copy(update={"policy": budget_policy}) for item in control.resolution.candidates
            )
        }
    )
    with client:
        response: Final = client.post(
            "/v1/responses",
            json={"model": "model-a", "input": "hello", "max_output_tokens": 321},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 200
    assert control.acquisitions[0].estimated_tokens > 321


def test_models_and_management_scope_are_separate_from_ordinary_keys() -> None:
    client, _ = setup_gateway(lambda _: pytest.fail("No upstream request expected"))
    with client:
        assert (
            client.get("/v1/models", headers={"Authorization": f"Bearer {_KEY}"}).json()["data"][1]["id"]
            == "public-model"
        )
        assert client.get("/account_pool/environments", headers={"Authorization": f"Bearer {_KEY}"}).status_code == 403
        assert client.post(
            "/v1/chat/completions", json={"model": "model-a"}, headers={"Authorization": "Bearer ordinary"}
        ).json() == {"route": "ordinary"}
        assert client.get("/v1/models", headers={"Authorization": "Bearer cpk_invalid"}).status_code == 401


def test_invalid_card_key_is_rejected_before_json_body_is_parsed() -> None:
    client, _ = setup_gateway(lambda _: pytest.fail("No upstream request expected"))
    invalid_key: Final = "cpk_" + "invalid-key-material"
    with client:
        response: Final = client.post(
            "/v1/chat/completions",
            content=b"not-json",
            headers={"Authorization": f"Bearer {invalid_key}", "Content-Type": "application/json"},
        )
    assert response.status_code == 401


def test_models_include_account_aliases_and_card_aliases_that_target_them() -> None:
    client, control = setup_gateway(lambda _: pytest.fail("No upstream request expected"))
    account: Final = control.resolution.candidates[0]
    account_alias: Final = account.model_copy(
        update={"policy": AccountPolicy(model_aliases=(ModelAlias(alias="account-model", target="model-a"),))}
    )
    control.resolution = control.resolution.model_copy(
        update={
            "policy": AccountPolicy(model_aliases=(ModelAlias(alias="public-account-model", target="account-model"),)),
            "candidates": (account_alias, *control.resolution.candidates[1:]),
        }
    )
    with client:
        response: Final = client.get("/v1/models", headers={"Authorization": f"Bearer {_KEY}"})
    assert {item["id"] for item in response.json()["data"]} >= {
        "model-a",
        "account-model",
        "public-account-model",
    }


def test_responses_compact_requires_policy_and_preserves_the_upstream_path() -> None:
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"output": []})

    client, control = setup_gateway(upstream)
    with client:
        denied: Final = client.post(
            "/v1/responses/compact",
            json={"model": "model-a", "input": "test"},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
        control.resolution = control.resolution.model_copy(
            update={"policy": AccountPolicy(codex=CodexPolicy(responses_compact_enabled=True))}
        )
        allowed: Final = client.post(
            "/v1/responses/compact",
            json={"model": "model-a", "input": "test"},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert seen == ["/v1/responses/compact"]


def test_image_tool_skips_accounts_that_disable_image_generation() -> None:
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, json={"output": []})

    client, control = setup_gateway(upstream)
    disabled: Final = control.resolution.candidates[0].model_copy(
        update={"policy": AccountPolicy(transport=TransportPolicy(image_generation="disabled"))}
    )
    control.resolution = control.resolution.model_copy(
        update={"candidates": (disabled, control.resolution.candidates[1])}
    )
    with client:
        response: Final = client.post(
            "/v1/responses",
            json={"model": "model-a", "tools": [{"type": "image_generation"}]},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 503
    assert seen == []


def test_upstream_redirect_is_reported_as_gateway_failure() -> None:
    client, control = setup_gateway(lambda _: httpx.Response(302, headers={"location": "https://example.test"}))
    with client:
        response: Final = client.post(
            "/v1/responses", json={"model": "model-a"}, headers={"Authorization": f"Bearer {_KEY}"}
        )
    assert response.status_code == 502
    assert control.finished[0].http_status == 502


@pytest.mark.parametrize("revoked,exhausted,expected", [(True, False, 401), (False, True, 503)])
def test_revocation_and_concurrency_are_checked_before_forwarding(
    revoked: bool, exhausted: bool, expected: int
) -> None:
    client, control = setup_gateway(lambda _: pytest.fail("No upstream request expected"))
    control.revoked = revoked
    control.exhausted = exhausted
    with client:
        response: Final = client.post(
            "/v1/responses", json={"model": "model-a"}, headers={"Authorization": f"Bearer {_KEY}"}
        )
    assert response.status_code == expected


def test_token_budget_exhaustion_falls_through_with_explicit_reason() -> None:
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, json={"output": []})

    client, control = setup_gateway(upstream, retry=True)
    rejected: Final = control.resolution.candidates[0].id
    control.budget_exhausted_account_ids = frozenset((rejected,))
    with client:
        response: Final = client.post(
            "/v1/responses",
            json={"model": "model-a", "input": "hello"},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 200
    assert seen == [f"cliproxy-{control.resolution.candidates[1].id.hex}"]
    assert [request.routing_reason for request in control.acquisitions] == ["automatic", "token_budget_fallback"]


def test_all_token_budgets_exhausted_returns_rate_limit() -> None:
    client, control = setup_gateway(lambda _: pytest.fail("No upstream request expected"))
    control.budget_exhausted_account_ids = frozenset(item.id for item in control.resolution.candidates)

    with client:
        response: Final = client.post(
            "/v1/responses",
            json={"model": "model-a", "input": "hello"},
            headers={"Authorization": f"Bearer {_KEY}"},
        )

    assert response.status_code == 429
    assert response.json()["error"]["message"] == "No bound account has available local token budget"


@pytest.mark.parametrize("fallback,expected_attempts,expected_accounts", [(False, 5, 1), (True, 10, 2)])
def test_same_card_attempt_budget_is_independent_of_failover(fallback, expected_attempts, expected_accounts) -> None:
    calls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        raise httpx.ConnectError("upstream unreachable", request=request)

    client, control = setup_gateway(upstream, retry=fallback, max_attempts=5)
    with client:
        response = client.post("/v1/responses", json={"model": "model-a"}, headers={"Authorization": f"Bearer {_KEY}"})
    assert response.status_code == 502
    assert len(calls) == expected_attempts
    assert len(set(calls)) == expected_accounts
    assert calls[:5] == [calls[0]] * 5
    assert [item.attempt for item in control.acquisitions] == list(range(1, expected_attempts + 1))
    assert len({item.request_id for item in control.acquisitions}) == 1
    assert control.acquisitions[1].routing_reason == "same_account_retry"
    assert not control.finished[0].switched_account
    if fallback:
        assert control.finished[4].switched_account
        assert control.acquisitions[5].routing_reason == "retry_failover"


@pytest.mark.parametrize(
    "error",
    [
        {"type": "error", "code": "server_error", "message": "private upstream details"},
        {"error": {"code": "server_error", "message": "private upstream details"}},
    ],
)
def test_stream_start_events_are_discarded_before_same_card_retry(error) -> None:
    calls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        content = (
            'data: {"type":"response.created","response":{"id":"failed-attempt"}}\n\n'
            'data: {"type":"response.in_progress"}\n\n'
            f"data: {json.dumps(error)}\n\n"
            if len(calls) == 1
            else 'data: {"type":"response.created","response":{"id":"successful-attempt"}}\n\n'
            'data: {"type":"response.output_text.delta","delta":"OK"}\n\n'
            'data: {"type":"response.completed"}\n\n'
        )
        return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})

    client, control = setup_gateway(upstream)
    control.resolution = control.resolution.model_copy(
        update={
            "candidates": tuple(
                account.model_copy(update={"supplier": "anthropic_claude"}) for account in control.resolution.candidates
            )
        }
    )
    with client:
        response = client.post(
            "/v1/responses", json={"model": "model-a", "stream": True}, headers={"Authorization": f"Bearer {_KEY}"}
        )
    assert response.status_code == 200
    assert "successful-attempt" in response.text and "OK" in response.text
    assert "failed-attempt" not in response.text and "private upstream" not in response.text
    assert len(calls) == 2 and len(set(calls)) == 1
    assert control.finished[0].upstream_code == "server_error"
    assert control.finished[0].model_cooldown_seconds == 1
    assert control.finished[-1].http_status == 200


@pytest.mark.parametrize(
    "event",
    [
        {"type": "response.output_text.delta", "delta": "hello"},
        {"type": "response.function_call_arguments.delta", "delta": "{}"},
        {"type": "response.reasoning_text.delta", "delta": "thinking"},
    ],
)
def test_stream_error_after_meaningful_event_is_never_replayed(event) -> None:
    content = "data: " + json.dumps(event) + '\n\ndata: {"type":"error","code":"server_error"}\n\n'
    client, control = setup_gateway(
        lambda _: httpx.Response(200, content=content, headers={"content-type": "text/event-stream"}), retry=True
    )
    with client:
        response = client.post(
            "/v1/responses", json={"model": "model-a", "stream": True}, headers={"Authorization": f"Bearer {_KEY}"}
        )
    assert response.status_code == 200
    assert len(control.acquisitions) == 1
    assert control.finished[0].http_status == 502
    assert not control.finished[0].retryable


@pytest.mark.parametrize("stream_error", [False, True])
@pytest.mark.parametrize("tools", [[], [{"type": "function", "function": {"name": "lookup"}}]])
def test_codex_retry_exhaustion_is_not_replayed_or_switched_by_gateway(stream_error, tools) -> None:
    def upstream(_: httpx.Request) -> httpx.Response:
        if stream_error:
            return httpx.Response(
                200,
                content='data: {"error":{"code":"server_error"}}\n\n',
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(503, json={"error": {"code": "server_is_overloaded"}})

    client, control = setup_gateway(upstream, retry=True, max_attempts=5)
    with client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "stream": True, "tools": tools},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == (502 if stream_error else 503)
    assert len(control.acquisitions) == len(control.finished) == 1
    assert control.acquisitions[0].account_id == control.resolution.card_id
    assert not control.finished[0].retryable
    assert not control.finished[0].switched_account


@pytest.mark.parametrize(
    "extra",
    [
        {"previous_response_id": "old-response"},
        {"conversation": "old-conversation"},
        {"tools": [{"type": "web_search"}]},
        {"background": True},
    ],
)
def test_stateful_or_tool_requests_are_not_replayed(extra) -> None:
    client, control = setup_gateway(
        lambda _: httpx.Response(503, json={"error": {"code": "server_error"}}), retry=True, max_attempts=5
    )
    with client:
        response = client.post(
            "/v1/responses", json={"model": "model-a", **extra}, headers={"Authorization": f"Bearer {_KEY}"}
        )
    assert response.status_code == 503
    assert len(control.acquisitions) == 1


@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.parametrize("code", ["invalid_prompt", "content_policy_violation"])
def test_stream_refusal_before_content_returns_400_without_retry(with_tools: bool, code: str) -> None:
    content: Final = (
        'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n\n'
        f'data: {{"error":{{"type":"invalid_request_error","code":"{code}",'
        '"message":"private upstream details internal-secret"}}\n\n'
    )
    client, control = setup_gateway(
        lambda _: httpx.Response(200, content=content, headers={"content-type": "text/event-stream"}),
        retry=True,
        max_attempts=5,
    )
    with client:
        response: Final = client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "stream": True, **({"tools": [{"type": "function"}]} if with_tools else {})},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "private upstream" not in response.text and "internal-secret" not in response.text
    assert len(control.acquisitions) == len(control.finished) == 1
    assert control.finished[0].http_status == 400
    assert control.finished[0].upstream_code == code
    assert control.finished[0].model_cooldown_seconds == 0
    assert not control.finished[0].retryable


@pytest.mark.parametrize(
    "delta", [{"content": "hello"}, {"tool_calls": [{"index": 0}]}, {"reasoning_content": "think"}]
)
def test_stream_refusal_after_content_preserves_error_without_replay(delta: dict[str, object]) -> None:
    content: Final = (
        "data: "
        + json.dumps({"choices": [{"index": 0, "delta": delta}]})
        + '\n\ndata: {"error":{"code":"invalid_prompt","type":"invalid_request_error",'
        '"message":"private upstream details internal-secret"}}\n\n'
    )
    client, control = setup_gateway(
        lambda _: httpx.Response(200, content=content, headers={"content-type": "text/event-stream"}),
        retry=True,
    )
    with client:
        response: Final = client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "stream": True},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 200
    error: Final = json.loads(response.text.split("data: ")[-1])["error"]
    assert error["code"] == "invalid_prompt"
    assert error["type"] == "invalid_request_error"
    assert error["status_code"] == 400
    assert "private upstream" not in response.text and "internal-secret" not in response.text
    assert len(control.acquisitions) == len(control.finished) == 1
    assert control.finished[0].http_status == 400
    assert not control.finished[0].retryable


def test_tool_stream_failure_before_output_is_not_replayed() -> None:
    client, control = setup_gateway(
        lambda _: httpx.Response(
            200,
            content='data: {"error":{"code":"server_error"}}\n\n',
            headers={"content-type": "text/event-stream"},
        ),
        retry=True,
        max_attempts=5,
    )
    with client:
        response: Final = client.post(
            "/v1/responses",
            json={"model": "model-a", "stream": True, "tools": [{"type": "web_search"}]},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 502
    assert len(control.acquisitions) == len(control.finished) == 1
    assert not control.finished[0].retryable


def test_retry_records_one_request_chain_and_uses_next_bound_account() -> None:
    calls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message": "Bearer private"}})
        return httpx.Response(200, json={"output": []})

    client, control = setup_gateway(upstream, retry=True)
    control.resolution = control.resolution.model_copy(
        update={
            "candidates": tuple(
                account.model_copy(update={"supplier": "anthropic_claude"}) for account in control.resolution.candidates
            )
        }
    )
    with client:
        response: Final = client.post(
            "/v1/responses", json={"model": "model-a"}, headers={"Authorization": f"Bearer {_KEY}"}
        )
    assert response.status_code == 200 and len(set(calls)) == 2
    assert len({request.request_id for request in control.acquisitions}) == 1
    assert control.finished[0].next_account_id == control.acquisitions[1].account_id
    assert control.finished[0].retryable and control.finished[1].http_status == 200
    assert [request.attempt for request in control.acquisitions] == [1, 2]
    assert [request.routing_reason for request in control.acquisitions] == ["automatic", "retry_failover"]
    assert "private" not in str(control.finished)


@pytest.mark.parametrize("previous_response_id,retry", [(None, False), ("upstream-response", True)])
def test_failover_requires_permission_and_does_not_replay_stateful_requests(previous_response_id, retry) -> None:
    client, control = setup_gateway(lambda _: httpx.Response(503, json={"error": {"code": "unavailable"}}), retry=retry)
    with client:
        response: Final = client.post(
            "/v1/responses",
            json={"model": "model-a", "previous_response_id": previous_response_id},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 503
    assert len(control.acquisitions) == 1
    assert len({item.account_id for item in control.acquisitions}) == 1
    assert not control.finished[-1].retryable


def test_read_timeout_is_not_retried_even_with_fallback_enabled() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret upstream response", request=request)

    client, control = setup_gateway(upstream, retry=True)
    with client:
        response: Final = client.post(
            "/v1/responses", json={"model": "model-a"}, headers={"Authorization": f"Bearer {_KEY}"}
        )
    assert response.status_code == 504 and len(control.acquisitions) == 1
    assert control.finished[0].http_status == 504 and not control.finished[0].retryable


@pytest.mark.parametrize("streaming", (False, True))
@pytest.mark.parametrize("responses_api", (False, True))
def test_gateway_preserves_cache_usage_in_completion_logs(streaming: bool, responses_api: bool) -> None:
    usage: Final = (
        {"input_tokens": 100, "output_tokens": 9, "input_tokens_details": {"cached_tokens": 80}}
        if responses_api
        else {"prompt_tokens": 100, "completion_tokens": 9, "prompt_tokens_details": {"cached_tokens": 80}}
    )
    payload: Final = {"type": "response.completed", "response": {"usage": usage}} if responses_api else {"usage": usage}
    body: Final = f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n"
    client, control = setup_gateway(
        lambda _: (
            httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
            if streaming
            else httpx.Response(200, json=payload)
        )
    )
    with client:
        response: Final = client.post(
            "/v1/responses" if responses_api else "/v1/chat/completions",
            headers={"Authorization": f"Bearer {_KEY}"},
            json={"model": "model-a", "stream": streaming},
        )
    assert response.status_code == 200
    result: Final = control.finished[0].model_dump()
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 9
    assert result.get("cache_read_input_tokens") == 80
    assert result.get("cache_creation_input_tokens") == 0


def test_stream_cache_usage_preserves_zero_and_missing_fields() -> None:
    from litellm.proxy.management_endpoints.account_pool_stream import EventStream

    state: Final = EventStream()
    state.observe_payload({"usage": {"input_tokens": 12, "cache_read_input_tokens": 0}})
    state.observe_payload({"usage": {"output_tokens": 3}})
    assert getattr(state, "cache_read_input_tokens", None) == 0
    assert getattr(state, "cache_creation_input_tokens", None) is None
    assert state.input_tokens == 12
    assert state.output_tokens == 3


def test_stream_keeps_sse_and_releases_lease_after_consumption() -> None:
    stream: Final = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'
    client, control = setup_gateway(
        lambda _: httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"})
    )
    with client:
        response: Final = client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "stream": True},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 200 and response.text == stream
    assert control.finished[0].http_status == 200


def test_concurrency_falls_through_to_next_candidate_without_consuming_retry_budget() -> None:
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, json={"output": []})

    client, control = setup_gateway(upstream, retry=True)
    sticky: Final = control.resolution.candidates[0].id
    control.resolution = control.resolution.model_copy(update={"sticky_account_id": sticky})
    control.unavailable_account_ids = frozenset((sticky,))
    with client:
        response: Final = client.post(
            "/v1/responses",
            json={"model": "model-a"},
            headers={"Authorization": f"Bearer {_KEY}", "x-session-id": "sticky-session"},
        )
    assert response.status_code == 200
    assert seen == [f"cliproxy-{control.resolution.candidates[1].id.hex}"]
    assert [request.attempt for request in control.acquisitions] == [1, 1]
    assert [request.allow_session_rebind for request in control.acquisitions] == [False, True]
    assert [request.routing_reason for request in control.acquisitions] == ["session_affinity", "concurrency_fallback"]


def test_truncated_stream_is_logged_as_failure_and_never_replayed() -> None:
    partial: Final = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    client, control = setup_gateway(
        lambda _: httpx.Response(200, content=partial, headers={"content-type": "text/event-stream"}), retry=True
    )
    with client:
        response: Final = client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "stream": True},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 200
    assert "Upstream stream interrupted" in response.text
    assert len(control.acquisitions) == 1
    assert control.finished[0].http_status == 502


def test_sticky_backup_is_kept_while_it_remains_eligible() -> None:
    card: Final = uuid4()
    backup: Final = candidate(uuid4())
    backup = backup.model_copy(update={"policy": AccountPolicy(routing=RoutingPolicy(is_backup=True))})
    control: Final = Control(
        Resolution(
            card_id=card,
            key_id=uuid4(),
            card_version=1,
            policy_version=1,
            policy=AccountPolicy(routing=RoutingPolicy(session_affinity=True, fallback_enabled=True)),
            candidates=(candidate(card, 10), backup),
            sticky_account_id=backup.id,
        )
    )
    app: Final = FastAPI()
    app.add_middleware(
        lambda app, **kwargs: AccountPoolGatewayMiddleware(app, **kwargs).dispatch_card,
        control_factory=lambda _: control,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"output": []}))
        ),
    )
    with TestClient(app) as client:
        response: Final = client.post(
            "/v1/responses", json={"model": "model-a"}, headers={"Authorization": f"Bearer {_KEY}"}
        )
    assert response.status_code == 200
    assert control.acquisitions[0].account_id == backup.id
    assert control.acquisitions[0].routing_reason == "session_affinity"


@pytest.mark.parametrize("cost", [None, "", "-0.01", "nan", "inf", "1_000", "invalid"])
def test_untrusted_upstream_cost_is_not_recorded(cost: str | None) -> None:
    headers: Final = {} if cost is None else {"x-litellm-response-cost": cost}
    client, control = setup_gateway(lambda _: httpx.Response(200, headers=headers, json={"output": []}))

    with client:
        response: Final = client.post(
            "/v1/responses", json={"model": "model-a"}, headers={"Authorization": f"Bearer {_KEY}"}
        )

    assert response.status_code == 200
    assert control.finished[0].cost_usd is None


def test_truncated_stream_keeps_trusted_upstream_cost() -> None:
    partial: Final = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    client, control = setup_gateway(
        lambda _: httpx.Response(
            200,
            content=partial,
            headers={"content-type": "text/event-stream", "x-litellm-response-cost": "0.00042"},
        )
    )

    with client:
        response: Final = client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "stream": True},
            headers={"Authorization": f"Bearer {_KEY}"},
        )

    assert response.status_code == 200
    assert control.finished[0].cost_usd == 0.00042
