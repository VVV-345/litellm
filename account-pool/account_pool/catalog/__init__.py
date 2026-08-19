"""汇总导出渠道目录的数据模型、仓储实现和应用服务。"""

from account_pool.catalog.identity import legacy_binding_id, legacy_channel_id
from account_pool.catalog.models import (
    AdministrativeState,
    BindingOwnership,
    CatalogImport,
    CatalogSnapshot,
    ChannelList,
    ChannelRecord,
    ChannelSummary,
    DeploymentBindingRecord,
    ImportConflict,
    ImportResult,
    ModelPolicyRecord,
)
from account_pool.catalog.postgres import PostgresCatalogRepository
from account_pool.catalog.query import ChannelCatalogQueryService, ChannelCatalogReader
from account_pool.catalog.repository import CatalogRepository
from account_pool.catalog.service import CatalogService

__all__ = [
    "AdministrativeState",
    "BindingOwnership",
    "CatalogImport",
    "CatalogRepository",
    "CatalogService",
    "CatalogSnapshot",
    "ChannelCatalogQueryService",
    "ChannelCatalogReader",
    "ChannelList",
    "ChannelRecord",
    "ChannelSummary",
    "DeploymentBindingRecord",
    "ImportConflict",
    "ImportResult",
    "ModelPolicyRecord",
    "PostgresCatalogRepository",
    "legacy_binding_id",
    "legacy_channel_id",
]
