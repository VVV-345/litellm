"""覆盖单卡兑换事务的持久身份、并发隔离及不明确结果，不连接真实供应商。"""

import asyncio
from uuid import uuid4

import pytest
from account_pool.credential_ownership import CredentialOwnership
from account_pool.quota_reset import QuotaResetService
from account_pool.quota_reset_models import ResetObservation, ResetReply
from test_cliproxy_supplier_client import _record


class Store:
    def __init__(self):
        self.items = {}

    async def latest(self, environment_id):
        return next(
            (item for item in reversed(tuple(self.items.values())) if item.environment_id == environment_id), None
        )

    async def get(self, environment_id, request_id):
        item = self.items.get(request_id)
        return item if item and item.environment_id == environment_id else None

    async def save(self, item):
        self.items[item.request_id] = item


class Environments:
    def __init__(self, record):
        self.record = record

    async def get(self, environment_id):
        return self.record if self.record.id == environment_id else None


class Upstream:
    def __init__(self):
        self.observation = ResetObservation(available=2, credit_id="fixture-credit", eligible=True)
        self.calls = []
        self.reply = ResetReply(code="reset", windows_reset=1)
        self.timeout = False

    async def observe(self, record):
        return self.observation

    async def consume(self, record, operation):
        self.calls.append((record.id, operation.request_id, operation.credit_id))
        await asyncio.sleep(0)
        if self.timeout:
            raise TimeoutError("fixture timeout")
        return self.reply


def setup_service():
    record = _record().model_copy(
        update={
            "auth_file_name": "fixture.json",
            "auth_index": "fixture",
            "credential_fingerprints": ("fixture-binding",),
        }
    )
    store, upstream, environments = Store(), Upstream(), Environments(record)
    return QuotaResetService(store, environments, upstream, CredentialOwnership()), record, store, upstream


@pytest.mark.asyncio
async def test_duplicate_clicks_share_one_durable_redemption_and_other_card_is_untouched():
    service, record, store, upstream = setup_service()
    first, second = await asyncio.gather(service.prepare(record.id, None), service.prepare(record.id, None))
    assert first.request_id == second.request_id
    results = await asyncio.gather(*(service.consume(record.id, first.request_id) for _ in range(3)))
    assert all(result.status == "reset" for result in results)
    assert upstream.calls == [(record.id, first.request_id, "fixture-credit")]
    assert len(store.items) == 1
    assert await store.latest(uuid4()) is None


@pytest.mark.asyncio
async def test_timeout_survives_restart_and_only_reconciles_with_same_redemption_identity():
    service, record, store, upstream = setup_service()
    operation = await service.prepare(record.id, None)
    upstream.timeout = True
    result = await service.consume(record.id, operation.request_id)
    assert result.status == "uncertain"
    restarted = QuotaResetService(store, Environments(record), upstream, CredentialOwnership())
    assert (await restarted.prepare(record.id, operation.request_id)).request_id == operation.request_id
    upstream.timeout = False
    upstream.reply = ResetReply(code="already_redeemed", windows_reset=0)
    reconciled = await restarted.consume(record.id, operation.request_id)
    assert reconciled.status == "already_redeemed"
    assert {item[1] for item in upstream.calls} == {operation.request_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["no_credit", "nothing_to_reset", "already_redeemed"])
async def test_business_result_is_not_reported_as_successful_reset(code):
    service, record, _, upstream = setup_service()
    upstream.reply = ResetReply(code=code, windows_reset=0)
    operation = await service.prepare(record.id, None)
    result = await service.consume(record.id, operation.request_id)
    assert result.status == code
    assert result.windows_reset == 0
    assert result.verification == "not_run"
    assert record.status.value == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("available,eligible", [(0, True), (2, False)])
async def test_ineligible_observation_never_dispatches_consume(available, eligible):
    service, record, _, upstream = setup_service()
    upstream.observation = ResetObservation(available=available, credit_id=None, eligible=eligible)
    with pytest.raises(ValueError):
        await service.prepare(record.id, None)
    assert upstream.calls == []


@pytest.mark.asyncio
async def test_crash_marker_is_durable_before_dispatch_and_binding_change_blocks_replay():
    service, record, store, upstream = setup_service()
    operation = await service.prepare(record.id, None)
    original = upstream.consume

    async def consume(current, pending):
        assert (await store.get(record.id, pending.request_id)).status == "uncertain"
        return await original(current, pending)

    upstream.consume = consume
    assert (await service.consume(record.id, operation.request_id)).status == "reset"
    service.environments.record = record.model_copy(update={"credential_fingerprints": ("new-binding",)})
    with pytest.raises(ValueError, match="凭据"):
        await service.consume(record.id, operation.request_id)
    assert len(upstream.calls) == 1
