"""提供内存和 Redis 状态存储，管理账号并发、租约、额度与健康状态。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import uuid4

from pydantic import TypeAdapter
from redis.asyncio import Redis

from account_pool.eligibility import (
    EligibilityExclusion,
    EligibilityState,
    EligibilitySubject,
    candidate_evidence,
    candidate_exclusion,
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


class StateStore(Protocol):
    async def configure(self, accounts: tuple[AccountConfig, ...]) -> None: ...

    async def snapshots(self) -> tuple[AccountSnapshot, ...]: ...

    async def eligibility_exclusions(self) -> tuple[EligibilityExclusion, ...]: ...

    async def reserve(
        self,
        account: AccountConfig,
        deployment_id: str,
        billing_route_id: str | None,
        public_model: str,
        request_id: str,
        estimated_tokens: int,
        ttl_seconds: int,
    ) -> ReserveResult: ...

    async def settle(self, request: SettleRequest) -> bool: ...

    async def release(self, lease_id: str) -> bool: ...

    async def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool: ...

    async def next_sequence(self, model: str) -> int: ...

    async def sweep_expired(self) -> int: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _MemoryLeaseState:
    lease: Lease
    usage_applied: bool
    probe_subject: EligibilitySubject | None
    quota_reservations: tuple[QuotaReservation, ...]


class MemoryStateStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._runtime: dict[str, AccountSnapshot] = {}
        self._leases: dict[str, _MemoryLeaseState] = {}
        self._requests: dict[str, str] = {}
        self._sequences: dict[str, int] = {}
        self._accounts: dict[str, AccountConfig] = {}
        self._quota_windows: dict[str, tuple[RuntimeQuotaWindow, ...]] = {}
        self._exclusions: tuple[EligibilityExclusion, ...] = ()
        self._probe_leases: dict[EligibilitySubject, str] = {}

    async def configure(self, accounts: tuple[AccountConfig, ...]) -> None:
        async with self._lock:
            previous_accounts: Final = self._accounts
            self._accounts = {account.id: account for account in accounts}
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
            )
            if exclusion is not None:
                return ReserveRejected(reason=exclusion.reason_code)
            evidence: Final = candidate_evidence(
                exclusions=self._exclusions,
                account_id=account.id,
                model=public_model,
                deployment_id=deployment_id,
                billing_route_id=billing_route_id,
                now=now,
            )
            probe_subject: Final = (
                exclusion_subject(evidence)
                if evidence is not None and effective_state(evidence, now) == EligibilityState.HALF_OPEN
                else None
            )
            if probe_subject is not None and probe_subject in self._probe_leases:
                return ReserveRejected(reason="half_open_probe_inflight")
            rejection: Final = _availability_rejection(runtime=runtime, now=now)
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

            lease: Final = Lease(
                lease_id=uuid4().hex,
                request_id=request_id,
                account_id=account.id,
                deployment_id=deployment_id,
                public_model=public_model,
                billing_route_id=billing_route_id,
                expires_at=now + ttl_seconds,
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
            extended: Final = lease_state.lease.model_copy(update={"expires_at": time.time() + ttl_seconds})
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

    async def sweep_expired(self) -> int:
        now: Final = time.time()
        async with self._lock:
            expired: Final = tuple(
                lease_id
                for lease_id, state in self._leases.items()
                if not state.lease.released and state.lease.expires_at <= now
            )
        results: Final = await asyncio.gather(*(self.release(lease_id) for lease_id in expired))
        return sum(1 for released in results if released)

    async def close(self) -> None:
        return None


_RESERVE_SCRIPT = """
local existing = redis.call('GET', KEYS[4])
if existing then
  return {2, existing, 'existing'}
end
local function exclusion_status(key, now)
  local entries = redis.call('HGETALL', key)
  local half_open = false
  for index = 1, #entries, 2 do
    local field = entries[index]
    local value = entries[index + 1]
    local value_separator = string.find(value, '|', 1, true)
    local retry_at = tonumber(string.sub(value, value_separator + 1)) or 0
    if retry_at == 0 or retry_at > now then
      local field_separator = string.find(field, '|', 1, true)
      return string.sub(field, field_separator + 1), false
    end
    half_open = true
  end
  return nil, half_open
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
for key_index = 6, 9 do
  local reason, half_open = exclusion_status(KEYS[key_index], now)
  if reason then return {0, '', reason} end
  if not probe_source_key and half_open then probe_source_key = KEYS[key_index] end
end
if health == 'unhealthy' then return {0, '', 'unhealthy'} end
if cooldown > now then return {0, '', 'cooldown'} end
local probe_key = ''
if probe_source_key then
  probe_key = 'pool:eligibility:probe:' .. probe_source_key
  if redis.call('EXISTS', probe_key) == 1 then return {0, '', 'half_open_probe_inflight'} end
end
if inflight >= max_concurrency then return {0, '', 'capacity'} end
if quota_total and quota_total ~= '' and tonumber(quota_total) <= 0 then return {0, '', 'total_quota'} end
if quota_five and quota_five ~= '' and tonumber(quota_five) <= 0 then return {0, '', 'five_hour_quota'} end
if quota_weekly and quota_weekly ~= '' and tonumber(quota_weekly) <= 0 then return {0, '', 'weekly_quota'} end
redis.call('INCR', KEYS[2])
redis.call('HSET', KEYS[3],
  'lease_id', ARGV[1], 'request_id', ARGV[2], 'account_id', ARGV[3],
  'deployment_id', ARGV[4], 'public_model', ARGV[5], 'billing_route_id', ARGV[6], 'expires_at', ARGV[7],
  'probe_key', probe_key, 'settled', '0', 'released', '0')
if probe_key ~= '' then redis.call('SET', probe_key, ARGV[1], 'EX', ARGV[10]) end
redis.call('SET', KEYS[4], ARGV[1], 'EX', ARGV[9])
redis.call('ZADD', KEYS[5], ARGV[7], ARGV[1])
return {1, ARGV[1], 'reserved'}
"""


_RELEASE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
if redis.call('HGET', KEYS[1], 'released') == '1' then return 1 end
local account_id = redis.call('HGET', KEYS[1], 'account_id')
local lease_id = redis.call('HGET', KEYS[1], 'lease_id')
local probe_key = redis.call('HGET', KEYS[1], 'probe_key')
local inflight_key = ARGV[1] .. account_id .. ':inflight'
local inflight = tonumber(redis.call('GET', inflight_key) or '0')
if inflight > 0 then redis.call('DECR', inflight_key) end
if probe_key and probe_key ~= '' and redis.call('GET', probe_key) == lease_id then redis.call('DEL', probe_key) end
redis.call('HSET', KEYS[1], 'released', '1')
redis.call('ZREM', KEYS[2], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""


_HEARTBEAT_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
if redis.call('HGET', KEYS[1], 'released') == '1' then return 0 end
local lease_id = redis.call('HGET', KEYS[1], 'lease_id')
local probe_key = redis.call('HGET', KEYS[1], 'probe_key')
if probe_key and probe_key ~= '' and redis.call('GET', probe_key) ~= lease_id then return 0 end
if probe_key and probe_key ~= '' then redis.call('EXPIRE', probe_key, ARGV[3]) end
redis.call('HSET', KEYS[1], 'expires_at', ARGV[1])
redis.call('ZADD', KEYS[2], ARGV[1], ARGV[2])
return 1
"""


_SETTLE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
if redis.call('HGET', KEYS[1], 'settled') == '1' then return 1 end
local account_id = redis.call('HGET', KEYS[1], 'account_id')
local state_key = ARGV[1] .. account_id .. ':state'
local action = ARGV[2]
local consumption = tonumber(ARGV[3] or '0')
local cooldown_until = tonumber(ARGV[4] or '0')
local reason_code = ARGV[5]
local exclusion_scope = ARGV[6]
local exclusion_source = ARGV[7]
local starts_at = ARGV[8]
local retry_at = ARGV[9]
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
redis.call('HSET', KEYS[1], 'settled', '1')
return 1
"""


_RESERVE_RESULT_ADAPTER: Final = TypeAdapter(tuple[int, str, str])
_SCRIPT_STATUS_ADAPTER: Final = TypeAdapter(int)


class RedisStateStore:
    _prefix = "pool:account:"
    _expiries = "pool:leases:expiries"

    def __init__(self, url: str) -> None:
        self._redis = Redis.from_url(url, decode_responses=True)
        self._accounts: dict[str, AccountConfig] = {}
        self._reserve_script = self._redis.register_script(_RESERVE_SCRIPT)
        self._release_script = self._redis.register_script(_RELEASE_SCRIPT)
        self._heartbeat_script = self._redis.register_script(_HEARTBEAT_SCRIPT)
        self._settle_script = self._redis.register_script(_SETTLE_SCRIPT)

    async def configure(self, accounts: tuple[AccountConfig, ...]) -> None:
        previous_accounts: Final = self._accounts
        runtime_reconfigure: Final = bool(previous_accounts)
        stale_eligibility: Final = frozenset(eligibility_subjects(tuple(previous_accounts.values()))) - frozenset(
            eligibility_subjects(accounts)
        )
        self._accounts = {account.id: account for account in accounts}
        await asyncio.gather(
            *(
                self._configure_account(
                    account,
                    reset_quotas=runtime_reconfigure
                    and (
                        previous_accounts.get(account.id) is None
                        or previous_accounts[account.id].quotas != account.quotas
                    ),
                )
                for account in accounts
            )
        )
        if stale_eligibility:
            await self._redis.delete(*(eligibility_key(subject) for subject in stale_eligibility))

    async def snapshots(self) -> tuple[AccountSnapshot, ...]:
        return tuple([await self._snapshot(account) for account in self._accounts.values()])

    async def eligibility_exclusions(self) -> tuple[EligibilityExclusion, ...]:
        subjects: Final = eligibility_subjects(tuple(self._accounts.values()))
        encoded: Final = await asyncio.gather(*(self._redis.hgetall(eligibility_key(subject)) for subject in subjects))
        return tuple(
            exclusion
            for subject, entries in zip(subjects, encoded, strict=True)
            for exclusion in decode_exclusions(subject, entries)
        )

    async def reserve(
        self,
        account: AccountConfig,
        deployment_id: str,
        billing_route_id: str | None,
        public_model: str,
        request_id: str,
        estimated_tokens: int,
        ttl_seconds: int,
    ) -> ReserveResult:
        lease_id: Final = uuid4().hex
        now: Final = time.time()
        retention: Final = max(ttl_seconds * 10, 600)
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
                ],
                args=[
                    lease_id,
                    request_id,
                    account.id,
                    deployment_id,
                    public_model,
                    billing_route_id or "",
                    now + ttl_seconds,
                    now,
                    retention,
                    ttl_seconds,
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
                ],
            )
        )
        return bool(result)

    async def release(self, lease_id: str) -> bool:
        result: Final = _SCRIPT_STATUS_ADAPTER.validate_python(
            await self._release_script(
                keys=[self._lease_key(lease_id), self._expiries],
                args=[self._prefix, lease_id, 600],
            )
        )
        return bool(result)

    async def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        expires_at: Final = time.time() + ttl_seconds
        result: Final = _SCRIPT_STATUS_ADAPTER.validate_python(
            await self._heartbeat_script(
                keys=[self._lease_key(lease_id), self._expiries],
                args=[expires_at, lease_id, ttl_seconds],
            )
        )
        return bool(result)

    async def next_sequence(self, model: str) -> int:
        return int(await self._redis.incr(f"pool:model:{model}:sequence"))

    async def sweep_expired(self) -> int:
        expired: Final = await self._redis.zrangebyscore(self._expiries, min=0, max=time.time())
        results: Final = await asyncio.gather(*(self.release(str(lease_id)) for lease_id in expired))
        return sum(1 for released in results if released)

    async def close(self) -> None:
        await self._redis.close()

    async def _configure_account(self, account: AccountConfig, reset_quotas: bool) -> None:
        state_key: Final = self._state_key(account.id)
        values: Final[dict[str, str]] = {
            "enabled": "1" if account.enabled else "0",
            "health": Health.UNKNOWN,
            "max_concurrency": str(account.max_concurrency),
            "cooldown_until": "0",
            "consecutive_failures": "0",
            "reason_code": "",
            "quota_unit": account.quotas.unit,
            "quota_total": _redis_quota(account.quotas.total),
            "quota_five_hour": _redis_quota(account.quotas.five_hour),
            "quota_weekly": _redis_quota(account.quotas.weekly),
        }
        exists: Final = bool(await self._redis.exists(state_key))
        runtime_values: Final = {
            "max_concurrency": str(account.max_concurrency),
            "enabled": "1" if account.enabled else "0",
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
        mapping: Final = runtime_values if exists else values
        await self._redis.hset(
            state_key,
            mapping=mapping,  # pyright: ignore[reportArgumentType]  # redis-py leaves hset mapping generics unresolved
        )

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
        return Lease(
            lease_id=data["lease_id"],
            request_id=data["request_id"],
            account_id=data["account_id"],
            deployment_id=data["deployment_id"],
            public_model=data["public_model"],
            billing_route_id=data.get("billing_route_id") or None,
            expires_at=float(data["expires_at"]),
            settled=data.get("settled") == "1",
            released=data.get("released") == "1",
        )

    @classmethod
    def _state_key(cls, account_id: str) -> str:
        return f"{cls._prefix}{account_id}:state"

    @classmethod
    def _inflight_key(cls, account_id: str) -> str:
        return f"{cls._prefix}{account_id}:inflight"

    @staticmethod
    def _lease_key(lease_id: str) -> str:
        return f"pool:lease:{lease_id}"


def _availability_rejection(runtime: AccountSnapshot, now: float) -> str | None:
    if not runtime.enabled or runtime.health == Health.DISABLED:
        return "disabled"
    if runtime.health == Health.UNHEALTHY:
        return "unhealthy"
    if runtime.cooldown_until is not None and runtime.cooldown_until > now:
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


def _synchronize_all_quota_exclusions(
    exclusions: tuple[EligibilityExclusion, ...],
    accounts: tuple[AccountConfig, ...],
    quota_windows: dict[str, tuple[RuntimeQuotaWindow, ...]],
) -> tuple[EligibilityExclusion, ...]:
    synchronized: Final = exclusions
    for account in accounts:
        synchronized = synchronize_quota_exclusions(
            exclusions=synchronized,
            account=account,
            windows=quota_windows.get(account.id, ()),
        )
    return synchronized


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
