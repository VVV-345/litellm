"""提供内存和 Redis 状态存储，管理账号并发、租约、额度与健康状态。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from functools import reduce
from typing import Final, Protocol, TypeVar, cast
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from redis.asyncio import Redis

from account_pool.eligibility import (
    EligibilityExclusion,
    EligibilityScope,
    EligibilitySource,
    EligibilityState,
    EligibilitySubject,
    activate_exclusion,
    candidate_evidence,
    candidate_exclusion,
    candidate_probe_evidence,
    effective_state,
    exclusion_subject,
    exclusions_after_settlement,
    retain_configured_exclusions,
    settlement_exclusion,
)
from account_pool.eligibility.redis import (
    billing_route_eligibility_key,
    channel_eligibility_key,
    decode_exclusions,
    deployment_eligibility_key,
    eligibility_key,
    eligibility_subjects,
    model_eligibility_key,
)
from account_pool.health.settlement import (
    HealthTransitionAction,
    SettlementHealthTransition,
    classify_settlement,
)
from account_pool.models import (
    AccountConfig,
    AccountSnapshot,
    Health,
    Lease,
    QuotaSnapshot,
    QuotaUnit,
    ReserveRejected,
    ReserveResult,
    ReserveSuccess,
    SettleRequest,
)
from account_pool.quota import (
    QuotaReservation,
    QuotaReserveRejected,
    RuntimeQuotaWindow,
    apply_quota_usage,
    reconcile_quota_windows,
    release_quota_capacity,
    reserve_quota_capacity,
    synchronize_quota_exclusions,
)
from account_pool.quota.backend import QuotaBackendState, QuotaBackendWindowState
from account_pool.quota.redis import (
    REDIS_QUOTA_DECIMAL_SCALE,
    EncodedQuotaAmount,
    RedisQuotaCodecFailure,
    RedisQuotaWindowRecord,
    configure_quota_script_args,
    decode_quota_window,
    encode_account_quota_windows,
    encode_quota_amount,
    encode_quota_window,
    prepare_quota_reservation_plan,
    prepare_quota_settlement_amounts,
    quota_manifest_key,
    quota_usage_key,
    quota_window_hash_fields,
    quota_window_key,
)
from account_pool.quota.redis_scripts import (
    REDIS_CONFIGURE_QUOTA_WINDOW_SCRIPT,
    REDIS_QUOTA_HEARTBEAT_LUA,
    REDIS_QUOTA_RELEASE_LUA,
    REDIS_QUOTA_RESERVE_CHECK_LUA,
    REDIS_QUOTA_RESERVE_COMMIT_LUA,
    REDIS_QUOTA_RUNTIME_LUA,
    REDIS_QUOTA_SETTLE_LUA,
)
from account_pool.quota.runtime import quota_window_exclusions
from account_pool.routing.latency import LATENCY_EWMA_ALPHA, DeploymentLatencyMetric, update_latency_ewma

_RedisOperation = TypeVar("_RedisOperation")
_REDIS_OPERATION_BATCH_SIZE: Final = 32


async def _gather_redis_operations(
    operations: tuple[Awaitable[_RedisOperation], ...],
) -> tuple[_RedisOperation, ...]:
    results: list[_RedisOperation] = []
    for start in range(0, len(operations), _REDIS_OPERATION_BATCH_SIZE):
        results.extend(await asyncio.gather(*operations[start : start + _REDIS_OPERATION_BATCH_SIZE]))
    return tuple(results)


class StateStore(Protocol):
    async def configure(self, accounts: tuple[AccountConfig, ...]) -> None: ...

    async def snapshots(self) -> tuple[AccountSnapshot, ...]: ...

    async def eligibility_exclusions(self) -> tuple[EligibilityExclusion, ...]: ...

    async def quota_backend_state(self, account_id: str | None = None) -> QuotaBackendState | None: ...

    async def reserve(
        self,
        account: AccountConfig,
        deployment_id: str,
        billing_route_id: str | None,
        public_model: str,
        request_id: str,
        estimated_tokens: int,
        ttl_seconds: int,
        probe: bool = False,
    ) -> ReserveResult: ...

    async def settle(self, request: SettleRequest) -> bool: ...

    async def read_lease(self, lease_id: str) -> Lease | None: ...

    async def release(self, lease_id: str) -> bool: ...

    async def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool: ...

    async def next_sequence(self, model: str) -> int: ...

    async def latency_metrics(self) -> tuple[DeploymentLatencyMetric, ...]: ...

    async def restore_latency_metrics(self, metrics: tuple[DeploymentLatencyMetric, ...]) -> None: ...

    async def set_latency_metric(self, metric: DeploymentLatencyMetric) -> None: ...

    async def sweep_expired(self) -> tuple[Lease, ...]: ...

    async def close(self) -> None: ...


class _AsyncClosable(Protocol):
    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _MemoryLeaseState:
    lease: Lease
    usage_applied: bool
    probe_subject: EligibilitySubject | None
    quota_reservations: tuple[QuotaReservation, ...]


class MemoryStateStore:
    def __init__(self, maximum_lease_seconds: int = 3_600) -> None:
        if maximum_lease_seconds < 1:
            raise ValueError("maximum_lease_seconds must be positive")
        self._lock = asyncio.Lock()
        self._maximum_lease_seconds: Final = maximum_lease_seconds
        self._runtime: dict[str, AccountSnapshot] = {}
        self._leases: dict[str, _MemoryLeaseState] = {}
        self._requests: dict[str, str] = {}
        self._sequences: dict[str, int] = {}
        self._latency_metrics: dict[str, DeploymentLatencyMetric] = {}
        self._accounts: dict[str, AccountConfig] = {}
        self._quota_windows: dict[str, tuple[RuntimeQuotaWindow, ...]] = {}
        self._exclusions: tuple[EligibilityExclusion, ...] = ()
        self._probe_leases: dict[EligibilitySubject, str] = {}
        self._quota_generation: UUID | None = None

    async def configure(self, accounts: tuple[AccountConfig, ...]) -> None:
        async with self._lock:
            previous_accounts: Final = self._accounts
            self._accounts = {account.id: account for account in accounts}
            configured_deployments: Final = frozenset(
                deployment.litellm_model_id for account in accounts for deployment in account.deployments
            )
            self._latency_metrics = {
                deployment_id: metric
                for deployment_id, metric in self._latency_metrics.items()
                if deployment_id in configured_deployments
            }
            self._runtime = {
                account.id: _configured_snapshot(
                    account=account,
                    previous_account=previous_accounts.get(account.id),
                    existing=self._runtime.get(account.id),
                )
                for account in accounts
            }
            self._quota_windows = {
                account.id: reconcile_quota_windows(
                    previous=self._quota_windows.get(account.id, ()),
                    configured=account.quota_windows,
                )
                for account in accounts
            }
            configured_exclusions: Final = retain_configured_exclusions(self._exclusions, accounts)
            self._exclusions = _synchronize_all_quota_exclusions(
                exclusions=configured_exclusions,
                accounts=accounts,
                quota_windows=self._quota_windows,
            )

    async def snapshots(self) -> tuple[AccountSnapshot, ...]:
        async with self._lock:
            return tuple(self._runtime.values())

    async def eligibility_exclusions(self) -> tuple[EligibilityExclusion, ...]:
        async with self._lock:
            return self._exclusions

    async def reserve(
        self,
        account: AccountConfig,
        deployment_id: str,
        billing_route_id: str | None,
        public_model: str,
        request_id: str,
        estimated_tokens: int,
        ttl_seconds: int,
        probe: bool = False,
    ) -> ReserveResult:
        async with self._lock:
            existing_id: Final = self._requests.get(request_id)
            existing: Final = self._leases.get(existing_id) if existing_id is not None else None
            if existing is not None and not existing.lease.released:
                return ReserveSuccess(lease=existing.lease)

            now: Final = time.time()
            runtime: Final = self._runtime[account.id]
            exclusion: Final = candidate_exclusion(
                exclusions=self._exclusions,
                account_id=account.id,
                model=public_model,
                deployment_id=deployment_id,
                billing_route_id=billing_route_id,
                now=now,
                ignored_sources=frozenset((EligibilitySource.HEALTH,)) if probe else frozenset(),
            )
            if exclusion is not None:
                return ReserveRejected(reason=exclusion.reason_code)
            evidence: Final = (
                candidate_probe_evidence(
                    exclusions=self._exclusions,
                    account_id=account.id,
                    model=public_model,
                    deployment_id=deployment_id,
                    billing_route_id=billing_route_id,
                    now=now,
                )
                if probe
                else candidate_evidence(
                    exclusions=self._exclusions,
                    account_id=account.id,
                    model=public_model,
                    deployment_id=deployment_id,
                    billing_route_id=billing_route_id,
                    now=now,
                )
            )
            probe_subject: Final = (
                exclusion_subject(evidence)
                if evidence is not None and (probe or effective_state(evidence, now) == EligibilityState.HALF_OPEN)
                else EligibilitySubject(
                    scope=EligibilityScope.DEPLOYMENT,
                    account_id=account.id,
                    model=public_model,
                    deployment_id=deployment_id,
                )
                if probe
                else None
            )
            if probe_subject is not None and probe_subject in self._probe_leases:
                return ReserveRejected(reason="half_open_probe_inflight")
            rejection: Final = _availability_rejection(runtime=runtime, now=now, probe=probe)
            if rejection is not None:
                return ReserveRejected(reason=rejection)
            quota_reserve: Final = reserve_quota_capacity(
                windows=self._quota_windows.get(account.id, ()),
                public_model=public_model,
                billing_route_id=billing_route_id,
                estimated_tokens=estimated_tokens,
                now=now,
            )
            if isinstance(quota_reserve, QuotaReserveRejected):
                return ReserveRejected(reason=quota_reserve.reason_code)

            absolute_expires_at: Final = now + self._maximum_lease_seconds
            lease: Final = Lease(
                lease_id=uuid4().hex,
                generation_id=self._quota_generation,
                request_id=request_id,
                account_id=account.id,
                deployment_id=deployment_id,
                public_model=public_model,
                billing_route_id=billing_route_id,
                probe=probe,
                expires_at=min(now + ttl_seconds, absolute_expires_at),
                absolute_expires_at=absolute_expires_at,
            )
            self._runtime[account.id] = runtime.model_copy(update={"inflight": runtime.inflight + 1})
            self._quota_windows[account.id] = quota_reserve.windows
            self._exclusions = synchronize_quota_exclusions(
                exclusions=self._exclusions,
                account=account,
                windows=quota_reserve.windows,
            )
            self._leases[lease.lease_id] = _MemoryLeaseState(
                lease=lease,
                usage_applied=False,
                probe_subject=probe_subject,
                quota_reservations=quota_reserve.reservations,
            )
            if probe_subject is not None:
                self._probe_leases[probe_subject] = lease.lease_id
            self._requests[request_id] = lease.lease_id
            return ReserveSuccess(lease=lease)

    async def settle(self, request: SettleRequest) -> bool:
        async with self._lock:
            lease_state: Final = self._leases.get(request.lease_id)
            if lease_state is None:
                return False
            if lease_state.usage_applied:
                return True

            runtime: Final = self._runtime[lease_state.lease.account_id]
            account: Final = self._accounts[lease_state.lease.account_id]
            consumption: Final = _consumption(account=account, request=request)
            quota: Final = _decrement_quota(runtime.quota, consumption)
            now: Final = time.time()
            transition: Final = classify_settlement(request, now)
            health_update: Final = _health_after_settlement(
                runtime=runtime,
                transition=transition,
            )
            self._exclusions = exclusions_after_settlement(
                exclusions=self._exclusions,
                lease=lease_state.lease,
                transition=transition,
                now=now,
                transient_threshold_reached=(
                    transition.action != HealthTransitionAction.TRANSIENT_FAILURE
                    or runtime.consecutive_failures + 1 >= 3
                ),
            )
            quota_windows: Final = apply_quota_usage(
                windows=self._quota_windows.get(account.id, ()),
                reservations=lease_state.quota_reservations,
                lease=lease_state.lease,
                request=request,
                now=now,
            )
            self._quota_windows[account.id] = quota_windows
            self._exclusions = synchronize_quota_exclusions(
                exclusions=self._exclusions,
                account=account,
                windows=quota_windows,
            )
            updated: Final = runtime.model_copy(update={"quota": quota, **health_update})
            settled_lease: Final = lease_state.lease.model_copy(update={"settled": True})
            self._runtime[lease_state.lease.account_id] = updated
            self._leases[request.lease_id] = _MemoryLeaseState(
                lease=settled_lease,
                usage_applied=True,
                probe_subject=lease_state.probe_subject,
                quota_reservations=(),
            )
            if (
                request.success
                and request.latency_ms is not None
                and request.latency_ms > 0
                and not lease_state.lease.probe
            ):
                deployment_id: Final = lease_state.lease.deployment_id
                self._latency_metrics[deployment_id] = update_latency_ewma(
                    current=self._latency_metrics.get(deployment_id),
                    deployment_id=deployment_id,
                    latency_ms=request.latency_ms,
                    observed_at=now,
                )
            return True

    async def release(self, lease_id: str) -> bool:
        async with self._lock:
            lease_state: Final = self._leases.get(lease_id)
            if lease_state is None:
                return False
            if lease_state.lease.released:
                return True

            runtime: Final = self._runtime[lease_state.lease.account_id]
            if not lease_state.usage_applied:
                quota_windows: Final = release_quota_capacity(
                    windows=self._quota_windows.get(lease_state.lease.account_id, ()),
                    reservations=lease_state.quota_reservations,
                )
                self._quota_windows[lease_state.lease.account_id] = quota_windows
                self._exclusions = synchronize_quota_exclusions(
                    exclusions=self._exclusions,
                    account=self._accounts[lease_state.lease.account_id],
                    windows=quota_windows,
                )
            released: Final = lease_state.lease.model_copy(update={"released": True})
            self._runtime[lease_state.lease.account_id] = runtime.model_copy(
                update={"inflight": max(0, runtime.inflight - 1)}
            )
            self._leases[lease_id] = _MemoryLeaseState(
                lease=released,
                usage_applied=lease_state.usage_applied,
                probe_subject=lease_state.probe_subject,
                quota_reservations=(),
            )
            if lease_state.probe_subject is not None and self._probe_leases.get(lease_state.probe_subject) == lease_id:
                del self._probe_leases[lease_state.probe_subject]
            return True

    async def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        async with self._lock:
            lease_state: Final = self._leases.get(lease_id)
            if lease_state is None or lease_state.lease.released:
                return False
            now: Final = time.time()
            if now >= lease_state.lease.absolute_expires_at:
                return False
            extended: Final = lease_state.lease.model_copy(
                update={"expires_at": min(now + ttl_seconds, lease_state.lease.absolute_expires_at)}
            )
            self._leases[lease_id] = _MemoryLeaseState(
                lease=extended,
                usage_applied=lease_state.usage_applied,
                probe_subject=lease_state.probe_subject,
                quota_reservations=lease_state.quota_reservations,
            )
            return True

    async def next_sequence(self, model: str) -> int:
        async with self._lock:
            value: Final = self._sequences.get(model, 0) + 1
            self._sequences[model] = value
            return value

    async def latency_metrics(self) -> tuple[DeploymentLatencyMetric, ...]:
        async with self._lock:
            return tuple(self._latency_metrics.values())

    async def restore_latency_metrics(self, metrics: tuple[DeploymentLatencyMetric, ...]) -> None:
        async with self._lock:
            configured: Final = frozenset(
                deployment.litellm_model_id for account in self._accounts.values() for deployment in account.deployments
            )
            supplied: Final = {metric.deployment_id: metric for metric in metrics}
            self._latency_metrics = {
                deployment_id: newest
                for deployment_id in configured
                for newest in (_newest_metric(self._latency_metrics.get(deployment_id), supplied.get(deployment_id)),)
                if newest is not None
            }

    async def set_latency_metric(self, metric: DeploymentLatencyMetric) -> None:
        async with self._lock:
            self._latency_metrics[metric.deployment_id] = metric

    async def sweep_expired(self) -> tuple[Lease, ...]:
        now: Final = time.time()
        async with self._lock:
            expired: Final = tuple(
                state.lease
                for state in self._leases.values()
                if not state.lease.released and state.lease.expires_at <= now
            )
        results: Final = await asyncio.gather(*(self.release(lease.lease_id) for lease in expired))
        return tuple(lease for lease, released in zip(expired, results, strict=True) if released)

    async def close(self) -> None:
        return None

    async def quota_backend_state(self, account_id: str | None = None) -> QuotaBackendState:
        async with self._lock:
            account_ids: Final = frozenset(self._accounts) if account_id is None else frozenset((account_id,))
            windows: Final = tuple(
                QuotaBackendWindowState(
                    account_id=current_account_id,
                    window=window,
                    reservation_expires_at=self._reservation_expires_at(
                        account_id=current_account_id,
                        window_id=window.config.window_id,
                    ),
                )
                for current_account_id in account_ids
                for window in self._quota_windows.get(current_account_id, ())
            )
            return QuotaBackendState(generation_id=self._quota_generation, windows=windows)

    async def read_quota_generation(self) -> UUID | None:
        async with self._lock:
            return self._quota_generation

    async def restore_quota_backend(
        self,
        generation_id: UUID,
        windows: tuple[QuotaBackendWindowState, ...],
    ) -> bool:
        async with self._lock:
            expected: Final = frozenset(
                (account.id, window.window_id)
                for account in self._accounts.values()
                for window in account.quota_windows
            )
            supplied: Final = frozenset((state.account_id, state.window.config.window_id) for state in windows)
            if supplied != expected or any(state.window.reserved != 0 for state in windows):
                return False
            restored: Final = {
                account.id: tuple(state.window for state in windows if state.account_id == account.id)
                for account in self._accounts.values()
            }
            self._quota_windows = restored
            self._runtime = {
                account_id: snapshot.model_copy(update={"inflight": 0})
                for account_id, snapshot in self._runtime.items()
            }
            self._leases = {}
            self._requests = {}
            self._probe_leases = {}
            self._quota_generation = generation_id
            self._exclusions = _synchronize_all_quota_exclusions(
                exclusions=self._exclusions,
                accounts=tuple(self._accounts.values()),
                quota_windows=restored,
            )
            return True

    async def set_quota_generation(self, generation_id: UUID | None) -> None:
        async with self._lock:
            self._quota_generation = generation_id

    async def read_lease(self, lease_id: str) -> Lease | None:
        async with self._lock:
            state: Final = self._leases.get(lease_id)
            return None if state is None else state.lease

    def _reservation_expires_at(self, account_id: str, window_id: str) -> float | None:
        expiries: Final = tuple(
            state.lease.absolute_expires_at
            for state in self._leases.values()
            if state.lease.account_id == account_id
            and not state.lease.released
            and not state.usage_applied
            and any(reservation.window_id == window_id for reservation in state.quota_reservations)
        )
        return max(expiries, default=None)


# Redis 脚本把资格、并发、额度预占和租约写入放在同一个原子操作中。
_RESERVE_SCRIPT = (
    REDIS_QUOTA_RUNTIME_LUA
    + r"""
local requested_quota_count = tonumber(ARGV[11])
local expected_generation = ARGV[13 + requested_quota_count]
local runtime_generation = redis.call('GET', KEYS[10 + requested_quota_count * 2]) or ''
if expected_generation ~= '' and runtime_generation ~= expected_generation then
  return {0, '', 'quota_generation_mismatch'}
end
local existing = redis.call('GET', KEYS[4])
if existing then
  return {2, existing, 'existing'}
end
local probe_mode = ARGV[14 + requested_quota_count] == '1'
local absolute_expires_at = ARGV[15 + requested_quota_count]
local function exclusion_status(key, now, ignore_health)
  local entries = redis.call('HGETALL', key)
  local half_open = false
  local health_evidence = false
  for index = 1, #entries, 2 do
    local field = entries[index]
    local value = entries[index + 1]
    local field_separator = string.find(field, '|', 1, true)
    if not field_separator then return 'eligibility_state_invalid', false, false end
    local source = string.sub(field, 1, field_separator - 1)
    local reason = string.sub(field, field_separator + 1)
    local value_separator = string.find(value, '|', 1, true)
    if not value_separator then return 'eligibility_state_invalid', false, false end
    local retry_at = tonumber(string.sub(value, value_separator + 1)) or 0
    if retry_at == 0 or retry_at > now then
      if not ignore_health or source ~= 'health' then return reason, false, false end
      health_evidence = true
    else
      half_open = true
    end
  end
  return nil, half_open, health_evidence
end
local enabled = redis.call('HGET', KEYS[1], 'enabled')
local health = redis.call('HGET', KEYS[1], 'health')
local cooldown = tonumber(redis.call('HGET', KEYS[1], 'cooldown_until') or '0')
local inflight = tonumber(redis.call('GET', KEYS[2]) or '0')
local max_concurrency = tonumber(redis.call('HGET', KEYS[1], 'max_concurrency') or '0')
local quota_total = redis.call('HGET', KEYS[1], 'quota_total')
local quota_five = redis.call('HGET', KEYS[1], 'quota_five_hour')
local quota_weekly = redis.call('HGET', KEYS[1], 'quota_weekly')
local now = tonumber(ARGV[8])
if enabled ~= '1' or health == 'disabled' then return {0, '', 'disabled'} end
local probe_source_key = nil
local health_source_key = nil
for key_index = 6, 9 do
  local reason, half_open, health_evidence = exclusion_status(KEYS[key_index], now, probe_mode)
  if reason then return {0, '', reason} end
  if not probe_source_key and half_open then probe_source_key = KEYS[key_index] end
  if not health_source_key and health_evidence then health_source_key = KEYS[key_index] end
end
if not probe_mode and health == 'unhealthy' then return {0, '', 'unhealthy'} end
if not probe_mode and cooldown > now then return {0, '', 'cooldown'} end
if probe_mode and not probe_source_key then probe_source_key = health_source_key or KEYS[8] end
local probe_key = ''
if probe_source_key then
  probe_key = 'pool:eligibility:probe:' .. probe_source_key
  if redis.call('EXISTS', probe_key) == 1 then return {0, '', 'half_open_probe_inflight'} end
end
if inflight >= max_concurrency then return {0, '', 'capacity'} end
if quota_total and quota_total ~= '' and tonumber(quota_total) <= 0 then return {0, '', 'total_quota'} end
if quota_five and quota_five ~= '' and tonumber(quota_five) <= 0 then return {0, '', 'five_hour_quota'} end
if quota_weekly and quota_weekly ~= '' and tonumber(quota_weekly) <= 0 then return {0, '', 'weekly_quota'} end
"""
    + REDIS_QUOTA_RESERVE_CHECK_LUA
    + r"""
redis.call('INCR', KEYS[2])
redis.call('HSET', KEYS[3],
  'lease_id', ARGV[1], 'request_id', ARGV[2], 'account_id', ARGV[3],
  'deployment_id', ARGV[4], 'public_model', ARGV[5], 'billing_route_id', ARGV[6], 'expires_at', ARGV[7],
  'absolute_expires_at', absolute_expires_at,
  'probe_key', probe_key, 'quota_count', quota_count, 'generation_id', ARGV[13 + quota_count],
  'probe', ARGV[14 + quota_count], 'settled', '0', 'released', '0')
"""
    + REDIS_QUOTA_RESERVE_COMMIT_LUA
    + r"""
if probe_key ~= '' then redis.call('SET', probe_key, ARGV[1], 'EX', ARGV[10]) end
redis.call('SET', KEYS[4], ARGV[1], 'EX', ARGV[9])
redis.call('ZADD', KEYS[5], ARGV[7], ARGV[1])
return {1, ARGV[1], 'reserved'}
"""
)


# release 和租约回收必须归还预占，并释放额度 half-open 探测令牌。
_RELEASE_SCRIPT = (
    REDIS_QUOTA_RUNTIME_LUA
    + r"""
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local lease_generation = redis.call('HGET', KEYS[1], 'generation_id') or ''
local runtime_generation = redis.call('GET', KEYS[3]) or ''
if lease_generation ~= '' and runtime_generation ~= lease_generation then
  redis.call('ZREM', KEYS[2], ARGV[2])
  redis.call('EXPIRE', KEYS[1], ARGV[3])
  return 0
end
if redis.call('HGET', KEYS[1], 'released') == '1' then return 1 end
local account_id = redis.call('HGET', KEYS[1], 'account_id')
local lease_id = redis.call('HGET', KEYS[1], 'lease_id')
local probe_key = redis.call('HGET', KEYS[1], 'probe_key')
local settled = redis.call('HGET', KEYS[1], 'settled')
"""
    + REDIS_QUOTA_RELEASE_LUA
    + r"""
local inflight_key = ARGV[1] .. account_id .. ':inflight'
local inflight = tonumber(redis.call('GET', inflight_key) or '0')
if inflight > 0 then redis.call('DECR', inflight_key) end
if probe_key and probe_key ~= '' and redis.call('GET', probe_key) == lease_id then redis.call('DEL', probe_key) end
redis.call('HSET', KEYS[1], 'released', '1')
redis.call('ZREM', KEYS[2], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""
)


# heartbeat 同时延长普通资格探测和额度窗口探测的有效期。
_HEARTBEAT_SCRIPT = (
    r"""
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local lease_generation = redis.call('HGET', KEYS[1], 'generation_id') or ''
local runtime_generation = redis.call('GET', KEYS[3]) or ''
if lease_generation ~= '' and runtime_generation ~= lease_generation then return 0 end
if redis.call('HGET', KEYS[1], 'released') == '1' then return 0 end
local lease_id = redis.call('HGET', KEYS[1], 'lease_id')
local probe_key = redis.call('HGET', KEYS[1], 'probe_key')
local absolute_expires_at = tonumber(redis.call('HGET', KEYS[1], 'absolute_expires_at') or '')
local now = tonumber(ARGV[4])
if not absolute_expires_at or not now or now >= absolute_expires_at then return 0 end
local expires_at = math.min(tonumber(ARGV[1]), absolute_expires_at)
local lease_ttl = math.ceil(expires_at - now)
if lease_ttl <= 0 then return 0 end
if probe_key and probe_key ~= '' and redis.call('GET', probe_key) ~= lease_id then return 0 end
if probe_key and probe_key ~= '' then redis.call('EXPIRE', probe_key, lease_ttl) end
"""
    + REDIS_QUOTA_HEARTBEAT_LUA
    + r"""
redis.call('HSET', KEYS[1], 'expires_at', expires_at)
redis.call('ZADD', KEYS[2], expires_at, ARGV[2])
return 1
"""
)


# settle 先校验所有窗口，再用可信 usage 替换 acquire 阶段的预占。
_SETTLE_SCRIPT = (
    REDIS_QUOTA_RUNTIME_LUA
    + r"""
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local lease_generation = redis.call('HGET', KEYS[1], 'generation_id') or ''
local runtime_generation = redis.call('GET', KEYS[7]) or ''
if lease_generation ~= '' and runtime_generation ~= lease_generation then return 0 end
if redis.call('HGET', KEYS[1], 'settled') == '1' then return 2 end
local account_id = redis.call('HGET', KEYS[1], 'account_id')
local lease_id = redis.call('HGET', KEYS[1], 'lease_id')
local state_key = ARGV[1] .. account_id .. ':state'
local action = ARGV[2]
local consumption = tonumber(ARGV[3] or '0')
local cooldown_until = tonumber(ARGV[4] or '0')
local reason_code = ARGV[5]
local exclusion_scope = ARGV[6]
local exclusion_source = ARGV[7]
local starts_at = ARGV[8]
local retry_at = ARGV[9]
"""
    + REDIS_QUOTA_SETTLE_LUA
    + r"""
if consumption > 0 then
  for _, field in ipairs({'quota_total', 'quota_five_hour', 'quota_weekly'}) do
    local value = redis.call('HGET', state_key, field)
    if value and value ~= '' then
      redis.call('HSET', state_key, field, math.max(0, tonumber(value) - consumption))
    end
  end
end
local write_exclusion = false
if action == 'success' then
  redis.call('HSET', state_key, 'health', 'healthy', 'consecutive_failures', '0', 'cooldown_until', '0', 'reason_code', '')
  for key_index = 2, 5 do redis.call('DEL', KEYS[key_index]) end
elseif action == 'disable' then
  redis.call('HSET', state_key, 'health', 'unhealthy', 'reason_code', reason_code)
  write_exclusion = true
elseif action == 'observe' then
  write_exclusion = exclusion_scope ~= ''
elseif action == 'cooldown' then
  write_exclusion = true
else
  local failures = redis.call('HINCRBY', state_key, 'consecutive_failures', 1)
  if failures >= 3 then
    redis.call('HSET', state_key, 'health', 'cooldown', 'cooldown_until', cooldown_until, 'reason_code', reason_code)
    write_exclusion = true
  else
    redis.call('HSET', state_key, 'health', 'degraded', 'reason_code', reason_code)
  end
end
if write_exclusion and exclusion_scope ~= '' then
  local target_key = KEYS[4]
  if exclusion_scope == 'channel' then target_key = KEYS[2] end
  if exclusion_scope == 'model' then target_key = KEYS[3] end
  if exclusion_scope == 'billing_route' then target_key = KEYS[5] end
  redis.call('HSET', target_key, exclusion_source .. '|' .. reason_code, starts_at .. '|' .. retry_at)
end
local latency = tonumber(ARGV[16] or '0')
local probe = redis.call('HGET', KEYS[1], 'probe') == '1'
if action == 'success' and latency > 0 and not probe then
  local current = tonumber(redis.call('HGET', KEYS[6], 'ewma_ms') or '')
  local count = tonumber(redis.call('HGET', KEYS[6], 'sample_count') or '0')
  local alpha = tonumber(ARGV[17])
  local ewma = current and (alpha * latency + (1 - alpha) * current) or latency
  redis.call('HSET', KEYS[6], 'deployment_id', redis.call('HGET', KEYS[1], 'deployment_id'),
    'ewma_ms', ewma, 'sample_count', count + 1, 'observed_at', ARGV[13])
end
redis.call('HSET', KEYS[1], 'settled', '1')
return 1
"""
)


_RESERVE_RESULT_ADAPTER: Final = TypeAdapter(tuple[int, str, str])
_SCRIPT_STATUS_ADAPTER: Final = TypeAdapter(int)


class RedisStateStore:
    _prefix = "pool:account:"
    _expiries = "pool:leases:expiries"
    _quota_generation_key = "pool:quota:generation"
    _quota_schema_version_key = "pool:quota:schema-version"
    _quota_schema_version = "2"

    def __init__(self, url: str, maximum_lease_seconds: int = 3_600, client: Redis[str] | None = None) -> None:
        if maximum_lease_seconds < 1:
            raise ValueError("maximum_lease_seconds must be positive")
        self._redis: Final = client or Redis.from_url(url, decode_responses=True)
        self._maximum_lease_seconds: Final = maximum_lease_seconds
        self._accounts: dict[str, AccountConfig] = {}
        self._reserve_script = self._redis.register_script(_RESERVE_SCRIPT)
        self._release_script = self._redis.register_script(_RELEASE_SCRIPT)
        self._heartbeat_script = self._redis.register_script(_HEARTBEAT_SCRIPT)
        self._settle_script = self._redis.register_script(_SETTLE_SCRIPT)
        self._quota_configure_script = self._redis.register_script(REDIS_CONFIGURE_QUOTA_WINDOW_SCRIPT)

    async def configure(self, accounts: tuple[AccountConfig, ...]) -> None:
        previous_accounts: Final = self._accounts
        runtime_reconfigure: Final = bool(previous_accounts)
        previous_deployments: Final = frozenset(
            deployment.litellm_model_id for account in previous_accounts.values() for deployment in account.deployments
        )
        configured_deployments: Final = frozenset(
            deployment.litellm_model_id for account in accounts for deployment in account.deployments
        )
        stale_eligibility: Final = frozenset(eligibility_subjects(tuple(previous_accounts.values()))) - frozenset(
            eligibility_subjects(accounts)
        )
        stale_latency: Final = previous_deployments - configured_deployments
        self._accounts = {account.id: account for account in accounts}
        quota_failures: Final = await _gather_redis_operations(
            tuple(self._configure_quota_account(account) for account in accounts)
        )
        await _gather_redis_operations(
            tuple(
                self._configure_account(
                    account,
                    reset_quotas=runtime_reconfigure
                    and (
                        previous_accounts.get(account.id) is None
                        or previous_accounts[account.id].quotas != account.quotas
                    ),
                    quota_failure=quota_failure,
                )
                for account, quota_failure in zip(accounts, quota_failures, strict=True)
            )
        )
        if stale_eligibility:
            await self._redis.delete(*(eligibility_key(subject) for subject in stale_eligibility))
        if stale_latency:
            await self._redis.delete(*(self._latency_key(deployment_id) for deployment_id in stale_latency))

    async def snapshots(self) -> tuple[AccountSnapshot, ...]:
        return tuple([await self._snapshot(account) for account in self._accounts.values()])

    async def eligibility_exclusions(self) -> tuple[EligibilityExclusion, ...]:
        subjects: Final = eligibility_subjects(tuple(self._accounts.values()))
        encoded: Final = await _gather_redis_operations(
            tuple(self._redis.hgetall(eligibility_key(subject)) for subject in subjects)
        )
        standard: Final = tuple(
            exclusion
            for subject, entries in zip(subjects, encoded, strict=True)
            for exclusion in decode_exclusions(subject, entries)
        )
        quota: Final = await _gather_redis_operations(
            tuple(self._quota_exclusions(account) for account in self._accounts.values())
        )
        return (*standard, *(exclusion for exclusions in quota for exclusion in exclusions))

    async def reserve(
        self,
        account: AccountConfig,
        deployment_id: str,
        billing_route_id: str | None,
        public_model: str,
        request_id: str,
        estimated_tokens: int,
        ttl_seconds: int,
        probe: bool = False,
    ) -> ReserveResult:
        quota_plan: Final = prepare_quota_reservation_plan(
            account=account,
            public_model=public_model,
            billing_route_id=billing_route_id,
            estimated_tokens=estimated_tokens,
        )
        if isinstance(quota_plan, RedisQuotaCodecFailure):
            return ReserveRejected(reason=f"quota_configuration_{quota_plan.code}")
        lease_id: Final = uuid4().hex
        now: Final = time.time()
        absolute_expires_at: Final = now + self._maximum_lease_seconds
        expires_at: Final = min(now + ttl_seconds, absolute_expires_at)
        retention: Final = max(ttl_seconds * 10, 600)
        generation_id: Final = str(await self._redis.get(self._quota_generation_key) or "")
        result: Final = _RESERVE_RESULT_ADAPTER.validate_python(
            await self._reserve_script(
                keys=[
                    self._state_key(account.id),
                    self._inflight_key(account.id),
                    self._lease_key(lease_id),
                    f"pool:request:{request_id}",
                    self._expiries,
                    channel_eligibility_key(account.id),
                    model_eligibility_key(account.id, public_model),
                    deployment_eligibility_key(account.id, deployment_id),
                    billing_route_eligibility_key(account.id, billing_route_id),
                    *quota_plan.keys,
                    self._quota_generation_key,
                ],
                args=[
                    lease_id,
                    request_id,
                    account.id,
                    deployment_id,
                    public_model,
                    billing_route_id or "",
                    expires_at,
                    now,
                    retention,
                    ttl_seconds,
                    len(quota_plan.reservations),
                    REDIS_QUOTA_DECIMAL_SCALE,
                    *quota_plan.arguments,
                    generation_id,
                    1 if probe else 0,
                    absolute_expires_at,
                ],
            )
        )
        status: Final = int(result[0])
        if status == 0:
            return ReserveRejected(reason=str(result[2]))
        actual_lease_id: Final = str(result[1])
        lease: Final = await self._read_lease(actual_lease_id)
        if lease is None:
            return ReserveRejected(reason="lease_not_found")
        return ReserveSuccess(lease=lease)

    async def settle(self, request: SettleRequest) -> bool:
        lease: Final = await self._read_lease(request.lease_id)
        if lease is None:
            return False
        quota_amounts: Final = prepare_quota_settlement_amounts(request)
        if isinstance(quota_amounts, RedisQuotaCodecFailure):
            return False
        account: Final = self._accounts[lease.account_id]
        consumption: Final = _consumption(account=account, request=request)
        now: Final = time.time()
        transition: Final = classify_settlement(request, now)
        exclusion: Final = settlement_exclusion(lease=lease, transition=transition, now=now)
        result: Final = _SCRIPT_STATUS_ADAPTER.validate_python(
            await self._settle_script(
                keys=[
                    self._lease_key(request.lease_id),
                    channel_eligibility_key(lease.account_id),
                    model_eligibility_key(lease.account_id, lease.public_model),
                    deployment_eligibility_key(lease.account_id, lease.deployment_id),
                    billing_route_eligibility_key(lease.account_id, lease.billing_route_id),
                    self._latency_key(lease.deployment_id),
                    self._quota_generation_key,
                ],
                args=[
                    self._prefix,
                    transition.action,
                    consumption,
                    transition.cooldown_until or 0,
                    transition.reason_code or "",
                    exclusion.scope if exclusion is not None else "",
                    exclusion.source if exclusion is not None else "",
                    exclusion.starts_at if exclusion is not None else 0,
                    exclusion.retry_at if exclusion is not None and exclusion.retry_at is not None else 0,
                    quota_amounts.request_units,
                    quota_amounts.token_units,
                    quota_amounts.currency_units,
                    now,
                    1 if request.success else 0,
                    REDIS_QUOTA_DECIMAL_SCALE,
                    request.latency_ms or 0,
                    LATENCY_EWMA_ALPHA,
                ],
            )
        )
        return result > 0

    async def release(self, lease_id: str) -> bool:
        now: Final = time.time()
        result: Final = _SCRIPT_STATUS_ADAPTER.validate_python(
            await self._release_script(
                keys=[self._lease_key(lease_id), self._expiries, self._quota_generation_key],
                args=[self._prefix, lease_id, 600, now, REDIS_QUOTA_DECIMAL_SCALE],
            )
        )
        return bool(result)

    async def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        now: Final = time.time()
        expires_at: Final = now + ttl_seconds
        result: Final = _SCRIPT_STATUS_ADAPTER.validate_python(
            await self._heartbeat_script(
                keys=[self._lease_key(lease_id), self._expiries, self._quota_generation_key],
                args=[expires_at, lease_id, ttl_seconds, now],
            )
        )
        return bool(result)

    async def next_sequence(self, model: str) -> int:
        return int(await self._redis.incr(f"pool:model:{model}:sequence"))

    async def latency_metrics(self) -> tuple[DeploymentLatencyMetric, ...]:
        deployment_ids: Final = tuple(
            deployment.litellm_model_id for account in self._accounts.values() for deployment in account.deployments
        )
        encoded: Final = await _gather_redis_operations(
            tuple(self._redis.hgetall(self._latency_key(item)) for item in deployment_ids)
        )
        return tuple(DeploymentLatencyMetric.model_validate(fields) for fields in encoded if fields)

    async def restore_latency_metrics(self, metrics: tuple[DeploymentLatencyMetric, ...]) -> None:
        existing: Final = {metric.deployment_id: metric for metric in await self.latency_metrics()}
        supplied: Final = {metric.deployment_id: metric for metric in metrics}
        configured: Final = tuple(
            deployment.litellm_model_id for account in self._accounts.values() for deployment in account.deployments
        )
        await _gather_redis_operations(
            tuple(
                self._restore_latency_metric(
                    deployment_id,
                    _newest_metric(existing.get(deployment_id), supplied.get(deployment_id)),
                )
                for deployment_id in configured
            )
        )

    async def set_latency_metric(self, metric: DeploymentLatencyMetric) -> None:
        await self._redis.hset(
            self._latency_key(metric.deployment_id),
            mapping={
                "deployment_id": metric.deployment_id,
                "ewma_ms": str(metric.ewma_ms),
                "sample_count": str(metric.sample_count),
                "observed_at": str(metric.observed_at),
            },
        )

    async def sweep_expired(self) -> tuple[Lease, ...]:
        lease_ids: Final = tuple(
            str(lease_id) for lease_id in await self._redis.zrangebyscore(self._expiries, min=0, max=time.time())
        )
        leases: Final = await _gather_redis_operations(tuple(self._read_lease(lease_id) for lease_id in lease_ids))
        results: Final = await _gather_redis_operations(tuple(self.release(lease_id) for lease_id in lease_ids))
        return tuple(lease for lease, released in zip(leases, results, strict=True) if lease is not None and released)

    async def close(self) -> None:
        await cast(_AsyncClosable, self._redis).aclose()

    async def quota_backend_state(self, account_id: str | None = None) -> QuotaBackendState | None:
        generation_value: Final = await self._redis.get(self._quota_generation_key)
        schema_version: Final = await self._redis.get(self._quota_schema_version_key)
        try:
            generation_id: Final = (
                None
                if generation_value is None or schema_version != self._quota_schema_version
                else UUID(str(generation_value))
            )
        except ValueError:
            return None
        accounts: Final = (
            tuple(self._accounts.values())
            if account_id is None
            else tuple(account for account in self._accounts.values() if account.id == account_id)
        )
        if account_id is not None and not accounts:
            return None
        states: Final = await _gather_redis_operations(
            tuple(
                self._read_quota_backend_window(account.id, window.window_id)
                for account in accounts
                for window in account.quota_windows
            )
        )
        if any(state is None for state in states):
            return None
        return QuotaBackendState(
            generation_id=generation_id,
            windows=tuple(state for state in states if state is not None),
        )

    async def read_quota_generation(self) -> UUID | None:
        value, schema_version = await asyncio.gather(
            self._redis.get(self._quota_generation_key),
            self._redis.get(self._quota_schema_version_key),
        )
        if schema_version != self._quota_schema_version:
            return None
        try:
            return None if value is None else UUID(str(value))
        except ValueError:
            return None

    async def restore_quota_backend(
        self,
        generation_id: UUID,
        windows: tuple[QuotaBackendWindowState, ...],
    ) -> bool:
        expected: Final = frozenset(
            (account.id, window.window_id) for account in self._accounts.values() for window in account.quota_windows
        )
        supplied: Final = frozenset((state.account_id, state.window.config.window_id) for state in windows)
        if supplied != expected or any(state.window.reserved != 0 for state in windows):
            return False
        encoded: Final = tuple((state, encode_quota_window(state.account_id, state.window)) for state in windows)
        if any(isinstance(record, RedisQuotaCodecFailure) for _, record in encoded):
            return False
        usage: Final = tuple(
            (
                state,
                tuple(encode_quota_amount(delta.amount, REDIS_QUOTA_DECIMAL_SCALE) for delta in state.window.usage),
            )
            for state in windows
        )
        if any(isinstance(amount, RedisQuotaCodecFailure) for _, amounts in usage for amount in amounts):
            return False

        # generation key 最后写入；恢复中途失败时，所有 acquire 都会因代次缺失而关闭。
        await self._redis.delete(self._quota_generation_key)
        await self._clear_lease_runtime()
        await _gather_redis_operations(
            tuple(
                self._restore_quota_window(state, record)
                for state, record in encoded
                if isinstance(record, RedisQuotaWindowRecord)
            )
        )
        await _gather_redis_operations(tuple(self._restore_quota_usage(state, amounts) for state, amounts in usage))
        await self._redis.set(self._quota_schema_version_key, self._quota_schema_version)
        await self._redis.set(self._quota_generation_key, str(generation_id))
        return True

    async def _clear_lease_runtime(self) -> None:
        lease_keys, request_keys, probe_keys = await asyncio.gather(
            self._scan_keys("pool:lease:*"),
            self._scan_keys("pool:request:*"),
            self._scan_keys("pool:eligibility:probe:*"),
        )
        stale_keys: Final = (self._expiries, *lease_keys, *request_keys, *probe_keys)
        await self._redis.delete(*stale_keys)
        await _gather_redis_operations(
            tuple(self._redis.set(self._inflight_key(account_id), 0) for account_id in self._accounts)
        )

    async def _scan_keys(self, pattern: str) -> tuple[str, ...]:
        return tuple([str(key) async for key in self._redis.scan_iter(match=pattern)])

    async def set_quota_generation(self, generation_id: UUID | None) -> None:
        if generation_id is None:
            await self._redis.delete(self._quota_generation_key)
            return
        await self._redis.set(self._quota_schema_version_key, self._quota_schema_version)
        await self._redis.set(self._quota_generation_key, str(generation_id))

    async def read_lease(self, lease_id: str) -> Lease | None:
        return await self._read_lease(lease_id)

    async def _read_quota_backend_window(
        self,
        account_id: str,
        window_id: str,
    ) -> QuotaBackendWindowState | None:
        window_key: Final = quota_window_key(account_id, window_id)
        fields: Final = await self._redis.hgetall(window_key)
        decoded: Final = decode_quota_window(fields)
        if isinstance(decoded, RedisQuotaCodecFailure):
            return None
        reservations_key: Final = self._absolute_reservation_key(window_key)
        await self._redis.zremrangebyscore(reservations_key, min=0, max=time.time())
        latest: Final = await self._redis.zrevrange(reservations_key, 0, 0, withscores=True)
        expires_at: Final = None if not latest else float(latest[0][1])
        return QuotaBackendWindowState(
            account_id=account_id,
            window=decoded,
            reservation_expires_at=expires_at,
        )

    async def _restore_quota_window(
        self,
        state: QuotaBackendWindowState,
        record: RedisQuotaWindowRecord,
    ) -> None:
        window_key: Final = quota_window_key(state.account_id, state.window.config.window_id)
        usage_key: Final = quota_usage_key(state.account_id, state.window.config.window_id)
        await self._redis.hset(
            window_key,
            mapping=quota_window_hash_fields(record),  # pyright: ignore[reportArgumentType]  # redis-py 泛型不完整
        )
        await self._redis.delete(
            usage_key,
            self._reservation_key(window_key),
            self._absolute_reservation_key(window_key),
            f"{window_key}:probe",
        )

    async def _restore_quota_usage(
        self,
        state: QuotaBackendWindowState,
        amounts: tuple[EncodedQuotaAmount | RedisQuotaCodecFailure, ...],
    ) -> None:
        usage_key: Final = quota_usage_key(state.account_id, state.window.config.window_id)
        mapping: Final = {
            f"{amount.units}|recovery-{index}": delta.occurred_at
            for index, (delta, amount) in enumerate(zip(state.window.usage, amounts, strict=True))
            if isinstance(amount, EncodedQuotaAmount)
        }
        if mapping:
            await self._redis.zadd(
                usage_key,
                mapping,  # pyright: ignore[reportArgumentType]  # redis-py 泛型不完整
            )

    @staticmethod
    def _reservation_key(window_key: str) -> str:
        return f"{window_key}:reservations"

    @staticmethod
    def _absolute_reservation_key(window_key: str) -> str:
        return f"{window_key}:absolute_reservations"

    async def _configure_account(
        self,
        account: AccountConfig,
        reset_quotas: bool,
        quota_failure: RedisQuotaCodecFailure | None,
    ) -> None:
        state_key: Final = self._state_key(account.id)
        existing_state: Final = await self._redis.hgetall(state_key)
        quota_reason: Final = None if quota_failure is None else f"quota_configuration_{quota_failure.code}"
        recovering_quota_failure: Final = quota_failure is None and existing_state.get("reason_code", "").startswith(
            "quota_configuration_"
        )
        values: Final[dict[str, str]] = {
            "enabled": "1" if account.enabled and quota_failure is None else "0",
            "health": Health.UNHEALTHY if quota_failure is not None else Health.UNKNOWN,
            "max_concurrency": str(account.max_concurrency),
            "cooldown_until": "0",
            "consecutive_failures": "0",
            "reason_code": quota_reason or "",
            "quota_unit": account.quotas.unit,
            "quota_total": _redis_quota(account.quotas.total),
            "quota_five_hour": _redis_quota(account.quotas.five_hour),
            "quota_weekly": _redis_quota(account.quotas.weekly),
        }
        runtime_values: Final = {
            "max_concurrency": str(account.max_concurrency),
            "enabled": "1" if account.enabled and quota_failure is None else "0",
            **(
                {"health": Health.UNHEALTHY, "reason_code": quota_reason or ""}
                if quota_failure is not None
                else ({"health": Health.UNKNOWN, "reason_code": ""} if recovering_quota_failure else {})
            ),
            **(
                {
                    "quota_unit": account.quotas.unit,
                    "quota_total": _redis_quota(account.quotas.total),
                    "quota_five_hour": _redis_quota(account.quotas.five_hour),
                    "quota_weekly": _redis_quota(account.quotas.weekly),
                }
                if reset_quotas
                else {}
            ),
        }
        mapping: Final = runtime_values if existing_state else values
        await self._redis.hset(
            state_key,
            mapping=mapping,  # pyright: ignore[reportArgumentType]  # redis-py leaves hset mapping generics unresolved
        )

    async def _configure_quota_account(self, account: AccountConfig) -> RedisQuotaCodecFailure | None:
        configuration: Final = encode_account_quota_windows(account)
        if isinstance(configuration, RedisQuotaCodecFailure):
            return configuration
        manifest_key: Final = quota_manifest_key(account.id)
        configured_keys: Final = tuple(
            quota_window_key(account.id, record.window_id) for record in configuration.records
        )
        existing_keys: Final = frozenset(str(key) for key in await self._redis.smembers(manifest_key))
        script_results: Final = await _gather_redis_operations(
            tuple(self._configure_quota_window(account.id, record) for record in configuration.records)
        )
        invalid_result: Final = next((result for result in script_results if int(result) not in (-1, 0, 1)), None)
        if invalid_result is not None:
            return RedisQuotaCodecFailure(
                code="invalid_units",
                detail=f"Redis quota calibration returned {invalid_result}",
            )
        configured_set: Final = frozenset(configured_keys)
        stale_keys: Final = existing_keys - configured_set
        if stale_keys:
            await self._redis.delete(
                *(
                    key
                    for stale_key in stale_keys
                    for key in (
                        stale_key,
                        f"{stale_key}:usage",
                        self._reservation_key(stale_key),
                        self._absolute_reservation_key(stale_key),
                        f"{stale_key}:probe",
                    )
                )
            )
        await self._redis.delete(manifest_key)
        if configured_keys:
            await self._redis.sadd(manifest_key, *configured_keys)
        return None

    async def _configure_quota_window(self, account_id: str, record: RedisQuotaWindowRecord) -> int:
        result: Final = cast(
            object,
            await self._quota_configure_script(
                keys=[
                    quota_window_key(account_id, record.window_id),
                    quota_usage_key(account_id, record.window_id),
                ],
                args=configure_quota_script_args(record),
            ),
        )
        return int(_SCRIPT_STATUS_ADAPTER.validate_python(result))

    async def _quota_exclusions(self, account: AccountConfig) -> tuple[EligibilityExclusion, ...]:
        encoded: Final = await _gather_redis_operations(
            tuple(
                self._redis.hgetall(quota_window_key(account.id, window.window_id)) for window in account.quota_windows
            )
        )
        if any(not fields for fields in encoded):
            return (_invalid_quota_state_exclusion(account.id),)
        decoded: Final = tuple(decode_quota_window(fields) for fields in encoded)
        if any(isinstance(window, RedisQuotaCodecFailure) for window in decoded):
            return (_invalid_quota_state_exclusion(account.id),)
        runtime_windows: Final = tuple(window for window in decoded if isinstance(window, RuntimeQuotaWindow))
        return quota_window_exclusions(account=account, windows=runtime_windows)

    async def _snapshot(self, account: AccountConfig) -> AccountSnapshot:
        state: Final = await self._redis.hgetall(self._state_key(account.id))
        inflight: Final = int(await self._redis.get(self._inflight_key(account.id)) or 0)
        cooldown_value: Final = float(state.get("cooldown_until", "0"))
        return AccountSnapshot(
            account_id=account.id,
            enabled=state.get("enabled") == "1",
            health=Health(state.get("health", Health.UNKNOWN)),
            inflight=inflight,
            max_concurrency=int(state.get("max_concurrency", account.max_concurrency)),
            cooldown_until=cooldown_value if cooldown_value > 0 else None,
            consecutive_failures=int(state.get("consecutive_failures", "0")),
            reason_code=state.get("reason_code") or None,
            quota=QuotaSnapshot(
                unit=QuotaUnit(state.get("quota_unit", account.quotas.unit)),
                total=_parse_redis_quota(state.get("quota_total")),
                five_hour=_parse_redis_quota(state.get("quota_five_hour")),
                weekly=_parse_redis_quota(state.get("quota_weekly")),
            ),
        )

    async def _read_lease(self, lease_id: str) -> Lease | None:
        data: Final = await self._redis.hgetall(self._lease_key(lease_id))
        if not data:
            return None
        try:
            return Lease(
                lease_id=data["lease_id"],
                generation_id=UUID(data["generation_id"]) if data.get("generation_id") else None,
                request_id=data["request_id"],
                account_id=data["account_id"],
                deployment_id=data["deployment_id"],
                public_model=data["public_model"],
                billing_route_id=data.get("billing_route_id") or None,
                probe=data.get("probe") == "1",
                expires_at=float(data["expires_at"]),
                absolute_expires_at=float(data["absolute_expires_at"]),
                settled=data.get("settled") == "1",
                released=data.get("released") == "1",
            )
        except (KeyError, ValidationError, ValueError):
            return None

    @classmethod
    def _state_key(cls, account_id: str) -> str:
        return f"{cls._prefix}{account_id}:state"

    @classmethod
    def _inflight_key(cls, account_id: str) -> str:
        return f"{cls._prefix}{account_id}:inflight"

    @staticmethod
    def _lease_key(lease_id: str) -> str:
        return f"pool:lease:{lease_id}"

    @staticmethod
    def _latency_key(deployment_id: str) -> str:
        return f"pool:latency:{deployment_id}"

    async def _restore_latency_metric(
        self,
        deployment_id: str,
        metric: DeploymentLatencyMetric | None,
    ) -> None:
        key: Final = self._latency_key(deployment_id)
        if metric is None:
            await self._redis.delete(key)
            return
        await self.set_latency_metric(metric)


def _availability_rejection(runtime: AccountSnapshot, now: float, probe: bool = False) -> str | None:
    if not runtime.enabled or runtime.health == Health.DISABLED:
        return "disabled"
    if not probe and runtime.health == Health.UNHEALTHY:
        return "unhealthy"
    if not probe and runtime.cooldown_until is not None and runtime.cooldown_until > now:
        return "cooldown"
    if runtime.inflight >= runtime.max_concurrency:
        return "capacity"
    if runtime.quota.total is not None and runtime.quota.total <= 0:
        return "total_quota"
    if runtime.quota.five_hour is not None and runtime.quota.five_hour <= 0:
        return "five_hour_quota"
    if runtime.quota.weekly is not None and runtime.quota.weekly <= 0:
        return "weekly_quota"
    return None


def _newest_metric(
    current: DeploymentLatencyMetric | None,
    restored: DeploymentLatencyMetric | None,
) -> DeploymentLatencyMetric | None:
    if current is None:
        return restored
    if restored is None:
        return current
    if current.observed_at != restored.observed_at:
        return current if current.observed_at > restored.observed_at else restored
    return current if current.sample_count >= restored.sample_count else restored


def _invalid_quota_state_exclusion(account_id: str) -> EligibilityExclusion:
    return activate_exclusion(
        scope=EligibilityScope.CHANNEL,
        source=EligibilitySource.RESTRICTION,
        account_id=account_id,
        model=None,
        deployment_id=None,
        billing_route_id=None,
        reason_code="quota_state_invalid",
        starts_at=time.time(),
        retry_at=None,
    )


def _synchronize_all_quota_exclusions(
    exclusions: tuple[EligibilityExclusion, ...],
    accounts: tuple[AccountConfig, ...],
    quota_windows: dict[str, tuple[RuntimeQuotaWindow, ...]],
) -> tuple[EligibilityExclusion, ...]:
    def synchronize(
        current: tuple[EligibilityExclusion, ...],
        account: AccountConfig,
    ) -> tuple[EligibilityExclusion, ...]:
        return synchronize_quota_exclusions(
            exclusions=current,
            account=account,
            windows=quota_windows.get(account.id, ()),
        )

    return reduce(synchronize, accounts, exclusions)


def _configured_snapshot(
    account: AccountConfig,
    previous_account: AccountConfig | None,
    existing: AccountSnapshot | None,
) -> AccountSnapshot:
    quota: Final = QuotaSnapshot(
        unit=account.quotas.unit,
        total=account.quotas.total,
        five_hour=account.quotas.five_hour,
        weekly=account.quotas.weekly,
    )
    return AccountSnapshot(
        account_id=account.id,
        enabled=account.enabled,
        health=existing.health if existing is not None else Health.UNKNOWN,
        inflight=existing.inflight if existing is not None else 0,
        max_concurrency=account.max_concurrency,
        cooldown_until=existing.cooldown_until if existing is not None else None,
        consecutive_failures=existing.consecutive_failures if existing is not None else 0,
        reason_code=existing.reason_code if existing is not None else None,
        quota=existing.quota
        if existing is not None and previous_account is not None and previous_account.quotas == account.quotas
        else quota,
    )


def _consumption(account: AccountConfig, request: SettleRequest) -> float:
    if account.quotas.unit == QuotaUnit.USD:
        return request.cost_usd or 0
    return float(request.input_tokens + request.output_tokens)


def _decrement_quota(quota: QuotaSnapshot, consumption: float) -> QuotaSnapshot:
    def decrement(value: float | None) -> float | None:
        return None if value is None else max(0, value - consumption)

    return QuotaSnapshot(
        unit=quota.unit,
        total=decrement(quota.total),
        five_hour=decrement(quota.five_hour),
        weekly=decrement(quota.weekly),
    )


def _health_after_settlement(
    runtime: AccountSnapshot,
    transition: SettlementHealthTransition,
) -> dict[str, object]:
    if transition.action == HealthTransitionAction.SUCCESS:
        return {
            "health": Health.HEALTHY,
            "consecutive_failures": 0,
            "cooldown_until": None,
            "reason_code": None,
        }
    if transition.action == HealthTransitionAction.DISABLE:
        return {"health": Health.UNHEALTHY, "reason_code": transition.reason_code}
    if transition.action == HealthTransitionAction.OBSERVE:
        return {}
    if transition.action == HealthTransitionAction.COOLDOWN:
        return {}
    failures: Final = runtime.consecutive_failures + 1
    if failures >= 3:
        return {
            "health": Health.COOLDOWN,
            "consecutive_failures": failures,
            "cooldown_until": transition.cooldown_until,
            "reason_code": transition.reason_code,
        }
    return {
        "health": Health.DEGRADED,
        "consecutive_failures": failures,
        "reason_code": transition.reason_code,
    }


def _redis_quota(value: float | None) -> str:
    return "" if value is None else str(value)


def _parse_redis_quota(value: str | None) -> float | None:
    return None if value is None or value == "" else float(value)
