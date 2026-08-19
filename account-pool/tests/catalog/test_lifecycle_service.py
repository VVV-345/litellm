"""验证外部同步成功后才原子应用渠道目录生命周期变更。"""

from typing import Final, Literal
from uuid import UUID, uuid4

import pytest
from account_pool.catalog.lifecycle import (
    ApplyChannelCreate,
    ApplyChannelDelete,
    ApplyChannelDetach,
    ApplyChannelImport,
    ApplyChannelUpdate,
    ApplyExternalBindingDelete,
    CatalogApplyFailure,
    CatalogApplyFailureCode,
    CatalogApplySuccess,
    CatalogLifecycleCommand,
    CatalogLifecycleResult,
    CatalogPendingDeleteResult,
    CatalogPendingDeleteSuccess,
    DeleteMode,
    ExternalSyncFailure,
    ExternalSyncSuccess,
)
from account_pool.catalog.models import (
    BindingOwnership,
    CatalogImport,
    CatalogSnapshot,
    ChannelRecord,
    DeploymentBindingRecord,
    ImportResult,
)
from account_pool.catalog.service import CatalogService
from pydantic import ValidationError
from tests.catalog.test_projection import imported_snapshot


class RecordingCatalogRepository:
    def __init__(self) -> None:
        self.applied: tuple[CatalogLifecycleCommand, ...] = ()

    async def load_snapshot(self) -> CatalogSnapshot:
        return CatalogSnapshot()

    async def import_once(self, command: CatalogImport) -> ImportResult:
        raise AssertionError("not used")

    async def apply_lifecycle(self, command: CatalogLifecycleCommand) -> CatalogApplySuccess:
        self.applied = (*self.applied, command)
        return CatalogApplySuccess(operation_id=command.operation_id)

    async def mark_pending_delete(self, operation_id: UUID, channel_id: UUID) -> CatalogPendingDeleteResult:
        return CatalogPendingDeleteSuccess(operation_id=operation_id)


async def _apply_named_command(
    service: CatalogService,
    method_name: Literal[
        "apply_channel_create",
        "apply_channel_update",
        "apply_channel_import",
        "apply_channel_detach",
        "apply_channel_delete",
        "apply_external_binding_delete",
    ],
    command: CatalogLifecycleCommand,
    failure: ExternalSyncFailure,
) -> CatalogLifecycleResult:
    if method_name == "apply_channel_create":
        assert isinstance(command, ApplyChannelCreate)
        return await service.apply_channel_create(command, failure)
    if method_name == "apply_channel_update":
        assert isinstance(command, ApplyChannelUpdate)
        return await service.apply_channel_update(command, failure)
    if method_name == "apply_channel_import":
        assert isinstance(command, ApplyChannelImport)
        return await service.apply_channel_import(command, failure)
    if method_name == "apply_channel_detach":
        assert isinstance(command, ApplyChannelDetach)
        return await service.apply_channel_detach(command, failure)
    if method_name == "apply_channel_delete":
        assert isinstance(command, ApplyChannelDelete)
        return await service.apply_channel_delete(command, failure)
    assert isinstance(command, ApplyExternalBindingDelete)
    return await service.apply_external_binding_delete(command, failure)


def _records() -> tuple[ChannelRecord, DeploymentBindingRecord, DeploymentBindingRecord]:
    _, snapshot = imported_snapshot()
    return snapshot.channels[0], snapshot.bindings[0], snapshot.bindings[1]


@pytest.mark.parametrize(
    "method_name",
    [
        "apply_channel_create",
        "apply_channel_update",
        "apply_channel_import",
        "apply_channel_detach",
        "apply_channel_delete",
        "apply_external_binding_delete",
    ],
)
async def test_failed_external_sync_never_changes_applied_catalog(
    method_name: Literal[
        "apply_channel_create",
        "apply_channel_update",
        "apply_channel_import",
        "apply_channel_detach",
        "apply_channel_delete",
        "apply_external_binding_delete",
    ],
) -> None:
    channel, external_binding, managed_binding = _records()
    operation_id: Final = uuid4()
    commands: Final = {
        "apply_channel_create": ApplyChannelCreate(
            operation_id=operation_id, channel=channel, bindings=(managed_binding,)
        ),
        "apply_channel_update": ApplyChannelUpdate(
            operation_id=operation_id, channel=channel, bindings=(external_binding, managed_binding)
        ),
        "apply_channel_import": ApplyChannelImport(
            operation_id=operation_id, channel=channel, bindings=(external_binding,)
        ),
        "apply_channel_detach": ApplyChannelDetach(
            operation_id=operation_id, channel_id=channel.channel_id, bindings=(external_binding, managed_binding)
        ),
        "apply_channel_delete": ApplyChannelDelete(
            operation_id=operation_id,
            channel_id=channel.channel_id,
            mode=DeleteMode.DELETE_MANAGED_DEPLOYMENT,
            bindings=(external_binding, managed_binding),
        ),
        "apply_external_binding_delete": ApplyExternalBindingDelete(
            operation_id=operation_id, channel_id=channel.channel_id, binding=external_binding
        ),
    }
    repository: Final = RecordingCatalogRepository()
    service: Final = CatalogService(repository)
    failure: Final = ExternalSyncFailure(operation_id=operation_id, code="upstream_unavailable", retryable=True)

    result: Final = await _apply_named_command(service, method_name, commands[method_name], failure)

    assert result is failure
    assert repository.applied == ()


async def test_successful_external_sync_applies_each_lifecycle_command_atomically() -> None:
    channel, external_binding, managed_binding = _records()
    operation_ids: Final = tuple(uuid4() for _ in range(6))
    commands: Final = (
        ApplyChannelCreate(operation_id=operation_ids[0], channel=channel, bindings=(managed_binding,)),
        ApplyChannelUpdate(
            operation_id=operation_ids[1], channel=channel, bindings=(external_binding, managed_binding)
        ),
        ApplyChannelImport(operation_id=operation_ids[2], channel=channel, bindings=(external_binding,)),
        ApplyChannelDetach(
            operation_id=operation_ids[3], channel_id=channel.channel_id, bindings=(external_binding, managed_binding)
        ),
        ApplyChannelDelete(
            operation_id=operation_ids[4],
            channel_id=channel.channel_id,
            mode=DeleteMode.DELETE_MANAGED_DEPLOYMENT,
            bindings=(external_binding, managed_binding),
        ),
        ApplyExternalBindingDelete(
            operation_id=operation_ids[5], channel_id=channel.channel_id, binding=external_binding
        ),
    )
    repository: Final = RecordingCatalogRepository()
    service: Final = CatalogService(repository)
    results: Final = (
        await service.apply_channel_create(
            commands[0], ExternalSyncSuccess(operation_id=commands[0].operation_id)
        ),
        await service.apply_channel_update(
            commands[1], ExternalSyncSuccess(operation_id=commands[1].operation_id)
        ),
        await service.apply_channel_import(
            commands[2], ExternalSyncSuccess(operation_id=commands[2].operation_id)
        ),
        await service.apply_channel_detach(
            commands[3], ExternalSyncSuccess(operation_id=commands[3].operation_id)
        ),
        await service.apply_channel_delete(
            commands[4], ExternalSyncSuccess(operation_id=commands[4].operation_id)
        ),
        await service.apply_external_binding_delete(
            commands[5], ExternalSyncSuccess(operation_id=commands[5].operation_id)
        ),
    )

    assert tuple(result.status for result in results) == ("applied",) * 6
    assert repository.applied == commands


async def test_mismatched_external_success_is_rejected_without_repository_call() -> None:
    channel, _, managed_binding = _records()
    command: Final = ApplyChannelCreate(operation_id=uuid4(), channel=channel, bindings=(managed_binding,))
    repository: Final = RecordingCatalogRepository()
    service: Final = CatalogService(repository)

    result: Final = await service.apply_channel_create(command, ExternalSyncSuccess(operation_id=uuid4()))

    assert result == CatalogApplyFailure(
        operation_id=command.operation_id,
        code=CatalogApplyFailureCode.OPERATION_MISMATCH,
        retryable=False,
    )
    assert repository.applied == ()


def test_lifecycle_commands_preserve_binding_ownership_and_are_immutable() -> None:
    channel, external_binding, managed_binding = _records()
    command: Final = ApplyChannelDelete(
        operation_id=uuid4(),
        channel_id=channel.channel_id,
        mode=DeleteMode.DELETE_MANAGED_DEPLOYMENT,
        bindings=(external_binding, managed_binding),
    )

    assert tuple(binding.ownership for binding in command.bindings) == (
        BindingOwnership.EXTERNALLY_MANAGED,
        BindingOwnership.POOL_MANAGED,
    )
    with pytest.raises(ValidationError):
        command.mode = DeleteMode.DETACH_ONLY


def test_external_binding_delete_requires_matching_external_binding() -> None:
    channel, external_binding, managed_binding = _records()

    with pytest.raises(ValidationError, match="externally managed"):
        ApplyExternalBindingDelete(
            operation_id=uuid4(), channel_id=channel.channel_id, binding=managed_binding
        )
    with pytest.raises(ValidationError, match="channel"):
        ApplyExternalBindingDelete(
            operation_id=uuid4(), channel_id=uuid4(), binding=external_binding
        )


def test_channel_commands_reject_bindings_from_another_channel() -> None:
    _, external_binding, _ = _records()
    wrong_channel_id: Final = UUID("00000000-0000-0000-0000-000000000001")

    with pytest.raises(ValidationError, match="channel"):
        ApplyChannelDetach(
            operation_id=uuid4(), channel_id=wrong_channel_id, bindings=(external_binding,)
        )
