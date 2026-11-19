"""验证流结束先归还同卡并发槽，慢日志、失败和取消都不重复 finish。"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from litellm.proxy.management_endpoints.account_pool_gateway_contracts import AcquireRejected, FinishRequest, Lease
from litellm.proxy.management_endpoints.account_pool_gateway_resources import managed_cleanup


class OneSlot:
    def __init__(self):
        self.account_id = uuid4()
        self.held = False
        self.finished = []
        self.log_updates = []

    async def acquire(self):
        if self.held:
            return AcquireRejected(reason="concurrency")
        self.held = True
        return Lease(
            lease_id=uuid4(),
            card_id=self.account_id,
            account_id=self.account_id,
            key_id=uuid4(),
            request_id=uuid4(),
            channel="cliproxyapi",
            supplier="openai_codex",
            model="model-a",
            started_at=datetime.now(timezone.utc),
        )

    async def log_status(self, request):
        self.log_updates.append(request)

    async def finish(self, result):
        self.finished.append(result)
        self.held = False


class BlockedLog:
    def __init__(self, failure):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.failure = failure
        self.persisted = False

    def prepare(self, result):
        return result.model_copy(update={"input_tokens": 11, "output_tokens": 7, "cost_usd": 0.25})

    async def persist(self, result):
        self.entered.set()
        await self.release.wait()
        if self.failure == "timeout":
            await asyncio.Event().wait()
        if self.failure == "failure":
            raise OSError("fixture logging failure")
        self.persisted = True
        return result

    def persistence_failed(self, result):
        return result.model_copy(update={"full_log_state": "failed", "spend_sync_state": "failed"})

    async def finish(self, result):
        return await self.persist(self.prepare(result))


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "websocket"])
@pytest.mark.parametrize("scenario", ["slow", "failure", "cancel", "timeout"])
async def test_stream_end_releases_slot_before_slow_persistence_and_reports_usage_once(transport, scenario):
    from litellm.proxy.management_endpoints.account_pool_gateway_forwarder import Attempt, finish_attempt
    from litellm.proxy.management_endpoints.account_pool_websocket import _Attempt, _finish_attempt

    control = OneSlot()
    lease = await control.acquire()
    log = BlockedLog(scenario)
    downstream = []

    async def send(message):
        downstream.append(message)

    if transport == "http":
        attempt = Attempt(lease, "/v1/responses", send, log=log)
        attempt.outcome(200, "stream complete", response_completed=True)
        await attempt.emit({"type": "http.response.body", "body": b"data: [DONE]\n\n", "more_body": False})
        task = asyncio.create_task(managed_cleanup(finish_attempt(control, attempt)))
    else:
        attempt = _Attempt(lease.lease_id, "/v1/responses", None)
        attempt.result = FinishRequest(
            lease_id=lease.lease_id,
            http_status=200,
            endpoint="/v1/responses",
            message="stream complete",
            response_completed=True,
        )
        attempt.log = log
        downstream.append({"type": "websocket.send", "text": "response.completed"})
        task = asyncio.create_task(_finish_attempt(control, attempt, log))
    try:
        await asyncio.wait_for(log.entered.wait(), 2)
        assert len(control.finished) == 1, "lease release must precede blocked logging"
        reported = control.finished[0]
        assert reported.input_tokens == 11 and reported.output_tokens == 7 and reported.cost_usd == 0.25
        assert downstream and reported.response_completed
        next_lease = await control.acquire()
        assert isinstance(next_lease, Lease) and next_lease.account_id == lease.account_id
        assert not task.done()
        if scenario == "cancel":
            task.cancel()
        log.release.set()
        if scenario == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        if transport == "http":
            await finish_attempt(control, attempt)
        else:
            await _finish_attempt(control, attempt, log)
        assert len(control.finished) == 1
        assert log.persisted is (scenario not in ("failure", "timeout"))
        assert len(control.log_updates) == 1
        if scenario in ("failure", "timeout"):
            assert control.log_updates[0].full_log_state == "failed"
            assert control.log_updates[0].spend_sync_state == "failed"
        assert control.log_updates == [] or control.log_updates[0].lease_id == lease.lease_id
    finally:
        log.release.set()
        if not task.done():
            await task
