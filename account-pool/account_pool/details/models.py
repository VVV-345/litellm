"""定义渠道配置、解析、健康、路由和事件组合后的脱敏详情契约。"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import Field, model_validator

from account_pool.events.models import EventLogEntry
from account_pool.health.service import ChannelHealthDetail
from account_pool.models import FrozenModel, RouteEntry
from account_pool.overview.models import ChannelOverview
from account_pool.parsing.service import EffectiveParserData
from account_pool.sync.contracts import ChannelDetail

SectionValue = TypeVar("SectionValue")


class DetailSectionFailure(FrozenModel):
    code: str = Field(min_length=1, max_length=100)
    retryable: bool


class DetailSection(FrozenModel, Generic[SectionValue]):
    status: Literal["loaded", "unavailable"]
    data: SectionValue | None = None
    failure: DetailSectionFailure | None = None

    @model_validator(mode="after")
    def validate_state(self) -> DetailSection[SectionValue]:
        if self.status == "loaded" and (self.data is None or self.failure is not None):
            raise ValueError("a loaded detail section requires data and forbids failure")
        if self.status == "unavailable" and (self.data is not None or self.failure is None):
            raise ValueError("an unavailable detail section requires failure and forbids data")
        return self


class ChannelAggregateDetail(FrozenModel):
    status: Literal["loaded"] = "loaded"
    channel: ChannelDetail
    overview: DetailSection[ChannelOverview]
    parser: DetailSection[EffectiveParserData]
    health: DetailSection[ChannelHealthDetail]
    routes: DetailSection[tuple[RouteEntry, ...]]
    events: DetailSection[tuple[EventLogEntry, ...]]


class ChannelAggregateFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: str = Field(min_length=1, max_length=100)
    retryable: bool


ChannelAggregateResult = ChannelAggregateDetail | ChannelAggregateFailure
