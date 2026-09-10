"""本文件验证卡片网关的鉴权隔离、重试边界、流式转发和租约释放。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Final
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from litellm.proxy.management_endpoints.account_pool_gateway import AccountPoolGatewayMiddleware
from litellm.proxy.management_endpoints.account_pool_gateway_client import ControlError
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import (
    AcquireRequest,
    Candidate,
    FinishRequest,
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

_KEY: Final = "cpk_" + "test-only-" * 5


class Control:
    def __init__(self, resolution: Resolution) -> None:
        self.resolution: Final = resolution
        self.acquisitions: list[AcquireRequest] = []
        self.finished: list[FinishRequest] = []
        self.revoked = False
        self.exhausted = False
        self.unavailable_account_ids: frozenset[UUID] = frozenset()

    async def resolve(self, request: ResolveRequest) -> Resolution:
        if request.card_key != _KEY:
            raise ControlError(401)
        return self.resolution

    async def acquire(self, request: AcquireRequest) -> Lease | None:
        self.acquisitions.append(request)
        if self.revoked:
            raise ControlError(401)
        if self.exhausted:
            return None
        if request.account_id in self.unavailable_account_ids:
            return None
        return Lease(lease_id=uuid4(), card_id=self.resolution.card_id, key_id=self.resolution.key_id,
                     account_id=request.account_id, request_id=request.request_id, model=request.model,
                     channel="cliproxyapi", supplier="openai_codex", started_at=datetime.now(timezone.utc),
                     attempt=request.attempt)

    async def finish(self, request: FinishRequest) -> None:
        self.finished.append(request)


def candidate(identifier: UUID, priority: int = 0) -> Candidate:
    return Candidate(id=identifier, channel="cliproxyapi", supplier="openai_codex", environment_version=2,
                     policy_version=1, enabled_models=("model-a",), api_base=f"http://cliproxy-{identifier.hex}:8317/v1",
                     api_key="internal-secret", concurrency_limit=1,
                     policy=AccountPolicy(routing=RoutingPolicy(priority=priority)))


def setup_gateway(handler: Callable[[httpx.Request], httpx.Response], *, retry: bool = False):
    card: Final = uuid4()
    policy: Final = AccountPolicy(model_aliases=(ModelAlias(alias="public-model", target="model-a"),),
                                 routing=RoutingPolicy(fallback_enabled=retry, max_attempts=2, backoff_ms=0))
    control: Final = Control(Resolution(card_id=card, key_id=uuid4(), card_version=1, policy_version=1,
                                       policy=policy, candidates=(candidate(card, 10), candidate(uuid4()))))
    app: Final = FastAPI()

    @app.post("/v1/chat/completions")
    async def ordinary() -> dict[str, str]:
        return {"route": "ordinary"}

    app.add_middleware(AccountPoolGatewayMiddleware, control_factory=lambda _: control,
                       client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return TestClient(app), control


def test_card_key_forwards_only_to_bound_target_with_internal_credentials() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "Bearer internal-secret"
        assert "x-api-key" not in request.headers and "cookie" not in request.headers
        assert "x-account-pool-card-id" not in request.headers
        assert json.loads(request.content)["model"] == "model-a"
        return httpx.Response(200, json={"model": "model-a", "choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}})

    client, control = setup_gateway(upstream)
    with client:
        response: Final = client.post("/v1/chat/completions", json={"model": "public-model", "messages": []},
                                      headers={"Authorization": f"Bearer {_KEY}", "Cookie": "token=private",
                                               "x-api-key": "downstream-key", "x-account-pool-card-id": str(uuid4())})
    assert response.status_code == 200 and response.json()["model"] == "public-model"
    assert seen[0].url.host == f"cliproxy-{control.resolution.card_id.hex}"
    assert len(control.finished) == 1 and control.finished[0].input_tokens == 4
    assert _KEY not in response.text and "internal-secret" not in response.text


def test_models_and_management_scope_are_separate_from_ordinary_keys() -> None:
    client, _ = setup_gateway(lambda _: pytest.fail("No upstream request expected"))
    with client:
        assert client.get("/v1/models", headers={"Authorization": f"Bearer {_KEY}"}).json()["data"][1]["id"] == "public-model"
        assert client.get("/account_pool/environments", headers={"Authorization": f"Bearer {_KEY}"}).status_code == 403
        assert client.post("/v1/chat/completions", json={"model": "model-a"}, headers={"Authorization": "Bearer ordinary"}).json() == {"route": "ordinary"}
        assert client.get("/v1/models", headers={"Authorization": "Bearer cpk_invalid"}).status_code == 401


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
    assert response.status_code == 200
    assert seen == [f"cliproxy-{control.resolution.candidates[1].id.hex}"]


def test_upstream_redirect_is_reported_as_gateway_failure() -> None:
    client, control = setup_gateway(lambda _: httpx.Response(302, headers={"location": "https://example.test"}))
    with client:
        response: Final = client.post(
            "/v1/responses", json={"model": "model-a"}, headers={"Authorization": f"Bearer {_KEY}"}
        )
    assert response.status_code == 502
    assert control.finished[0].http_status == 502


@pytest.mark.parametrize("revoked,exhausted,expected", [(True, False, 401), (False, True, 503)])
def test_revocation_and_concurrency_are_checked_before_forwarding(revoked: bool, exhausted: bool, expected: int) -> None:
    client, control = setup_gateway(lambda _: pytest.fail("No upstream request expected"))
    control.revoked = revoked
    control.exhausted = exhausted
    with client:
        response: Final = client.post("/v1/responses", json={"model": "model-a"},
                                      headers={"Authorization": f"Bearer {_KEY}"})
    assert response.status_code == expected


def test_retry_records_one_request_chain_and_uses_next_bound_account() -> None:
    calls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message": "Bearer private"}})
        return httpx.Response(200, json={"output": []})

    client, control = setup_gateway(upstream, retry=True)
    with client:
        response: Final = client.post("/v1/responses", json={"model": "model-a"},
                                      headers={"Authorization": f"Bearer {_KEY}"})
    assert response.status_code == 200 and len(set(calls)) == 2
    assert len({request.request_id for request in control.acquisitions}) == 1
    assert control.finished[0].next_account_id == control.acquisitions[1].account_id
    assert control.finished[0].retryable and control.finished[1].http_status == 200
    assert [request.attempt for request in control.acquisitions] == [1, 2]
    assert "private" not in str(control.finished)


@pytest.mark.parametrize("previous_response_id,retry", [(None, False), ("upstream-response", True)])
def test_failover_requires_permission_and_does_not_replay_stateful_requests(previous_response_id, retry) -> None:
    client, control = setup_gateway(lambda _: httpx.Response(503, json={"error": {"code": "unavailable"}}), retry=retry)
    with client:
        response: Final = client.post("/v1/responses", json={"model": "model-a", "previous_response_id": previous_response_id},
                                      headers={"Authorization": f"Bearer {_KEY}"})
    assert response.status_code == 503 and len(control.acquisitions) == 1
    assert not control.finished[0].retryable


def test_read_timeout_is_not_retried_even_with_fallback_enabled() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret upstream response", request=request)

    client, control = setup_gateway(upstream, retry=True)
    with client:
        response: Final = client.post("/v1/responses", json={"model": "model-a"},
                                      headers={"Authorization": f"Bearer {_KEY}"})
    assert response.status_code == 504 and len(control.acquisitions) == 1
    assert control.finished[0].http_status == 504 and not control.finished[0].retryable


def test_stream_keeps_sse_and_releases_lease_after_consumption() -> None:
    stream: Final = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'
    client, control = setup_gateway(lambda _: httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"}))
    with client:
        response: Final = client.post("/v1/chat/completions", json={"model": "model-a", "stream": True},
                                      headers={"Authorization": f"Bearer {_KEY}"})
    assert response.status_code == 200 and response.text == stream
    assert control.finished[0].http_status == 200


def test_concurrency_falls_through_to_next_candidate_without_consuming_retry_budget() -> None:
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, json={"output": []})

    client, control = setup_gateway(upstream)
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


def test_truncated_stream_is_logged_as_failure_and_never_replayed() -> None:
    partial: Final = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    client, control = setup_gateway(
        lambda _: httpx.Response(200, content=partial, headers={"content-type": "text/event-stream"}), retry=True
    )
    with client:
        response: Final = client.post(
            "/v1/chat/completions", json={"model": "model-a", "stream": True},
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
            policy=AccountPolicy(routing=RoutingPolicy(session_affinity=True)),
            candidates=(candidate(card, 10), backup),
            sticky_account_id=backup.id,
        )
    )
    app: Final = FastAPI()
    app.add_middleware(
        AccountPoolGatewayMiddleware,
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
