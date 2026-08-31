"""比较资格快照并构造不含凭证的限制状态变化事件。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid5

from account_pool.eligibility import EligibilityExclusion, EligibilitySource
from account_pool.models import AccountConfig, AccountSnapshot, Lease, ReserveResult, SettleRequest
from account_pool.quota.backend import QuotaBackendState
from account_pool.operational.models import (
    OperationalEventFact,
    OperationalEventOutcome,
    OperationalEventRecord,
    OperationalEventSource,
    OperationalEventType,
    OperationalPoolEvent,
)
from account_pool.operational.repository import OperationalEventRepository, OperationalWriteSuccess
from account_pool.operational.restriction_models import (
    RestrictionActivatedDetails,
    RestrictionClearedDetails,
    RestrictionEventDetails,
    RestrictionUpdatedDetails,
)
from account_pool.routing.latency import DeploymentLatencyMetric
from account_pool.store import StateStore

_RESTRICTION_NAMESPACE: Final = UUID("2a9bfc97-ec51-47ae-b93d-481e74389d71")
_LOGGER: Final = logging.getLogger(__name__)


class RestrictionEventRecorder:
    def __init__(
        self,
        repository: OperationalEventRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository: Final = repository
        self._clock: Final = clock or _utc_now

    async def record_changes(
        self,
        accounts: tuple[AccountConfig, ...],
        before: tuple[EligibilityExclusion, ...],
        after: tuple[EligibilityExclusion, ...],
    ) -> None:
        records: Final = build_restriction_transition_records(
            accounts=accounts,
            before=before,
            after=after,
            occurred_at=self._clock(),
        )
        results: Final = await asyncio.gather(*(self._repository.append(record) for record in records))
        failures: Final = tuple(result for result in results if not isinstance(result, OperationalWriteSuccess))
        for failure in failures:
            _LOGGER.error("Failed to persist eligibility restriction event: %s", failure.code)


class RestrictionEventStateStore:
    def __init__(self, backend: StateStore, recorder: RestrictionEventRecorder) -> None:
        self._backend: Final = backend
        self._recorder: Final = recorder
        self._accounts: dict[str, AccountConfig] = {}

    async def configure(self, accounts: tuple[AccountConfig, ...]) -> None:
        before: Final = await self._backend.eligibility_exclusions()
        self._accounts = {account.id: account for account in accounts}
        await self._backend.configure(accounts)
        await self._record_changes(before)

    async def snapshots(self) -> tuple[AccountSnapshot, ...]:
        return await self._backend.snapshots()

    async def eligibility_exclusions(self) -> tuple[EligibilityExclusion, ...]:
        return await self._backend.eligibility_exclusions()

    async def quota_backend_state(self, account_id: str | None = None) -> QuotaBackendState | None:
        return await self._backend.quota_backend_state(account_id)

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
        before: Final = await self._backend.eligibility_exclusions()
        result: Final = await self._backend.reserve(
            account=account,
            deployment_id=deployment_id,
            billing_route_id=billing_route_id,
            public_model=public_model,
            request_id=request_id,
            estimated_tokens=estimated_tokens,
            ttl_seconds=ttl_seconds,
            probe=probe,
        )
        await self._record_changes(before)
        return result

    async def settle(self, request: SettleRequest) -> bool:
        before: Final = await self._backend.eligibility_exclusions()
        settled: Final = await self._backend.settle(request)
        await self._record_changes(before)
        return settled

    async def read_lease(self, lease_id: str) -> Lease | None:
        return await self._backend.read_lease(lease_id)

    async def release(self, lease_id: str) -> bool:
        before: Final = await self._backend.eligibility_exclusions()
        released: Final = await self._backend.release(lease_id)
        await self._record_changes(before)
        return released

    async def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        return await self._backend.heartbeat(lease_id, ttl_seconds)

    async def next_sequence(self, model: str) -> int:
        return await self._backend.next_sequence(model)

    async def latency_metrics(self) -> tuple[DeploymentLatencyMetric, ...]:
        return await self._backend.latency_metrics()

    async def restore_latency_metrics(self, metrics: tuple[DeploymentLatencyMetric, ...]) -> None:
        await self._backend.restore_latency_metrics(metrics)

    async def set_latency_metric(self, metric: DeploymentLatencyMetric) -> None:
        await self._backend.set_latency_metric(metric)

    async def sweep_expired(self) -> tuple[Lease, ...]:
        before: Final = await self._backend.eligibility_exclusions()
        expired: Final = await self._backend.sweep_expired()
        await self._record_changes(before)
        return expired

    async def close(self) -> None:
        await self._backend.close()

    async def _record_changes(self, before: tuple[EligibilityExclusion, ...]) -> None:
        await self._recorder.record_changes(
            accounts=tuple(self._accounts.values()),
            before=before,
            after=await self._backend.eligibility_exclusions(),
        )


def build_restriction_transition_records(
    *,
    accounts: tuple[AccountConfig, ...],
    before: tuple[EligibilityExclusion, ...],
    after: tuple[EligibilityExclusion, ...],
    occurred_at: datetime,
) -> tuple[OperationalEventRecord, ...]:
    accounts_by_id: Final = {account.id: account for account in accounts if account.channel_id is not None}
    previous: Final = {_restriction_key(item): item for item in before if item.source == EligibilitySource.RESTRICTION}
    current: Final = {_restriction_key(item): item for item in after if item.source == EligibilitySource.RESTRICTION}
    keys: Final = tuple(sorted(previous.keys() | current.keys()))
    return tuple(
        record
        for key in keys
        for account in (accounts_by_id.get(key[0]),)
        if account is not None and account.channel_id is not None
        for record in (_transition_record(account.channel_id, previous.get(key), current.get(key), occurred_at),)
        if record is not None
    )


def _transition_record(
    channel_id: UUID,
    previous: EligibilityExclusion | None,
    current: EligibilityExclusion | None,
    occurred_at: datetime,
) -> OperationalEventRecord | None:
    if previous == current:
        return None
    reference: Final = current or previous
    if reference is None:
        return None
    restriction_id: Final = _restriction_id(reference)
    event_type: Final = (
        OperationalEventType.ELIGIBILITY_RESTRICTION_ACTIVATED
        if previous is None
        else OperationalEventType.ELIGIBILITY_RESTRICTION_CLEARED
        if current is None
        else OperationalEventType.ELIGIBILITY_RESTRICTION_UPDATED
    )
    details: Final = _transition_details(restriction_id, previous, current)
    fingerprint: Final = hashlib.sha256(details.model_dump_json().encode("utf-8")).hexdigest()
    event_id: Final = uuid5(_RESTRICTION_NAMESPACE, f"{restriction_id}:{event_type.value}:{fingerprint}")
    event_time: Final = datetime.fromtimestamp(reference.starts_at, UTC) if current is not None else occurred_at
    return OperationalEventRecord(
        event=OperationalPoolEvent(
            event_id=event_id,
            event_type=event_type,
            occurred_at=event_time,
            channel_id=channel_id,
            model_id=reference.model,
            deployment_id=reference.deployment_id,
            reason_code=reference.reason_code,
            actor_id="account_pool_eligibility",
            safe_details=details,
        ),
        operational=OperationalEventFact(
            event_id=event_id,
            source=OperationalEventSource.ELIGIBILITY_TRANSITION,
            operation_id=restriction_id,
            outcome=OperationalEventOutcome.SUCCEEDED,
        ),
    )


def _transition_details(
    restriction_id: UUID,
    previous: EligibilityExclusion | None,
    current: EligibilityExclusion | None,
) -> RestrictionEventDetails:
    if previous is None and current is not None:
        return RestrictionActivatedDetails(
            restriction_id=restriction_id,
            scope=current.scope,
            state=current.state,
            billing_route_id=current.billing_route_id,
            starts_at=current.starts_at,
            retry_at=current.retry_at,
        )
    if previous is not None and current is None:
        return RestrictionClearedDetails(
            restriction_id=restriction_id,
            scope=previous.scope,
            previous_state=previous.state,
            billing_route_id=previous.billing_route_id,
            starts_at=previous.starts_at,
            previous_retry_at=previous.retry_at,
        )
    if previous is not None and current is not None:
        return RestrictionUpdatedDetails(
            restriction_id=restriction_id,
            scope=current.scope,
            previous_state=previous.state,
            state=current.state,
            billing_route_id=current.billing_route_id,
            starts_at=current.starts_at,
            previous_retry_at=previous.retry_at,
            retry_at=current.retry_at,
        )
    raise ValueError("restriction transition requires a previous or current value")


def _restriction_id(exclusion: EligibilityExclusion) -> UUID:
    return uuid5(_RESTRICTION_NAMESPACE, "\x00".join(_restriction_key(exclusion)))


def _restriction_key(exclusion: EligibilityExclusion) -> tuple[str, str, str, str, str, str]:
    return (
        exclusion.account_id,
        exclusion.scope.value,
        exclusion.model or "",
        exclusion.deployment_id or "",
        exclusion.billing_route_id or "",
        exclusion.reason_code,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)
