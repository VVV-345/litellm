from typing import Final

from pydantic import AwareDatetime

from account_pool.catalog.importer import catalog_import_from_pool_config
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
