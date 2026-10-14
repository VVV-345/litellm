"""本模块按周期停用到期 ISP，并要求绑定网关确认失败关闭状态。"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Final, Protocol
from uuid import UUID

_LOGGER: Final = logging.getLogger(__name__)


class ExpiringProxyCatalog(Protocol):
    async def deactivate_expired(self, today: date) -> tuple[UUID, ...]: ...


class ISPBoundGateways(Protocol):
    async def enforce_inactive_isps(self, today: date) -> None: ...


class ISPExpiryScheduler:
    def __init__(
        self,
        catalog: ExpiringProxyCatalog,
        gateways: ISPBoundGateways,
        *,
        interval_seconds: float = 30.0,
    ) -> None:
        self._catalog: Final = catalog
        self._gateways: Final = gateways
        self._interval_seconds: Final = interval_seconds

    async def run_once(self, today: date) -> tuple[UUID, ...]:
        deactivated: Final = await self._catalog.deactivate_expired(today)
        await self._gateways.enforce_inactive_isps(today)
        return deactivated

    async def run_until_cancelled(self, stopped: asyncio.Event) -> None:
        while not stopped.is_set():
            try:
                await self.run_once(date.today())
            except Exception as error:
                _LOGGER.warning("ISP expiry reconciliation failed: %s", error.__class__.__name__)
            try:
                await asyncio.wait_for(stopped.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue
