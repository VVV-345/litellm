"""本模块声明 Anthropic Claude 的 CLIProxyAPI 静态供应商契约。"""

from __future__ import annotations

from typing import Final

from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition, parse_empty_quota
from account_pool.domain import AuthorizationFlow, SupplierKind


DEFINITION: Final = SupplierDefinition(
    kind=SupplierKind.ANTHROPIC_CLAUDE,
    authorization_flow=AuthorizationFlow.BROWSER_OAUTH,
    authorization_path="/v0/management/anthropic-auth-url",
    callback_provider_key="anthropic",
    auth_file_provider_key="claude",
    excluded_models_key="claude",
    callback_port=54545,
    callback_path="/callback",
    quota_parser=parse_empty_quota,
)
