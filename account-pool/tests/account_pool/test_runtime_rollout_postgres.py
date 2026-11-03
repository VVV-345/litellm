"""在显式隔离数据库验证维护准入与正常结算并存，断点跨实例保留。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from account_pool.gateway_contracts import AcquireRejected, AcquireRequest, Candidate, Lease, Resolution
from account_pool.gateway_repository import PostgresLeaseRepository
from account_pool.management_repository import initialize_management_schema
from account_pool.policies import AccountPolicy, PostgresPolicyRepository
from account_pool.runtime_rollout_repository import PostgresRuntimeRolloutRepository
from test_cliproxy_runtime_image import _NEW_IMAGE
from test_quota_reset import setup_service
from test_quota_reset_postgres import database as database


@pytest.mark.asyncio
async def test_gate_survives_restart_blocks_new_leases_but_does_not_block_settlement(database):
    url, environments, _ = database
    _, record, _, _ = setup_service()
    await environments.save(record)
    await PostgresPolicyRepository(url).initialize()
    await initialize_management_schema(url)
    key_id = uuid4()
    candidate = Candidate(id=record.id, channel=record.channel, supplier=record.supplier,
        environment_version=record.version, policy_version=0, enabled_models=("model-a",),
        api_base="http://fixture", api_key="fixture-only", concurrency_limit=2, policy=AccountPolicy())
    resolution = Resolution(card_id=record.id, key_id=key_id, card_version=record.version, policy_version=0,
                            policy=AccountPolicy(), candidates=(candidate,))
    request = AcquireRequest(trusted_card_id=record.id, trusted_key_id=key_id, account_id=record.id,
        request_id=uuid4(), model="model-a", card_version=record.version, policy_version=0,
        account_version=record.version, account_policy_version=0, timeout_seconds=60, attempt=1)
    leases = PostgresLeaseRepository(url)
    admitted = await leases.acquire(request, resolution, candidate, None)
    assert isinstance(admitted, Lease)
    store = PostgresRuntimeRolloutRepository(url)
    checkpoint = await store.begin(record.id, "a" * 40, _NEW_IMAGE)
    restarted = PostgresRuntimeRolloutRepository(url)
    assert await restarted.get(record.id) == checkpoint
    assert await restarted.has_leases(record.id)
    assert isinstance(await leases.acquire(request, resolution, candidate, None), AcquireRejected)
    await asyncio.wait_for(leases.release(admitted, 0, 0), 2)
    assert not await restarted.has_leases(record.id)
    assert await environments.get(record.id) == record
    with pytest.raises(ValueError, match="another release"):
        await restarted.begin(record.id, "b" * 40, _NEW_IMAGE)
    await restarted.save(replace(checkpoint, phase="verified", image_verified=True, credential_state="preserved"))
    next_lease = await leases.acquire(request, resolution, candidate, None)
    assert isinstance(next_lease, Lease)
    await leases.release(next_lease, 0, 0)


@pytest.mark.asyncio
async def test_completed_card_is_not_reinterpreted_when_same_release_resumes(database):
    url, environments, _ = database
    _, record, _, _ = setup_service()
    await environments.save(record)
    store = PostgresRuntimeRolloutRepository(url)
    checkpoint = await store.begin(record.id, "a" * 40, _NEW_IMAGE)
    completed = replace(checkpoint, phase="verified", image_verified=True, credential_state="preserved")
    await store.save(completed)
    await environments.save(record.model_copy(update={"configuration_pending": True}))
    assert await PostgresRuntimeRolloutRepository(url).begin(record.id, "a" * 40, _NEW_IMAGE) == completed
    with pytest.raises(ValueError, match="applied configuration"):
        await store.begin(record.id, "b" * 40, _NEW_IMAGE)
    assert await store.get(record.id) == completed
