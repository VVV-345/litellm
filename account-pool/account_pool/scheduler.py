"""根据模型策略排列候选账号，并通过状态存储原子申请并发租约。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
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
    AcquireCandidateRejection,
    AcquireRejectionScope,
    AcquireRejectionSource,
    AcquireRejectionStage,
    AcquireRejectionState,
    AcquireRequest,
    AcquireResult,
    AcquireSuccess,
    AcquireUnavailable,
    CostEvidenceKind,
    DeploymentConfig,
    Health,
    ModelPolicy,
    PoolConfig,
    ReserveSuccess,
    RouteEntry,
    RuntimeQuotaScope,
    Strategy,
)
from account_pool.operational.request_lifecycle import RequestEventRecorder
from account_pool.quota.backend import QuotaBackendState
from account_pool.quota.runtime import RuntimeQuotaWindow
from account_pool.routing import RoutingCandidate, RoutingOrder, order_candidates
from account_pool.store import StateStore


@dataclass(frozen=True, slots=True)
class _RouteQuota:
    remaining_ratio: float | None
    remaining: Decimal | None
    unit: str | None
    unavailable_reason: str | None


class Scheduler:
    def __init__(
        self,
        config: PoolConfig,
        store: StateStore,
        lease_ttl_seconds: int,
        request_events: RequestEventRecorder | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._lease_ttl_seconds = lease_ttl_seconds
        self._accounts = {account.id: account for account in config.accounts}
        self._policies = {policy.model: policy for policy in config.policies}
        self._request_events: Final = request_events

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
            return AcquireUnavailable(model=request.model, reason_codes=("model_not_configured",))

        snapshots: Final = await self._snapshot_map()
        latency: Final = await self._latency_map()
        exclusions: Final = await self._store.eligibility_exclusions()
        quota_state: Final = await self._store.quota_backend_state()
        now: Final = time.time()
        policy: Final = self._policies.get(request.model, ModelPolicy(model=request.model))
        ordered: Final = await self._ordered_candidates(
            model=request.model,
            strategy=policy.strategy,
            candidates=candidates,
            snapshots=snapshots,
            exclusions=exclusions,
            quota_state=quota_state,
            latency=latency,
            now=now,
            request_id=request.request_id,
        )
        rejections: list[AcquireCandidateRejection] = []
        for account, deployment in ordered:
            configuration_rejection = _configuration_rejection(account, deployment)
            if configuration_rejection is not None:
                rejections.append(configuration_rejection)
                continue
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
                if self._request_events is not None:
                    await self._request_events.acquired(
                        account,
                        result.lease,
                        request.estimated_tokens,
                        self._lease_ttl_seconds,
                    )
                return AcquireSuccess(lease=result.lease)
            rejections.append(
                _reserve_rejection(
                    account=account,
                    deployment=deployment,
                    snapshot=snapshots[account.id],
                    exclusions=exclusions,
                    reason_code=result.reason,
                    now=now,
                )
            )
        unavailable: Final = AcquireUnavailable(
            model=request.model,
            reason_codes=tuple(dict.fromkeys(rejection.reason_code for rejection in rejections)),
            candidates=tuple(rejections),
            retry_at=min(
                (rejection.retry_at for rejection in rejections if rejection.retry_at is not None),
                default=None,
            ),
        )
        if self._request_events is not None:
            accounts: Final = {account.id: account for account, _ in candidates}
            for rejection in unavailable.candidates:
                await self._request_events.acquire_failed(
                    accounts[rejection.account_id],
                    request.model,
                    request.request_id,
                    rejection,
                )
        return unavailable

    async def route_table(self, model: str) -> tuple[RouteEntry, ...]:
        snapshots: Final = await self._snapshot_map()
        latency: Final = await self._latency_map()
        exclusions: Final = await self._store.eligibility_exclusions()
        quota_state: Final = await self._store.quota_backend_state()
        now: Final = time.time()
        policy: Final = self.policy(model)
        candidates: Final = self._candidates(model)
        routing_candidates: Final = tuple(
            _routing_candidate(
                account=account,
                deployment=deployment,
                snapshot=snapshots[account.id],
                exclusions=exclusions,
                quota_state=quota_state,
                now=now,
                latency_ewma_ms=latency.get(deployment.litellm_model_id),
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

    async def _latency_map(self) -> dict[str, float]:
        metrics: Final = await self._store.latency_metrics()
        return {metric.deployment_id: metric.ewma_ms for metric in metrics}

    def _candidates(self, model: str) -> tuple[tuple[AccountConfig, DeploymentConfig], ...]:
        return tuple(
            (account, deployment)
            for account in self._config.accounts
            for deployment in account.deployments
            if deployment.public_model == model
        )

    async def _ordered_candidates(
        self,
        model: str,
        strategy: Strategy,
        candidates: tuple[tuple[AccountConfig, DeploymentConfig], ...],
        snapshots: dict[str, AccountSnapshot],
        exclusions: tuple[EligibilityExclusion, ...],
        quota_state: QuotaBackendState | None,
        latency: dict[str, float],
        now: float,
        request_id: str,
    ) -> tuple[tuple[AccountConfig, DeploymentConfig], ...]:
        routing_candidates: Final = tuple(
            _routing_candidate(
                account=account,
                deployment=deployment,
                snapshot=snapshots[account.id],
                exclusions=exclusions,
                quota_state=quota_state,
                now=now,
                latency_ewma_ms=latency.get(deployment.litellm_model_id),
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
        reason: Final = (
            "deployment_disabled"
            if not deployment.enabled
            else "manual_pause"
            if deployment.routing_paused
            else order.candidate.quota_unavailable_reason
            or _unavailable_reason(snapshot=snapshot, exclusion=active_exclusion)
        )
        return RouteEntry(
            account_id=account.id,
            display_name=account.display_name,
            provider=account.provider,
            base_url_display=account.base_url_display,
            deployment_id=deployment.litellm_model_id,
            billing_route_id=deployment.billing_route_id,
            billing_mode=deployment.billing_mode,
            binding_id=deployment.binding_id,
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
            remaining_quota=order.candidate.remaining_quota,
            remaining_quota_unit=order.candidate.remaining_quota_unit,
            latency_ewma_ms=order.candidate.latency_ewma_ms,
            effective_cost=order.candidate.effective_cost,
            cost_evidence=deployment.cost_evidence,
            manual_order=deployment.manual_order,
            effective_weight=deployment.routing_weight or account.weight,
            routing_paused=deployment.routing_paused,
        )


def _quota_ratio(account: AccountConfig, snapshot: AccountSnapshot) -> float | None:
    pairs: Final = (
        (snapshot.quota.total, account.quotas.total),
        (snapshot.quota.five_hour, account.quotas.five_hour),
        (snapshot.quota.weekly, account.quotas.weekly),
    )
    ratios: Final = tuple(remaining / limit for remaining, limit in pairs if remaining is not None and limit)
    return min(ratios) if ratios else None


def _route_quota(
    account: AccountConfig,
    deployment: DeploymentConfig,
    snapshot: AccountSnapshot,
    quota_state: QuotaBackendState | None,
) -> _RouteQuota:
    matching: Final = (
        ()
        if quota_state is None
        else tuple(
            state.window
            for state in quota_state.windows
            if state.account_id == account.id and _quota_window_matches(state.window, deployment)
        )
    )
    if not matching:
        return _RouteQuota(
            remaining_ratio=_quota_ratio(account=account, snapshot=snapshot),
            remaining=None,
            unit=None,
            unavailable_reason=None,
        )
    quantified: Final = tuple(
        (window, _available_quota_units(window))
        for window in matching
        if _available_quota_units(window) is not None
    )
    exhausted: Final = next(
        (window.config.reason_code for window, remaining in quantified if remaining is not None and remaining <= 0),
        None,
    )
    ratios: Final = tuple(
        (window, remaining / window.config.limit)
        for window, remaining in quantified
        if remaining is not None and window.config.limit is not None and window.config.limit > 0
    )
    if ratios:
        selected, ratio = min(ratios, key=lambda item: item[1])
        remaining: Final = next(
            current for window, current in quantified if window.config.window_id == selected.config.window_id
        )
        return _RouteQuota(
            remaining_ratio=float(ratio),
            remaining=remaining,
            unit=selected.config.kind.value,
            unavailable_reason=exhausted,
        )
    if quantified:
        selected, remaining = quantified[0]
        return _RouteQuota(
            remaining_ratio=None,
            remaining=remaining,
            unit=selected.config.kind.value,
            unavailable_reason=exhausted,
        )
    return _RouteQuota(
        remaining_ratio=None,
        remaining=None,
        unit=None,
        unavailable_reason=None,
    )


def _quota_window_matches(window: RuntimeQuotaWindow, deployment: DeploymentConfig) -> bool:
    scope: Final = window.config.scope
    return (
        scope == RuntimeQuotaScope.CHANNEL
        or (scope == RuntimeQuotaScope.MODEL and window.config.subject_id == deployment.public_model)
        or (scope == RuntimeQuotaScope.BILLING_ROUTE and window.config.subject_id == deployment.billing_route_id)
    )


def _available_quota_units(window: RuntimeQuotaWindow) -> Decimal | None:
    if window.remaining is None:
        return None
    return max(Decimal("0"), window.remaining - window.config.safety_reserve - window.reserved)


def _routing_candidate(
    account: AccountConfig,
    deployment: DeploymentConfig,
    snapshot: AccountSnapshot,
    exclusions: tuple[EligibilityExclusion, ...],
    quota_state: QuotaBackendState | None,
    now: float,
    latency_ewma_ms: float | None,
) -> RoutingCandidate:
    quota: Final = _route_quota(
        account=account,
        deployment=deployment,
        snapshot=snapshot,
        quota_state=quota_state,
    )
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
        available=(
            deployment.enabled
            and not deployment.routing_paused
            and quota.unavailable_reason is None
            and _unavailable_reason(snapshot=snapshot, exclusion=exclusion) is None
        ),
        priority=account.priority,
        weight=deployment.routing_weight or account.weight,
        manual_order=deployment.manual_order,
        inflight=snapshot.inflight,
        max_concurrency=snapshot.max_concurrency,
        remaining_quota_ratio=quota.remaining_ratio,
        remaining_quota=quota.remaining,
        remaining_quota_unit=quota.unit,
        quota_unavailable_reason=quota.unavailable_reason,
        latency_ewma_ms=latency_ewma_ms,
        effective_cost=None if deployment.cost_evidence is None else deployment.cost_evidence.effective_cost,
        cost_currency=None if deployment.cost_evidence is None else deployment.cost_evidence.currency,
        cost_unit=None if deployment.cost_evidence is None else deployment.cost_evidence.unit,
        cost_partial=False if deployment.cost_evidence is None else deployment.cost_evidence.partial,
        cost_included=(
            False
            if deployment.cost_evidence is None
            else deployment.cost_evidence.kind == CostEvidenceKind.SUBSCRIPTION_INCLUDED
        ),
    )


def _candidate_id(account: AccountConfig, deployment: DeploymentConfig) -> str:
    return f"{account.id}\x00{deployment.litellm_model_id}\x00{deployment.billing_route_id or ''}"


def _configuration_rejection(
    account: AccountConfig,
    deployment: DeploymentConfig,
) -> AcquireCandidateRejection | None:
    reason_code: Final = (
        "deployment_disabled" if not deployment.enabled else "manual_pause" if deployment.routing_paused else None
    )
    if reason_code is None:
        return None
    return AcquireCandidateRejection(
        account_id=account.id,
        deployment_id=deployment.litellm_model_id,
        binding_id=deployment.binding_id,
        billing_route_id=deployment.billing_route_id,
        stage=AcquireRejectionStage.CONFIGURATION,
        reason_code=reason_code,
        scope=AcquireRejectionScope.DEPLOYMENT,
        source=AcquireRejectionSource.ADMINISTRATIVE,
    )


def _reserve_rejection(
    account: AccountConfig,
    deployment: DeploymentConfig,
    snapshot: AccountSnapshot,
    exclusions: tuple[EligibilityExclusion, ...],
    reason_code: str,
    now: float,
) -> AcquireCandidateRejection:
    candidate_evidence_value: Final = candidate_evidence(
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
    matching_evidence: Final = (
        candidate_evidence_value
        if candidate_evidence_value is not None and candidate_evidence_value.reason_code == reason_code
        else None
    )
    quota_scope: Final = _quota_rejection_scope(account, deployment, reason_code)
    prechecked_reason: Final = _unavailable_reason(snapshot=snapshot, exclusion=active_exclusion)
    return AcquireCandidateRejection(
        account_id=account.id,
        deployment_id=deployment.litellm_model_id,
        binding_id=deployment.binding_id,
        billing_route_id=deployment.billing_route_id,
        stage=(
            AcquireRejectionStage.ELIGIBILITY if prechecked_reason == reason_code else AcquireRejectionStage.RESERVATION
        ),
        reason_code=reason_code,
        scope=(
            AcquireRejectionScope(matching_evidence.scope.value)
            if matching_evidence is not None
            else quota_scope
            if quota_scope is not None
            else AcquireRejectionScope.CHANNEL
        ),
        source=(
            AcquireRejectionSource(matching_evidence.source.value)
            if matching_evidence is not None
            else _fallback_rejection_source(reason_code)
        ),
        state=(
            AcquireRejectionState.HALF_OPEN
            if candidate_evidence_value is not None
            and effective_state(candidate_evidence_value, now).value == AcquireRejectionState.HALF_OPEN
            else AcquireRejectionState.ACTIVE
        ),
        retry_at=(
            matching_evidence.retry_at
            if matching_evidence is not None
            else snapshot.cooldown_until
            if reason_code == "cooldown"
            else None
        ),
    )


def _quota_rejection_scope(
    account: AccountConfig,
    deployment: DeploymentConfig,
    reason_code: str,
) -> AcquireRejectionScope | None:
    matching: Final = tuple(
        window
        for window in account.quota_windows
        if window.reason_code == reason_code
        and (
            window.scope == RuntimeQuotaScope.CHANNEL
            or (window.scope == RuntimeQuotaScope.MODEL and window.subject_id == deployment.public_model)
            or (window.scope == RuntimeQuotaScope.BILLING_ROUTE and window.subject_id == deployment.billing_route_id)
        )
    )
    if not matching:
        return None
    scope: Final = matching[0].scope
    return (
        AcquireRejectionScope.BILLING_ROUTE
        if scope == RuntimeQuotaScope.BILLING_ROUTE
        else AcquireRejectionScope(scope.value)
    )


def _fallback_rejection_source(reason_code: str) -> AcquireRejectionSource:
    if reason_code in {"disabled", "deployment_disabled", "manual_pause"}:
        return AcquireRejectionSource.ADMINISTRATIVE
    if reason_code == "unhealthy":
        return AcquireRejectionSource.HEALTH
    if reason_code in {"capacity", "cooldown", "half_open_probe_inflight"}:
        return AcquireRejectionSource.CAPACITY
    if "quota" in reason_code or reason_code.endswith("_exhausted"):
        return AcquireRejectionSource.QUOTA
    return AcquireRejectionSource.RUNTIME


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
