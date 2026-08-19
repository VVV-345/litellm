"""定义不依赖数据库或运行后端的调度候选和排序结果。"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from account_pool.models import AccountId


class RoutingModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RoutingCandidate(RoutingModel):
    account_id: AccountId
    deployment_id: str = Field(min_length=1)
    billing_route_id: str | None = Field(default=None, min_length=1)
    available: bool
    priority: int
    weight: int = Field(ge=1, le=100)
    manual_order: int | None = Field(default=None, ge=0)
    inflight: int = Field(ge=0)
    max_concurrency: int = Field(ge=1)
    remaining_quota_ratio: float | None = Field(default=None, ge=0)
    latency_ewma_ms: float | None = Field(default=None, ge=0)
    effective_cost: Decimal | None = Field(default=None, ge=0)

    def stable_id(self) -> str:
        return f"{self.account_id}\x00{self.deployment_id}\x00{self.billing_route_id or ''}"


class RoutingOrder(RoutingModel):
    candidate: RoutingCandidate
    reason_codes: tuple[str, ...] = ()
    dynamic: bool = False
