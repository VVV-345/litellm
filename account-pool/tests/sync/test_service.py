"""验证渠道 desired-first 服务的调用顺序、所有权边界和失败恢复结果。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from account_pool.audit.models import ManagementAuditRecord
from account_pool.audit.repository import AuditLoadResult, AuditWriteSuccess
from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.catalog.lifecycle import (
    ApplyChannelCreate,
    ApplyChannelDelete,
    ApplyChannelDetach,
    ApplyChannelImport,
    ApplyChannelUpdate,
    CatalogApplySuccess,
    CatalogLifecycleCommand,
    CatalogPendingDeleteResult,
    CatalogPendingDeleteSuccess,
)
from account_pool.catalog.models import (
    AdministrativeState,
    BindingOwnership,
    CatalogImport,
    CatalogSnapshot,
    ImportResult,
)
from account_pool.models import AccountSnapshot, Health, PoolConfig, QuotaSnapshot, QuotaUnit
from account_pool.operational.models import OperationalEventRecord
from account_pool.operational.repository import OperationalWriteResult, OperationalWriteSuccess
from account_pool.runtime_projection import RuntimeProjector
from account_pool.sync.litellm import (
    LiteLLMSyncAction,
    LiteLLMSyncFailure,
    LiteLLMSyncSuccess,
    ManagedDeploymentListSuccess,
    ManagedDeploymentMarker,
)
from account_pool.sync.models import (
    DeleteMode,
    DesiredBinding,
    ExternalDeploymentDelete,
    SafeSyncFailure,
    SyncOperation,
    SyncStatus,
)
from account_pool.sync.repository import (
    SyncOperationListSuccess,
    SyncOperationLoadSuccess,
    SyncOperationWriteSuccess,
)
from account_pool.sync.service import (
    ChannelBindingMutation,
    ChannelDeleteRequest,
    ChannelManagementFailure,
    ChannelManagementService,
    ChannelMutation,
    ChannelOperationView,
    ExternalDeploymentDeleteRequest,
    ReconcilePassResult,
)
from pydantic import SecretStr

_NOW: Final = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


class RecordingOperationRepository:
    def __init__(self, events: list[str]) -> None:
        self._events: Final = events
        self._operation: SyncOperation | None = None

    async def create(self, operation: SyncOperation) -> SyncOperationWriteSuccess:
        if self._operation is not None and self._operation.idempotency_key == operation.idempotency_key:
            return SyncOperationWriteSuccess(status="existing", operation=self._operation)
        self._events.append("operation:create")
        self._operation = operation
        return SyncOperationWriteSuccess(status="created", operation=operation)

    async def load(self, operation_id: UUID) -> SyncOperationLoadSuccess:
        assert self._operation is not None and self._operation.operation_id == operation_id
        return SyncOperationLoadSuccess(operation=self._operation)

    async def load_by_idempotency_key(self, idempotency_key: str) -> SyncOperationLoadSuccess:
        assert self._operation is not None and self._operation.idempotency_key == idempotency_key
        return SyncOperationLoadSuccess(operation=self._operation)

    async def list_pending_and_failed(self, limit: int = 100) -> SyncOperationListSuccess:
        assert limit == 100
        return SyncOperationListSuccess(operations=() if self._operation is None else (self._operation,))

    async def record_attempt(self, operation_id: UUID, at: datetime) -> SyncOperationWriteSuccess:
        assert self._operation is not None and self._operation.operation_id == operation_id
        self._events.append("operation:attempt")
        self._operation = self._operation.model_copy(
            update={"attempt_count": self._operation.attempt_count + 1, "updated_at": at}
        )
        return SyncOperationWriteSuccess(status="updated", operation=self._operation)

    async def mark_applied(self, operation_id: UUID, at: datetime) -> SyncOperationWriteSuccess:
        raise AssertionError("catalog transaction owns the applied transition")

    async def mark_failed(
        self,
        operation_id: UUID,
        failure: SafeSyncFailure,
        requires_key: bool,
        at: datetime,
    ) -> SyncOperationWriteSuccess:
        assert self._operation is not None and self._operation.operation_id == operation_id
        self._events.append("operation:failed")
        self._operation = self._operation.model_copy(
            update={
                "status": SyncStatus.FAILED,
                "failure": failure,
                "requires_key": requires_key,
                "updated_at": at,
            }
        )
        return SyncOperationWriteSuccess(status="updated", operation=self._operation)

    def mark_catalog_applied(self, operation_id: UUID) -> None:
        assert self._operation is not None and self._operation.operation_id == operation_id
        self._operation = self._operation.model_copy(
            update={
                "status": SyncStatus.APPLIED,
                "applied_at": _NOW,
                "updated_at": _NOW,
                "failure": None,
                "requires_key": False,
            }
        )


class RecordingCatalogRepository:
    def __init__(self, events: list[str], operations: RecordingOperationRepository) -> None:
        self._events: Final = events
        self._operations: Final = operations
        self.snapshot: CatalogSnapshot = CatalogSnapshot()
        self.commands: tuple[CatalogLifecycleCommand, ...] = ()

    async def load_snapshot(self) -> CatalogSnapshot:
        return self.snapshot

    async def import_once(self, command: CatalogImport) -> ImportResult:
        raise AssertionError("not used")

    async def apply_lifecycle(self, command: CatalogLifecycleCommand) -> CatalogApplySuccess:
        self._events.append("catalog:apply")
        self.commands = (*self.commands, command)
        if isinstance(command, (ApplyChannelDelete, ApplyChannelDetach)):
            self.snapshot = CatalogSnapshot(
                channels=tuple(
                    channel for channel in self.snapshot.channels if channel.channel_id != command.channel_id
                ),
                bindings=tuple(
                    binding for binding in self.snapshot.bindings if binding.channel_id != command.channel_id
                ),
                policies=self.snapshot.policies,
            )
        self._operations.mark_catalog_applied(command.operation_id)
        return CatalogApplySuccess(operation_id=command.operation_id)

    async def mark_pending_delete(self, operation_id: UUID, channel_id: UUID) -> CatalogPendingDeleteResult:
        self._events.append("catalog:pending-delete")
        self.snapshot = self.snapshot.model_copy(
            update={
                "channels": tuple(
                    channel.model_copy(update={"administrative_state": AdministrativeState.PENDING_DELETE})
                    if channel.channel_id == channel_id
                    else channel
                    for channel in self.snapshot.channels
                )
            }
        )
        return CatalogPendingDeleteSuccess(operation_id=operation_id)


class RecordingSynchronizer:
    def __init__(self, events: list[str], fail: bool = False) -> None:
        self._events: Final = events
        self._fail: Final = fail
        self._fail_next_update = False
        self.created_keys: tuple[str, ...] = ()
        self.created_deployments: tuple[str, ...] = ()
        self.updated_deployments: tuple[str, ...] = ()
        self.managed_deletes: tuple[str, ...] = ()
        self.external_deletes: tuple[str, ...] = ()
        self.discovered_deployments: tuple[ManagedDeploymentMarker, ...] = ()

    async def create_deployment(
        self,
        operation: SyncOperation,
        binding: DesiredBinding,
        api_base: str,
        api_key: SecretStr,
    ) -> LiteLLMSyncSuccess | LiteLLMSyncFailure:
        self._events.append("litellm:create")
        self.created_keys = (*self.created_keys, api_key.get_secret_value())
        self.created_deployments = (*self.created_deployments, binding.litellm_deployment_id)
        if self._fail:
            return LiteLLMSyncFailure(
                action=LiteLLMSyncAction.CREATE,
                failure=SafeSyncFailure(code="upstream_status", message="LiteLLM request failed"),
                retryable=True,
            )
        return LiteLLMSyncSuccess(
            action=LiteLLMSyncAction.CREATE,
            litellm_deployment_id=binding.litellm_deployment_id,
        )

    async def update_deployment(
        self,
        operation: SyncOperation,
        binding: DesiredBinding,
        api_base: str,
        api_key: SecretStr | None = None,
    ) -> LiteLLMSyncSuccess | LiteLLMSyncFailure:
        self._events.append("litellm:update")
        self.updated_deployments = (*self.updated_deployments, binding.litellm_deployment_id)
        if self._fail_next_update:
            self._fail_next_update = False
            return LiteLLMSyncFailure(
                action=LiteLLMSyncAction.UPDATE,
                failure=SafeSyncFailure(code="transport_failed", message="LiteLLM management request failed"),
                retryable=True,
            )
        return LiteLLMSyncSuccess(
            action=LiteLLMSyncAction.UPDATE,
            litellm_deployment_id=binding.litellm_deployment_id,
        )

    def fail_next_update(self) -> None:
        self._fail_next_update = True

    async def delete_managed_deployment(self, binding: DesiredBinding) -> LiteLLMSyncSuccess:
        self._events.append("litellm:delete-managed")
        self.managed_deletes = (*self.managed_deletes, binding.litellm_deployment_id)
        return LiteLLMSyncSuccess(
            action=LiteLLMSyncAction.DELETE,
            litellm_deployment_id=binding.litellm_deployment_id,
        )

    async def delete_external_deployment(self, deletion: ExternalDeploymentDelete) -> LiteLLMSyncSuccess:
        self._events.append("litellm:delete-external")
        self.external_deletes = (*self.external_deletes, deletion.litellm_deployment_id)
        return LiteLLMSyncSuccess(
            action=LiteLLMSyncAction.DELETE_EXTERNAL,
            litellm_deployment_id=deletion.litellm_deployment_id,
        )

    async def list_managed_deployments(self) -> ManagedDeploymentListSuccess:
        return ManagedDeploymentListSuccess(deployments=self.discovered_deployments)


class RecordingCatalogProjection:
    async def projected_config(self) -> PoolConfig:
        return PoolConfig()


class RecordingScheduler:
    def __init__(self, events: list[str], fail_once: bool = False) -> None:
        self._events: Final = events
        self._fail_once = fail_once
        self._account_id = "runtime-account"
        self._inflight = 0

    async def reconfigure(self, config: PoolConfig) -> None:
        assert config == PoolConfig()
        if self._fail_once:
            self._fail_once = False
            raise RuntimeError("runtime unavailable")
        self._events.append("runtime:project")

    async def account_snapshots(self) -> tuple[AccountSnapshot, ...]:
        return (
            AccountSnapshot(
                account_id=self._account_id,
                enabled=False,
                health=Health.UNKNOWN,
                inflight=self._inflight,
                max_concurrency=1,
                cooldown_until=None,
                consecutive_failures=0,
                quota=QuotaSnapshot(unit=QuotaUnit.TOKENS, total=None, five_hour=None, weekly=None),
            ),
        )

    def set_runtime(self, account_id: str, inflight: int) -> None:
        self._account_id = account_id
        self._inflight = inflight


class RecordingAuditRepository:
    def __init__(self, events: list[str]) -> None:
        self._events: Final = events
        self.records: tuple[ManagementAuditRecord, ...] = ()

    async def append(self, record: ManagementAuditRecord) -> AuditWriteSuccess:
        self._events.append("audit:append")
        self.records = (*self.records, record)
        return AuditWriteSuccess(status="created", record=record)

    async def load(self, event_id: UUID) -> AuditLoadResult:
        raise AssertionError("not used")


class RecordingOperationalRepository:
    def __init__(self, events: list[str]) -> None:
        self._events: Final = events

    async def append(self, record: OperationalEventRecord) -> OperationalWriteResult:
        self._events.append(f"operational:{record.event.event_type.value}")
        return OperationalWriteSuccess(status="created", record=record)


def _service(fail_sync: bool = False, fail_projection_once: bool = False):
    events: Final[list[str]] = []
    operations: Final = RecordingOperationRepository(events)
    catalog: Final = RecordingCatalogRepository(events, operations)
    synchronizer: Final = RecordingSynchronizer(events, fail_sync)
    audit: Final = RecordingAuditRepository(events)
    scheduler: Final = RecordingScheduler(events, fail_projection_once)
    service: Final = ChannelManagementService(
        catalog_repository=catalog,
        operations=operations,
        synchronizer=synchronizer,
        runtime_projector=RuntimeProjector(
            RecordingCatalogProjection(),
            scheduler,
        ),
        audit=audit,
        operational_events=RecordingOperationalRepository(events),
        clock=lambda: _NOW,
    )
    return service, events, operations, catalog, synchronizer, audit, scheduler


def _actor(action: ActorAction) -> ActorContext:
    return ActorContext(
        user_id="admin-user",
        role="proxy_admin",
        request_id="request-123",
        action=action,
        envelope_id=UUID("50000000-0000-0000-0000-000000000001"),
    )


def _create_request(
    ownership: BindingOwnership = BindingOwnership.POOL_MANAGED,
) -> ChannelMutation:
    return ChannelMutation(
        display_name="Primary",
        provider="openai_compatible",
        model_discovery_provider_id="openai_compatible",
        base_url_display="https://provider.example/v1",
        api_key=SecretStr("provider-secret"),
        bindings=(
            ChannelBindingMutation(
                public_model="public-model",
                provider_model="openai/provider-model",
                litellm_deployment_id=(
                    "stable-deployment-id" if ownership == BindingOwnership.EXTERNALLY_MANAGED else None
                ),
                ownership=ownership,
            ),
        ),
    )


async def test_create_persists_before_litellm_then_applies_projects_and_audits() -> None:
    service, events, _, catalog, synchronizer, audit, _ = _service()

    result: Final = await service.create(
        _create_request(),
        "create-idempotency",
        _actor(ActorAction.CHANNEL_CREATE),
    )

    assert isinstance(result, ChannelOperationView)
    assert result.operation_status == SyncStatus.APPLIED
    assert events == [
        "operation:create",
        "operation:attempt",
        "litellm:create",
        "catalog:apply",
        "runtime:project",
        "audit:append",
    ]
    assert synchronizer.created_keys == ("provider-secret",)
    assert len(catalog.commands) == 1
    create_command: Final = catalog.commands[0]
    assert isinstance(create_command, ApplyChannelCreate)
    assert create_command.channel.model_discovery_provider_id == "openai_compatible"
    assert create_command.channel.parser_provider_id is None
    assert audit.records[0].audit.actor_action == ActorAction.CHANNEL_CREATE
    serialized: Final = result.model_dump_json()
    assert "provider-secret" not in serialized
    assert "api_key" not in serialized


async def test_upstream_failure_leaves_catalog_and_runtime_unchanged() -> None:
    service, events, _, catalog, _, audit, _ = _service(fail_sync=True)

    result: Final = await service.create(
        _create_request(),
        "failed-create",
        _actor(ActorAction.CHANNEL_CREATE),
    )

    assert isinstance(result, ChannelOperationView)
    assert result.operation_status == SyncStatus.FAILED
    assert result.requires_key
    assert result.failure is not None and result.failure.code == "upstream_status"
    assert catalog.commands == ()
    assert "runtime:project" not in events
    assert events == [
        "operation:create",
        "operation:attempt",
        "litellm:create",
        "operation:failed",
        "audit:append",
    ]
    assert audit.records[0].event.reason_code is None

    reconcile_result: Final = await service.reconcile_pending()
    assert isinstance(reconcile_result, ReconcilePassResult)
    assert reconcile_result.items[0].status == "requires_key"
    assert events.count("litellm:create") == 1
    assert events[-1] == "operational:sync_retry_deferred"


async def test_external_deployment_delete_uses_separate_confirmed_operation() -> None:
    service, _, _, catalog, synchronizer, _, _ = _service()
    imported: Final = await service.import_channel(
        _create_request(BindingOwnership.EXTERNALLY_MANAGED).model_copy(update={"api_key": None}),
        "import-external",
        _actor(ActorAction.CHANNEL_IMPORT),
    )
    assert isinstance(imported, ChannelOperationView)
    applied_command: Final = catalog.commands[0]
    assert isinstance(applied_command, ApplyChannelImport)
    catalog.snapshot = CatalogSnapshot(
        channels=(applied_command.channel,),
        bindings=applied_command.bindings,
    )
    binding_id: Final = applied_command.bindings[0].binding_id

    deleted: Final = await service.delete_external(
        imported.channel_id,
        binding_id,
        ExternalDeploymentDeleteRequest(confirmed=True),
        "delete-external",
        _actor(ActorAction.CHANNEL_DELETE_EXTERNAL_DEPLOYMENT),
    )

    assert isinstance(deleted, ChannelOperationView)
    assert deleted.operation_status == SyncStatus.APPLIED
    assert synchronizer.external_deletes == ("stable-deployment-id",)


async def test_update_syncs_active_bindings_by_ownership_and_deletes_only_retired_managed_bindings() -> None:
    service, _, _, catalog, synchronizer, _, _ = _service()
    created: Final = await service.create(
        _create_request(),
        "create-for-update",
        _actor(ActorAction.CHANNEL_CREATE),
    )
    assert isinstance(created, ChannelOperationView)
    create_command: Final = catalog.commands[0]
    assert isinstance(create_command, ApplyChannelCreate)
    retained: Final = create_command.bindings[0]
    retired: Final = retained.model_copy(
        update={
            "binding_id": UUID("60000000-0000-0000-0000-000000000001"),
            "litellm_deployment_id": "retired-managed-deployment",
            "public_model": "retired-model",
        }
    )
    catalog.snapshot = CatalogSnapshot(
        channels=(create_command.channel,),
        bindings=(retained, retired),
    )
    request: Final = ChannelMutation(
        display_name="Updated",
        provider="openai_compatible",
        model_discovery_provider_id="gemini",
        base_url_display="https://provider.example/v1",
        api_key=SecretStr("replacement-secret"),
        bindings=(
            ChannelBindingMutation(
                binding_id=retained.binding_id,
                public_model=retained.public_model,
                provider_model=retained.provider_model,
                litellm_deployment_id=retained.litellm_deployment_id,
                ownership=BindingOwnership.POOL_MANAGED,
            ),
            ChannelBindingMutation(
                public_model="new-model",
                provider_model="openai/new-model",
                ownership=BindingOwnership.POOL_MANAGED,
            ),
            ChannelBindingMutation(
                public_model="external-model",
                provider_model="openai/external-model",
                litellm_deployment_id="external-deployment",
                ownership=BindingOwnership.EXTERNALLY_MANAGED,
            ),
        ),
    )

    updated: Final = await service.update(
        created.channel_id,
        request,
        "update-bindings",
        _actor(ActorAction.CHANNEL_UPDATE),
    )

    assert isinstance(updated, ChannelOperationView)
    update_command: Final = catalog.commands[-1]
    assert isinstance(update_command, ApplyChannelUpdate)
    assert update_command.channel.model_discovery_provider_id == "gemini"
    assert tuple(binding.public_model for binding in update_command.bindings) == (
        "public-model",
        "new-model",
        "external-model",
    )
    assert synchronizer.updated_deployments == (retained.litellm_deployment_id,)
    assert synchronizer.created_deployments[-1] == update_command.bindings[1].litellm_deployment_id
    assert synchronizer.managed_deletes == (retired.litellm_deployment_id,)
    assert "external-deployment" not in synchronizer.updated_deployments
    assert "external-deployment" not in synchronizer.created_deployments


async def test_runtime_projection_failure_keeps_catalog_operation_applied_and_retries_projection() -> None:
    service, events, operations, catalog, _, audit, _ = _service(fail_projection_once=True)

    failed_projection: Final = await service.create(
        _create_request(),
        "projection-retry",
        _actor(ActorAction.CHANNEL_CREATE),
    )

    assert failed_projection == ChannelManagementFailure(code="runtime_projection_failed", retryable=True)
    loaded_operation: Final = await operations.load(catalog.commands[0].operation_id)
    assert loaded_operation.operation.status == SyncStatus.APPLIED
    assert len(catalog.commands) == 1
    assert "operation:failed" not in events
    assert audit.records[-1].audit.outcome.value == "failed"

    retried: Final = await service.create(
        _create_request(),
        "projection-retry",
        _actor(ActorAction.CHANNEL_CREATE),
    )

    assert isinstance(retried, ChannelOperationView)
    assert retried.status == "existing"
    assert retried.operation_status == SyncStatus.APPLIED
    assert len(catalog.commands) == 1
    assert events.count("runtime:project") == 1
    assert audit.records[-1].audit.outcome.value == "succeeded"


async def test_background_reconciler_retries_keyless_update_with_system_audit_identity() -> None:
    service, events, _, catalog, synchronizer, audit, _ = _service()
    created: Final = await service.create(
        _create_request(),
        "create-before-reconcile",
        _actor(ActorAction.CHANNEL_CREATE),
    )
    assert isinstance(created, ChannelOperationView)
    create_command: Final = catalog.commands[0]
    assert isinstance(create_command, ApplyChannelCreate)
    retained: Final = create_command.bindings[0]
    catalog.snapshot = CatalogSnapshot(channels=(create_command.channel,), bindings=(retained,))
    synchronizer.fail_next_update()
    update_request: Final = ChannelMutation(
        display_name="Updated without key rotation",
        provider="openai_compatible",
        base_url_display="https://provider.example/v1",
        bindings=(
            ChannelBindingMutation(
                binding_id=retained.binding_id,
                public_model=retained.public_model,
                provider_model=retained.provider_model,
                litellm_deployment_id=retained.litellm_deployment_id,
                ownership=BindingOwnership.POOL_MANAGED,
            ),
        ),
    )
    failed_update: Final = await service.update(
        created.channel_id,
        update_request,
        "retry-keyless-update",
        _actor(ActorAction.CHANNEL_UPDATE),
    )
    assert isinstance(failed_update, ChannelOperationView)
    assert failed_update.operation_status == SyncStatus.FAILED
    assert not failed_update.requires_key

    synchronizer.fail_next_update()
    failed_reconcile: Final = await service.reconcile_pending()

    assert isinstance(failed_reconcile, ReconcilePassResult)
    assert failed_reconcile.items[0].status == "failed"
    assert events[-1] == "operational:sync_retry_failed"

    reconciled: Final = await service.reconcile_pending()

    assert isinstance(reconciled, ReconcilePassResult)
    assert reconciled.inspected == 1
    assert reconciled.items[0].status == "applied"
    assert synchronizer.updated_deployments == (
        retained.litellm_deployment_id,
        retained.litellm_deployment_id,
        retained.litellm_deployment_id,
    )
    assert events[-1] == "operational:sync_retry_succeeded"
    assert audit.records[-1].event.actor_type == "system"
    assert audit.records[-1].audit.actor_role == "system"
    assert audit.records[-1].audit.actor_action == ActorAction.CHANNEL_RECONCILE


async def test_delete_stays_pending_until_inflight_requests_are_released() -> None:
    service, _, _, catalog, synchronizer, _, scheduler = _service()
    request: Final = _create_request().model_copy(update={"legacy_account_id": "waiting-channel"})
    created: Final = await service.create(
        request,
        "create-before-delete",
        _actor(ActorAction.CHANNEL_CREATE),
    )
    assert isinstance(created, ChannelOperationView)
    create_command: Final = catalog.commands[0]
    assert isinstance(create_command, ApplyChannelCreate)
    catalog.snapshot = CatalogSnapshot(
        channels=(create_command.channel,),
        bindings=create_command.bindings,
    )
    scheduler.set_runtime("waiting-channel", inflight=1)

    pending: Final = await service.delete(
        created.channel_id,
        ChannelDeleteRequest(delete_mode=DeleteMode.DELETE_MANAGED_DEPLOYMENT),
        "delete-after-drain",
        _actor(ActorAction.CHANNEL_DELETE),
    )

    assert isinstance(pending, ChannelOperationView)
    assert pending.operation_status == SyncStatus.PENDING_DELETE
    assert catalog.snapshot.channels[0].administrative_state.value == "pending_delete"
    assert synchronizer.managed_deletes == ()
    assert not any(isinstance(command, ApplyChannelDelete) for command in catalog.commands)

    scheduler.set_runtime("waiting-channel", inflight=0)
    reconciled: Final = await service.reconcile_pending()

    assert isinstance(reconciled, ReconcilePassResult)
    assert reconciled.items[0].status == "applied"
    assert synchronizer.managed_deletes == (create_command.bindings[0].litellm_deployment_id,)
    assert isinstance(catalog.commands[-1], ApplyChannelDelete)
    assert catalog.snapshot.channels == ()


async def test_reconciler_reports_account_pool_marked_deployment_without_catalog_binding() -> None:
    service, _, _, _, synchronizer, _, _ = _service()
    orphan: Final = ManagedDeploymentMarker(
        litellm_deployment_id="orphan-deployment",
        channel_id=UUID("70000000-0000-0000-0000-000000000001"),
        binding_id=UUID("70000000-0000-0000-0000-000000000002"),
        operation_id=UUID("70000000-0000-0000-0000-000000000003"),
    )
    synchronizer.discovered_deployments = (orphan,)

    result: Final = await service.reconcile_pending()

    assert isinstance(result, ReconcilePassResult)
    assert result.inspected == 0
    assert result.orphan_deployments == (orphan,)
    assert result.orphan_scan_failure_code is None
