"""声明渠道目录仓储协议，隔离业务服务与具体数据库实现。"""

from typing import Protocol

from account_pool.catalog.models import CatalogImport, CatalogSnapshot, ImportResult


class CatalogRepository(Protocol):
    async def load_snapshot(self) -> CatalogSnapshot: ...

    async def import_once(self, command: CatalogImport) -> ImportResult: ...
