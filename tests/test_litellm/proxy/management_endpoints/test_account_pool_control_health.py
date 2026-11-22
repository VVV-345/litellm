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

    async def log_status(self, request) -> None:
        pass


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


@pytest.mark.asyncio
async def test_successful_resolve_cannot_mask_repeated_acquire_outages():
    class BrokenAcquire(_StubControl):
        async def acquire(self, request):
            self.acquisitions.append(request)
            raise ControlError(503)
    health = ControlHealth(failures=3)
    control = BrokenAcquire()
    wrapped = health.wrap(control)
    request = AcquireRequest(card_key=_KEY, account_id=control.resolution.card_id, request_id=uuid4(), model="model-a",
        card_version=1, policy_version=1, account_version=1, account_policy_version=1, timeout_seconds=30, attempt=1)
    for _ in range(5):
        try:
            await wrapped.resolve(ResolveRequest(card_key=_KEY))
            await wrapped.acquire(request)
        except ControlError:
            pass
    assert health.state is ControlState.OPEN
    assert len(control.acquisitions) == 3 and control.resolve_calls == 3


@pytest.mark.asyncio
async def test_late_pre_open_success_cannot_close_newer_failure_generation():
    import asyncio
    entered, release = asyncio.Event(), asyncio.Event()
    class Delayed(_StubControl):
        async def resolve(self, request):
            if request.session_hash == "0" * 64:
                entered.set()
                await release.wait()
                return self.resolution
            raise ControlError(503)
    health = ControlHealth(failures=1)
    wrapped = health.wrap(Delayed())
    task = asyncio.create_task(wrapped.resolve(ResolveRequest(card_key=_KEY, session_hash="0" * 64)))
    await entered.wait()
    with pytest.raises(ControlError):
        await wrapped.resolve(ResolveRequest(card_key=_KEY))
    release.set()
    await task
    assert health.state is ControlState.OPEN


@pytest.mark.asyncio
async def test_cancelled_half_open_probe_releases_ownership_without_claiming_recovery():
    import asyncio
    clock, entered = _Clock(), asyncio.Event()
    class Blocked(_StubControl):
        async def resolve(self, request):
            entered.set()
            await asyncio.Event().wait()
            return self.resolution
    health = ControlHealth(failures=1, clock=clock)
    health.failed(outage=True)
    clock.advance(31)
    wrapped = health.wrap(Blocked())
    task = asyncio.create_task(wrapped.resolve(ResolveRequest(card_key=_KEY)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert health.state is ControlState.HALF_OPEN


@pytest.mark.parametrize("error", [
    httpx.PoolTimeout("local capacity"), httpx.LocalProtocolError("local header"),
    httpx.UnsupportedProtocol("bad config"),
])
def test_local_transport_configuration_and_pool_wait_are_not_manager_outages(error):
    assert not control_failure_is_outage(error)


@pytest.mark.asyncio
async def test_open_breaker_websocket_handshake_is_http_503_with_retry_after_and_no_manager_io():
    from starlette.websockets import WebSocket
    clock = _Clock()
    health = ControlHealth(failures=1, clock=clock)
    health.failed(outage=True)
    control = _StubControl()
    events = []
    async def receive():
        return {"type": "websocket.connect"}
    async def send(message):
        events.append(message)
    async def app(scope, receive, send):
        raise AssertionError("ordinary application must not run")
    scope = {"type":"websocket", "path":"/v1/responses", "raw_path":b"/v1/responses", "scheme":"ws",
        "query_string":b"model=model-a", "headers":[], "server":("test",80), "client":("test",1234),
        "subprotocols":[], "extensions":{"websocket.http.response":{}}}
    gateway = AccountPoolGatewayMiddleware(app, lambda _: control, health=health)
    await gateway.websocket(WebSocket(scope, receive, send), _KEY)
    assert events[0]["type"] == "websocket.http.response.start" and events[0]["status"] == 503
    assert dict(events[0]["headers"])[b"retry-after"] == b"30"
    assert control.resolve_calls == 0 and control.acquisitions == []


def test_probe_watchdog_ignores_success_from_replaced_probe():
    clock = _Clock()
    health = ControlHealth(failures=1, clock=clock)
    health.failed(outage=True)
    clock.advance(31)
    old = health.begin("resolve")
    clock.advance(31)
    replacement = health.begin("resolve")
    assert old is not None and replacement is not None
    health.succeeded(old)
    assert health.state is ControlState.HALF_OPEN and not health.available()
    health.succeeded(replacement)
    assert health.state is ControlState.CLOSED


def test_invalid_body_authentication_does_not_consume_admission_probe():
    clock = _Clock()
    health = ControlHealth(failures=1, clock=clock)
    health.failed(outage=True)
    clock.advance(31)
    control = _StubControl()
    with _client(control, health) as client:
        invalid = client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_KEY}"}, json={})
        assert invalid.status_code == 400 and control.resolve_calls == 1
        assert health.state is ControlState.HALF_OPEN and health.available()
        assert _post(client).status_code == 200


@pytest.mark.asyncio
async def test_control_finish_is_not_blocked_while_breaker_is_open():
    health = ControlHealth(failures=1)
    health.failed(outage=True)
    control = _StubControl()
    result = FinishRequest(lease_id=uuid4(), http_status=200, endpoint="/v1/responses", message="completed")
    await health.wrap(control).finish(result)
    assert control.finished == [result] and health.state is ControlState.OPEN


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ControlError(503), httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), TimeoutError("control deadline"),
])
@pytest.mark.parametrize("threshold", [1, 3])
async def test_probe_control_error_before_websocket_accept_is_also_http_503(error, threshold):
    from starlette.websockets import WebSocket
    health = ControlHealth(failures=threshold)
    control = _StubControl(error=error)
    events = []
    async def receive():
        return {"type":"websocket.connect"}
    async def send(message):
        events.append(message)
    async def app(scope, receive, send):
        pass
    scope = {"type":"websocket", "path":"/v1/responses", "query_string":b"model=model-a", "headers":[],
        "scheme":"ws", "extensions":{"websocket.http.response":{}}}
    gateway = AccountPoolGatewayMiddleware(app, lambda _: control,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(_upstream)), health=health)
    await gateway.websocket(WebSocket(scope, receive, send), _KEY)
    assert events[0]["type"] == "websocket.http.response.start" and events[0]["status"] == 503
    assert dict(events[0]["headers"])[b"retry-after"] == (b"30" if threshold == 1 else b"1")
    assert control.resolve_calls == 1


def test_expired_probe_cannot_heal_before_a_replacement_has_started():
    clock = _Clock()
    health = ControlHealth(failures=1, open_seconds=2, clock=clock)
    health.failed(outage=True)
    clock.advance(3)
    expired = health.begin("resolve")
    assert expired is not None
    clock.advance(3)
    health.succeeded(expired)
    assert health.state is ControlState.HALF_OPEN and health.available()


def test_stalled_control_deadline_is_service_unavailable_not_body_timeout(monkeypatch):
    import asyncio
    import time
    from litellm.proxy.management_endpoints import account_pool_control_health as module

    class Blocked(_StubControl):
        async def resolve(self, request):
            self.resolve_calls += 1
            await asyncio.Event().wait()

    original = asyncio.timeout
    monkeypatch.setattr(module.asyncio, "timeout", lambda seconds: original(0.02 if seconds == 15 else seconds))
    control = Blocked()
    health = ControlHealth(failures=1)
    with _client(control, health) as client:
        started = time.monotonic()
        result = _post(client)
        assert result.status_code == 503 and time.monotonic() - started < 1
        assert result.headers["Retry-After"] == "30"
    assert control.resolve_calls == 1 and health.state is ControlState.OPEN


def test_half_open_acquire_outage_gates_concurrent_resolve_calls() -> None:
    clock = _Clock()
    health = ControlHealth(failures=1, open_seconds=30.0, clock=clock)
    obs = health.begin("acquire")
    assert obs is not None
    health.failed(outage=True, observation=obs)
    clock.advance(31.0)
    assert health.state is ControlState.HALF_OPEN
    first_resolve = health.begin("resolve")
    assert first_resolve is not None
    assert health.begin("resolve") is None
    health.succeeded(first_resolve)
    acquire_probe = health.begin("acquire")
    assert acquire_probe is not None
    assert health.begin("acquire") is None
    health.succeeded(acquire_probe)
    assert health.state is ControlState.CLOSED
