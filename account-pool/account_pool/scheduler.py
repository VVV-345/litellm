"""根据模型策略排列候选账号，并通过状态存储原子申请并发租约。"""

from __future__ import annotations

import time
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
from account_pool.routing import RoutingCandidate, RoutingOrder, order_candidates
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
            request_id=request.request_id,
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
        policy: Final = self.policy(model)
        candidates: Final = self._candidates(model)
        routing_candidates: Final = tuple(
            _routing_candidate(
                account=account,
                deployment=deployment,
                snapshot=snapshots[account.id],
                exclusions=exclusions,
                now=now,
            )
            for account, deployment in candidates
        )
        ordered: Final = order_candidates(
            candidates=routing_candidates,
            strategy=policy.strategy,
            model=model,
        )
        candidate_by_id: Final = {
            _candidate_id(account, deployment): (account, deployment) for account, deployment in candidates
        }
        return tuple(
            self._route_entry(
                account=candidate_by_id[item.candidate.stable_id()][0],
                deployment=candidate_by_id[item.candidate.stable_id()][1],
                snapshot=snapshots[item.candidate.account_id],
                exclusions=exclusions,
                now=now,
                order=item,
                position=position,
                strategy=policy.strategy,
            )
            for position, item in enumerate(ordered, start=1)
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
        request_id: str,
    ) -> tuple[tuple[AccountConfig, DeploymentConfig], ...]:
        routing_candidates: Final = tuple(
            _routing_candidate(
                account=account,
                deployment=deployment,
                snapshot=snapshots[account.id],
                exclusions=exclusions,
                now=now,
            )
            for account, deployment in candidates
        )
        sequence: Final = await self._store.next_sequence(model) if strategy == Strategy.WEIGHTED_ROUND_ROBIN else None
        ordered: Final = order_candidates(
            candidates=routing_candidates,
            strategy=strategy,
            model=model,
            request_id=request_id,
            sequence=sequence,
        )
        candidate_by_id: Final = {
            _candidate_id(account, deployment): (account, deployment) for account, deployment in candidates
        }
        return tuple(candidate_by_id[item.candidate.stable_id()] for item in ordered)

    @staticmethod
    def _route_entry(
        account: AccountConfig,
        deployment: DeploymentConfig,
        snapshot: AccountSnapshot,
        exclusions: tuple[EligibilityExclusion, ...],
        now: float,
        order: RoutingOrder,
        position: int,
        strategy: Strategy,
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
            position=position,
            strategy=strategy,
            dynamic_order=order.dynamic,
            sort_reason_codes=order.reason_codes,
            remaining_quota_ratio=order.candidate.remaining_quota_ratio,
            latency_ewma_ms=order.candidate.latency_ewma_ms,
            effective_cost=order.candidate.effective_cost,
        )


def _quota_ratio(account: AccountConfig, snapshot: AccountSnapshot) -> float | None:
    pairs: Final = (
        (snapshot.quota.total, account.quotas.total),
        (snapshot.quota.five_hour, account.quotas.five_hour),
        (snapshot.quota.weekly, account.quotas.weekly),
    )
    ratios: Final = tuple(remaining / limit for remaining, limit in pairs if remaining is not None and limit)
    return min(ratios) if ratios else None


def _routing_candidate(
    account: AccountConfig,
    deployment: DeploymentConfig,
    snapshot: AccountSnapshot,
    exclusions: tuple[EligibilityExclusion, ...],
    now: float,
) -> RoutingCandidate:
    exclusion: Final = candidate_exclusion(
        exclusions=exclusions,
        account_id=account.id,
        model=deployment.public_model,
        deployment_id=deployment.litellm_model_id,
        billing_route_id=deployment.billing_route_id,
        now=now,
    )
    return RoutingCandidate(
        account_id=account.id,
        deployment_id=deployment.litellm_model_id,
        billing_route_id=deployment.billing_route_id,
        available=_unavailable_reason(snapshot=snapshot, exclusion=exclusion) is None,
        priority=account.priority,
        weight=account.weight,
        inflight=snapshot.inflight,
        max_concurrency=snapshot.max_concurrency,
        remaining_quota_ratio=_quota_ratio(account=account, snapshot=snapshot),
    )


def _candidate_id(account: AccountConfig, deployment: DeploymentConfig) -> str:
    return f"{account.id}\x00{deployment.litellm_model_id}\x00{deployment.billing_route_id or ''}"


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
