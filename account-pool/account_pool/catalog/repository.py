from typing import Protocol

from account_pool.catalog.models import CatalogImport, CatalogSnapshot, ImportResult


class CatalogRepository(Protocol):
    async def load_snapshot(self) -> CatalogSnapshot: ...

    async def import_once(self, command: CatalogImport) -> ImportResult: ...
