"""声明解析运行仓储协议，使 Worker 不依赖具体数据库实现。"""

from typing import Protocol
from uuid import UUID

from account_pool.parsing.models import ParserRun
from account_pool.parsing.persistence import (
    ParserExportAttempt,
    ParserExportUpdateResult,
    ParserRunsLoadResult,
    ParserRunWriteResult,
)


class ParserRunRepository(Protocol):
    async def persist(self, run: ParserRun) -> ParserRunWriteResult: ...

    async def load_exportable(self, limit: int) -> ParserRunsLoadResult: ...

    async def record_export_attempt(
        self,
        parser_run_id: UUID,
        attempt: ParserExportAttempt,
    ) -> ParserExportUpdateResult: ...
