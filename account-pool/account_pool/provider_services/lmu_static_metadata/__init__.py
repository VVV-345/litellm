"""注册 LMU 公开静态模型发现服务。"""

from account_pool.provider_services.lmu_static_metadata.parser import parse_lmu_static_metadata_result
from account_pool.provider_services.lmu_static_metadata.service import LmuStaticMetadataProviderService

__all__ = ("LmuStaticMetadataProviderService", "parse_lmu_static_metadata_result")
