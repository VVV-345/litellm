"""费用期间计算及报表投影，不读取供应商价格，不修改账号状态。"""

from collections.abc import Mapping
from datetime import date
from functools import reduce
from typing import Final
from uuid import UUID

from account_pool.cost_models import PoolCostAccount, PoolCostQuery, PoolCostRecord, PoolCostReport, PoolCostTotals


def period_cost(record: PoolCostRecord, query: PoolCostQuery) -> int:
    if record.voided_at is not None:
        return 0
    days: Final = (record.ends_on - record.starts_on).days + 1
    before: Final = max(0, min(days, (query.starts_on - record.starts_on).days))
    through: Final = max(0, min(days, (query.ends_on - record.starts_on).days + 1))
    # 累计整数分差保证任意相邻期间相加等于原始金额，不产生逐月舍入尾差。
    return record.amount_cents * through // days - record.amount_cents * before // days


def paid_cost(record: PoolCostRecord, query: PoolCostQuery) -> int:
    return record.amount_cents if record.voided_at is None and query.starts_on <= record.paid_on <= query.ends_on else 0


def totals(values: tuple[tuple[PoolCostRecord, int], ...]) -> PoolCostTotals:
    return PoolCostTotals(
        subscription_cents=sum(amount for record, amount in values if record.category == "subscription"),
        proxy_cents=sum(amount for record, amount in values if record.category == "proxy"),
        server_cents=sum(amount for record, amount in values if record.category == "server"),
        total_cents=sum(amount for _, amount in values),
    )


def subscription_expiry(records: tuple[PoolCostRecord, ...], today: date) -> date | None:
    started: Final = tuple(r.ends_on for r in records if r.starts_on <= today)
    if not started:
        return None
    return reduce(
        lambda end, record: max(end, record.ends_on) if record.starts_on.toordinal() <= end.toordinal() + 1 else end,
        sorted(records, key=lambda r: r.starts_on),
        max(started),
    )


def account_summary(
    account_id: UUID, name: str, archived: bool, records: tuple[PoolCostRecord, ...], query: PoolCostQuery, today: date
) -> PoolCostAccount:
    subscriptions: Final = tuple(r for r in records if r.account_id == account_id and r.voided_at is None)
    latest: Final = max(subscriptions, key=lambda r: (r.ends_on, r.created_at, r.id.int), default=None)
    expiry: Final = subscription_expiry(subscriptions, today)
    return PoolCostAccount(
        account_id=account_id,
        account_name=name,
        archived=archived,
        recorded=bool(subscriptions),
        paid_cents=sum(paid_cost(r, query) for r in subscriptions),
        period_cents=sum(period_cost(r, query) for r in subscriptions),
        latest=latest,
        days_until_expiry=(expiry - today).days if expiry is not None else None,
    )


def cost_report(
    records: tuple[PoolCostRecord, ...], accounts: Mapping[UUID, str], query: PoolCostQuery, today: date
) -> PoolCostReport:
    historical: Final = {r.account_id: r.account_name or "已删除账号" for r in records if r.account_id is not None}
    names: Final = {**historical, **accounts}
    summaries: Final = tuple(
        account_summary(account_id, name, account_id not in accounts, records, query, today)
        for account_id, name in names.items()
    )
    visible: Final = tuple(
        r
        for r in records
        if query.starts_on <= r.paid_on <= query.ends_on
        or (r.starts_on <= query.ends_on and r.ends_on >= query.starts_on)
    )
    return PoolCostReport(
        starts_on=query.starts_on,
        ends_on=query.ends_on,
        today=today,
        paid=totals(tuple((r, paid_cost(r, query)) for r in records)),
        period=totals(tuple((r, period_cost(r, query)) for r in records)),
        accounts=summaries,
        records=visible,
        record_count=len(visible),
        expiring_count=sum(
            not a.archived and a.days_until_expiry is not None and 0 <= a.days_until_expiry <= 7 for a in summaries
        ),
        overdue_count=sum(
            not a.archived and a.days_until_expiry is not None and a.days_until_expiry < 0 for a in summaries
        ),
    )
