"""编排模型策略写入、运行配置刷新和脱敏管理审计。"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Protocol
from uuid import UUID, uuid5

from account_pool.audit.models import (
    AuditOutcome,
    ManagementAuditDetails,
    RoutingCandidateDeleteDetails,
    RoutingCandidateUpdateDetails,
    RoutingOrderUpdateDetails,
    RoutingPolicyUpdateDetails,
    SafeAuditOutcome,
    build_management_audit_record,
)
from account_pool.audit.repository import AuditPersistenceFailure, ManagementAuditRepository
from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.routing.models import (
    RoutingCandidateMutation,
    RoutingFailure,
    RoutingFailureCode,
    RoutingOrderMutation,
    RoutingPolicyMutation,
    RoutingPolicyResult,
    RoutingVersionMutation,
)
from account_pool.routing.repository import RoutingPolicyRepository

Clock = Callable[[], datetime]
_ROUTING_AUDIT_NAMESPACE: Final = UUID("8b20fc4b-3bfa-49e7-a6c7-81d0ac7c2755")


class RoutingRuntimeProjector(Protocol):
    async def project(self) -> object: ...


class RoutingPolicyService:
    def __init__(
        self,
        repository: RoutingPolicyRepository,
        projector: RoutingRuntimeProjector,
        audit: ManagementAuditRepository,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._repository: Final = repository
        self._projector: Final = projector
        self._audit: Final = audit
        self._clock: Final = clock

    async def read(self, model: str) -> RoutingPolicyResult:
        return await self._repository.load(model)

    async def update_policy(
        self,
        model: str,
        mutation: RoutingPolicyMutation,
        actor: ActorContext,
    ) -> RoutingPolicyResult:
        if actor.action != ActorAction.ROUTING_POLICY_UPDATE:
            return _failure(RoutingFailureCode.INVALID_ACTOR, retryable=False)
        result: Final = await self._repository.update_policy(
            model=model,
            strategy=mutation.strategy,
            expected_version=mutation.expected_version,
        )
        return await self._complete(
            model=model,
            actor=actor,
            result=result,
            details=lambda outcome, version: RoutingPolicyUpdateDetails(
                outcome=outcome,
                expected_version=mutation.expected_version,
                resulting_version=version,
            ),
        )

    async def update_candidate(
        self,
        model: str,
        binding_id: UUID,
        mutation: RoutingCandidateMutation,
        actor: ActorContext,
    ) -> RoutingPolicyResult:
        if actor.action != ActorAction.ROUTING_CANDIDATE_UPDATE:
            return _failure(RoutingFailureCode.INVALID_ACTOR, retryable=False)
        result: Final = await self._repository.update_candidate(model, binding_id, mutation)
        return await self._complete(
            model=model,
            actor=actor,
            result=result,
            details=lambda outcome, version: RoutingCandidateUpdateDetails(
                outcome=outcome,
                binding_id=binding_id,
                expected_version=mutation.expected_version,
                resulting_version=version,
            ),
        )

    async def update_order(
        self,
        model: str,
        mutation: RoutingOrderMutation,
        actor: ActorContext,
    ) -> RoutingPolicyResult:
        if actor.action != ActorAction.ROUTING_ORDER_UPDATE:
            return _failure(RoutingFailureCode.INVALID_ACTOR, retryable=False)
        result: Final = await self._repository.update_order(model, mutation)
        return await self._complete(
            model=model,
            actor=actor,
            result=result,
            details=lambda outcome, version: RoutingOrderUpdateDetails(
                outcome=outcome,
                binding_count=len(mutation.binding_ids),
                expected_version=mutation.expected_version,
                resulting_version=version,
            ),
        )

    async def delete_candidate(
        self,
        model: str,
        binding_id: UUID,
        mutation: RoutingVersionMutation,
        actor: ActorContext,
    ) -> RoutingPolicyResult:
        if actor.action != ActorAction.ROUTING_CANDIDATE_DELETE:
            return _failure(RoutingFailureCode.INVALID_ACTOR, retryable=False)
        result: Final = await self._repository.delete_candidate(model, binding_id, mutation.expected_version)
        return await self._complete(
            model=model,
            actor=actor,
            result=result,
            details=lambda outcome, version: RoutingCandidateDeleteDetails(
                outcome=outcome,
                binding_id=binding_id,
                expected_version=mutation.expected_version,
                resulting_version=version,
            ),
        )

    async def _complete(
        self,
        *,
        model: str,
        actor: ActorContext,
        result: RoutingPolicyResult,
        details: Callable[[SafeAuditOutcome, int | None], ManagementAuditDetails],
    ) -> RoutingPolicyResult:
        if isinstance(result, RoutingFailure):
            return await self._audited(model, actor, result, details)
        try:
            await self._projector.project()
        except Exception:
            projection_failure: Final = _failure(
                RoutingFailureCode.RUNTIME_PROJECTION_FAILED,
                retryable=True,
                current_version=result.version,
            )
            return await self._audited(model, actor, projection_failure, details, resulting_version=result.version)
        return await self._audited(model, actor, result, details)

    async def _audited(
        self,
        model: str,
        actor: ActorContext,
        result: RoutingPolicyResult,
        details: Callable[[SafeAuditOutcome, int | None], ManagementAuditDetails],
        resulting_version: int | None = None,
    ) -> RoutingPolicyResult:
        failure_code: Final = result.code.value if isinstance(result, RoutingFailure) else None
        outcome: Final = SafeAuditOutcome(
            status=AuditOutcome.FAILED if failure_code is not None else AuditOutcome.SUCCEEDED,
            failure_code=failure_code,
        )
        version: Final = (
            result.version
            if not isinstance(result, RoutingFailure)
            else resulting_version if resulting_version is not None else result.current_version
        )
        audit_result: Final = await self._audit.append(
            build_management_audit_record(
                event_id=_audit_event_id(actor, model, outcome),
                occurred_at=self._clock(),
                actor=actor,
                model_id=model,
                details=details(outcome, version),
            )
        )
        if isinstance(audit_result, AuditPersistenceFailure):
            return _failure(
                RoutingFailureCode.AUDIT_UNAVAILABLE,
                retryable=audit_result.retryable,
                current_version=version,
            )
        return result


def _audit_event_id(actor: ActorContext, model: str, outcome: SafeAuditOutcome) -> UUID:
    identity: Final = f"{actor.envelope_id}:{actor.action.value}:{model}:{outcome.status.value}:{outcome.failure_code or 'none'}"
    return uuid5(_ROUTING_AUDIT_NAMESPACE, identity)


def _failure(
    code: RoutingFailureCode,
    retryable: bool,
    current_version: int | None = None,
) -> RoutingFailure:
    return RoutingFailure(code=code, retryable=retryable, current_version=current_version)
