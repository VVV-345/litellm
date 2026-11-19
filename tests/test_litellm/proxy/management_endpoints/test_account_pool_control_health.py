"""控制面熔断的单元与中间件级验证：只统计连接类故障，打开期间不发起控制面调用。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from litellm.proxy.management_endpoints.account_pool_control_health import (
    ControlHealth,
    ControlState,
    control_failure_is_outage,
)
from litellm.proxy.management_endpoints.account_pool_gateway import AccountPoolGatewayMiddleware
from litellm.proxy.management_endpoints.account_pool_gateway_client import ControlError
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import (
    AcquireRejected,
    AcquireRequest,
    Candidate,
    FinishRequest,
    Lease,
    Resolution,
    ResolveRequest,
)
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPolicy,
    RoutingPolicy,
)

_KEY: Final = "cpk_" + "test-only-" * 5


class _Clock:
    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _resolution(card: UUID) -> Resolution:
    policy: Final = AccountPolicy(routing=RoutingPolicy(fallback_enabled=False, max_attempts=1, backoff_ms=0))
    return Resolution(
        card_id=card,
        key_id=uuid4(),
        card_version=1,
        policy_version=1,
        policy=policy,
        candidates=(
            Candidate(
                id=card,
                channel="cliproxyapi",
                supplier="openai_codex",
                environment_version=1,
                policy_version=1,
                enabled_models=("model-a",),
                api_base=f"http://cliproxy-{card.hex}:8317/v1",
                api_key="internal-secret",
                concurrency_limit=1,
                policy=policy,
            ),
        ),
        enabled_models=("model-a",),
    )


class _StubControl:
    def __init__(self, *, error: BaseException | None = None, resolution: Resolution | None = None) -> None:
        self.resolution: Final = resolution or _resolution(uuid4())
        self.error: BaseException | None = error
        self.resolve_calls = 0
        self.acquisitions: list[AcquireRequest] = []
        self.finished: list[FinishRequest] = []

    async def resolve(self, request: ResolveRequest) -> Resolution:
        self.resolve_calls += 1
        if self.error is not None:
            raise self.error
        return self.resolution

    async def acquire(self, request: AcquireRequest) -> Lease | AcquireRejected:
        self.acquisitions.append(request)
        if self.error is not None:
            raise self.error
        return Lease(
            lease_id=uuid4(),
            card_id=self.resolution.card_id,
            key_id=self.resolution.key_id,
            account_id=request.account_id,
            request_id=request.request_id,
            channel="cliproxyapi",
            supplier="openai_codex",
            model=request.model,
            started_at=datetime.now(timezone.utc),
        )

    async def finish(self, request: FinishRequest) -> None:
        self.finished.append(request)


def _upstream(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"id": "chatcmpl-test", "object": "chat.completion", "choices": []})


def _client(control: _StubControl, health: ControlHealth) -> TestClient:
    app: Final = FastAPI()

    @app.post("/v1/chat/completions")
    async def ordinary() -> dict[str, str]:
        return {"route": "ordinary"}

    app.add_middleware(
        lambda app, **kwargs: AccountPoolGatewayMiddleware(app, **kwargs).dispatch_card,
        control_factory=lambda _: control,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(_upstream)),
        health=health,
    )
    return TestClient(app)


def _post(client: TestClient) -> httpx.Response:
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {_KEY}"},
        json={"model": "model-a", "messages": [{"role": "user", "content": "hi"}]},
    )


def test_health_opens_after_consecutive_outages_and_blocks() -> None:
    clock: Final = _Clock()
    health: Final = ControlHealth(failures=2, open_seconds=30.0, clock=clock)
    assert health.state is ControlState.CLOSED
    health.failed(outage=True)
    assert health.permits() is True
    health.failed(outage=True)
    assert health.state is ControlState.OPEN
    assert health.permits() is False
    assert health.retry_after_seconds() == 30


def test_health_half_open_allows_one_probe_and_closes_on_success() -> None:
    clock: Final = _Clock()
    health: Final = ControlHealth(failures=1, open_seconds=30.0, clock=clock)
    health.failed(outage=True)
    assert health.permits() is False
    clock.advance(31.0)
    assert health.state is ControlState.HALF_OPEN
    assert health.permits() is True
    assert health.permits() is False
    clock.advance(1.0)
    health.succeeded()
    assert health.state is ControlState.CLOSED
    assert health.permits() is True


def test_health_reopens_when_probe_fails_again() -> None:
    clock: Final = _Clock()
    health: Final = ControlHealth(failures=1, open_seconds=30.0, clock=clock)
    health.failed(outage=True)
    clock.advance(31.0)
    assert health.permits() is True
    health.failed(outage=True)
    assert health.state is ControlState.OPEN
    assert health.permits() is False


def test_reachable_control_errors_do_not_count_as_outage() -> None:
    health: Final = ControlHealth(failures=1, open_seconds=30.0)
    for error in (ControlError(401), ControlError(403), ControlError(429), ControlError(503, reason="configuration")):
        assert control_failure_is_outage(error) is False
    for error in (
        ControlError(500),
        ControlError(502),
        ControlError(0),
        httpx.ConnectError("down"),
        httpx.ReadTimeout("slow"),
    ):
        assert control_failure_is_outage(error) is True
    health.failed(outage=False)
    assert health.state is ControlState.CLOSED


def test_client_status_keeps_authentication_and_hides_internal_failures() -> None:
    assert ControlError(401).client_status == 401
    assert ControlError(403).client_status == 403
    assert ControlError(429).client_status == 503
    assert ControlError(500).client_status == 503


def test_gateway_short_circuits_without_control_calls_while_open() -> None:
    clock: Final = _Clock()
    health: Final = ControlHealth(failures=2, open_seconds=30.0, clock=clock)
    control: Final = _StubControl(error=ControlError(500))
    with _client(control, health) as client:
        assert _post(client).status_code == 503
        assert _post(client).status_code == 503
        assert control.resolve_calls == 2
        blocked: Final = _post(client)
        assert blocked.status_code == 503
        assert blocked.headers["Retry-After"] == "30"
        assert blocked.headers["x-account-pool-failure"] == "control_plane_unavailable"
        assert control.resolve_calls == 2


def test_gateway_permission_errors_never_open_the_breaker() -> None:
    health: Final = ControlHealth(failures=2, open_seconds=30.0)
    control: Final = _StubControl(error=ControlError(401))
    with _client(control, health) as client:
        for _ in range(4):
            assert _post(client).status_code == 401
        assert control.resolve_calls == 4
        assert health.state is ControlState.CLOSED


def test_gateway_probe_after_cooldown_serves_request_again() -> None:
    clock: Final = _Clock()
    health: Final = ControlHealth(failures=1, open_seconds=30.0, clock=clock)
    control: Final = _StubControl(error=ControlError(503))
    with _client(control, health) as client:
        assert _post(client).status_code == 503
        assert control.resolve_calls == 1
        assert _post(client).status_code == 503
        assert control.resolve_calls == 1
        clock.advance(31.0)
        control.error = None
        recovered: Final = _post(client)
        assert recovered.status_code == 200
        assert control.resolve_calls == 2
        assert len(control.acquisitions) == 1
        assert health.state is ControlState.CLOSED


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_transport_failures_open_the_breaker(error: BaseException) -> None:
    health: Final = ControlHealth(failures=1, open_seconds=30.0)
    control: Final = _StubControl(error=error)
    with _client(control, health) as client:
        assert _post(client).status_code == 503
        assert control.resolve_calls == 1
        assert health.state is ControlState.OPEN
