"""验证单卡额度失败事件的合并、并发、退避及关闭，不调用供应商。"""
import asyncio
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_event_refresh_is_debounced_and_retains_later_event():
    from account_pool.quota_refresh_trigger import QuotaRefreshTrigger
    now = [0.0]
    calls = []
    first = asyncio.Event()
    second = asyncio.Event()
    async def refresh(card_id):
        calls.append(card_id)
        (first if len(calls) == 1 else second).set()
        return True
    trigger = QuotaRefreshTrigger(refresh, 2, clock=lambda: now[0])
    card = uuid4()
    stopped = asyncio.Event()
    task = asyncio.create_task(trigger.run_until_cancelled(stopped))
    try:
        trigger.submit(card)
        await asyncio.wait_for(first.wait(), 2)
        for _ in range(20):
            trigger.submit(card)
        await asyncio.sleep(0)
        assert calls == [card]
        now[0] = 61
        trigger.submit(card)
        await asyncio.wait_for(second.wait(), 2)
        assert calls == [card, card]
    finally:
        stopped.set()
        await task


@pytest.mark.asyncio
async def test_refresh_workers_bound_concurrency_and_stop_inflight_work():
    from account_pool.quota_refresh_trigger import QuotaRefreshTrigger
    active = []
    entered = asyncio.Event()
    cleaned = []
    async def refresh(card_id):
        active.append(card_id)
        if len(active) == 2:
            entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.append(card_id)
        return True
    trigger = QuotaRefreshTrigger(refresh, 2)
    for _ in range(10):
        trigger.submit(uuid4())
    stopped = asyncio.Event()
    task = asyncio.create_task(trigger.run_until_cancelled(stopped))
    await asyncio.wait_for(entered.wait(), 2)
    assert len(active) == 2
    stopped.set()
    await task
    assert len(cleaned) == 2


@pytest.mark.asyncio
async def test_failure_refresh_does_not_retry_without_new_event_and_backs_off():
    from account_pool.quota_refresh_trigger import QuotaRefreshTrigger
    now = [0.0]
    calls = []
    changed = asyncio.Queue()
    async def refresh(card_id):
        calls.append(card_id)
        changed.put_nowait(len(calls))
        return False
    trigger = QuotaRefreshTrigger(refresh, 1, clock=lambda: now[0])
    card = uuid4()
    stopped = asyncio.Event()
    task = asyncio.create_task(trigger.run_until_cancelled(stopped))
    try:
        trigger.submit(card)
        assert await asyncio.wait_for(changed.get(), 2) == 1
        now[0] = 61
        await asyncio.sleep(0)
        assert len(calls) == 1
        trigger.submit(card)
        assert await asyncio.wait_for(changed.get(), 2) == 2
        now[0] = 122
        trigger.submit(card)
        await asyncio.sleep(0)
        assert len(calls) == 2
        now[0] = 182
        trigger.submit(card)
        assert await asyncio.wait_for(changed.get(), 2) == 3
    finally:
        stopped.set()
        await task


@pytest.mark.asyncio
async def test_refresh_queue_is_bounded_and_timeout_releases_worker(caplog):
    from account_pool.quota_refresh_trigger import QuotaRefreshTrigger
    entered = []
    next_entered = asyncio.Event()
    async def refresh(card_id):
        entered.append(card_id)
        if len(entered) == 2:
            next_entered.set()
        await asyncio.Event().wait()
        return True
    trigger = QuotaRefreshTrigger(refresh, 1, refresh_timeout_seconds=0.01)
    for _ in range(300):
        trigger.submit(uuid4())
    assert len(trigger._cards) == 256
    assert caplog.messages == [
        "Event quota refresh queue full; scheduled collection will refresh skipped cards"
    ]
    stopped = asyncio.Event()
    task = asyncio.create_task(trigger.run_until_cancelled(stopped))
    try:
        await asyncio.wait_for(next_entered.wait(), 2)
        assert len(entered) == 2
    finally:
        stopped.set()
        await task
