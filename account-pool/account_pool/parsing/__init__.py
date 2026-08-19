"""提供与厂商无关的渠道解析结果契约。"""

from account_pool.parsing.models import ParsedChannelData, ParserRun, ParserRunStatus
from account_pool.parsing.snapshots import ParserSnapshot, ParserSnapshotStore

__all__ = (
    "ParsedChannelData",
    "ParserRun",
    "ParserRunStatus",
    "ParserSnapshot",
    "ParserSnapshotStore",
)
