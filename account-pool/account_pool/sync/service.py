"""编排渠道期望状态持久化、LiteLLM 同步、目录应用、运行投影和管理审计。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Literal
from uuid import UUID, uuid5

from pydantic import SecretStr

from account_pool.audit.repository import ManagementAuditRepository
from account_pool.auth.actor import ActorContext
from account_pool.catalog.models import (
    BindingOwnership,
    ChannelRecord,
    DeploymentBindingRecord,
)
from account_pool.catalog.repository import CatalogRepository
from account_pool.catalog.service import CatalogService
from account_pool.models import AccountId
from account_pool.operational.models import build_sync_reconcile_record
from account_pool.operational.repository import OperationalEventRepository
from account_pool.runtime_projection import RuntimeProjector
from account_pool.sync.audit import reconcile_event_outcome, system_reconcile_actor
from account_pool.sync.contracts import (
    ChannelBindingMutation,
    ChannelDeleteRequest,
    ChannelDetail,
    ChannelManagementFailure,
    ChannelManagementResult,
    ChannelMutation,
    ChannelOperationView,
    ChannelReconcileRequest,
    DeploymentSynchronizer,
    ExternalDeploymentDeleteRequest,
    ReconcilePassItem,
    ReconcilePassResult,
)
from account_pool.sync.desired_state import (
    build_desired_state,
    desired_state_from_snapshot,
    new_operation,
    operation_id_from_idempotency_key,
)
from account_pool.sync.executor import ChannelOperationExecutor
from account_pool.sync.external import orphan_deployments
from account_pool.sync.litellm import (
    LiteLLMSyncFailure,
)
from account_pool.sync.models import (
    DeleteMode,
    SyncAction,
    SyncOperation,
    SyncStatus,
)
from account_pool.sync.repository import (
    SyncOperationPersistenceFailure,
    SyncOperationRepository,
)
from account_pool.sync.results import management_failure, operation_view, persistence_failure

Clock = Callable[[], datetime]


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
        self._operations: Final = operations
        self._synchronizer: Final = synchronizer
        self._executor: Final = ChannelOperationExecutor(
            catalog=CatalogService(catalog_repository),
            operations=operations,
            synchronizer=synchronizer,
            runtime_projector=runtime_projector,
            audit=audit,
            clock=clock,
        )
        self._operational_events: Final = operational_events
        self._clock: Final = clock

    async def detail(self, channel_id: UUID) -> ChannelDetail | ChannelManagementFailure:
        snapshot: Final = await self._catalog_repository.load_snapshot()
        channel: Final = next((item for item in snapshot.channels if item.channel_id == channel_id), None)
        if channel is None:
            return management_failure("channel_not_found", retryable=False)
        return self._channel_detail(channel, snapshot.bindings)

    async def detail_by_legacy_account(self, account_id: AccountId) -> ChannelDetail | ChannelManagementFailure:
        snapshot: Final = await self._catalog_repository.load_snapshot()
        channel: Final = next((item for item in snapshot.channels if item.legacy_account_id == account_id), None)
        if channel is None:
            return management_failure("channel_not_found", retryable=False)
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
            return persistence_failure(loaded)
        return operation_view(loaded.operation, "existing")

    async def create(
        self,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult:
        now: Final = self._clock()
        operation_id: Final = operation_id_from_idempotency_key(idempotency_key)
        channel_id: Final = uuid5(operation_id, "channel")
        snapshot: Final = await self._catalog_repository.load_snapshot()
        desired: Final = build_desired_state(
            request,
            channel_id,
            len(snapshot.channels),
            now,
            None,
            (),
            operation_id,
        )
        operation: Final = new_operation(
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
        operation_id: Final = operation_id_from_idempotency_key(idempotency_key)
        channel_id: Final = uuid5(operation_id, "channel")
        snapshot: Final = await self._catalog_repository.load_snapshot()
        desired: Final = build_desired_state(
            request,
            channel_id,
            len(snapshot.channels),
            now,
            None,
            (),
            operation_id,
        )
        operation: Final = new_operation(
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
            return management_failure("channel_not_found", retryable=False)
        existing_bindings: Final = tuple(item for item in snapshot.bindings if item.channel_id == channel_id)
        operation_id: Final = operation_id_from_idempotency_key(idempotency_key)
        desired: Final = build_desired_state(
            request,
            channel_id,
            existing.account_order,
            self._clock(),
            existing,
            existing_bindings,
            operation_id,
        )
        operation: Final = new_operation(
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
        loaded_desired: Final = desired_state_from_snapshot(snapshot, channel_id)
        if loaded_desired is None:
            return management_failure("channel_not_found", retryable=False)
        binding: Final = next((item for item in loaded_desired.bindings if item.binding_id == binding_id), None)
        if binding is None or binding.ownership != BindingOwnership.EXTERNALLY_MANAGED:
            return management_failure("external_binding_not_found", retryable=False)
        desired: Final = loaded_desired.model_copy(update={"target_binding_id": binding_id})
        operation_id: Final = operation_id_from_idempotency_key(idempotency_key)
        operation: Final = new_operation(
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
            return persistence_failure(listed)
        operation: Final = next(
            (item for item in listed.operations if item.channel_id == channel_id),
            None,
        )
        if operation is None:
            return management_failure("operation_not_found", retryable=False)
        if operation.requires_key and request.api_key is None:
            return operation_view(operation, "existing")
        return await self._executor.execute(operation, request.api_key, actor, "existing")

    async def reconcile_pending(
        self,
        limit: int = 100,
    ) -> ReconcilePassResult | ChannelManagementFailure:
        listed: Final = await self._operations.list_pending_and_failed(limit=limit)
        if isinstance(listed, SyncOperationPersistenceFailure):
            return persistence_failure(listed)
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
            orphan_deployments=orphan_deployments(discovered, snapshot.bindings),
        )

    async def _reconcile_operation(self, operation: SyncOperation) -> ReconcilePassItem:
        result: Final = await self._executor.execute(
            operation,
            None,
            system_reconcile_actor(operation),
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
        event_type, reason_code = reconcile_event_outcome(item)
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
        desired: Final = desired_state_from_snapshot(snapshot, channel_id)
        if desired is None:
            return management_failure("channel_not_found", retryable=False)
        return new_operation(
            operation_id_from_idempotency_key(idempotency_key),
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
            return persistence_failure(created)
        status: Final[Literal["accepted", "existing"]] = "accepted" if created.status == "created" else "existing"
        if created.status == "existing" and created.operation.status == SyncStatus.APPLIED:
            return await self._executor.resume_applied(created.operation, actor, status, external_binding_id)
        return await self._executor.execute(created.operation, api_key, actor, status, external_binding_id)
