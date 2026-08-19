"""编排旧配置导入和渠道目录到运行配置的投影。"""

from typing import Final
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.lifecycle import (
    ApplyChannelCreate,
    ApplyChannelDelete,
    ApplyChannelDetach,
    ApplyChannelImport,
    ApplyChannelUpdate,
    ApplyExternalBindingDelete,
    CatalogApplyFailure,
    CatalogApplyFailureCode,
    CatalogLifecycleCommand,
    CatalogLifecycleResult,
    CatalogPendingDeleteResult,
    ExternalSyncFailure,
    ExternalSyncResult,
)
from account_pool.catalog.models import ImportResult
from account_pool.catalog.projection import project_pool_config
from account_pool.catalog.repository import CatalogRepository
from account_pool.models import PoolConfig


class CatalogService:
    def __init__(self, repository: CatalogRepository) -> None:
        self._repository: Final = repository

    async def import_legacy_config(
        self,
        config: PoolConfig,
        imported_at: AwareDatetime,
    ) -> ImportResult:
        command: Final = catalog_import_from_pool_config(config=config, imported_at=imported_at)
        return await self._repository.import_once(command)

    async def projected_config(self) -> PoolConfig:
        snapshot: Final = await self._repository.load_snapshot()
        return project_pool_config(snapshot)

    async def mark_pending_delete(self, operation_id: UUID, channel_id: UUID) -> CatalogPendingDeleteResult:
        return await self._repository.mark_pending_delete(operation_id, channel_id)

    async def apply_channel_create(
        self,
        command: ApplyChannelCreate,
        external_result: ExternalSyncResult,
    ) -> CatalogLifecycleResult:
        return await self._apply_after_external_success(command, external_result)

    async def apply_channel_update(
        self,
        command: ApplyChannelUpdate,
        external_result: ExternalSyncResult,
    ) -> CatalogLifecycleResult:
        return await self._apply_after_external_success(command, external_result)

    async def apply_channel_import(
        self,
        command: ApplyChannelImport,
        external_result: ExternalSyncResult,
    ) -> CatalogLifecycleResult:
        return await self._apply_after_external_success(command, external_result)

    async def apply_channel_detach(
        self,
        command: ApplyChannelDetach,
        external_result: ExternalSyncResult,
    ) -> CatalogLifecycleResult:
        return await self._apply_after_external_success(command, external_result)

    async def apply_channel_delete(
        self,
        command: ApplyChannelDelete,
        external_result: ExternalSyncResult,
    ) -> CatalogLifecycleResult:
        return await self._apply_after_external_success(command, external_result)

    async def apply_external_binding_delete(
        self,
        command: ApplyExternalBindingDelete,
        external_result: ExternalSyncResult,
    ) -> CatalogLifecycleResult:
        return await self._apply_after_external_success(command, external_result)

    async def apply_lifecycle(
        self,
        command: CatalogLifecycleCommand,
        external_result: ExternalSyncResult,
    ) -> CatalogLifecycleResult:
        return await self._apply_after_external_success(command, external_result)

    async def _apply_after_external_success(
        self,
        command: CatalogLifecycleCommand,
        external_result: ExternalSyncResult,
    ) -> CatalogLifecycleResult:
        if isinstance(external_result, ExternalSyncFailure):
            return external_result
        if external_result.operation_id != command.operation_id:
            return CatalogApplyFailure(
                operation_id=command.operation_id,
                code=CatalogApplyFailureCode.OPERATION_MISMATCH,
                retryable=False,
            )
        return await self._repository.apply_lifecycle(command)
