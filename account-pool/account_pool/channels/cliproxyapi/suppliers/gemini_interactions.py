"""本模块声明 Gemini Interactions API Key 的 CLIProxyAPI 静态供应商契约。"""

from __future__ import annotations

from typing import Final

from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.domain import AuthorizationFlow, SupplierKind
from account_pool.quota import parse_provider_quota

DEFINITION: Final = SupplierDefinition(
    kind=SupplierKind.GEMINI_INTERACTIONS,
    authorization_flow=AuthorizationFlow.DIRECT_CREDENTIAL,
    authorization_path="",
    callback_provider_key="gemini-interactions",
    auth_file_provider_key="gemini-interactions",
    excluded_models_key="interactions",
    callback_port=None,
    callback_path=None,
    quota_parser=lambda observation: parse_provider_quota(
        observation, ("gemini-", "interactions-"), ("gemini-plan-type", "plan_type", "plan-type")
    ),
)
