"""本模块声明 Google Antigravity 的 CLIProxyAPI 静态供应商契约。"""

from __future__ import annotations

from typing import Final

from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition, parse_empty_quota
from account_pool.domain import AuthorizationFlow, SupplierKind


DEFINITION: Final = SupplierDefinition(
    kind=SupplierKind.GOOGLE_ANTIGRAVITY,
    authorization_flow=AuthorizationFlow.BROWSER_OAUTH,
    authorization_path="/v0/management/antigravity-auth-url",
    callback_provider_key="antigravity",
    auth_file_provider_key="antigravity",
    excluded_models_key="antigravity",
    callback_port=51121,
    callback_path="/oauth-callback",
    quota_parser=parse_empty_quota,
)
