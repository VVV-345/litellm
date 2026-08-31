"""一次读取最新解析结果，并组合额度与调度成本的运行时投影。"""

from __future__ import annotations

import asyncio
from typing import Final

from account_pool.models import AccountConfig, PoolConfig
from account_pool.parsing.models import ParsedChannelData
from account_pool.parsing.service import ParserDataFailure, ParserDataFailureCode, ParserDataReader
from account_pool.quota.projection import QuotaProjectionError, project_quota_windows
from account_pool.routing.projection import project_routing_deployments


class ParserRuntimeConfigEnricher:
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
            raise QuotaProjectionError(f"parser runtime projection failed: {loaded.code}")
        parsed: Final = loaded.effective_result
        return account.model_copy(
            update={
                "max_concurrency": _channel_max_concurrency(account=account, parsed=parsed),
                "quota_windows": project_quota_windows(account=account, parsed=parsed),
                "deployments": project_routing_deployments(account=account, parsed=parsed),
            }
        )


def _channel_max_concurrency(account: AccountConfig, parsed: ParsedChannelData) -> int:
    subscription: Final = parsed.subscription
    if subscription is None or subscription.channel_concurrency is None:
        return account.max_concurrency
    return subscription.channel_concurrency
