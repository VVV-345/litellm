"""New API 自托管网关渠道模块。"""

from account_pool.provider_services.new_api.parser import parse_new_api_result
from account_pool.provider_services.new_api.service import NewApiProviderService

__all__ = ("NewApiProviderService", "parse_new_api_result")
