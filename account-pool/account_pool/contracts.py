"""本模块集中定义 Manager 对外公开的号池响应 contract。"""

from typing import TypeAlias

from account_pool.domain import (
    AuthorizationView,
    EnvironmentView,
    GatewayEnvironment,
    ProxyProfile,
    QuotaSnapshot,
    QuotaWindow,
)

PublicAuthorization: TypeAlias = AuthorizationView
PublicEnvironment: TypeAlias = EnvironmentView
GatewaySnapshot: TypeAlias = GatewayEnvironment
PublicProxyProfile: TypeAlias = ProxyProfile
PublicQuotaSnapshot: TypeAlias = QuotaSnapshot
PublicQuotaWindow: TypeAlias = QuotaWindow

__all__ = [
    "AuthorizationView",
    "EnvironmentView",
    "GatewayEnvironment",
    "ProxyProfile",
    "QuotaSnapshot",
    "QuotaWindow",
]
