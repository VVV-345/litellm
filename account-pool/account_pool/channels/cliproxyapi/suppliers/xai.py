"""本模块声明 xAI 的 CLIProxyAPI 静态供应商契约。"""

from __future__ import annotations

from typing import Final

from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition, parse_empty_quota
from account_pool.domain import AuthorizationFlow, SupplierKind


DEFINITION: Final = SupplierDefinition(
    kind=SupplierKind.XAI,
    authorization_flow=AuthorizationFlow.DEVICE_CODE,
    authorization_path="/v0/management/xai-auth-url",
    callback_provider_key="xai",
    auth_file_provider_key="xai",
    excluded_models_key="xai",
    callback_port=None,
    callback_path=None,
    quota_parser=parse_empty_quota,
)
