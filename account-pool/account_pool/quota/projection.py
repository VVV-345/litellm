"""把最新有效解析结果中的套餐额度安全投影为运行时额度窗口。"""

from __future__ import annotations

import asyncio
import json
from typing import Final
from uuid import UUID, uuid5

from account_pool.models import (
    AccountConfig,
    PoolConfig,
    QuotaWindowConfig,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
)
from account_pool.parsing.models import (
    ParsedChannelData,
    QuotaLimit,
    QuotaScope,
    SubscriptionData,
)
from account_pool.parsing.models import (
    QuotaWindowType as ParsedQuotaWindowType,
)
from account_pool.parsing.service import ParserDataFailure, ParserDataFailureCode, ParserDataReader
from account_pool.parsing.subscription import subscription_includes_model


class QuotaProjectionError(RuntimeError):
    pass


class ParserQuotaConfigEnricher:
    def __init__(self, parser_data: ParserDataReader) -> None:
        self._parser_data: Final = parser_data

    async def enrich(self, config: PoolConfig) -> PoolConfig:
        accounts: Final = await asyncio.gather(*(self._enrich_account(account) for account in config.accounts))
        return config.model_copy(update={"accounts": tuple(accounts)})

    async def _enrich_account(self, account: AccountConfig) -> AccountConfig:
        channel_id: Final = account.channel_id
        if channel_id is None:
            return account
        loaded: Final = await self._parser_data.effective_data(channel_id)
        if isinstance(loaded, ParserDataFailure):
            if loaded.code in (ParserDataFailureCode.CHANNEL_NOT_FOUND, ParserDataFailureCode.RUN_NOT_FOUND):
                return account
            raise QuotaProjectionError(f"quota projection failed: {loaded.code}")
        windows: Final = project_quota_windows(account=account, parsed=loaded.effective_result)
        return account.model_copy(update={"quota_windows": windows})


def project_quota_windows(
    account: AccountConfig,
    parsed: ParsedChannelData,
) -> tuple[QuotaWindowConfig, ...]:
    subscription: Final = parsed.subscription
    if subscription is None or account.channel_id is None:
        return ()
    limit_windows: Final = tuple(
        window
        for limit in subscription.limits
        for window in _project_limit(account=account, parsed=parsed, limit=limit)
    )
    balance_windows: Final = _project_subscription_balance(account=account, subscription=subscription)
    windows: Final = (*limit_windows, *balance_windows)
    window_ids: Final = tuple(window.window_id for window in windows)
    if len(window_ids) != len(set(window_ids)):
        raise QuotaProjectionError("quota projection contains duplicate semantic windows")
    return windows


def _project_subscription_balance(
    account: AccountConfig,
    subscription: SubscriptionData,
) -> tuple[QuotaWindowConfig, ...]:
    if subscription.balance is None:
        return ()
    models: Final = tuple(
        dict.fromkeys(
            deployment.public_model
            for deployment in account.deployments
            if subscription_includes_model(
                subscription,
                public_model=deployment.public_model,
                provider_model=deployment.provider_model,
            )
        )
    )
    return tuple(
        _subscription_balance_window(account=account, subscription=subscription, public_model=public_model)
        for public_model in models
    )


def _subscription_balance_window(
    account: AccountConfig,
    subscription: SubscriptionData,
    public_model: str,
) -> QuotaWindowConfig:
    channel_id: Final = _required_channel_id(account)
    identity: Final = json.dumps(("subscription_balance", public_model, subscription.plan_id), separators=(",", ":"))
    return QuotaWindowConfig(
        window_id=str(uuid5(channel_id, identity)),
        scope=RuntimeQuotaScope.MODEL,
        subject_id=public_model,
        kind=RuntimeQuotaKind.PROVIDER_UNITS,
        window_type=RuntimeQuotaWindowType.LIFETIME,
        remaining=subscription.balance,
        observed_at=0,
        source="subscription_balance",
        reason_code="subscription_balance_exhausted",
    )


def _project_limit(
    account: AccountConfig,
    parsed: ParsedChannelData,
    limit: QuotaLimit,
) -> tuple[QuotaWindowConfig, ...]:
    subjects: Final = _runtime_subjects(account=account, parsed=parsed, limit=limit)
    return tuple(
        _window(account=account, limit=limit, scope=scope, subject_id=subject_id) for scope, subject_id in subjects
    )


def _runtime_subjects(
    account: AccountConfig,
    parsed: ParsedChannelData,
    limit: QuotaLimit,
) -> tuple[tuple[RuntimeQuotaScope, str | None], ...]:
    if limit.scope == QuotaScope.CHANNEL:
        return ((RuntimeQuotaScope.CHANNEL, None),)
    if limit.subject_id is None:
        return ()
    if limit.scope == QuotaScope.MODEL:
        models: Final = tuple(
            dict.fromkeys(
                deployment.public_model
                for deployment in account.deployments
                if limit.subject_id in (deployment.public_model, deployment.provider_model)
            )
        )
        return tuple((RuntimeQuotaScope.MODEL, model) for model in models)
    binding_ids: Final = frozenset(
        deployment.binding_id for deployment in account.deployments if deployment.binding_id is not None
    )
    route_ids: Final = tuple(
        str(route.route_id)
        for route in parsed.billing_routes
        if route.deployment_binding_id in binding_ids and route.provider_group_id == limit.subject_id
    )
    return tuple((RuntimeQuotaScope.BILLING_ROUTE, route_id) for route_id in dict.fromkeys(route_ids))


def _window(
    account: AccountConfig,
    limit: QuotaLimit,
    scope: RuntimeQuotaScope,
    subject_id: str | None,
) -> QuotaWindowConfig:
    channel_id: Final = _required_channel_id(account)
    identity: Final = json.dumps(
        (
            "quota",
            scope,
            subject_id,
            limit.kind,
            limit.window_type,
            limit.duration_seconds,
            _window_boundary(limit),
            limit.source,
        ),
        separators=(",", ":"),
    )
    return QuotaWindowConfig(
        window_id=str(uuid5(channel_id, identity)),
        scope=scope,
        subject_id=subject_id,
        kind=RuntimeQuotaKind(limit.kind),
        window_type=None if limit.window_type is None else RuntimeQuotaWindowType(limit.window_type),
        duration_seconds=limit.duration_seconds,
        limit=limit.limit,
        remaining=limit.remaining,
        reset_at=None if limit.reset_at is None else limit.reset_at.timestamp(),
        observed_at=limit.observed_at.timestamp(),
        source=limit.source,
        reason_code=_reason_code(limit),
    )


def _reason_code(limit: QuotaLimit) -> str:
    duration: Final = limit.duration_seconds
    if duration == 18_000:
        return "five_hour_exhausted"
    if duration == 604_800:
        return "weekly_exhausted"
    if duration is not None and 2_419_200 <= duration <= 2_678_400:
        return "monthly_exhausted"
    return "quota_window_exhausted"


def _window_boundary(limit: QuotaLimit) -> str | None:
    if limit.window_type not in (ParsedQuotaWindowType.FIXED, ParsedQuotaWindowType.RESET_AT):
        return None
    return None if limit.reset_at is None else limit.reset_at.isoformat()


def _required_channel_id(account: AccountConfig) -> UUID:
    if account.channel_id is None:
        raise ValueError("quota projection requires channel_id")
    return account.channel_id
