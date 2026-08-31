"""执行单个渠道同步操作，并协调目录、运行态和审计的提交顺序。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import SecretStr

from account_pool.audit.models import AuditOutcome, build_management_audit_record
from account_pool.audit.repository import AuditPersistenceFailure, ManagementAuditRepository
from account_pool.auth.actor import ActorContext
from account_pool.catalog.lifecycle import CatalogApplyFailure, ExternalSyncSuccess
from account_pool.catalog.service import CatalogService
from account_pool.runtime_projection import RuntimeProjector
from account_pool.sync.audit import audit_details, audit_event_id
from account_pool.sync.contracts import (
    ChannelManagementFailure,
    ChannelManagementResult,
    DeploymentSynchronizer,
)
from account_pool.sync.desired_state import catalog_command
from account_pool.sync.external import retry_requires_key, synchronize_operation
from account_pool.sync.models import SafeSyncFailure, SyncAction, SyncOperation, SyncStatus
from account_pool.sync.repository import (
    SyncOperationLoadSuccess,
    SyncOperationPersistenceFailure,
    SyncOperationRepository,
    SyncOperationWriteSuccess,
)
from account_pool.sync.results import management_failure, operation_view, persistence_failure

Clock = Callable[[], datetime]


class ChannelOperationExecutor:
    """将一次同步的外部副作用限制在固定且可恢复的执行顺序中。"""

    def __init__(
        self,
        *,
        catalog: CatalogService,
        operations: SyncOperationRepository,
        synchronizer: DeploymentSynchronizer,
        runtime_projector: RuntimeProjector,
        audit: ManagementAuditRepository,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._catalog: Final = catalog
        self._operations: Final = operations
        self._synchronizer: Final = synchronizer
        self._runtime_projector: Final = runtime_projector
        self._audit: Final = audit
        self._clock: Final = clock

    async def resume_applied(
        self,
        operation: SyncOperation,
        actor: ActorContext,
        response_status: Literal["accepted", "existing"],
        external_binding_id: UUID | None,
    ) -> ChannelManagementResult:
        try:
            await self._runtime_projector.project()
        except Exception:
            projection_audit_failure: Final = await self._write_audit(
                operation,
                actor,
                AuditOutcome.FAILED,
                "runtime_projection_failed",
                external_binding_id,
            )
            return projection_audit_failure or management_failure("runtime_projection_failed", retryable=True)
        success_audit_failure: Final = await self._write_audit(
            operation,
            actor,
            AuditOutcome.SUCCEEDED,
            None,
            external_binding_id,
        )
        return success_audit_failure or operation_view(operation, response_status)

    async def execute(
        self,
        operation: SyncOperation,
        api_key: SecretStr | None,
        actor: ActorContext,
        response_status: Literal["accepted", "existing"],
        external_binding_id: UUID | None = None,
    ) -> ChannelManagementResult:
        if operation.action in {SyncAction.DETACH_CHANNEL, SyncAction.DELETE_CHANNEL}:
            waiting: Final = await self._prepare_removal(operation, actor, response_status)
            if waiting is not None:
                return waiting
        attempted: Final = await self._operations.record_attempt(operation.operation_id, self._clock())
        if isinstance(attempted, SyncOperationPersistenceFailure):
            return persistence_failure(attempted)
        active: Final = attempted.operation
        target_binding_id: Final = external_binding_id or active.desired.target_binding_id
        external_failure: Final = await synchronize_operation(
            self._synchronizer,
            active,
            api_key,
            target_binding_id,
        )
        if external_failure is not None:
            return await self._record_failure(
                active,
                external_failure,
                retry_requires_key(active, api_key),
                actor,
                response_status,
            )
        command: Final = catalog_command(active, target_binding_id, self._clock())
        applied: Final = await self._catalog.apply_lifecycle(
            command,
            ExternalSyncSuccess(operation_id=active.operation_id),
        )
        if isinstance(applied, CatalogApplyFailure):
            apply_failure: Final = SafeSyncFailure(code=applied.code.value, message="Catalog apply failed")
            return await self._record_failure(active, apply_failure, False, actor, response_status)
        try:
            await self._runtime_projector.project()
        except Exception:
            projection_audit_failure: Final = await self._write_audit(
                active,
                actor,
                AuditOutcome.FAILED,
                "runtime_projection_failed",
                target_binding_id,
            )
            return projection_audit_failure or management_failure("runtime_projection_failed", retryable=True)
        success_audit_failure: Final = await self._write_audit(
            active,
            actor,
            AuditOutcome.SUCCEEDED,
            None,
            target_binding_id,
        )
        if success_audit_failure is not None:
            return success_audit_failure
        loaded: Final = await self._operations.load(active.operation_id)
        if isinstance(loaded, SyncOperationLoadSuccess):
            return operation_view(loaded.operation, response_status)
        return operation_view(
            active.model_copy(update={"status": SyncStatus.APPLIED, "applied_at": self._clock()}), response_status
        )

    async def _prepare_removal(
        self,
        operation: SyncOperation,
        actor: ActorContext,
        response_status: Literal["accepted", "existing"],
    ) -> ChannelManagementResult | None:
        # 先投影 pending_delete，避免新请求在删除等待阶段继续进入该渠道。
        pending: Final = await self._catalog.mark_pending_delete(operation.operation_id, operation.channel_id)
        if isinstance(pending, CatalogApplyFailure):
            failure: Final = SafeSyncFailure(code=pending.code.value, message="Pending delete apply failed")
            return await self._record_failure(operation, failure, False, actor, response_status)
        try:
            await self._runtime_projector.project()
        except Exception:
            projection_audit_failure: Final = await self._write_audit(
                operation,
                actor,
                AuditOutcome.FAILED,
                "runtime_projection_failed",
                None,
            )
            return projection_audit_failure or management_failure("runtime_projection_failed", retryable=True)
        account_id: Final = operation.desired.legacy_account_id or str(operation.channel_id)
        if await self._runtime_projector.inflight(account_id) == 0:
            return None
        waiting_audit_failure: Final = await self._write_audit(
            operation,
            actor,
            AuditOutcome.ACCEPTED,
            None,
            None,
        )
        return waiting_audit_failure or operation_view(operation, response_status)

    async def _record_failure(
        self,
        operation: SyncOperation,
        failure: SafeSyncFailure,
        requires_key: bool,
        actor: ActorContext,
        response_status: Literal["accepted", "existing"],
    ) -> ChannelManagementResult:
        updated: Final = await self._operations.mark_failed(
            operation.operation_id,
            failure,
            requires_key,
            self._clock(),
        )
        audit_failure: Final = await self._write_audit(
            operation,
            actor,
            AuditOutcome.FAILED,
            failure.code,
            None,
        )
        if audit_failure is not None:
            return audit_failure
        if isinstance(updated, SyncOperationWriteSuccess):
            return operation_view(updated.operation, response_status)
        return persistence_failure(updated)

    async def _write_audit(
        self,
        operation: SyncOperation,
        actor: ActorContext,
        outcome: AuditOutcome,
        failure_code: str | None,
        external_binding_id: UUID | None,
    ) -> ChannelManagementFailure | None:
        result: Final = await self._audit.append(
            build_management_audit_record(
                event_id=audit_event_id(operation.operation_id, actor, outcome, failure_code),
                occurred_at=self._clock(),
                actor=actor,
                operation_id=operation.operation_id,
                channel_id=operation.channel_id,
                details=audit_details(
                    operation,
                    actor.action,
                    outcome,
                    failure_code,
                    external_binding_id,
                ),
            )
        )
        if isinstance(result, AuditPersistenceFailure):
            return management_failure(f"audit_{result.code.value}", result.retryable)
        return None
