"""本文件验证控制面拒绝越权账号、旧 Key、旧策略和禁用卡片，并持久化请求结果。"""

from __future__ import annotations

from typing import Final
from uuid import UUID, uuid4

import pytest
from account_pool.card_keys import CardKeyService
from account_pool.domain import EnvironmentRecord, EnvironmentStatus, GatewayEnvironment, utc_now
from account_pool.error_logs import ErrorLogRecord, ErrorLogService
from account_pool.gateway_contracts import (
    AcquireRejected,
    AcquireRequest,
    Candidate,
    FinishRequest,
    Lease,
    Resolution,
    ResolveRequest,
)
from account_pool.gateway_service import GatewayService
from account_pool.policies import AccountPolicy, PolicyUpdate, RoutingPolicy
from account_pool.result import Success
from account_pool.settings import (
    AccountPoolSettings,
    AccountPoolSettingsHistoryEntry,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
    NetworkSettingsProfile,
    NetworkSettingsValues,
    StreamingSettingsProfile,
    StreamingSettingsValues,
)
from fastapi import HTTPException
from test_account_pool import MemoryRepository, _record
from test_management import MemoryKeys, MemoryLogs, MemoryPolicies


class MemoryLeases:
    def __init__(self) -> None:
        self.leases: dict[UUID, Lease] = {}
        self.bindings: dict[str, UUID] = {}
        self.cooled: tuple[UUID, ...] = ()
        self.settled_tokens: dict[UUID, int] = {}

    async def sticky(self, binding_hash: str | None) -> UUID | None:
        return self.bindings.get(binding_hash) if binding_hash else None

    async def cooling(self) -> tuple[UUID, ...]:
        return self.cooled

    async def clear_cooldown(self, account_id: UUID) -> None:
        self.cooled = tuple(identifier for identifier in self.cooled if identifier != account_id)

    async def acquire(
        self,
        request: AcquireRequest,
        resolution: Resolution,
        candidate: Candidate,
        binding_hash: str | None,
    ) -> Lease | AcquireRejected:
        if (
            binding_hash
            and binding_hash in self.bindings
            and self.bindings[binding_hash] != candidate.id
            and not request.allow_session_rebind
        ):
            return AcquireRejected(reason="session")
        if (
            len(tuple(item for item in self.leases.values() if item.account_id == candidate.id))
            >= candidate.concurrency_limit
        ):
            return AcquireRejected(reason="concurrency")
        budget: Final = candidate.policy.routing.token_budget_limit
        reserved: Final = sum(item.reserved_tokens for item in self.leases.values() if item.account_id == candidate.id)
        if (
            budget is not None
            and self.settled_tokens.get(candidate.id, 0) + reserved + request.estimated_tokens > budget
        ):
            return AcquireRejected(reason="token_budget")
        lease: Final = Lease(
            lease_id=uuid4(),
            card_id=resolution.card_id,
            key_id=resolution.key_id,
            account_id=candidate.id,
            request_id=request.request_id,
            channel=candidate.channel,
            supplier=candidate.supplier,
            model=request.model,
            started_at=utc_now(),
            reserved_tokens=request.estimated_tokens if budget is not None else 0,
            budget_enabled=budget is not None,
            budget_window_seconds=candidate.policy.routing.token_budget_window_seconds if budget is not None else None,
            budget_window_started_at=utc_now() if budget is not None else None,
            attempt=request.attempt,
            routing_reason=request.routing_reason,
        )
        self.leases[lease.lease_id] = lease
        if binding_hash:
            self.bindings[binding_hash] = candidate.id
        return lease

    async def get(self, lease_id: UUID) -> Lease | None:
        return self.leases.get(lease_id)

    async def release(self, lease: Lease, cooldown_seconds: int, actual_tokens: int | None) -> None:
        self.leases.pop(lease.lease_id, None)
        if lease.budget_window_seconds is not None:
            self.settled_tokens[lease.account_id] = self.settled_tokens.get(lease.account_id, 0) + (actual_tokens or 0)
        if cooldown_seconds:
            self.cooled = (*self.cooled, lease.account_id)


class FailingLogs(MemoryLogs):
    async def append(self, event: ErrorLogRecord) -> None:
        raise RuntimeError("log storage unavailable")


class StaticSettings:
    def __init__(self, values: AccountPoolSettings) -> None:
        self.values: Final = values

    async def get(self) -> AccountPoolSettingsView:
        return AccountPoolSettingsView(version=1, values=self.values)

    async def initialize(self) -> None:
        return None

    async def save(self, request: AccountPoolSettingsUpdate) -> AccountPoolSettingsView | None:
        _ = request
        return None

    async def history(self) -> tuple[AccountPoolSettingsHistoryEntry, ...]:
        return ()

    async def rollback(self, expected_version: int, target_version: int) -> AccountPoolSettingsView | None:
        _ = expected_version, target_version
        return None


def gateway(record: EnvironmentRecord) -> GatewayEnvironment:
    return GatewayEnvironment(
        id=record.id,
        routable=record.status == EnvironmentStatus.READY and record.enabled,
        concurrency_limit=record.concurrency_limit,
        enabled_models=record.enabled_models,
        api_base=f"http://cliproxy-{record.id.hex}:8317/v1",
        api_key="internal-only",
    )


@pytest.mark.asyncio
async def test_named_network_and_streaming_profiles_reach_gateway_resolution() -> None:
    card: Final = _record(status=EnvironmentStatus.READY)
    environments: Final = MemoryRepository(card)
    keys: Final = CardKeyService(MemoryKeys())
    issued: Final = await keys.issue(card.id)
    assert isinstance(issued, Success)
    settings: Final = StaticSettings(
        AccountPoolSettings(
            max_attempts=2,
            request_timeout_seconds=120,
            network_profiles=(
                NetworkSettingsProfile(
                    id="network",
                    name="慢线路",
                    card_ids=(card.id,),
                    inherit_global=False,
                    values=NetworkSettingsValues(max_attempts=4, request_timeout_seconds=300),
                ),
            ),
            streaming_profiles=(
                StreamingSettingsProfile(
                    id="stream",
                    name="禁用流式",
                    card_ids=(card.id,),
                    inherit_global=False,
                    values=StreamingSettingsValues(enabled=False),
                ),
            ),
        )
    )
    service: Final = GatewayService(
        keys,
        environments,
        MemoryPolicies(),
        MemoryLeases(),
        ErrorLogService(MemoryLogs()),
        gateway,
        settings,
    )

    resolution: Final = await service.resolve(ResolveRequest(card_key=issued.value.key))

    assert resolution.streaming_mode == "disabled"
    assert resolution.policy.routing.max_attempts == 4
    assert resolution.policy.transport.request_timeout_seconds == 300


@pytest.mark.asyncio
async def test_card_membership_key_revocation_policy_version_and_completion() -> None:
    card: Final = _record(status=EnvironmentStatus.READY)
    other: Final = _record(status=EnvironmentStatus.READY)
    outsiders: Final = _record(status=EnvironmentStatus.READY)
    environments: Final = MemoryRepository(card)
    await environments.save(other)
    await environments.save(outsiders)
    keys: Final = CardKeyService(MemoryKeys())
    issued: Final = await keys.issue(card.id)
    assert isinstance(issued, Success)
    policies: Final = MemoryPolicies()
    await policies.save(card.id, PolicyUpdate(version=0, policy=AccountPolicy(account_ids=(other.id,))))
    logs: Final = MemoryLogs()
    leases: Final = MemoryLeases()
    service: Final = GatewayService(keys, environments, policies, leases, ErrorLogService(logs), gateway)
    resolution: Final = await service.resolve(ResolveRequest(card_key=issued.value.key))
    assert {item.id for item in resolution.candidates} == {card.id, other.id}
    request: Final = AcquireRequest(
        card_key=issued.value.key,
        account_id=other.id,
        request_id=uuid4(),
        model="gpt-5",
        card_version=0,
        policy_version=1,
        account_version=0,
        account_policy_version=0,
        timeout_seconds=30,
        attempt=2,
        routing_reason="retry_failover",
    )
    with pytest.raises(HTTPException) as outside:
        await service.acquire(request.model_copy(update={"account_id": outsiders.id}))
    assert outside.value.status_code == 403
    lease: Final = await service.acquire(request)
    await service.finish(
        FinishRequest(
            lease_id=lease.lease_id,
            http_status=429,
            message="Bearer private",
            endpoint="/v1/responses",
            retryable=True,
            switched_account=True,
            next_account_id=card.id,
            cost_usd=0.00042,
        )
    )
    assert not leases.leases and other.id in leases.cooled
    assert logs.events[0].card_id == card.id and logs.events[0].account_id == other.id
    assert logs.events[0].request_id == request.request_id
    assert logs.events[0].card_key_id == issued.value.status.key_id
    assert logs.events[0].attempt == 2 and logs.events[0].retry_count == 1
    assert logs.events[0].routing_reason == "retry_failover" and logs.events[0].cost_usd == 0.00042
    assert "private" not in logs.events[0].message
    await policies.save(card.id, PolicyUpdate(version=1, policy=AccountPolicy()))
    with pytest.raises(HTTPException):
        await service.acquire(request)
    await keys.revoke(card.id, issued.value.status.key_id)
    with pytest.raises(HTTPException) as revoked:
        await service.resolve(ResolveRequest(card_key=issued.value.key))
    assert revoked.value.status_code == 401


@pytest.mark.asyncio
async def test_token_budget_reserves_active_requests_and_settles_successful_usage() -> None:
    card: Final = _record(status=EnvironmentStatus.READY)
    environments: Final = MemoryRepository(card)
    keys: Final = CardKeyService(MemoryKeys())
    issued: Final = await keys.issue(card.id)
    assert isinstance(issued, Success)
    policies: Final = MemoryPolicies()
    await policies.save(
        card.id,
        PolicyUpdate(
            version=0,
            policy=AccountPolicy(routing=RoutingPolicy(token_budget_limit=100, token_budget_window_seconds=3600)),
        ),
    )
    logs: Final = MemoryLogs()
    leases: Final = MemoryLeases()
    service: Final = GatewayService(keys, environments, policies, leases, ErrorLogService(logs), gateway)
    resolution: Final = await service.resolve(ResolveRequest(card_key=issued.value.key))
    candidate: Final = resolution.candidates[0]
    request: Final = AcquireRequest(
        card_key=issued.value.key,
        account_id=card.id,
        request_id=uuid4(),
        model="gpt-5",
        card_version=resolution.card_version,
        policy_version=resolution.policy_version,
        account_version=candidate.environment_version,
        account_policy_version=candidate.policy_version,
        timeout_seconds=30,
        estimated_tokens=60,
        attempt=1,
    )

    lease: Final = await service.acquire(request)
    with pytest.raises(HTTPException) as exhausted:
        await service.acquire(request.model_copy(update={"request_id": uuid4(), "estimated_tokens": 41}))
    assert exhausted.value.status_code == 409
    assert exhausted.value.detail == {"reason": "token_budget"}

    await service.finish(
        FinishRequest(
            lease_id=lease.lease_id,
            http_status=200,
            message="Request completed",
            endpoint="/v1/responses",
            input_tokens=20,
            output_tokens=10,
        )
    )
    assert leases.settled_tokens[card.id] == 30
    assert (
        await service.acquire(request.model_copy(update={"request_id": uuid4(), "estimated_tokens": 70}))
    ).reserved_tokens == 70


@pytest.mark.asyncio
async def test_failed_request_releases_token_reservation() -> None:
    card: Final = _record(status=EnvironmentStatus.READY)
    environments: Final = MemoryRepository(card)
    keys: Final = CardKeyService(MemoryKeys())
    issued: Final = await keys.issue(card.id)
    assert isinstance(issued, Success)
    policies: Final = MemoryPolicies()
    await policies.save(
        card.id,
        PolicyUpdate(version=0, policy=AccountPolicy(routing=RoutingPolicy(token_budget_limit=50))),
    )
    leases: Final = MemoryLeases()
    service: Final = GatewayService(keys, environments, policies, leases, ErrorLogService(MemoryLogs()), gateway)
    resolution: Final = await service.resolve(ResolveRequest(card_key=issued.value.key))
    candidate: Final = resolution.candidates[0]
    request: Final = AcquireRequest(
        card_key=issued.value.key,
        account_id=card.id,
        request_id=uuid4(),
        model="gpt-5",
        card_version=resolution.card_version,
        policy_version=resolution.policy_version,
        account_version=candidate.environment_version,
        account_policy_version=candidate.policy_version,
        timeout_seconds=30,
        estimated_tokens=50,
        attempt=1,
    )

    lease: Final = await service.acquire(request)
    await service.finish(
        FinishRequest(lease_id=lease.lease_id, http_status=503, message="Unavailable", endpoint="/v1/responses")
    )

    assert leases.settled_tokens[card.id] == 0
    assert (await service.acquire(request.model_copy(update={"request_id": uuid4()}))).reserved_tokens == 50


@pytest.mark.asyncio
async def test_card_disabling_blocks_bound_accounts_and_disabled_member_is_removed() -> None:
    card: Final = _record(status=EnvironmentStatus.READY)
    disabled: Final = _record(status=EnvironmentStatus.DISABLED)
    environments: Final = MemoryRepository(card)
    await environments.save(disabled)
    policies: Final = MemoryPolicies()
    await policies.save(card.id, PolicyUpdate(version=0, policy=AccountPolicy(account_ids=(disabled.id,))))
    keys: Final = CardKeyService(MemoryKeys())
    issued: Final = await keys.issue(card.id)
    assert isinstance(issued, Success)
    service: Final = GatewayService(
        keys, environments, policies, MemoryLeases(), ErrorLogService(MemoryLogs()), gateway
    )
    resolution: Final = await service.resolve(ResolveRequest(card_key=issued.value.key))
    assert len(resolution.candidates) == 1 and resolution.candidates[0].id == card.id
    await environments.save(card.model_copy(update={"enabled": False}))
    with pytest.raises(HTTPException) as result:
        await service.resolve(ResolveRequest(card_key=issued.value.key))
    assert result.value.status_code == 403


@pytest.mark.asyncio
async def test_full_sticky_account_can_rebind_to_next_candidate() -> None:
    card: Final = _record(status=EnvironmentStatus.READY).model_copy(update={"concurrency_limit": 1})
    other: Final = _record(status=EnvironmentStatus.READY).model_copy(update={"concurrency_limit": 1})
    environments: Final = MemoryRepository(card)
    await environments.save(other)
    policies: Final = MemoryPolicies()
    await policies.save(
        card.id,
        PolicyUpdate(
            version=0,
            policy=AccountPolicy(
                account_ids=(other.id,),
                routing=RoutingPolicy(session_affinity=True),
            ),
        ),
    )
    keys: Final = CardKeyService(MemoryKeys())
    issued: Final = await keys.issue(card.id)
    assert isinstance(issued, Success)
    leases: Final = MemoryLeases()
    service: Final = GatewayService(keys, environments, policies, leases, ErrorLogService(MemoryLogs()), gateway)
    session_hash: Final = "a" * 64
    resolution: Final = await service.resolve(ResolveRequest(card_key=issued.value.key, session_hash=session_hash))
    first: Final = next(item for item in resolution.candidates if item.id == card.id)
    second: Final = next(item for item in resolution.candidates if item.id == other.id)

    def request(candidate: Candidate, *, allow_session_rebind: bool = False) -> AcquireRequest:
        return AcquireRequest(
            card_key=issued.value.key,
            session_hash=session_hash,
            account_id=candidate.id,
            request_id=uuid4(),
            model="gpt-5",
            card_version=resolution.card_version,
            policy_version=resolution.policy_version,
            account_version=candidate.environment_version,
            account_policy_version=candidate.policy_version,
            timeout_seconds=30,
            attempt=1,
            allow_session_rebind=allow_session_rebind,
        )

    await service.acquire(request(first))
    with pytest.raises(HTTPException) as full:
        await service.acquire(request(first))
    assert full.value.status_code == 409
    with pytest.raises(HTTPException) as bound:
        await service.acquire(request(second))
    assert bound.value.status_code == 409

    lease: Final = await service.acquire(request(second, allow_session_rebind=True))
    rebound: Final = await service.resolve(ResolveRequest(card_key=issued.value.key, session_hash=session_hash))
    assert lease.account_id == other.id
    assert rebound.sticky_account_id == other.id


@pytest.mark.asyncio
async def test_finish_releases_lease_when_request_log_cannot_be_persisted() -> None:
    card: Final = _record(status=EnvironmentStatus.READY)
    environments: Final = MemoryRepository(card)
    keys: Final = CardKeyService(MemoryKeys())
    issued: Final = await keys.issue(card.id)
    assert isinstance(issued, Success)
    leases: Final = MemoryLeases()
    service: Final = GatewayService(
        keys,
        environments,
        MemoryPolicies(),
        leases,
        ErrorLogService(FailingLogs()),
        gateway,
    )
    resolution: Final = await service.resolve(ResolveRequest(card_key=issued.value.key))
    candidate: Final = resolution.candidates[0]
    lease: Final = await service.acquire(
        AcquireRequest(
            card_key=issued.value.key,
            account_id=candidate.id,
            request_id=uuid4(),
            model="gpt-5",
            card_version=resolution.card_version,
            policy_version=resolution.policy_version,
            account_version=candidate.environment_version,
            account_policy_version=candidate.policy_version,
            timeout_seconds=30,
            attempt=1,
        )
    )

    with pytest.raises(RuntimeError, match="log storage unavailable"):
        await service.finish(
            FinishRequest(
                lease_id=lease.lease_id,
                http_status=429,
                message="rate limited",
                endpoint="/v1/responses",
            )
        )

    assert lease.lease_id not in leases.leases
    assert card.id in leases.cooled
