"""编排渠道期望状态持久化、LiteLLM 同步、目录应用、运行投影和管理审计。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Final, Literal, Protocol
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, SecretStr, field_validator

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
    build_management_audit_record,
)
from account_pool.audit.repository import AuditPersistenceFailure, ManagementAuditRepository
from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.catalog.lifecycle import (
    ApplyChannelCreate,
    ApplyChannelDelete,
    ApplyChannelDetach,
    ApplyChannelImport,
    ApplyChannelUpdate,
    ApplyExternalBindingDelete,
    CatalogApplyFailure,
    CatalogLifecycleCommand,
    ExternalSyncSuccess,
)
from account_pool.catalog.lifecycle import (
    DeleteMode as CatalogDeleteMode,
)
from account_pool.catalog.models import (
    AdministrativeState,
    BindingOwnership,
    CatalogSnapshot,
    ChannelRecord,
    DeploymentBindingRecord,
)
from account_pool.catalog.repository import CatalogRepository
from account_pool.catalog.service import CatalogService
from account_pool.models import AccountId, ChannelPriority, FrozenModel, QuotaConfig
from account_pool.operational.models import OperationalEventType, build_sync_reconcile_record
from account_pool.operational.repository import OperationalEventRepository
from account_pool.runtime_projection import RuntimeProjector
from account_pool.sync.litellm import (
    LiteLLMSyncAction,
    LiteLLMSyncFailure,
    LiteLLMSyncResult,
    LiteLLMSyncSuccess,
    ManagedDeploymentListResult,
    ManagedDeploymentListSuccess,
    ManagedDeploymentMarker,
)
from account_pool.sync.models import (
    ChannelDesiredState,
    DeleteMode,
    DesiredBinding,
    ExternalDeploymentDelete,
    SafeSyncFailure,
    SyncAction,
    SyncOperation,
    SyncStatus,
)
from account_pool.sync.repository import (
    SyncOperationLoadSuccess,
    SyncOperationPersistenceFailure,
    SyncOperationRepository,
    SyncOperationWriteSuccess,
)

Clock = Callable[[], datetime]
_IDEMPOTENCY_NAMESPACE: Final = UUID("4b5bf3b9-3992-4bd7-b475-3f4b5bdd955d")


class DeploymentSynchronizer(Protocol):
    async def create_deployment(
        self,
        operation: SyncOperation,
        binding: DesiredBinding,
        api_base: str,
        api_key: SecretStr,
    ) -> LiteLLMSyncResult: ...

    async def update_deployment(
        self,
        operation: SyncOperation,
        binding: DesiredBinding,
        api_base: str,
        api_key: SecretStr | None = None,
    ) -> LiteLLMSyncResult: ...

    async def delete_managed_deployment(self, binding: DesiredBinding) -> LiteLLMSyncResult: ...

    async def delete_external_deployment(self, deletion: ExternalDeploymentDelete) -> LiteLLMSyncResult: ...

    async def list_managed_deployments(self) -> ManagedDeploymentListResult: ...


class ChannelBindingMutation(FrozenModel):
    binding_id: UUID | None = None
    public_model: str = Field(min_length=1)
    provider_model: str | None = None
    litellm_deployment_id: str | None = None
    ownership: BindingOwnership
    enabled: bool = True


class ChannelMutation(FrozenModel):
    legacy_account_id: AccountId | None = None
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_discovery_provider_id: str | None = Field(default=None, min_length=1)
    group: str | None = None
    base_url_display: str = Field(min_length=1)
    administrative_state: AdministrativeState = AdministrativeState.ENABLED
    max_concurrency: int = Field(default=1, ge=1)
    priority: ChannelPriority = ChannelPriority.MEDIUM
    weight: int = Field(default=1, ge=1, le=100)
    quotas: QuotaConfig = QuotaConfig()
    api_key: SecretStr | None = None
    bindings: tuple[ChannelBindingMutation, ...] = Field(min_length=1)

    @field_validator("administrative_state")
    @classmethod
    def reject_internal_pending_delete_state(cls, value: AdministrativeState) -> AdministrativeState:
        if value == AdministrativeState.PENDING_DELETE:
            raise ValueError("pending_delete is managed by the lifecycle service")
        return value


class ChannelDeleteRequest(FrozenModel):
    delete_mode: DeleteMode


class ExternalDeploymentDeleteRequest(FrozenModel):
    confirmed: Literal[True]


class ChannelReconcileRequest(FrozenModel):
    api_key: SecretStr | None = None


class ChannelDetail(FrozenModel):
    channel_id: UUID
    display_name: str
    provider: str
    model_discovery_provider_id: str | None = None
    parser_provider_id: str | None = None
    group: str | None
    base_url_display: str
    administrative_state: AdministrativeState
    max_concurrency: int
    priority: ChannelPriority
    weight: int
    quotas: QuotaConfig
    key_mask: str | None
    bindings: tuple[ChannelBindingMutation, ...]


class ChannelOperationView(FrozenModel):
    status: Literal["accepted", "existing"]
    operation_id: UUID
    channel_id: UUID
    operation_status: SyncStatus
    requires_key: bool
    failure: SafeSyncFailure | None


class ChannelManagementFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: str
    retryable: bool


ChannelManagementResult = ChannelOperationView | ChannelManagementFailure


class ReconcilePassItem(FrozenModel):
    operation_id: UUID
    channel_id: UUID
    status: Literal["applied", "failed", "requires_key"]
    failure_code: str | None = None


class ReconcilePassResult(FrozenModel):
    inspected: int = Field(ge=0)
    items: tuple[ReconcilePassItem, ...]
    orphan_deployments: tuple[ManagedDeploymentMarker, ...] = ()
    orphan_scan_failure_code: str | None = None


class ChannelManager(Protocol):
    async def detail(self, channel_id: UUID) -> ChannelDetail | ChannelManagementFailure: ...

    async def detail_by_legacy_account(self, account_id: AccountId) -> ChannelDetail | ChannelManagementFailure: ...

    async def operation(self, operation_id: UUID) -> ChannelOperationView | ChannelManagementFailure: ...

    async def create(
        self,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def import_channel(
        self,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def update(
        self,
        channel_id: UUID,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def detach(
        self,
        channel_id: UUID,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def delete(
        self,
        channel_id: UUID,
        request: ChannelDeleteRequest,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def delete_external(
        self,
        channel_id: UUID,
        binding_id: UUID,
        request: ExternalDeploymentDeleteRequest,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def reconcile(
        self,
        channel_id: UUID,
        request: ChannelReconcileRequest,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def reconcile_pending(self, limit: int = 100) -> ReconcilePassResult | ChannelManagementFailure: ...


class ChannelManagementService:
    def __init__(
        self,
        *,
        catalog_repository: CatalogRepository,
        operations: SyncOperationRepository,
        synchronizer: DeploymentSynchronizer,
        runtime_projector: RuntimeProjector,
        audit: ManagementAuditRepository,
        operational_events: OperationalEventRepository,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._catalog_repository: Final = catalog_repository
        self._catalog: Final = CatalogService(catalog_repository)
        self._operations: Final = operations
        self._synchronizer: Final = synchronizer
        self._runtime_projector: Final = runtime_projector
        self._audit: Final = audit
        self._operational_events: Final = operational_events
        self._clock: Final = clock

    async def detail(self, channel_id: UUID) -> ChannelDetail | ChannelManagementFailure:
        snapshot: Final = await self._catalog_repository.load_snapshot()
        channel: Final = next((item for item in snapshot.channels if item.channel_id == channel_id), None)
        if channel is None:
            return _failure("channel_not_found", retryable=False)
        return self._channel_detail(channel, snapshot.bindings)

    async def detail_by_legacy_account(self, account_id: AccountId) -> ChannelDetail | ChannelManagementFailure:
        snapshot: Final = await self._catalog_repository.load_snapshot()
        channel: Final = next((item for item in snapshot.channels if item.legacy_account_id == account_id), None)
        if channel is None:
            return _failure("channel_not_found", retryable=False)
        return self._channel_detail(channel, snapshot.bindings)

    @staticmethod
    def _channel_detail(
        channel: ChannelRecord,
        all_bindings: tuple[DeploymentBindingRecord, ...],
    ) -> ChannelDetail:
        bindings: Final = tuple(
            ChannelBindingMutation(
                binding_id=binding.binding_id,
                public_model=binding.public_model,
                provider_model=binding.provider_model,
                litellm_deployment_id=binding.litellm_deployment_id,
                ownership=binding.ownership,
                enabled=binding.enabled,
            )
            for binding in all_bindings
            if binding.channel_id == channel.channel_id
        )
        return ChannelDetail(
            channel_id=channel.channel_id,
            display_name=channel.display_name,
            provider=channel.provider,
            model_discovery_provider_id=channel.model_discovery_provider_id,
            parser_provider_id=channel.parser_provider_id,
            group=channel.group,
            base_url_display=channel.base_url_display,
            administrative_state=channel.administrative_state,
            max_concurrency=channel.max_concurrency,
            priority=channel.priority,
            weight=channel.weight,
            quotas=channel.quotas,
            key_mask=channel.key_mask,
            bindings=bindings,
        )

    async def operation(self, operation_id: UUID) -> ChannelOperationView | ChannelManagementFailure:
        loaded: Final = await self._operations.load(operation_id)
        if isinstance(loaded, SyncOperationPersistenceFailure):
            return _persistence_failure(loaded)
        return _operation_view(loaded.operation, "existing")

    async def create(
        self,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult:
        now: Final = self._clock()
        operation_id: Final = _operation_id(idempotency_key)
        channel_id: Final = uuid5(operation_id, "channel")
        snapshot: Final = await self._catalog_repository.load_snapshot()
        desired: Final = _desired_state(
            request,
            channel_id,
            len(snapshot.channels),
            now,
            None,
            (),
            operation_id,
        )
        operation: Final = _new_operation(
            operation_id,
            idempotency_key,
            SyncAction.CREATE_CHANNEL,
            desired,
            now,
        )
        return await self._submit(operation, request.api_key, actor)

    async def import_channel(
        self,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult:
        now: Final = self._clock()
        operation_id: Final = _operation_id(idempotency_key)
        channel_id: Final = uuid5(operation_id, "channel")
        snapshot: Final = await self._catalog_repository.load_snapshot()
        desired: Final = _desired_state(
            request,
            channel_id,
            len(snapshot.channels),
            now,
            None,
            (),
            operation_id,
        )
        operation: Final = _new_operation(
            operation_id,
            idempotency_key,
            SyncAction.IMPORT_CHANNEL,
            desired,
            now,
        )
        return await self._submit(operation, request.api_key, actor)

    async def update(
        self,
        channel_id: UUID,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult:
        snapshot: Final = await self._catalog_repository.load_snapshot()
        existing: Final = next((item for item in snapshot.channels if item.channel_id == channel_id), None)
        if existing is None:
            return _failure("channel_not_found", retryable=False)
        existing_bindings: Final = tuple(
            item for item in snapshot.bindings if item.channel_id == channel_id
        )
        operation_id: Final = _operation_id(idempotency_key)
        desired: Final = _desired_state(
            request,
            channel_id,
            existing.account_order,
            self._clock(),
            existing,
            existing_bindings,
            operation_id,
        )
        operation: Final = _new_operation(
            operation_id,
            idempotency_key,
            SyncAction.UPDATE_CHANNEL,
            desired,
            self._clock(),
        )
        return await self._submit(operation, request.api_key, actor)

    async def detach(
        self,
        channel_id: UUID,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult:
        operation: Final = await self._removal_operation(channel_id, idempotency_key, SyncAction.DETACH_CHANNEL)
        if isinstance(operation, ChannelManagementFailure):
            return operation
        return await self._submit(operation, None, actor)

    async def delete(
        self,
        channel_id: UUID,
        request: ChannelDeleteRequest,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult:
        operation: Final = await self._removal_operation(
            channel_id,
            idempotency_key,
            SyncAction.DELETE_CHANNEL,
            request.delete_mode,
        )
        if isinstance(operation, ChannelManagementFailure):
            return operation
        return await self._submit(operation, None, actor)

    async def delete_external(
        self,
        channel_id: UUID,
        binding_id: UUID,
        request: ExternalDeploymentDeleteRequest,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult:
        del request
        snapshot: Final = await self._catalog_repository.load_snapshot()
        loaded_desired: Final = _desired_from_snapshot(snapshot, channel_id)
        if loaded_desired is None:
            return _failure("channel_not_found", retryable=False)
        binding: Final = next((item for item in loaded_desired.bindings if item.binding_id == binding_id), None)
        if binding is None or binding.ownership != BindingOwnership.EXTERNALLY_MANAGED:
            return _failure("external_binding_not_found", retryable=False)
        desired: Final = loaded_desired.model_copy(update={"target_binding_id": binding_id})
        operation_id: Final = _operation_id(idempotency_key)
        operation: Final = _new_operation(
            operation_id,
            idempotency_key,
            SyncAction.DELETE_EXTERNAL_DEPLOYMENT,
            desired,
            self._clock(),
        )
        return await self._submit(operation, None, actor, external_binding_id=binding_id)

    async def reconcile(
        self,
        channel_id: UUID,
        request: ChannelReconcileRequest,
        actor: ActorContext,
    ) -> ChannelManagementResult:
        listed: Final = await self._operations.list_pending_and_failed(limit=100)
        if isinstance(listed, SyncOperationPersistenceFailure):
            return _persistence_failure(listed)
        operation: Final = next(
            (item for item in listed.operations if item.channel_id == channel_id),
            None,
        )
        if operation is None:
            return _failure("operation_not_found", retryable=False)
        if operation.requires_key and request.api_key is None:
            return _operation_view(operation, "existing")
        return await self._execute(operation, request.api_key, actor, "existing")

    async def reconcile_pending(
        self,
        limit: int = 100,
    ) -> ReconcilePassResult | ChannelManagementFailure:
        listed: Final = await self._operations.list_pending_and_failed(limit=limit)
        if isinstance(listed, SyncOperationPersistenceFailure):
            return _persistence_failure(listed)
        items: Final = tuple(
            [
                await self._reconcile_operation(operation)
                if not operation.requires_key
                else ReconcilePassItem(
                    operation_id=operation.operation_id,
                    channel_id=operation.channel_id,
                    status="requires_key",
                )
                for operation in listed.operations
            ]
        )
        await asyncio.gather(
            *(self._record_reconcile_event(operation, item) for operation, item in zip(listed.operations, items))
        )
        discovered: Final = await self._synchronizer.list_managed_deployments()
        if isinstance(discovered, LiteLLMSyncFailure):
            return ReconcilePassResult(
                inspected=len(listed.operations),
                items=items,
                orphan_scan_failure_code=discovered.failure.code,
            )
        snapshot: Final = await self._catalog_repository.load_snapshot()
        return ReconcilePassResult(
            inspected=len(listed.operations),
            items=items,
            orphan_deployments=_orphan_deployments(discovered, snapshot.bindings),
        )

    async def _reconcile_operation(self, operation: SyncOperation) -> ReconcilePassItem:
        result: Final = await self._execute(
            operation,
            None,
            _system_reconcile_actor(operation),
            "existing",
        )
        if isinstance(result, ChannelManagementFailure):
            return ReconcilePassItem(
                operation_id=operation.operation_id,
                channel_id=operation.channel_id,
                status="failed",
                failure_code=result.code,
            )
        return ReconcilePassItem(
            operation_id=operation.operation_id,
            channel_id=operation.channel_id,
            status="applied" if result.operation_status == SyncStatus.APPLIED else "failed",
            failure_code=None if result.failure is None else result.failure.code,
        )

    async def _record_reconcile_event(self, operation: SyncOperation, item: ReconcilePassItem) -> None:
        event_type, reason_code = _reconcile_event_outcome(item)
        attempt_count: Final = operation.attempt_count + (
            1 if item.status == "applied" or item.failure_code is not None else 0
        )
        await self._operational_events.append(
            build_sync_reconcile_record(
                operation_id=operation.operation_id,
                channel_id=operation.channel_id,
                sync_action=operation.action.value,
                attempt_count=attempt_count,
                occurred_at=self._clock(),
                event_type=event_type,
                reason_code=reason_code,
            )
        )

    async def _removal_operation(
        self,
        channel_id: UUID,
        idempotency_key: str,
        action: SyncAction,
        delete_mode: DeleteMode | None = None,
    ) -> SyncOperation | ChannelManagementFailure:
        snapshot: Final = await self._catalog_repository.load_snapshot()
        desired: Final = _desired_from_snapshot(snapshot, channel_id)
        if desired is None:
            return _failure("channel_not_found", retryable=False)
        return _new_operation(
            _operation_id(idempotency_key),
            idempotency_key,
            action,
            desired,
            self._clock(),
            delete_mode,
        )

    async def _submit(
        self,
        operation: SyncOperation,
        api_key: SecretStr | None,
        actor: ActorContext,
        external_binding_id: UUID | None = None,
    ) -> ChannelManagementResult:
        created: Final = await self._operations.create(operation)
        if isinstance(created, SyncOperationPersistenceFailure):
            return _persistence_failure(created)
        status: Final[Literal["accepted", "existing"]] = (
            "accepted" if created.status == "created" else "existing"
        )
        if created.status == "existing" and created.operation.status == SyncStatus.APPLIED:
            return await self._resume_applied(created.operation, actor, status, external_binding_id)
        return await self._execute(created.operation, api_key, actor, status, external_binding_id)

    async def _resume_applied(
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
            return projection_audit_failure or _failure("runtime_projection_failed", retryable=True)
        success_audit_failure: Final = await self._write_audit(
            operation,
            actor,
            AuditOutcome.SUCCEEDED,
            None,
            external_binding_id,
        )
        return success_audit_failure or _operation_view(operation, response_status)

    async def _execute(
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
            return _persistence_failure(attempted)
        active: Final = attempted.operation
        target_binding_id: Final = external_binding_id or active.desired.target_binding_id
        external_failure: Final = await self._sync(active, api_key, target_binding_id)
        if external_failure is not None:
            return await self._record_failure(
                active,
                external_failure,
                _retry_requires_key(active, api_key),
                actor,
                response_status,
            )
        command: Final = _catalog_command(active, target_binding_id, self._clock())
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
            return projection_audit_failure or _failure("runtime_projection_failed", retryable=True)
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
            return _operation_view(loaded.operation, response_status)
        return _operation_view(active.model_copy(update={"status": SyncStatus.APPLIED, "applied_at": self._clock()}), response_status)

    async def _prepare_removal(
        self,
        operation: SyncOperation,
        actor: ActorContext,
        response_status: Literal["accepted", "existing"],
    ) -> ChannelManagementResult | None:
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
            return projection_audit_failure or _failure("runtime_projection_failed", retryable=True)
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
        return waiting_audit_failure or _operation_view(operation, response_status)

    async def _sync(
        self,
        operation: SyncOperation,
        api_key: SecretStr | None,
        external_binding_id: UUID | None,
    ) -> SafeSyncFailure | None:
        if operation.action == SyncAction.IMPORT_CHANNEL or operation.action == SyncAction.DETACH_CHANNEL:
            return None
        if operation.action == SyncAction.DELETE_EXTERNAL_DEPLOYMENT:
            binding: Final = next(
                item for item in operation.desired.bindings if item.binding_id == external_binding_id
            )
            result: Final = await self._synchronizer.delete_external_deployment(
                ExternalDeploymentDelete(
                    channel_id=operation.channel_id,
                    binding_id=binding.binding_id,
                    litellm_deployment_id=binding.litellm_deployment_id,
                    ownership=BindingOwnership.EXTERNALLY_MANAGED,
                    confirmed=True,
                )
            )
            return result.failure if isinstance(result, LiteLLMSyncFailure) else None
        if operation.action == SyncAction.DELETE_CHANNEL:
            if operation.delete_mode == DeleteMode.DETACH_ONLY:
                return None
            return await _delete_managed_bindings(self._synchronizer, operation.desired.bindings)
        if api_key is None and operation.action == SyncAction.CREATE_CHANNEL:
            return SafeSyncFailure(code="provider_key_required", message="Provider key is required")
        active_failure: Final = await _sync_managed_bindings(self._synchronizer, operation, api_key)
        if active_failure is not None:
            return active_failure
        return await _delete_managed_bindings(self._synchronizer, operation.desired.retired_bindings)

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
            return _operation_view(updated.operation, response_status)
        return _persistence_failure(updated)

    async def _write_audit(
        self,
        operation: SyncOperation,
        actor: ActorContext,
        outcome: AuditOutcome,
        failure_code: str | None,
        external_binding_id: UUID | None,
    ) -> ChannelManagementFailure | None:
        details: Final = _audit_details(
            operation,
            actor.action,
            outcome,
            failure_code,
            external_binding_id,
        )
        result: Final = await self._audit.append(
            build_management_audit_record(
                event_id=_audit_event_id(operation.operation_id, actor, outcome, failure_code),
                occurred_at=self._clock(),
                actor=actor,
                operation_id=operation.operation_id,
                channel_id=operation.channel_id,
                details=details,
            )
        )
        if isinstance(result, AuditPersistenceFailure):
            return _failure(f"audit_{result.code.value}", result.retryable)
        return None


async def _sync_binding(
    synchronizer: DeploymentSynchronizer,
    operation: SyncOperation,
    binding: DesiredBinding,
    api_key: SecretStr | None,
) -> LiteLLMSyncResult:
    if operation.action == SyncAction.CREATE_CHANNEL or binding.sync_mode == "create":
        if api_key is None:
            return LiteLLMSyncFailure(
                action=LiteLLMSyncAction.CREATE,
                failure=SafeSyncFailure(code="provider_key_required", message="Provider key is required"),
                retryable=False,
            )
        return await synchronizer.create_deployment(operation, binding, operation.desired.base_url_display, api_key)
    if binding.sync_mode == "none":
        return LiteLLMSyncSuccess(
            action=LiteLLMSyncAction.UPDATE,
            litellm_deployment_id=binding.litellm_deployment_id,
        )
    return await synchronizer.update_deployment(operation, binding, operation.desired.base_url_display, api_key)


async def _sync_managed_bindings(
    synchronizer: DeploymentSynchronizer,
    operation: SyncOperation,
    api_key: SecretStr | None,
) -> SafeSyncFailure | None:
    for binding in operation.desired.bindings:
        if binding.ownership != BindingOwnership.POOL_MANAGED:
            continue
        if (failure := await _sync_managed_binding(synchronizer, operation, binding, api_key)) is not None:
            return failure
    return None


async def _delete_managed_bindings(
    synchronizer: DeploymentSynchronizer,
    bindings: tuple[DesiredBinding, ...],
) -> SafeSyncFailure | None:
    for binding in bindings:
        if binding.ownership != BindingOwnership.POOL_MANAGED:
            continue
        if (failure := await _delete_managed_binding(synchronizer, binding)) is not None:
            return failure
    return None


async def _sync_managed_binding(
    synchronizer: DeploymentSynchronizer,
    operation: SyncOperation,
    binding: DesiredBinding,
    api_key: SecretStr | None,
) -> SafeSyncFailure | None:
    result: Final = await _sync_binding(synchronizer, operation, binding, api_key)
    return result.failure if isinstance(result, LiteLLMSyncFailure) else None


async def _delete_managed_binding(
    synchronizer: DeploymentSynchronizer,
    binding: DesiredBinding,
) -> SafeSyncFailure | None:
    result: Final = await synchronizer.delete_managed_deployment(binding)
    return result.failure if isinstance(result, LiteLLMSyncFailure) else None


def _desired_state(
    request: ChannelMutation,
    channel_id: UUID,
    account_order: int,
    now: AwareDatetime,
    existing: ChannelRecord | None,
    existing_bindings: tuple[DeploymentBindingRecord, ...],
    operation_id: UUID,
) -> ChannelDesiredState:
    key_value: Final = request.api_key.get_secret_value() if request.api_key is not None else None
    bindings: Final = tuple(
        DesiredBinding(
            binding_id=binding.binding_id or uuid5(operation_id, f"binding:{index}"),
            channel_id=channel_id,
            deployment_order=index,
            public_model=binding.public_model,
            provider_model=binding.provider_model,
            litellm_deployment_id=binding.litellm_deployment_id or str(uuid5(operation_id, f"deployment:{index}")),
            ownership=binding.ownership,
            sync_mode=_binding_sync_mode(binding, existing_bindings),
            enabled=binding.enabled,
        )
        for index, binding in enumerate(request.bindings)
    )
    return ChannelDesiredState(
        channel_id=channel_id,
        legacy_account_id=(
            request.legacy_account_id
            if request.legacy_account_id is not None
            else (None if existing is None else existing.legacy_account_id)
        ),
        account_order=account_order,
        display_name=request.display_name,
        provider=request.provider,
        model_discovery_provider_id=(
            request.model_discovery_provider_id
            if request.model_discovery_provider_id is not None
            else (None if existing is None else existing.model_discovery_provider_id)
        ),
        parser_provider_id=None if existing is None else existing.parser_provider_id,
        group=request.group,
        base_url_display=request.base_url_display,
        administrative_state=request.administrative_state,
        max_concurrency=request.max_concurrency,
        priority=request.priority,
        weight=request.weight,
        quotas=request.quotas,
        credential_ref=None if existing is None else existing.credential_ref,
        key_mask=_key_mask(key_value) if key_value is not None else (None if existing is None else existing.key_mask),
        key_fingerprint=(
            sha256(key_value.encode()).hexdigest()
            if key_value is not None
            else (None if existing is None else existing.key_fingerprint)
        ),
        bindings=bindings,
        retired_bindings=tuple(_retired_binding(binding) for binding in _removed_bindings(request, existing_bindings)),
    )


def _desired_from_snapshot(snapshot: CatalogSnapshot, channel_id: UUID) -> ChannelDesiredState | None:
    channel: Final = next((item for item in snapshot.channels if item.channel_id == channel_id), None)
    if channel is None:
        return None
    bindings: Final = tuple(
        DesiredBinding(
            binding_id=binding.binding_id,
            channel_id=binding.channel_id,
            deployment_order=binding.deployment_order,
            public_model=binding.public_model,
            provider_model=binding.provider_model,
            litellm_deployment_id=binding.litellm_deployment_id,
            ownership=binding.ownership,
            sync_mode="none",
            enabled=binding.enabled,
        )
        for binding in snapshot.bindings
        if binding.channel_id == channel_id
    )
    return ChannelDesiredState(
        channel_id=channel.channel_id,
        legacy_account_id=channel.legacy_account_id,
        account_order=channel.account_order,
        display_name=channel.display_name,
        provider=channel.provider,
        model_discovery_provider_id=channel.model_discovery_provider_id,
        parser_provider_id=channel.parser_provider_id,
        group=channel.group,
        base_url_display=channel.base_url_display,
        administrative_state=channel.administrative_state,
        max_concurrency=channel.max_concurrency,
        priority=channel.priority,
        weight=channel.weight,
        quotas=channel.quotas,
        credential_ref=channel.credential_ref,
        key_mask=channel.key_mask,
        key_fingerprint=channel.key_fingerprint,
        bindings=bindings,
        retired_bindings=(),
    )


def _new_operation(
    operation_id: UUID,
    idempotency_key: str,
    action: SyncAction,
    desired: ChannelDesiredState,
    now: AwareDatetime,
    delete_mode: DeleteMode | None = None,
) -> SyncOperation:
    pending: Final = {
        SyncAction.CREATE_CHANNEL: SyncStatus.PENDING_CREATE,
        SyncAction.IMPORT_CHANNEL: SyncStatus.PENDING_CREATE,
        SyncAction.UPDATE_CHANNEL: SyncStatus.PENDING_UPDATE,
        SyncAction.DETACH_CHANNEL: SyncStatus.PENDING_DELETE,
        SyncAction.DELETE_CHANNEL: SyncStatus.PENDING_DELETE,
        SyncAction.DELETE_EXTERNAL_DEPLOYMENT: SyncStatus.PENDING_DELETE,
    }[action]
    return SyncOperation(
        operation_id=operation_id,
        idempotency_key=idempotency_key,
        channel_id=desired.channel_id,
        action=action,
        status=pending,
        delete_mode=delete_mode,
        desired=desired,
        created_at=now,
        updated_at=now,
    )


def _catalog_command(
    operation: SyncOperation,
    external_binding_id: UUID | None,
    now: AwareDatetime,
) -> CatalogLifecycleCommand:
    desired: Final = operation.desired
    channel: Final = ChannelRecord(
        channel_id=desired.channel_id,
        legacy_account_id=desired.legacy_account_id,
        account_order=desired.account_order,
        display_name=desired.display_name,
        provider=desired.provider,
        model_discovery_provider_id=desired.model_discovery_provider_id,
        parser_provider_id=desired.parser_provider_id,
        group=desired.group,
        base_url_display=desired.base_url_display,
        administrative_state=desired.administrative_state,
        max_concurrency=desired.max_concurrency,
        priority=desired.priority,
        weight=desired.weight,
        quotas=desired.quotas,
        credential_ref=desired.credential_ref,
        key_mask=desired.key_mask,
        key_fingerprint=desired.key_fingerprint,
        created_at=operation.created_at,
        updated_at=now,
    )
    bindings: Final = tuple(
        DeploymentBindingRecord(
            binding_id=binding.binding_id,
            channel_id=binding.channel_id,
            deployment_order=binding.deployment_order,
            public_model=binding.public_model,
            provider_model=binding.provider_model,
            litellm_deployment_id=binding.litellm_deployment_id,
            ownership=binding.ownership,
            enabled=binding.enabled,
            created_at=operation.created_at,
            updated_at=now,
        )
        for binding in desired.bindings
    )
    if operation.action == SyncAction.CREATE_CHANNEL:
        return ApplyChannelCreate(operation_id=operation.operation_id, channel=channel, bindings=bindings)
    if operation.action == SyncAction.IMPORT_CHANNEL:
        return ApplyChannelImport(operation_id=operation.operation_id, channel=channel, bindings=bindings)
    if operation.action == SyncAction.UPDATE_CHANNEL:
        return ApplyChannelUpdate(operation_id=operation.operation_id, channel=channel, bindings=bindings)
    if operation.action == SyncAction.DETACH_CHANNEL:
        return ApplyChannelDetach(operation_id=operation.operation_id, channel_id=desired.channel_id, bindings=bindings)
    if operation.action == SyncAction.DELETE_CHANNEL:
        assert operation.delete_mode is not None
        return ApplyChannelDelete(
            operation_id=operation.operation_id,
            channel_id=desired.channel_id,
            bindings=bindings,
            mode=CatalogDeleteMode(operation.delete_mode.value),
        )
    binding: Final = next(item for item in bindings if item.binding_id == external_binding_id)
    return ApplyExternalBindingDelete(
        operation_id=operation.operation_id,
        channel_id=desired.channel_id,
        binding=binding,
    )


def _audit_details(
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


def _operation_view(
    operation: SyncOperation,
    status: Literal["accepted", "existing"],
) -> ChannelOperationView:
    return ChannelOperationView(
        status=status,
        operation_id=operation.operation_id,
        channel_id=operation.channel_id,
        operation_status=operation.status,
        requires_key=operation.requires_key,
        failure=operation.failure,
    )


def _persistence_failure(failure: SyncOperationPersistenceFailure) -> ChannelManagementFailure:
    return _failure(failure.code.value, failure.retryable)


def _failure(code: str, retryable: bool) -> ChannelManagementFailure:
    return ChannelManagementFailure(code=code, retryable=retryable)


def _key_mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-4:]}"


def _operation_id(idempotency_key: str) -> UUID:
    return uuid5(_IDEMPOTENCY_NAMESPACE, idempotency_key)


def _audit_event_id(
    operation_id: UUID,
    actor: ActorContext,
    outcome: AuditOutcome,
    failure_code: str | None,
) -> UUID:
    return uuid5(operation_id, f"{actor.action.value}:{actor.request_id}:{outcome.value}:{failure_code or 'none'}")


def _system_reconcile_actor(operation: SyncOperation) -> ActorContext:
    request_id: Final = f"reconcile:{operation.operation_id.hex}:{operation.attempt_count + 1}"
    return ActorContext(
        user_id="account-pool-reconciler",
        role="system",
        actor_type="system",
        request_id=request_id,
        action=ActorAction.CHANNEL_RECONCILE,
        envelope_id=uuid5(operation.operation_id, request_id),
    )


def _retry_requires_key(operation: SyncOperation, api_key: SecretStr | None) -> bool:
    if api_key is not None or operation.action == SyncAction.CREATE_CHANNEL:
        return True
    return any(
        binding.ownership == BindingOwnership.POOL_MANAGED and binding.sync_mode == "create"
        for binding in operation.desired.bindings
    )


def _reconcile_event_outcome(item: ReconcilePassItem) -> tuple[OperationalEventType, str | None]:
    if item.status == "applied":
        return OperationalEventType.SYNC_RETRY_SUCCEEDED, None
    if item.status == "requires_key":
        return OperationalEventType.SYNC_RETRY_DEFERRED, "requires_key"
    if item.failure_code is not None:
        return OperationalEventType.SYNC_RETRY_FAILED, item.failure_code
    return OperationalEventType.SYNC_RETRY_DEFERRED, "operation_pending"


def _orphan_deployments(
    discovered: ManagedDeploymentListSuccess,
    bindings: tuple[DeploymentBindingRecord, ...],
) -> tuple[ManagedDeploymentMarker, ...]:
    return tuple(
        marker
        for marker in discovered.deployments
        if not any(
            binding.litellm_deployment_id == marker.litellm_deployment_id
            and binding.channel_id == marker.channel_id
            and binding.binding_id == marker.binding_id
            for binding in bindings
        )
    )


def _binding_sync_mode(
    binding: ChannelBindingMutation,
    existing_bindings: tuple[DeploymentBindingRecord, ...],
) -> Literal["create", "update", "none"]:
    if binding.ownership == BindingOwnership.EXTERNALLY_MANAGED:
        return "none"
    if binding.binding_id is None:
        return "create"
    matched: Final = next((item for item in existing_bindings if item.binding_id == binding.binding_id), None)
    return "update" if matched is not None else "create"


def _removed_bindings(
    request: ChannelMutation,
    existing_bindings: tuple[DeploymentBindingRecord, ...],
) -> tuple[DeploymentBindingRecord, ...]:
    retained_ids: Final = frozenset(
        binding.binding_id for binding in request.bindings if binding.binding_id is not None
    )
    return tuple(binding for binding in existing_bindings if binding.binding_id not in retained_ids)


def _retired_binding(binding: DeploymentBindingRecord) -> DesiredBinding:
    return DesiredBinding(
        binding_id=binding.binding_id,
        channel_id=binding.channel_id,
        deployment_order=binding.deployment_order,
        public_model=binding.public_model,
        provider_model=binding.provider_model,
        litellm_deployment_id=binding.litellm_deployment_id,
        ownership=binding.ownership,
        sync_mode="none",
        enabled=False,
    )
