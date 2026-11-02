"""验证自然周期到期的无兑换恢复、重启补查与任务退避，不连接上游。"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from account_pool.domain import QuotaSnapshot, QuotaWindow, utc_now
from account_pool.quota_recovery_scheduler import due_models
from test_quota_reset import setup_service


@pytest.mark.asyncio
async def test_natural_due_recovers_model_without_consuming_credit():
    service, record, _, upstream = setup_service()
    old = QuotaSnapshot(
        windows=(
            QuotaWindow(
                name="5h",
                used_percent=100,
                remaining_percent=0,
                window_minutes=300,
                resets_at=utc_now() - timedelta(seconds=1),
            ),
        )
    )
    service.environments.record = record.model_copy(update={"quota": old})
    upstream.observation = upstream.observation.model_copy(
        update={
            "quota": QuotaSnapshot(
                observed_at=utc_now(),
                windows=(QuotaWindow(name="5h", used_percent=1, remaining_percent=99, window_minutes=300),),
            )
        }
    )
    assert await service.recover_due(record.id)
    assert upstream.calls == []
    assert upstream.probes == [(record.id, "model-a", True)]


@pytest.mark.asyncio
async def test_due_but_upstream_still_empty_does_not_probe_or_consume():
    service, record, _, upstream = setup_service()
    empty = QuotaSnapshot(
        windows=(
            QuotaWindow(
                name="5h",
                used_percent=100,
                remaining_percent=0,
                window_minutes=300,
                resets_at=utc_now() - timedelta(seconds=1),
            ),
        )
    )
    service.environments.record = record.model_copy(update={"quota": empty})
    upstream.observation = upstream.observation.model_copy(update={"quota": empty})
    assert not await service.recover_due(record.id)
    assert upstream.calls == [] and upstream.probes == []


@pytest.mark.asyncio
async def test_natural_scheduler_submits_overdue_on_start_without_hot_loop():
    from account_pool.quota_recovery_scheduler import QuotaRecoveryScheduler

    service, record, _, _ = setup_service()
    due = record.model_copy(
        update={
            "quota": QuotaSnapshot(
                windows=(
                    QuotaWindow(
                        name="5h",
                        used_percent=100,
                        remaining_percent=0,
                        window_minutes=300,
                        resets_at=utc_now() - timedelta(seconds=1),
                    ),
                )
            )
        }
    )
    listing = AsyncMock(return_value=(due, due.model_copy(update={"id": record.id, "manual_cooldown": True})))
    recovery = AsyncMock(return_value=False)
    clock = [0.0]
    scheduler = QuotaRecoveryScheduler(listing, recovery, 1, clock=lambda: clock[0])
    await scheduler.scan_once()
    await scheduler.trigger._step()
    assert recovery.await_count == 1
    await scheduler.scan_once()
    assert scheduler.trigger._cards[record.id].due > clock[0]
    task = asyncio.create_task(scheduler.trigger._step())
    await asyncio.sleep(0)
    assert recovery.await_count == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_due_models_uses_instants_and_never_runs_before_the_window_deadline():
    _, record, _, _ = setup_service()
    deadline = datetime(2026, 11, 9, 4, 40, tzinfo=timezone.utc)
    due = record.model_copy(update={"quota": QuotaSnapshot(windows=(
        QuotaWindow(name="5h", used_percent=100, remaining_percent=0, window_minutes=300, resets_at=deadline),
    ))})
    assert due_models(due, deadline - timedelta(seconds=1)) == ()
    assert due_models(due, deadline) == record.enabled_models
    assert due_models(due, deadline.astimezone(timezone(timedelta(hours=8)))) == record.enabled_models


@pytest.mark.parametrize("block", [
    {"enabled": False}, {"manual_cooldown": True}, {"auth_file_disabled": True}, {"configuration_pending": True},
])
def test_natural_schedule_preserves_independent_blocks(block):
    _, record, _, _ = setup_service()
    due = record.model_copy(update={**block, "quota": QuotaSnapshot(windows=(
        QuotaWindow(name="5h", used_percent=100, remaining_percent=0, window_minutes=300, resets_at=utc_now() - timedelta(days=1)),
    ))})
    assert due_models(due, utc_now()) == ()


def test_only_fresh_positive_provider_observation_can_recheck_stale_cooldown():
    _, record, _, _ = setup_service()
    now = utc_now()
    fresh = QuotaSnapshot(observed_at=now, source="provider_api", windows=(
        QuotaWindow(name="5h", used_percent=1, remaining_percent=99, window_minutes=300),
    ))
    blocked = record.model_copy(update={"automatic_cooldown": True, "quota": fresh})
    assert due_models(blocked, now) == record.enabled_models
    assert due_models(blocked, now + timedelta(minutes=6)) == ()
    assert due_models(blocked, now - timedelta(seconds=1)) == ()
