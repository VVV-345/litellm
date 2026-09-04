"""本模块声明 OpenAI Codex 的 CLIProxyAPI 静态供应商契约。"""

from __future__ import annotations

from typing import Final

from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.domain import AuthorizationFlow, SupplierKind
from account_pool.quota import parse_quota


DEFINITION: Final = SupplierDefinition(
    kind=SupplierKind.OPENAI_CODEX,
    authorization_flow=AuthorizationFlow.BROWSER_OAUTH,
    authorization_path="/v0/management/codex-auth-url",
    callback_provider_key="codex",
    auth_file_provider_key="codex",
    excluded_models_key="codex",
    callback_port=1455,
    callback_path="/auth/callback",
    quota_parser=parse_quota,
)
