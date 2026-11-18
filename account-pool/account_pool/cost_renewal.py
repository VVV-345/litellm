"""订阅周期日期与到期台账续期调度，不向厂商发起扣款。"""

import asyncio
import calendar
import logging
from datetime import date, datetime, timedelta
from typing import Final, Protocol
from zoneinfo import ZoneInfo

_LOGGER: Final = logging.getLogger(__name__)


def cycle_end(start: date, months: int) -> date:
    month_index: Final = start.year * 12 + start.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    day: Final = min(start.day, calendar.monthrange(year, month_zero + 1)[1])
    return date(year, month_zero + 1, day) - timedelta(days=1)


class RenewableCosts(Protocol):
    async def renew_due(self, today: date) -> None: ...


class CostRenewalScheduler:
    def __init__(self, repository: RenewableCosts) -> None:
        self._repository: Final = repository

    async def run_until_cancelled(self, stopped: asyncio.Event) -> None:
        while not stopped.is_set():
            try:
                await self._repository.renew_due(datetime.now(ZoneInfo("Asia/Shanghai")).date())
            except Exception as error:
                _LOGGER.warning("Cost renewal failed: %s", error.__class__.__name__)
            try:
                await asyncio.wait_for(stopped.wait(), timeout=60)
            except TimeoutError:
                continue
