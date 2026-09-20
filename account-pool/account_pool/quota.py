"""保留旧额度导入路径，解析与路由计算分别由供应商层和应用层实现。"""

from __future__ import annotations

from datetime import datetime

from account_pool.application.quota_state import effective_cooldown_until as effective_cooldown_until
from account_pool.application.quota_state import routing_quota_state as routing_quota_state
from account_pool.domain import (
    EnvironmentRecord as EnvironmentRecord,
)
from account_pool.domain import (
    ModelQuotaSnapshot as ModelQuotaSnapshot,
)
from account_pool.domain import (
    ProviderEndpointFailure as ProviderEndpointFailure,
)
from account_pool.domain import (
    QuotaBalance as QuotaBalance,
)
from account_pool.domain import (
    QuotaSnapshot as QuotaSnapshot,
)
from account_pool.domain import (
    QuotaWindow as QuotaWindow,
)
from account_pool.providers.usage.antigravity import AntigravityAssist as AntigravityAssist
from account_pool.providers.usage.antigravity import parse_antigravity_assist as parse_antigravity_assist
from account_pool.providers.usage.antigravity import (
    parse_antigravity_onboard_project as parse_antigravity_onboard_project,
)
from account_pool.providers.usage.antigravity import parse_antigravity_quota as parse_antigravity_quota
from account_pool.providers.usage.contracts import ProviderQuotaError as ProviderQuotaError
from account_pool.providers.usage.contracts import ProviderQuotaRefresh as ProviderQuotaRefresh
from account_pool.providers.usage.contracts import QuotaObservation as QuotaObservation
from account_pool.providers.usage.signals import parse_provider_quota as parse_provider_quota
from account_pool.providers.usage.signals import parse_quota as parse_quota


def parse_xai_billing_quota(
    weekly_body: str | None,
    monthly_body: str | None,
    observed_at: datetime,
    *,
    user_body: str | None = None,
    subscriptions_body: str | None = None,
    task_usage_body: str | None = None,
) -> ProviderQuotaRefresh | None:
    from account_pool.provider_quota import parse_xai_quota

    return parse_xai_quota(
        weekly_body,
        monthly_body,
        observed_at,
        user_body=user_body,
        subscriptions_body=subscriptions_body,
        task_usage_body=task_usage_body,
    )
