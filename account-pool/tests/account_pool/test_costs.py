"""费用口径与服务日期回归，覆盖人民币整数分、独立资源和历史账号。"""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from account_pool.cost_models import PoolCostCreate, PoolCostQuery, PoolCostRecord
from account_pool.costs import cost_report, period_cost
from pydantic import ValidationError


def record(**changes):
    return PoolCostRecord.model_validate(
        {
            "id": uuid4(),
            "category": "subscription",
            "account_id": uuid4(),
            "account_name": "真实账号",
            "name": "月订阅",
            "amount_cents": 100,
            "paid_on": "2026-01-31",
            "starts_on": "2026-01-31",
            "ends_on": "2026-02-02",
            "created_at": datetime.now(timezone.utc),
            "created_by": "admin:test",
            **changes,
        }
    )


def test_cross_month_rounding_conserves_every_cent_and_includes_end_date():
    cost = record()
    january = PoolCostQuery(starts_on=date(2026, 1, 1), ends_on=date(2026, 1, 31))
    february = PoolCostQuery(starts_on=date(2026, 2, 1), ends_on=date(2026, 2, 28))
    assert period_cost(cost, january) == 33
    assert period_cost(cost, february) == 67
    assert (
        sum(
            period_cost(cost, PoolCostQuery(starts_on=d, ends_on=d))
            for d in (date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 2))
        )
        == 100
    )
    assert period_cost(cost, PoolCostQuery(starts_on=date(2027, 1, 1), ends_on=date(2027, 1, 2))) == 0


def test_leap_day_and_one_day_services():
    cost = record(starts_on="2024-02-28", ends_on="2024-03-01", amount_cents=300)
    assert period_cost(cost, PoolCostQuery(starts_on=date(2024, 2, 29), ends_on=date(2024, 2, 29))) == 100
    day = record(starts_on="2024-02-29", ends_on="2024-02-29", amount_cents=99)
    assert period_cost(day, PoolCostQuery(starts_on=date(2024, 2, 29), ends_on=date(2024, 2, 29))) == 99


def test_resources_never_allocated_and_missing_cost_not_zero_record():
    account, missing = uuid4(), uuid4()
    costs = (
        record(account_id=account, amount_cents=300),
        record(category="proxy", account_id=None, amount_cents=600),
        record(category="server", account_id=None, amount_cents=900),
    )
    report = cost_report(
        costs,
        {account: "账号", missing: "未录入账号"},
        PoolCostQuery(starts_on=date(2026, 2, 1), ends_on=date(2026, 2, 28)),
        date(2026, 2, 1),
    )
    assert report.paid.total_cents == 0
    assert report.period.total_cents == 1200
    assert report.accounts[0].period_cents == 200
    assert report.accounts[1].recorded is False
    assert report.expiring_count == 1


def test_renewal_supersedes_expiry_reminder_void_and_deleted_history_preserved():
    account = uuid4()
    old = record(account_id=account)
    renewal = record(account_id=account, starts_on="2026-02-03", ends_on="2026-03-02", paid_on="2026-02-01")
    query = PoolCostQuery(starts_on=date(2026, 1, 1), ends_on=date(2026, 3, 31))
    live = cost_report((old, renewal), {account: "重命名账号"}, query, date(2026, 2, 1))
    assert live.accounts[0].account_name == "重命名账号"
    assert live.expiring_count == 0
    assert live.accounts[0].latest == renewal
    voided = renewal.model_copy(update={"voided_at": datetime.now(timezone.utc)})
    history = cost_report((old, voided), {}, query, date(2026, 2, 4))
    assert history.accounts[0].archived
    assert history.period.total_cents == 100
    assert history.overdue_count == 0
    assert len(history.records) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"account_id": None},
        {"category": "server"},
        {"amount_cents": -1},
        {"amount_cents": 1.2},
        {"amount_cents": True},
        {"ends_on": "2025-01-01"},
        {"currency": "USD"},
        {"name": " "},
    ],
)
def test_reject_invalid_ledger_entries(change):
    with pytest.raises(ValidationError):
        record(**change)


def test_proxy_and_manager_contracts_match():
    from litellm.proxy.management_endpoints.account_pool_cost_models import PoolCostCreate as ProxyCreate

    assert PoolCostCreate.model_json_schema() == ProxyCreate.model_json_schema()


def test_future_payment_does_not_hide_a_gap_in_subscription_coverage():
    account = uuid4()
    old = record(account_id=account)
    later = record(account_id=account, starts_on="2026-02-10", ends_on="2026-03-09")
    query = PoolCostQuery(starts_on=date(2026, 2, 1), ends_on=date(2026, 2, 28))
    report = cost_report((old, later), {account: "账号"}, query, date(2026, 2, 4))
    assert report.overdue_count == 1
    assert report.accounts[0].days_until_expiry == -2
    assert report.accounts[0].latest == later
    future = cost_report((later,), {account: "账号"}, query, date(2026, 2, 4))
    assert future.accounts[0].recorded
    assert future.accounts[0].days_until_expiry is None
    assert future.overdue_count == future.expiring_count == 0


@pytest.mark.parametrize(
    "start, months, end",
    [
        ("2026-11-18", 1, "2026-12-17"),
        ("2026-01-31", 1, "2026-02-27"),
        ("2024-01-31", 1, "2024-02-28"),
        ("2024-02-29", 12, "2025-02-27"),
        ("2026-11-18", 3, "2027-02-17"),
    ],
)
def test_renewal_calendar_end(start, months, end):
    from account_pool.cost_renewal import cycle_end

    assert cycle_end(date.fromisoformat(start), months) == date.fromisoformat(end)


def test_auto_renew_requires_account_and_cycle():
    with pytest.raises(ValidationError):
        record(auto_renew=True)
    with pytest.raises(ValidationError):
        record(category="server", account_id=None, auto_renew=True, cycle_months=1)
    assert record().auto_renew is False
