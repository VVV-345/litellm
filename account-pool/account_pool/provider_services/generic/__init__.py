"""提供通用解析器的模型、价格和管理员重试能力。"""

from account_pool.provider_services.generic.parser import parse_generic_result
from account_pool.provider_services.generic.service import GenericProviderService

__all__ = ("GenericProviderService", "parse_generic_result")
