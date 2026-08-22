"""导出渠道聚合详情的分区契约与只读组合服务。"""

from account_pool.details.models import (
    ChannelAggregateDetail,
    ChannelAggregateFailure,
    ChannelAggregateResult,
    DetailSection,
    DetailSectionFailure,
)
from account_pool.details.service import ChannelAggregateReader, ChannelAggregateService

__all__ = [
    "ChannelAggregateDetail",
    "ChannelAggregateFailure",
    "ChannelAggregateReader",
    "ChannelAggregateResult",
    "ChannelAggregateService",
    "DetailSection",
    "DetailSectionFailure",
]
