"""通过 LiteLLM 定向检查 Deployment，并把安全结果接入现有健康状态机。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Final, Literal, Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import ConfigDict

from account_pool.eligibility import EligibilityExclusion, EligibilityScope, EligibilityState, effective_state
from account_pool.health.models import (
    HealthActivity,
    HealthProbeRequest,
    HealthProbeResult,
    HealthProbeStatus,
    HealthProbeTrigger,
)
from account_pool.health.repository import (
    HealthActivityLoadSuccess,
    HealthEventRepository,
)
from account_pool.health.service import HealthEventRecorder
from account_pool.models import (
    AccountConfig,
    AccountSnapshot,
    DeploymentConfig,
    FrozenModel,
    Health,
    ReserveRejected,
    SettleRequest,
)
from account_pool.provider_services.http_response import read_limited_response
from account_pool.store import StateStore

_MAX_RESPONSE_BYTES: Final = 65_536
_LOGGER: Final = logging.getLogger(__name__)


class ProbeAccountSource(Protocol):
    def account_configs(self) -> tuple[AccountConfig, ...]: ...


class HealthProbeManager(Protocol):
    async def probe_channel(
        self,
        channel_id: UUID,
        request: HealthProbeRequest,
        trigger: HealthProbeTrigger = HealthProbeTrigger.MANUAL,
    ) -> HealthProbeResult: ...

    async def probe_account(
        self,
        account_id: str,
        request: HealthProbeRequest,
        trigger: HealthProbeTrigger = HealthProbeTrigger.MANUAL,
    ) -> HealthProbeResult: ...

    async def probe_due(self) -> tuple[HealthProbeResult, ...]: ...


class _ProbeResponse(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    status: Literal["success", "error"]


class _UpstreamProbeSuccess(FrozenModel):
    status: Literal["succeeded"] = "succeeded"
    response_status_code: int


class _UpstreamProbeFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    reason_code: str
    response_status_code: int | None = None
    provider_signal: bool


UpstreamProbeResult = _UpstreamProbeSuccess | _UpstreamProbeFailure


class ActiveHealthProbeService:
    def __init__(
        self,
        *,
        accounts: ProbeAccountSource,
        store: StateStore,
        client: httpx.AsyncClient,
        litellm_url: str,
        admin_key: str | None,
        lease_ttl_seconds: int,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        events: HealthEventRepository | None = None,
        recorder: HealthEventRecorder | None = None,
        idle_probe_after_seconds: int = 0,
    ) -> None:
        self._accounts: Final = accounts
        self._store: Final = store
        self._client: Final = client
        self._litellm_url: Final = litellm_url.rstrip("/")
        self._admin_key: Final = admin_key
        self._lease_ttl_seconds: Final = lease_ttl_seconds
        self._max_response_bytes: Final = max_response_bytes
        self._events: Final = events
        self._recorder: Final = (
            recorder
            if recorder is not None
            else None
            if events is None
            else HealthEventRecorder(accounts=accounts, events=events)
        )
        self._idle_probe_after_seconds: Final = idle_probe_after_seconds

    async def probe_channel(
        self,
        channel_id: UUID,
        request: HealthProbeRequest,
        trigger: HealthProbeTrigger = HealthProbeTrigger.MANUAL,
    ) -> HealthProbeResult:
        account: Final = next(
            (candidate for candidate in self._accounts.account_configs() if candidate.channel_id == channel_id),
            None,
        )
        return await self._probe(account=account, channel_id=channel_id, request=request, trigger=trigger)

    async def probe_account(
        self,
        account_id: str,
        request: HealthProbeRequest,
        trigger: HealthProbeTrigger = HealthProbeTrigger.MANUAL,
    ) -> HealthProbeResult:
        account: Final = next(
            (candidate for candidate in self._accounts.account_configs() if candidate.id == account_id),
            None,
        )
        channel_id: Final = None if account is None else account.channel_id
        return await self._probe(account=account, channel_id=channel_id, request=request, trigger=trigger)

    async def probe_due(self) -> tuple[HealthProbeResult, ...]:
        now: Final = time.time()
        snapshots: Final = {snapshot.account_id: snapshot for snapshot in await self._store.snapshots()}
        exclusions: Final = await self._store.eligibility_exclusions()
        activities: Final = await self._load_activities()
        due: Final = tuple(
            (account, deployment, trigger)
            for account in self._accounts.account_configs()
            for deployment, trigger in _due_deployments(
                account=account,
                snapshot=snapshots.get(account.id),
                exclusions=exclusions,
                activities=activities,
                idle_probe_after_seconds=self._idle_probe_after_seconds,
                now=now,
            )
        )
        return tuple(
            await asyncio.gather(
                *(
                    self._probe(
                        account=account,
                        channel_id=account.channel_id,
                        request=HealthProbeRequest(deployment_id=deployment.litellm_model_id),
                        trigger=trigger,
                    )
                    for account, deployment, trigger in due
                )
            )
        )

    async def _load_activities(self) -> Mapping[tuple[str, str], HealthActivity]:
        repository: Final = self._events
        if repository is None or self._idle_probe_after_seconds <= 0:
            return {}
        result: Final = await repository.load_activity()
        if isinstance(result, HealthActivityLoadSuccess):
            return {(item.account_id, item.deployment_id): item for item in result.activities}
        _LOGGER.error("Failed to load Account Pool health activity: %s", result.code)
        return {}

    async def _probe(
        self,
        *,
        account: AccountConfig | None,
        channel_id: UUID | None,
        request: HealthProbeRequest,
        trigger: HealthProbeTrigger,
    ) -> HealthProbeResult:
        probe_id: Final = uuid4()
        started: Final = time.perf_counter()
        if account is None:
            return _result(
                probe_id=probe_id,
                status=HealthProbeStatus.FAILED,
                trigger=trigger,
                channel_id=channel_id,
                reason_code="channel_not_found",
                started=started,
            )
        deployment: Final = _select_deployment(account, request.deployment_id)
        if deployment is None:
            reason: Final = "deployment_not_found" if request.deployment_id is not None else "deployment_unavailable"
            return _result(
                probe_id=probe_id,
                status=HealthProbeStatus.SKIPPED,
                trigger=trigger,
                channel_id=channel_id,
                account=account,
                reason_code=reason,
                started=started,
            )
        if not account.enabled or not deployment.enabled:
            return _result(
                probe_id=probe_id,
                status=HealthProbeStatus.SKIPPED,
                trigger=trigger,
                channel_id=channel_id,
                account=account,
                deployment=deployment,
                reason_code="disabled",
                started=started,
            )
        if self._admin_key is None:
            return _result(
                probe_id=probe_id,
                status=HealthProbeStatus.FAILED,
                trigger=trigger,
                channel_id=channel_id,
                account=account,
                deployment=deployment,
                reason_code="litellm_admin_key_missing",
                started=started,
            )
        reserved: Final = await self._store.reserve(
            account=account,
            deployment_id=deployment.litellm_model_id,
            billing_route_id=deployment.billing_route_id,
            public_model=deployment.public_model,
            request_id=f"health-probe:{probe_id}",
            estimated_tokens=1,
            ttl_seconds=self._lease_ttl_seconds,
            probe=True,
        )
        if isinstance(reserved, ReserveRejected):
            return _result(
                probe_id=probe_id,
                status=HealthProbeStatus.SKIPPED,
                trigger=trigger,
                channel_id=channel_id,
                account=account,
                deployment=deployment,
                reason_code=reserved.reason,
                started=started,
            )

        upstream: Final = await self._request(deployment.litellm_model_id)
        settlement: Final = _settlement(reserved.lease.lease_id, upstream)
        observed: Final = _result(
            probe_id=probe_id,
            status=(
                HealthProbeStatus.FAILED
                if isinstance(upstream, _UpstreamProbeFailure)
                else HealthProbeStatus.SUCCEEDED
            ),
            trigger=trigger,
            channel_id=channel_id,
            account=account,
            deployment=deployment,
            reason_code=upstream.reason_code if isinstance(upstream, _UpstreamProbeFailure) else None,
            response_status_code=upstream.response_status_code,
            started=started,
        )
        await self._record_observation(observed, settlement)
        settled: Final = await self._store.settle(settlement)
        released: Final = await self._store.release(reserved.lease.lease_id)
        if not settled or not released:
            return _result(
                probe_id=probe_id,
                status=HealthProbeStatus.FAILED,
                trigger=trigger,
                channel_id=channel_id,
                account=account,
                deployment=deployment,
                reason_code="runtime_settlement_failed",
                response_status_code=upstream.response_status_code,
                started=started,
            )
        return observed

    async def _record_observation(self, result: HealthProbeResult, settlement: SettleRequest) -> None:
        if self._recorder is not None:
            await self._recorder.record_probe(result, settlement)

    async def _request(self, deployment_id: str) -> UpstreamProbeResult:
        try:
            async with self._client.stream(
                method="POST",
                url=f"{self._litellm_url}/health/test_connection",
                headers={"authorization": f"Bearer {self._admin_key}"},
                json={"model_info": {"id": deployment_id}},
                follow_redirects=False,
            ) as response:
                status_code: Final = response.status_code
                if 300 <= status_code < 400:
                    return _UpstreamProbeFailure(
                        reason_code="litellm_redirect_rejected",
                        response_status_code=status_code,
                        provider_signal=False,
                    )
                if status_code in (401, 403):
                    return _UpstreamProbeFailure(
                        reason_code="litellm_admin_auth_failed",
                        response_status_code=status_code,
                        provider_signal=False,
                    )
                if status_code >= 400:
                    return _UpstreamProbeFailure(
                        reason_code="upstream_probe_failed" if status_code >= 500 else "litellm_probe_rejected",
                        response_status_code=status_code,
                        provider_signal=status_code >= 500,
                    )
                content: Final = await read_limited_response(response, self._max_response_bytes)
        except httpx.HTTPError:
            return _UpstreamProbeFailure(reason_code="litellm_transport_failed", provider_signal=True)
        if content is None:
            return _UpstreamProbeFailure(
                reason_code="litellm_response_too_large",
                response_status_code=status_code,
                provider_signal=False,
            )
        try:
            parsed: Final = _ProbeResponse.model_validate_json(content)
        except ValueError:
            return _UpstreamProbeFailure(
                reason_code="litellm_invalid_response",
                response_status_code=status_code,
                provider_signal=False,
            )
        if parsed.status == "error":
            return _UpstreamProbeFailure(
                reason_code="upstream_probe_failed",
                response_status_code=status_code,
                provider_signal=True,
            )
        return _UpstreamProbeSuccess(response_status_code=status_code)


def _select_deployment(account: AccountConfig, deployment_id: str | None) -> DeploymentConfig | None:
    if deployment_id is not None:
        return next(
            (deployment for deployment in account.deployments if deployment.litellm_model_id == deployment_id),
            None,
        )
    return next((deployment for deployment in account.deployments if deployment.enabled), None)


def _due_deployments(
    *,
    account: AccountConfig,
    snapshot: AccountSnapshot | None,
    exclusions: tuple[EligibilityExclusion, ...],
    activities: Mapping[tuple[str, str], HealthActivity],
    idle_probe_after_seconds: int,
    now: float,
) -> tuple[tuple[DeploymentConfig, HealthProbeTrigger], ...]:
    if not account.enabled:
        return ()
    half_open: Final = tuple(
        deployment
        for exclusion in exclusions
        if exclusion.account_id == account.id and effective_state(exclusion, now) == EligibilityState.HALF_OPEN
        for deployment in _deployments_for_exclusion(account, exclusion)
        if deployment.enabled
    )
    unique_half_open: Final = tuple(dict.fromkeys(deployment.litellm_model_id for deployment in half_open))
    if unique_half_open:
        by_id: Final = {deployment.litellm_model_id: deployment for deployment in account.deployments}
        return ((by_id[unique_half_open[0]], HealthProbeTrigger.HALF_OPEN),)
    initial: Final = _select_deployment(account, None)
    if snapshot is not None and snapshot.health in (Health.UNKNOWN, Health.DEGRADED) and initial is not None:
        return ((initial, HealthProbeTrigger.INITIAL),)
    idle: Final = next(
        (
            deployment
            for deployment in account.deployments
            if deployment.enabled
            and _idle_probe_due(
                activities.get((account.id, deployment.litellm_model_id)),
                idle_probe_after_seconds,
                now,
            )
        ),
        None,
    )
    if idle is not None:
        return ((idle, HealthProbeTrigger.IDLE),)
    return ()


def _idle_probe_due(activity: HealthActivity | None, idle_probe_after_seconds: int, now: float) -> bool:
    if activity is None or idle_probe_after_seconds <= 0:
        return False
    reference: Final = max(
        (
            value.timestamp()
            for value in (activity.last_request_at, activity.last_probe_at)
            if value is not None
        ),
        default=None,
    )
    return reference is not None and reference + idle_probe_after_seconds <= now


def _deployments_for_exclusion(
    account: AccountConfig,
    exclusion: EligibilityExclusion,
) -> tuple[DeploymentConfig, ...]:
    if exclusion.scope == EligibilityScope.CHANNEL:
        selected: Final = _select_deployment(account, None)
        return () if selected is None else (selected,)
    if exclusion.scope == EligibilityScope.MODEL:
        return tuple(deployment for deployment in account.deployments if deployment.public_model == exclusion.model)
    if exclusion.scope == EligibilityScope.DEPLOYMENT:
        return tuple(
            deployment
            for deployment in account.deployments
            if deployment.litellm_model_id == exclusion.deployment_id
        )
    return tuple(
        deployment
        for deployment in account.deployments
        if deployment.litellm_model_id == exclusion.deployment_id
        and deployment.billing_route_id == exclusion.billing_route_id
    )


def _settlement(lease_id: str, upstream: UpstreamProbeResult) -> SettleRequest:
    if isinstance(upstream, _UpstreamProbeSuccess):
        return SettleRequest(lease_id=lease_id, success=True, latency_ms=0)
    status_code: Final = (
        upstream.response_status_code
        if upstream.provider_signal and upstream.response_status_code is not None
        else 503
        if upstream.provider_signal
        else 400
    )
    return SettleRequest(
        lease_id=lease_id,
        success=False,
        status_code=status_code,
        error_type="active_health_probe",
        latency_ms=0,
    )


def _result(
    *,
    probe_id: UUID,
    status: HealthProbeStatus,
    trigger: HealthProbeTrigger,
    channel_id: UUID | None,
    started: float,
    account: AccountConfig | None = None,
    deployment: DeploymentConfig | None = None,
    reason_code: str | None = None,
    response_status_code: int | None = None,
) -> HealthProbeResult:
    return HealthProbeResult(
        probe_id=probe_id,
        status=status,
        trigger=trigger,
        channel_id=channel_id,
        account_id=None if account is None else account.id,
        deployment_id=None if deployment is None else deployment.litellm_model_id,
        public_model=None if deployment is None else deployment.public_model,
        reason_code=reason_code,
        response_status_code=response_status_code,
        latency_ms=(time.perf_counter() - started) * 1000,
    )
