from datetime import UTC, datetime
from typing import Final

from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.models import CatalogImport, CatalogSnapshot, ImportConflict, ImportResult
from account_pool.catalog.service import CatalogService
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


def _snapshot(imported_at: datetime) -> CatalogSnapshot:
    command: Final = catalog_import_from_pool_config(legacy_config(), imported_at)
    return CatalogSnapshot(channels=command.channels, bindings=command.bindings, policies=command.policies)


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
    assert projected == legacy_config()
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

    assert await service.projected_config() == legacy_config()
    assert (repository.import_calls, repository.load_calls) == (0, 1)
