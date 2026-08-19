"""汇总导出与厂商无关的解析结果和持久化契约。"""

from account_pool.parsing.models import ParsedChannelData, ParserRun, ParserRunStatus
from account_pool.parsing.persistence import ParserExportState, ParserExportStatus, PersistedParserRun
from account_pool.parsing.registry import ParserRegistry, ParserSelection, ParserSelectionRequest
from account_pool.parsing.snapshots import ParserSnapshot, ParserSnapshotStore

__all__ = (
    "ParsedChannelData",
    "ParserExportState",
    "ParserExportStatus",
    "ParserRegistry",
    "ParserRun",
    "ParserRunStatus",
    "ParserSelection",
    "ParserSelectionRequest",
    "ParserSnapshot",
    "ParserSnapshotStore",
    "PersistedParserRun",
)
