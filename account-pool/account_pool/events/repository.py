"""声明统一事件日志的只读查询协议。"""

from typing import Protocol

from account_pool.events.models import EventLogResult, EventQuery


class EventLogReader(Protocol):
    async def list_events(self, query: EventQuery) -> EventLogResult: ...
