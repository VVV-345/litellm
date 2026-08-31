"""验证模型调度策略服务的持久化、运行投影、身份校验和审计顺序。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from account_pool.audit.models import ManagementAuditRecord
from account_pool.audit.repository import (
    AuditLoadResult,
    AuditPersistenceFailure,
    AuditPersistenceFailureCode,
    AuditWriteResult,
    AuditWriteSuccess,
)
from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.models import Strategy
from account_pool.routing.models import (
    RoutingCandidateMutation,
    RoutingFailure,
    RoutingFailureCode,
    RoutingOrderMutation,
    RoutingPolicyMutation,
    RoutingPolicyResult,
    RoutingPolicyState,
    RoutingVersionMutation,
)
from account_pool.routing.service import RoutingPolicyService


class FakeRepository:
    def __init__(self, result: RoutingPolicyResult) -> None:
        self.result: Final = result

    async def load(self, model: str) -> RoutingPolicyResult:
        return self.result

    async def update_policy(
        self,
        model: str,
        strategy: Strategy,
        expected_version: int,
    ) -> RoutingPolicyResult:
        return self.result

    async def update_candidate(
        self,
        model: str,
        binding_id: UUID,
        mutation: RoutingCandidateMutation,
    ) -> RoutingPolicyResult:
        return self.result

    async def update_order(
        self,
        model: str,
        mutation: RoutingOrderMutation,
    ) -> RoutingPolicyResult:
        return self.result

    async def delete_candidate(
        self,
        model: str,
        binding_id: UUID,
        expected_version: int,
    ) -> RoutingPolicyResult:
        return self.result


class FakeProjector:
    def __init__(self) -> None:
        self.calls = 0

    async def project(self) -> object:
        self.calls += 1
        return object()


class FakeAuditRepository:
    def __init__(self) -> None:
        self.records: tuple[ManagementAuditRecord, ...] = ()

    async def append(self, record: ManagementAuditRecord) -> AuditWriteResult:
        self.records = (*self.records, record)
        return AuditWriteSuccess(status="created", record=record)

    async def load(self, event_id: UUID) -> AuditLoadResult:
        raise AssertionError("load is not used by the routing service")


class FailingAuditRepository(FakeAuditRepository):
    async def append(self, record: ManagementAuditRecord) -> AuditWriteResult:
        return AuditPersistenceFailure(
            code=AuditPersistenceFailureCode.DATABASE_UNAVAILABLE,
            retryable=True,
        )


class FailingProjector(FakeProjector):
    async def project(self) -> object:
        self.calls += 1
        raise RuntimeError("projection failed")


def _actor(action: ActorAction) -> ActorContext:
    return ActorContext(
        user_id="admin-user",
        role="proxy_admin",
        request_id="request-routing",
        action=action,
        envelope_id=UUID("20000000-0000-0000-0000-000000000002"),
    )


async def test_successful_mutations_refresh_runtime_projection() -> None:
    state: Final = RoutingPolicyState(model="model-a", strategy=Strategy.PRIORITY, version=4)
    projector: Final = FakeProjector()
    audit: Final = FakeAuditRepository()
    service: Final = RoutingPolicyService(
        FakeRepository(state),
        projector,
        audit,
        clock=lambda: datetime(2026, 8, 21, tzinfo=UTC),
    )
    binding_id: Final = UUID("10000000-0000-0000-0000-000000000001")

    policy: Final = await service.update_policy(
        "model-a",
        RoutingPolicyMutation(expected_version=3, strategy=Strategy.PRIORITY),
        _actor(ActorAction.ROUTING_POLICY_UPDATE),
    )
    candidate: Final = await service.update_candidate(
        "model-a",
        binding_id,
        RoutingCandidateMutation(expected_version=4, weight=10),
        _actor(ActorAction.ROUTING_CANDIDATE_UPDATE),
    )
    ordered: Final = await service.update_order(
        "model-a",
        RoutingOrderMutation(expected_version=5, binding_ids=(binding_id,)),
        _actor(ActorAction.ROUTING_ORDER_UPDATE),
    )
    deleted: Final = await service.delete_candidate(
        "model-a",
        binding_id,
        RoutingVersionMutation(expected_version=6),
        _actor(ActorAction.ROUTING_CANDIDATE_DELETE),
    )

    assert (policy, candidate, ordered, deleted) == (state, state, state, state)
    assert projector.calls == 4
    assert tuple(record.event.event_type.value for record in audit.records) == (
        "routing_policy_update",
        "routing_candidate_update",
        "routing_order_update",
        "routing_candidate_delete",
    )
    assert all(record.event.model_id == "model-a" for record in audit.records)


async def test_failed_mutation_does_not_refresh_runtime_projection() -> None:
    failure: Final = RoutingFailure(
        code=RoutingFailureCode.VERSION_CONFLICT,
        retryable=False,
        current_version=7,
    )
    projector: Final = FakeProjector()
    audit: Final = FakeAuditRepository()
    service: Final = RoutingPolicyService(FakeRepository(failure), projector, audit)

    result: Final = await service.update_policy(
        "model-a",
        RoutingPolicyMutation(expected_version=6, strategy=Strategy.RANDOM),
        _actor(ActorAction.ROUTING_POLICY_UPDATE),
    )

    assert result == failure
    assert projector.calls == 0
    assert audit.records[0].event.reason_code is None
    assert audit.records[0].event.safe_details.outcome.failure_code == "version_conflict"


async def test_wrong_actor_action_is_rejected_without_writes_or_audit() -> None:
    state: Final = RoutingPolicyState(model="model-a", strategy=Strategy.PRIORITY, version=4)
    projector: Final = FakeProjector()
    audit: Final = FakeAuditRepository()
    service: Final = RoutingPolicyService(FakeRepository(state), projector, audit)

    result: Final = await service.update_policy(
        "model-a",
        RoutingPolicyMutation(expected_version=3, strategy=Strategy.PRIORITY),
        _actor(ActorAction.ROUTING_CANDIDATE_UPDATE),
    )

    assert isinstance(result, RoutingFailure)
    assert result.code == RoutingFailureCode.INVALID_ACTOR
    assert projector.calls == 0
    assert audit.records == ()


async def test_projection_failure_is_audited_with_persisted_version() -> None:
    state: Final = RoutingPolicyState(model="model-a", strategy=Strategy.PRIORITY, version=4)
    projector: Final = FailingProjector()
    audit: Final = FakeAuditRepository()
    service: Final = RoutingPolicyService(FakeRepository(state), projector, audit)

    result: Final = await service.update_policy(
        "model-a",
        RoutingPolicyMutation(expected_version=3, strategy=Strategy.PRIORITY),
        _actor(ActorAction.ROUTING_POLICY_UPDATE),
    )

    assert isinstance(result, RoutingFailure)
    assert result.code == RoutingFailureCode.RUNTIME_PROJECTION_FAILED
    assert result.current_version == 4
    assert audit.records[0].event.safe_details.outcome.failure_code == "runtime_projection_failed"


async def test_audit_failure_reports_persisted_version() -> None:
    state: Final = RoutingPolicyState(model="model-a", strategy=Strategy.PRIORITY, version=4)
    service: Final = RoutingPolicyService(
        FakeRepository(state),
        FakeProjector(),
        FailingAuditRepository(),
    )

    result: Final = await service.update_policy(
        "model-a",
        RoutingPolicyMutation(expected_version=3, strategy=Strategy.PRIORITY),
        _actor(ActorAction.ROUTING_POLICY_UPDATE),
    )

    assert isinstance(result, RoutingFailure)
    assert result.code == RoutingFailureCode.AUDIT_UNAVAILABLE
    assert result.current_version == 4
