"""验证正式模型策略和候选覆盖 API 的版本字段、兼容写入和安全错误映射。"""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import httpx
from account_pool.app import create_app
from account_pool.audit.models import ManagementAuditRecord
from account_pool.audit.repository import AuditLoadResult, AuditWriteSuccess
from account_pool.auth.actor import ActorAction
from account_pool.config import Settings
from account_pool.models import Strategy
from account_pool.routing.models import (
    RoutingCandidateMutation,
    RoutingCandidateOverride,
    RoutingFailure,
    RoutingFailureCode,
    RoutingOrderMutation,
    RoutingPolicyResult,
    RoutingPolicyState,
)
from account_pool.routing.service import RoutingPolicyService

_BINDING_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_ACTOR_SECRET: Final = "actor-signing-secret-with-at-least-32-bytes"


def _settings(actor_secret: str | None = None) -> Settings:
    return Settings(
        config_path=Path(__file__).resolve().parents[2] / "config" / "accounts.demo.yaml",
        store_mode="memory",
        redis_url="redis://unused",
        litellm_url="http://litellm.internal",
        litellm_admin_key=None,
        lease_ttl_seconds=60,
        internal_token="test-service-token",
        actor_secret=actor_secret,
    )


class FakeRoutingRepository:
    async def load(self, model: str) -> RoutingPolicyResult:
        return RoutingPolicyState(model=model, strategy=Strategy.PRIORITY, version=2)

    async def update_policy(
        self,
        model: str,
        strategy: Strategy,
        expected_version: int,
    ) -> RoutingPolicyResult:
        return RoutingPolicyState(model=model, strategy=strategy, version=expected_version + 1)

    async def update_candidate(
        self,
        model: str,
        binding_id: UUID,
        mutation: RoutingCandidateMutation,
    ) -> RoutingPolicyResult:
        return RoutingPolicyState(
            model=model,
            strategy=Strategy.PRIORITY,
            version=mutation.expected_version + 1,
            overrides=(
                RoutingCandidateOverride(
                    binding_id=binding_id,
                    manual_order=None,
                    weight=mutation.weight,
                    paused=mutation.paused,
                ),
            ),
        )

    async def update_order(
        self,
        model: str,
        mutation: RoutingOrderMutation,
    ) -> RoutingPolicyResult:
        return RoutingPolicyState(
            model=model,
            strategy=Strategy.PRIORITY,
            version=mutation.expected_version + 1,
            overrides=tuple(
                RoutingCandidateOverride(binding_id=binding_id, manual_order=index)
                for index, binding_id in enumerate(mutation.binding_ids)
            ),
        )

    async def delete_candidate(
        self,
        model: str,
        binding_id: UUID,
        expected_version: int,
    ) -> RoutingPolicyResult:
        return RoutingPolicyState(model=model, strategy=Strategy.PRIORITY, version=expected_version + 1)


class FailingRoutingRepository(FakeRoutingRepository):
    async def load(self, model: str) -> RoutingPolicyResult:
        return RoutingFailure(
            code=RoutingFailureCode.VERSION_CONFLICT,
            retryable=False,
            current_version=8,
        )


class NoopProjector:
    async def project(self) -> object:
        return object()


class NoopAuditRepository:
    async def append(self, record: ManagementAuditRecord) -> AuditWriteSuccess:
        return AuditWriteSuccess(status="created", record=record)

    async def load(self, event_id: UUID) -> AuditLoadResult:
        raise AssertionError("load is not used by the routing service")


async def test_routing_policy_and_candidate_apis_return_versions() -> None:
    service: Final = RoutingPolicyService(FakeRoutingRepository(), NoopProjector(), NoopAuditRepository())
    app: Final = create_app(settings=_settings(actor_secret=_ACTOR_SECRET), routing_policies=service)
    headers: Final = {"x-account-pool-token": "test-service-token"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://account-pool",
            headers=headers,
        ) as client:
            loaded: Final = await client.get("/api/models/gpt-4o/routing-policy")
            slash_model: Final = await client.get("/api/models/openai%2Fgpt-4o/routing-policy")
            updated: Final = await client.put(
                "/api/models/gpt-4o/routing-policy",
                json={"expected_version": 2, "strategy": "random"},
                headers={
                    **headers,
                    "x-account-pool-request-id": "routing-policy-update",
                    "x-account-pool-actor": _actor_token(ActorAction.ROUTING_POLICY_UPDATE, "routing-policy-update"),
                },
            )
            candidate: Final = await client.put(
                f"/api/models/openai%2Fgpt-4o/routing-candidates/{_BINDING_ID}",
                json={"expected_version": 3, "weight": 9, "paused": True},
                headers={
                    **headers,
                    "x-account-pool-request-id": "routing-candidate-update",
                    "x-account-pool-actor": _actor_token(ActorAction.ROUTING_CANDIDATE_UPDATE, "routing-candidate-update"),
                },
            )
            ordered: Final = await client.put(
                "/api/models/openai%2Fgpt-4o/routing-order",
                json={"expected_version": 4, "binding_ids": [str(_BINDING_ID)]},
                headers={
                    **headers,
                    "x-account-pool-request-id": "routing-order-update",
                    "x-account-pool-actor": _actor_token(ActorAction.ROUTING_ORDER_UPDATE, "routing-order-update"),
                },
            )
            deleted: Final = await client.request(
                "DELETE",
                f"/api/models/gpt-4o/routing-candidates/{_BINDING_ID}",
                json={"expected_version": 5},
                headers={
                    **headers,
                    "x-account-pool-request-id": "routing-candidate-delete",
                    "x-account-pool-actor": _actor_token(ActorAction.ROUTING_CANDIDATE_DELETE, "routing-candidate-delete"),
                },
            )
    assert (loaded.status_code, updated.status_code, candidate.status_code, ordered.status_code, deleted.status_code) == (
        200,
        200,
        200,
        200,
        200,
    )
    assert loaded.json()["version"] == 2
    assert slash_model.json()["model"] == "openai/gpt-4o"
    assert (updated.json()["strategy"], updated.json()["version"]) == ("random", 3)
    assert candidate.json()["overrides"][0] == {
        "binding_id": str(_BINDING_ID),
        "manual_order": None,
        "weight": 9,
        "paused": True,
    }
    assert candidate.json()["model"] == "openai/gpt-4o"
    assert ordered.json()["overrides"][0]["manual_order"] == 0
    assert ordered.json()["version"] == 5
    assert deleted.json()["overrides"] == []


async def test_routing_failure_maps_to_structured_conflict() -> None:
    service: Final = RoutingPolicyService(FailingRoutingRepository(), NoopProjector(), NoopAuditRepository())
    app: Final = create_app(settings=_settings(), routing_policies=service)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://account-pool",
            headers={"x-account-pool-token": "test-service-token"},
        ) as client:
            response: Final = await client.get("/api/models/gpt-4o/routing-policy")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "version_conflict",
        "retryable": False,
        "current_version": 8,
    }


def _actor_token(action: ActorAction, request_id: str) -> str:
    issued_at: Final = int(datetime.now(UTC).timestamp())
    header: Final = {"alg": "HS256", "typ": "JWT"}
    claims: Final = {
        "iss": "litellm-proxy",
        "aud": "account-pool",
        "sub": "admin-user",
        "role": "proxy_admin",
        "request_id": request_id,
        "action": action,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + 30,
        "jti": str(uuid4()),
    }
    encoded_header: Final = _encode_actor_segment(header)
    encoded_claims: Final = _encode_actor_segment(claims)
    signed: Final = f"{encoded_header}.{encoded_claims}"
    signature: Final = hmac.new(_ACTOR_SECRET.encode(), signed.encode("ascii"), hashlib.sha256).digest()
    encoded_signature: Final = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{signed}.{encoded_signature}"


def _encode_actor_segment(value: object) -> str:
    payload: Final = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
