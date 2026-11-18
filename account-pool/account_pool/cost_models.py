"""人民币费用台账契约，仅记录人工确认的支出，不参与模型计价或调度。"""

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PoolCostCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    id: UUID
    category: Literal["subscription", "proxy", "server"]
    account_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    amount_cents: int = Field(strict=True, gt=0, le=1000000000000)
    currency: Literal["CNY"] = "CNY"
    paid_on: date
    starts_on: date
    ends_on: date
    note: str = Field(default="", max_length=2000)
    auto_renew: bool = False
    cycle_months: Literal[1, 3, 12] | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if (self.category == "subscription") != (self.account_id is not None):
            raise ValueError("订阅费用必须关联已有账号，代理和服务器费用不能关联账号")
        if self.auto_renew and (self.category != "subscription" or self.cycle_months is None):
            raise ValueError("自动续期仅支持指定计费周期的账号订阅")
        if self.ends_on < self.starts_on:
            raise ValueError("服务结束日期不能早于开始日期")
        if (self.ends_on - self.starts_on).days > 3660:
            raise ValueError("单条服务期间不能超过十年")
        return self


class PoolCostRecord(PoolCostCreate):
    renewal_of: UUID | None = None
    account_name: str | None = None
    created_at: datetime
    created_by: str
    voided_at: datetime | None = None
    voided_by: str | None = None
    void_reason: str | None = None


class PoolCostVoid(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=500)


class PoolCostQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    starts_on: date
    ends_on: date

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.ends_on < self.starts_on or (self.ends_on - self.starts_on).days > 3660:
            raise ValueError("查询日期无效，期间不能超过十年")
        return self


class PoolCostTotals(BaseModel):
    subscription_cents: int = 0
    proxy_cents: int = 0
    server_cents: int = 0
    total_cents: int = 0


class PoolCostAccount(BaseModel):
    account_id: UUID
    account_name: str
    archived: bool
    recorded: bool
    paid_cents: int
    period_cents: int
    latest: PoolCostRecord | None = None
    days_until_expiry: int | None = None


class PoolCostReport(BaseModel):
    currency: Literal["CNY"] = "CNY"
    starts_on: date
    ends_on: date
    today: date
    paid: PoolCostTotals
    period: PoolCostTotals
    accounts: tuple[PoolCostAccount, ...]
    records: tuple[PoolCostRecord, ...]
    record_count: int
    expiring_count: int
    overdue_count: int
