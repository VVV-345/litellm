"""根据模型策略排列候选账号，并通过状态存储原子申请并发租约。"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Final

from account_pool.eligibility import (
    EligibilityExclusion,
    candidate_evidence,
    candidate_exclusion,
    effective_state,
)
from account_pool.models import (
    AccountConfig,
    AccountSnapshot,
    AcquireRequest,
    AcquireResult,
    AcquireSuccess,
    AcquireUnavailable,
    DeploymentConfig,
    Health,
    ModelPolicy,
    PoolConfig,
    ReserveSuccess,
    RouteEntry,
    Strategy,
)
from account_pool.store import StateStore


class Scheduler:
    def __init__(self, config: PoolConfig, store: StateStore, lease_ttl_seconds: int) -> None:
        self._config = config
        self._store = store
        self._lease_ttl_seconds = lease_ttl_seconds
        self._accounts = {account.id: account for account in config.accounts}
        self._policies = {policy.model: policy for policy in config.policies}

    async def initialize(self) -> None:
        await self._store.configure(self._config.accounts)

    async def reconfigure(self, config: PoolConfig) -> None:
        await self._store.configure(config.accounts)
        self._config = config
        self._accounts = {account.id: account for account in config.accounts}
        self._policies = {policy.model: policy for policy in config.policies}

    def config(self) -> PoolConfig:
        return self._config

    def policy(self, model: str) -> ModelPolicy:
        return self._policies.get(model, ModelPolicy(model=model))

    def account_configs(self) -> tuple[AccountConfig, ...]:
        return self._config.accounts

    async def acquire(self, request: AcquireRequest) -> AcquireResult:
        candidates: Final = self._candidates(request.model)
        if not candidates:
            return AcquireUnavailable(model=request.model, reasons=("model_not_configured",))

        snapshots: Final = await self._snapshot_map()
        exclusions: Final = await self._store.eligibility_exclusions()
        now: Final = time.time()
        policy: Final = self._policies.get(request.model, ModelPolicy(model=request.model))
        ordered: Final = await self._ordered_candidates(
            model=request.model,
            strategy=policy.strategy,
            candidates=candidates,
            snapshots=snapshots,
            exclusions=exclusions,
            now=now,
        )
        reasons: list[str] = []
        for account, deployment in ordered:
            result = await self._store.reserve(
                account=account,
                deployment_id=deployment.litellm_model_id,
                billing_route_id=deployment.billing_route_id,
                public_model=request.model,
                request_id=request.request_id,
                estimated_tokens=request.estimated_tokens,
                ttl_seconds=self._lease_ttl_seconds,
            )
            if isinstance(result, ReserveSuccess):
                return AcquireSuccess(lease=result.lease)
            reasons.append(f"{account.id}:{result.reason}")
        return AcquireUnavailable(model=request.model, reasons=tuple(reasons))

    async def route_table(self, model: str) -> tuple[RouteEntry, ...]:
        snapshots: Final = await self._snapshot_map()
        exclusions: Final = await self._store.eligibility_exclusions()
        now: Final = time.time()
        entries: Final = tuple(
            self._route_entry(
                account=account,
                deployment=deployment,
                snapshot=snapshots[account.id],
                exclusions=exclusions,
                now=now,
            )
            for account, deployment in self._candidates(model)
        )
        return tuple(
            sorted(
                entries,
                key=lambda entry: (
                    not entry.available,
                    -entry.priority,
                    entry.account_id,
                    entry.deployment_id,
                ),
            )
        )

    async def account_snapshots(self) -> tuple[AccountSnapshot, ...]:
        return await self._store.snapshots()

    def models(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    deployment.public_model
                    for account in self._config.accounts
                    for deployment in account.deployments
                    if deployment.enabled
                }
            )
        )

    def account_config(self, account_id: str) -> AccountConfig:
        return self._accounts[account_id]

    async def _snapshot_map(self) -> dict[str, AccountSnapshot]:
        snapshots: Final = await self._store.snapshots()
        return {snapshot.account_id: snapshot for snapshot in snapshots}

    def _candidates(self, model: str) -> tuple[tuple[AccountConfig, DeploymentConfig], ...]:
        return tuple(
            (account, deployment)
            for account in self._config.accounts
            for deployment in account.deployments
            if deployment.enabled and deployment.public_model == model
        )

    async def _ordered_candidates(
        self,
        model: str,
        strategy: Strategy,
        candidates: tuple[tuple[AccountConfig, DeploymentConfig], ...],
        snapshots: dict[str, AccountSnapshot],
        exclusions: tuple[EligibilityExclusion, ...],
        now: float,
    ) -> tuple[tuple[AccountConfig, DeploymentConfig], ...]:
        if strategy == Strategy.PRIORITY:
            return tuple(
                sorted(
                    candidates,
                    key=lambda item: (
                        _candidate_availability_rank(item, model, snapshots, exclusions, now),
                        -item[0].priority,
                        item[0].id,
                        item[1].litellm_model_id,
                    ),
                )
            )
        if strategy == Strategy.WEIGHTED_ROUND_ROBIN:
            sequence: Final = await self._store.next_sequence(model)
            weighted: Final = _weighted_order(candidates=candidates, sequence=sequence)
            return tuple(
                sorted(
                    weighted,
                    key=lambda item: _candidate_availability_rank(item, model, snapshots, exclusions, now),
                )
            )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    _candidate_availability_rank(item, model, snapshots, exclusions, now),
                    _inflight_ratio(snapshots[item[0].id]),
                    -_quota_ratio(account=item[0], snapshot=snapshots[item[0].id])
                    if strategy == Strategy.QUOTA_AWARE_LEAST_INFLIGHT
                    else 0,
                    -item[0].priority,
                    item[0].id,
                ),
            )
        )

    @staticmethod
    def _route_entry(
        account: AccountConfig,
        deployment: DeploymentConfig,
        snapshot: AccountSnapshot,
        exclusions: tuple[EligibilityExclusion, ...],
        now: float,
    ) -> RouteEntry:
        evidence: Final = candidate_evidence(
            exclusions=exclusions,
            account_id=account.id,
            model=deployment.public_model,
            deployment_id=deployment.litellm_model_id,
            billing_route_id=deployment.billing_route_id,
            now=now,
        )
        active_exclusion: Final = candidate_exclusion(
            exclusions=exclusions,
            account_id=account.id,
            model=deployment.public_model,
            deployment_id=deployment.litellm_model_id,
            billing_route_id=deployment.billing_route_id,
            now=now,
        )
        reason: Final = _unavailable_reason(snapshot=snapshot, exclusion=active_exclusion)
        return RouteEntry(
            account_id=account.id,
            display_name=account.display_name,
            provider=account.provider,
            base_url_display=account.base_url_display,
            deployment_id=deployment.litellm_model_id,
            public_model=deployment.public_model,
            enabled=snapshot.enabled,
            health=snapshot.health,
            inflight=snapshot.inflight,
            max_concurrency=snapshot.max_concurrency,
            cooldown_until=evidence.retry_at if evidence is not None else snapshot.cooldown_until,
            reason_code=evidence.reason_code if evidence is not None else snapshot.reason_code,
            exclusion_scope=evidence.scope if evidence is not None else None,
            exclusion_source=evidence.source if evidence is not None else None,
            exclusion_state=effective_state(evidence, now) if evidence is not None else None,
            retry_at=evidence.retry_at if evidence is not None else None,
            quota=snapshot.quota,
            priority=account.priority,
            weight=account.weight,
            available=reason is None,
            unavailable_reason=reason,
        )


def _weighted_order(
    candidates: tuple[tuple[AccountConfig, DeploymentConfig], ...],
    sequence: int,
) -> tuple[tuple[AccountConfig, DeploymentConfig], ...]:
    wheel: Final = tuple(item for item in candidates for _ in range(item[0].weight))
    pivot: Final = (sequence - 1) % len(wheel)
    rotated: Final = wheel[pivot:] + wheel[:pivot]
    return _unique_candidates(rotated)


def _unique_candidates(
    candidates: Iterable[tuple[AccountConfig, DeploymentConfig]],
) -> tuple[tuple[AccountConfig, DeploymentConfig], ...]:
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[AccountConfig, DeploymentConfig]] = []
    for item in candidates:
        if (item[0].id, item[1].litellm_model_id) not in seen:
            seen.add((item[0].id, item[1].litellm_model_id))
            ordered.append(item)
    return tuple(ordered)


def _inflight_ratio(snapshot: AccountSnapshot) -> float:
    return snapshot.inflight / snapshot.max_concurrency


def _quota_ratio(account: AccountConfig, snapshot: AccountSnapshot) -> float:
    pairs: Final = (
        (snapshot.quota.total, account.quotas.total),
        (snapshot.quota.five_hour, account.quotas.five_hour),
        (snapshot.quota.weekly, account.quotas.weekly),
    )
    ratios: Final = tuple(remaining / limit for remaining, limit in pairs if remaining is not None and limit)
    return min(ratios) if ratios else 1.0


def _availability_rank(snapshot: AccountSnapshot, exclusion: EligibilityExclusion | None) -> int:
    return 0 if _unavailable_reason(snapshot=snapshot, exclusion=exclusion) is None else 1


def _candidate_availability_rank(
    candidate: tuple[AccountConfig, DeploymentConfig],
    model: str,
    snapshots: dict[str, AccountSnapshot],
    exclusions: tuple[EligibilityExclusion, ...],
    now: float,
) -> int:
    account, deployment = candidate
    exclusion: Final = candidate_exclusion(
        exclusions=exclusions,
        account_id=account.id,
        model=model,
        deployment_id=deployment.litellm_model_id,
        billing_route_id=deployment.billing_route_id,
        now=now,
    )
    return _availability_rank(snapshot=snapshots[account.id], exclusion=exclusion)


def _unavailable_reason(
    snapshot: AccountSnapshot,
    exclusion: EligibilityExclusion | None = None,
) -> str | None:
    if exclusion is not None:
        return exclusion.reason_code
    if not snapshot.enabled or snapshot.health == Health.DISABLED:
        return "disabled"
    if snapshot.health == Health.UNHEALTHY:
        return "unhealthy"
    if snapshot.cooldown_until is not None and snapshot.cooldown_until > time.time():
        return "cooldown"
    if snapshot.inflight >= snapshot.max_concurrency:
        return "capacity"
    if snapshot.quota.total is not None and snapshot.quota.total <= 0:
        return "total_quota"
    if snapshot.quota.five_hour is not None and snapshot.quota.five_hour <= 0:
        return "five_hour_quota"
    if snapshot.quota.weekly is not None and snapshot.quota.weekly <= 0:
        return "weekly_quota"
    return None
