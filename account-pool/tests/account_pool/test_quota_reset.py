"""覆盖单卡兑换事务的持久身份、并发隔离及不明确结果，不连接真实供应商。"""

import asyncio
from datetime import timedelta
from typing import Final
from uuid import uuid4

import pytest
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import (
    EnvironmentStatus,
    ModelCooldown,
    ModelQuotaSnapshot,
    QuotaSnapshot,
    QuotaWindow,
    utc_now,
)
from account_pool.quota_reset import QuotaResetService, recovered_record
from account_pool.quota_reset_models import ResetObservation, ResetReply, ResetVerification
from test_cliproxy_supplier_client import _record


class Store:
    def __init__(self):
        self.items = {}
        self.verifications = {}

    async def save_verification(self, environment_id, verification):
        self.verifications[environment_id] = verification

    async def latest_verification(self, environment_id):
        return self.verifications.get(environment_id)

    async def latest_api_call(self, environment_id):
        return None

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

    async def restore_verified(self, record, model, quota, model_quotas):
        if self.record != record:
            return False
        self.record = recovered_record(record, model, quota, model_quotas)
        return True


class Upstream:
    def __init__(self):
        self.observation = ResetObservation(available=2, credit_id="fixture-credit", eligible=True)
        self.calls = []
        self.reply = ResetReply(code="reset", windows_reset=1)
        self.timeout = False
        self.verification = ResetVerification(passed=True, restored=True, reason="restored")
        self.probes = []

    async def observe(self, record):
        return self.observation

    async def consume(self, record, operation):
        self.calls.append((record.id, operation.request_id, operation.credit_id))
        await asyncio.sleep(0)
        if self.timeout:
            raise TimeoutError("fixture timeout")
        return self.reply

    async def verify(self, record, model, recover):
        self.probes.append((record.id, model, recover))
        return self.verification


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
    assert reconciled.status == "uncertain"
    assert len(upstream.calls) == 1
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


@pytest.mark.asyncio
async def test_inference_failure_cannot_recover_despite_successful_quota_query():
    service, record, _, upstream = setup_service()
    upstream.verification = ResetVerification(passed=False, restored=False, reason="authentication_failed")
    assert (await service.upstream.observe(record)).available == 2
    result = await service.verify(record.id, "model-a", True)
    assert not result.restored
    assert service.environments.record == record


@pytest.mark.asyncio
async def test_verification_persisted_without_fabricating_redemption():
    service, record, store, _ = setup_service()
    result = await service.verify(record.id, "model-a", True)
    saved = await store.latest_verification(record.id)
    assert saved.result == result and saved.model == "model-a"
    assert saved.observed_at is not None
    assert await store.latest(record.id) is None


@pytest.mark.asyncio
async def test_verification_updates_existing_redemption_audit():
    service, record, store, _ = setup_service()
    operation = await service.prepare(record.id, None)
    await service.consume(record.id, operation.request_id)
    await service.verify(record.id, "model-a", True)
    assert (await store.get(record.id, operation.request_id)).verification == "restored"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "block",
    [
        {"enabled": False},
        {"manual_cooldown": True},
        {"configuration_pending": True},
        {"last_error": "independent failure"},
    ],
)
async def test_recovery_preserves_independent_blocks(block):
    service, record, _, upstream = setup_service()
    service.environments.record = record.model_copy(update=block)
    result = await service.verify(record.id, "model-a", True)
    assert not result.restored
    assert upstream.probes == []


@pytest.mark.asyncio
async def test_verified_recovery_and_disabled_model_guard():
    service, record, _, upstream = setup_service()
    assert (await service.verify(record.id, "model-a", True)).restored
    with pytest.raises(ValueError):
        await service.verify(record.id, "unselected-model", True)
    assert upstream.probes == [(record.id, "model-a", True)]


@pytest.mark.asyncio
async def test_verified_recovery_replaces_exhausted_quota_with_fresh_observation():
    service, record, _, upstream = setup_service()
    stale = QuotaSnapshot(windows=(QuotaWindow(name="5h", remaining_percent=0, used_percent=100, window_minutes=300),))
    service.environments.record = record.model_copy(update={"quota": stale})
    fresh = QuotaSnapshot(
        observed_at=utc_now(),
        source="provider_api",
        windows=(QuotaWindow(name="5h", remaining_percent=99, used_percent=1, window_minutes=300),),
    )
    upstream.observation = upstream.observation.model_copy(update={"quota": fresh})
    result = await service.verify(record.id, "model-a", True)
    assert result.restored
    assert service.environments.record.quota.windows == fresh.windows
    assert service.environments.record.quota.observed_at == fresh.observed_at


@pytest.mark.asyncio
async def test_failed_quota_recheck_does_not_report_manager_recovery():
    service, record, _, upstream = setup_service()

    async def failed_observe(current):
        raise TimeoutError("fixture quota unavailable")

    upstream.observe = failed_observe
    result = await service.verify(record.id, "model-a", True)
    assert result.passed and not result.restored
    assert result.reason == "quota_check_failed"
    assert service.environments.record == record


def test_recovery_projection_clears_only_verified_model_rate_limit_cooldown():
    _, record, _, _ = setup_service()
    other = ModelCooldown(model="other", reason="rate_limit", retry_at=utc_now() + timedelta(days=1))
    selected = ModelCooldown(model="model-a", reason="rate_limit", retry_at=utc_now() + timedelta(days=1))
    timeouts = ModelCooldown(model="model-b", reason="consecutive_timeouts", retry_at=utc_now() + timedelta(days=1))
    blocked = record.model_copy(
        update={
            "status": EnvironmentStatus.COOLING_DOWN,
            "automatic_cooldown": True,
            "last_error": "上游认证失效，请重新认证",
            "model_cooldowns": (selected, other, timeouts),
        }
    )
    result = recovered_record(blocked, "model-a")
    assert result.status == EnvironmentStatus.READY
    assert result.model_cooldowns == (other, timeouts)
    assert result.credential_fingerprints == blocked.credential_fingerprints
    assert not result.automatic_cooldown and result.last_error is None


def test_recovered_record_preserves_other_models_and_writes_observed_model_quotas() -> None:
    now: Final = utc_now()
    record = _record().model_copy(
        update={
            "model_cooldowns": (
                ModelCooldown(model="model-a", reason="rate_limit", retry_at=now + timedelta(hours=2)),
                ModelCooldown(model="model-b", reason="rate_limit", retry_at=now + timedelta(hours=2)),
                ModelCooldown(model="model-c", reason="consecutive_timeouts", retry_at=now + timedelta(hours=2)),
            )
        }
    )
    quota: Final = QuotaSnapshot(
        observed_at=now,
        windows=(QuotaWindow(name="5h", remaining_percent=100, used_percent=0, window_minutes=300),),
    )
    model_quotas: Final = (
        ModelQuotaSnapshot(
            model="model-a",
            quota=QuotaSnapshot(
                observed_at=now,
                windows=(QuotaWindow(name="5h", remaining_percent=100, used_percent=0, window_minutes=300),),
            ),
        ),
    )

    recovered = recovered_record(record, "model-a", quota, model_quotas)

    assert recovered is not None
    assert tuple(item.model for item in recovered.model_cooldowns) == ("model-b", "model-c")
    assert recovered.model_quotas == model_quotas
    assert recovered.quota.windows == quota.windows


def test_recovery_expires_old_card_deadline_but_preserves_future_block():
    _, record, _, _ = setup_service()
    old = record.model_copy(update={"cooldown_until": utc_now() - timedelta(seconds=1)})
    restored = recovered_record(old, "model-a")
    assert restored is not None and restored.cooldown_until is None
    assert recovered_record(record.model_copy(update={"cooldown_until": utc_now() + timedelta(hours=1)}), "model-a") is None


def test_selected_model_cache_invalidated_only_after_verification_and_other_cache_preserved():
    _, record, _, _ = setup_service()
    empty = QuotaSnapshot(windows=(QuotaWindow(name="5h", remaining_percent=0, used_percent=100, window_minutes=300),))
    models = (ModelQuotaSnapshot(model="model-a", quota=empty), ModelQuotaSnapshot(model="model-b", quota=empty))
    record = record.model_copy(update={"model_quotas": models})
    result = recovered_record(record, "model-a", QuotaSnapshot(observed_at=utc_now()))
    assert result.model_quotas[0] == models[1]
    assert result.model_quotas[1].model == "model-a"
    assert result.model_quotas[1].quota.windows == ()
    assert result.model_quotas[1].quota.observed_at is not None


@pytest.mark.asyncio
async def test_fresh_exhausted_model_cannot_be_reported_restored():
    service, record, _, upstream = setup_service()
    empty = QuotaSnapshot(observed_at=utc_now(), windows=(QuotaWindow(name="5h", remaining_percent=0, used_percent=100, window_minutes=300),))
    upstream.observation = upstream.observation.model_copy(update={"model_quotas": (ModelQuotaSnapshot(model="model-a", quota=empty),)})
    result = await service.verify(record.id, "model-a", True)
    assert result.passed and not result.restored and result.reason == "quota_not_recovered"
    assert service.environments.record == record


@pytest.mark.asyncio
async def test_manual_reset_can_verify_old_quota_deadline_without_clearing_independent_deadline():
    service, record, _, upstream = setup_service()
    deadline = utc_now() + timedelta(days=1)
    old = QuotaSnapshot(windows=(QuotaWindow(name="weekly", used_percent=100, remaining_percent=0, window_minutes=10080, resets_at=deadline),))
    service.environments.record = record.model_copy(update={"quota": old, "cooldown_until": deadline})
    upstream.observation = upstream.observation.model_copy(update={"quota": QuotaSnapshot(observed_at=utc_now(), windows=(QuotaWindow(name="weekly", used_percent=1, remaining_percent=99, window_minutes=10080, resets_at=deadline),))})
    assert (await service.verify(record.id, "model-a", True)).restored
    assert service.environments.record.cooldown_until is None
    service.environments.record = record.model_copy(update={"cooldown_until": deadline})
    assert not (await service.verify(record.id, "model-a", True)).restored
