"""验证渠道目录服务对导入仓储和运行配置投影的编排。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.lifecycle import CatalogApplyResult, CatalogLifecycleCommand, CatalogPendingDeleteResult
from account_pool.catalog.models import CatalogImport, CatalogSnapshot, ImportConflict, ImportResult
from account_pool.catalog.service import CatalogService
from account_pool.models import PoolConfig
from tests.catalog.test_importer import legacy_config


class FakeCatalogRepository:
    def __init__(self, snapshot: CatalogSnapshot, import_result: ImportResult) -> None:
        self._snapshot: Final = snapshot
        self._import_result: Final = import_result
        self.last_import: CatalogImport | None = None
        self.load_calls: int = 0
        self.import_calls: int = 0

    async def load_snapshot(self) -> CatalogSnapshot:
        self.load_calls += 1
        return self._snapshot

    async def import_once(self, command: CatalogImport) -> ImportResult:
        self.import_calls += 1
        self.last_import = command
        return self._import_result

    async def apply_lifecycle(self, command: CatalogLifecycleCommand) -> CatalogApplyResult:
        raise AssertionError(f"catalog service must not apply lifecycle action {command.action}")

    async def mark_pending_delete(self, operation_id: UUID, channel_id: UUID) -> CatalogPendingDeleteResult:
        raise AssertionError(f"catalog service must not mark {channel_id} pending for {operation_id}")


def _snapshot(imported_at: datetime) -> CatalogSnapshot:
    command: Final = catalog_import_from_pool_config(legacy_config(), imported_at)
    return CatalogSnapshot(channels=command.channels, bindings=command.bindings, policies=command.policies)


def _assert_projected_legacy_config(projected: PoolConfig) -> None:
    expected: Final = legacy_config()
    without_catalog_ids: Final = projected.model_copy(
        update={
            "accounts": tuple(
                account.model_copy(
                    update={
                        "channel_id": None,
                        "deployments": tuple(
                            deployment.model_copy(update={"binding_id": None}) for deployment in account.deployments
                        ),
                    }
                )
                for account in projected.accounts
            )
        }
    )
    assert without_catalog_ids == expected
    assert all(account.channel_id is not None for account in projected.accounts)
    assert all(
        deployment.binding_id is not None for account in projected.accounts for deployment in account.deployments
    )


async def test_service_imports_and_projects_through_injected_repository() -> None:
    imported_at: Final = datetime(2026, 8, 19, 5, 0, tzinfo=UTC)
    repository: Final = FakeCatalogRepository(
        snapshot=_snapshot(imported_at),
        import_result=ImportResult(status="created", created_channels=2, created_bindings=3, created_policies=2),
    )
    service: Final = CatalogService(repository)

    result: Final = await service.import_legacy_config(legacy_config(), imported_at)
    projected: Final = await service.projected_config()

    assert result.status == "created"
    _assert_projected_legacy_config(projected)
    assert repository.last_import is not None
    assert "provider-secret" not in repository.last_import.model_dump_json()
    assert (repository.import_calls, repository.load_calls) == (1, 1)


async def test_import_returns_structured_conflict_without_loading_snapshot() -> None:
    conflict: Final = ImportResult(
        status="conflict",
        conflicts=(ImportConflict(entity="channel", identity="channel-id", reason="different"),),
    )
    repository: Final = FakeCatalogRepository(snapshot=CatalogSnapshot(), import_result=conflict)
    service: Final = CatalogService(repository)

    result: Final = await service.import_legacy_config(
        legacy_config(),
        datetime(2026, 8, 19, 5, 0, tzinfo=UTC),
    )

    assert result is conflict
    assert (repository.import_calls, repository.load_calls) == (1, 0)


async def test_projection_does_not_import() -> None:
    imported_at: Final = datetime(2026, 8, 19, 5, 0, tzinfo=UTC)
    repository: Final = FakeCatalogRepository(
        snapshot=_snapshot(imported_at),
        import_result=ImportResult(status="unchanged"),
    )
    service: Final = CatalogService(repository)

    _assert_projected_legacy_config(await service.projected_config())
    assert (repository.import_calls, repository.load_calls) == (0, 1)
