"""本模块定义 CLIProxyAPI 供应商的不可变静态契约。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from account_pool.domain import AuthorizationFlow, QuotaSnapshot, SupplierKind
from account_pool.quota import QuotaObservation


@dataclass(frozen=True, slots=True)
class SupplierDefinition:
    kind: SupplierKind
    authorization_flow: AuthorizationFlow
    authorization_path: str
    callback_provider_key: str
    auth_file_provider_key: str
    excluded_models_key: str
    callback_port: int | None
    callback_path: str | None
    quota_parser: Callable[[QuotaObservation], QuotaSnapshot]

    @property
    def uses_oauth_model_exclusions(self) -> bool:
        return self.authorization_flow is not AuthorizationFlow.DIRECT_CREDENTIAL


def parse_empty_quota(observation: QuotaObservation) -> QuotaSnapshot:
    return QuotaSnapshot(observed_at=observation.observed_at)
