"""封装渠道同步审计记录与后台对账事件的纯映射规则。"""

from __future__ import annotations

from typing import Final
from uuid import UUID, uuid5

from account_pool.audit.models import (
    AuditOutcome,
    ChannelCreateDetails,
    ChannelDeleteDetails,
    ChannelDeleteExternalDeploymentDetails,
    ChannelDetachDetails,
    ChannelImportDetails,
    ChannelReconcileDetails,
    ChannelUpdateDetails,
    ManagementAuditDetails,
    SafeAuditOutcome,
)
from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.operational.models import OperationalEventType
from account_pool.sync.contracts import ReconcilePassItem
from account_pool.sync.models import SyncAction, SyncOperation


def audit_details(
    operation: SyncOperation,
    actor_action: ActorAction,
    outcome: AuditOutcome,
    failure_code: str | None,
    external_binding_id: UUID | None,
) -> ManagementAuditDetails:
    safe_outcome: Final = SafeAuditOutcome(status=outcome, failure_code=failure_code)
    if actor_action == ActorAction.CHANNEL_RECONCILE:
        return ChannelReconcileDetails(outcome=safe_outcome)
    if operation.action == SyncAction.CREATE_CHANNEL:
        return ChannelCreateDetails(outcome=safe_outcome)
    if operation.action == SyncAction.UPDATE_CHANNEL:
        return ChannelUpdateDetails(outcome=safe_outcome)
    if operation.action == SyncAction.IMPORT_CHANNEL:
        return ChannelImportDetails(outcome=safe_outcome)
    if operation.action == SyncAction.DETACH_CHANNEL:
        return ChannelDetachDetails(outcome=safe_outcome)
    if operation.action == SyncAction.DELETE_CHANNEL:
        assert operation.delete_mode is not None
        return ChannelDeleteDetails(outcome=safe_outcome, delete_mode=operation.delete_mode)
    if operation.action == SyncAction.DELETE_EXTERNAL_DEPLOYMENT:
        assert external_binding_id is not None
        return ChannelDeleteExternalDeploymentDetails(outcome=safe_outcome, binding_id=external_binding_id)
    return ChannelReconcileDetails(outcome=safe_outcome)


def audit_event_id(
    operation_id: UUID,
    actor: ActorContext,
    outcome: AuditOutcome,
    failure_code: str | None,
) -> UUID:
    return uuid5(operation_id, f"{actor.action.value}:{actor.request_id}:{outcome.value}:{failure_code or 'none'}")


def system_reconcile_actor(operation: SyncOperation) -> ActorContext:
    request_id: Final = f"reconcile:{operation.operation_id.hex}:{operation.attempt_count + 1}"
    return ActorContext(
        user_id="account-pool-reconciler",
        role="system",
        actor_type="system",
        request_id=request_id,
        action=ActorAction.CHANNEL_RECONCILE,
        envelope_id=uuid5(operation.operation_id, request_id),
    )


def reconcile_event_outcome(item: ReconcilePassItem) -> tuple[OperationalEventType, str | None]:
    if item.status == "applied":
        return OperationalEventType.SYNC_RETRY_SUCCEEDED, None
    if item.status == "requires_key":
        return OperationalEventType.SYNC_RETRY_DEFERRED, "requires_key"
    if item.failure_code is not None:
        return OperationalEventType.SYNC_RETRY_FAILED, item.failure_code
    return OperationalEventType.SYNC_RETRY_DEFERRED, "operation_pending"
