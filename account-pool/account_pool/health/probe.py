"""通过 LiteLLM 定向检查 Deployment，并把安全结果接入现有健康状态机。"""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from typing import Final, Literal, Protocol, Self
from uuid import UUID, uuid4

import httpx
from pydantic import ConfigDict, Field, model_validator

from account_pool.eligibility import EligibilityExclusion, EligibilityScope, EligibilityState, effective_state
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


class HealthProbeTrigger(StrEnum):
    MANUAL = "manual"
    INITIAL = "initial"
    HALF_OPEN = "half_open"
    IDLE = "idle"


class HealthProbeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class HealthProbeRequest(FrozenModel):
    deployment_id: str | None = Field(default=None, min_length=1)


class HealthProbeResult(FrozenModel):
    probe_id: UUID
    status: HealthProbeStatus
    trigger: HealthProbeTrigger
    channel_id: UUID | None = None
    account_id: str | None = None
    deployment_id: str | None = None
    public_model: str | None = None
    reason_code: str | None = Field(default=None, min_length=1, max_length=100)
    response_status_code: int | None = Field(default=None, ge=100, le=599)
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if (self.status == HealthProbeStatus.SUCCEEDED) == (self.reason_code is not None):
            raise ValueError("only unsuccessful health probes require a reason code")
        if self.status == HealthProbeStatus.SUCCEEDED and self.deployment_id is None:
            raise ValueError("successful health probes require a deployment")
        return self


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
    ) -> None:
        self._accounts: Final = accounts
        self._store: Final = store
        self._client: Final = client
        self._litellm_url: Final = litellm_url.rstrip("/")
        self._admin_key: Final = admin_key
        self._lease_ttl_seconds: Final = lease_ttl_seconds
        self._max_response_bytes: Final = max_response_bytes

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
        snapshots: Final = {snapshot.account_id: snapshot for snapshot in await self._store.snapshots()}
        exclusions: Final = await self._store.eligibility_exclusions()
        due: Final = tuple(
            (account, deployment, trigger)
            for account in self._accounts.account_configs()
            for deployment, trigger in _due_deployments(
                account=account,
                snapshot=snapshots.get(account.id),
                exclusions=exclusions,
                now=time.time(),
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
        if isinstance(upstream, _UpstreamProbeFailure):
            return _result(
                probe_id=probe_id,
                status=HealthProbeStatus.FAILED,
                trigger=trigger,
                channel_id=channel_id,
                account=account,
                deployment=deployment,
                reason_code=upstream.reason_code,
                response_status_code=upstream.response_status_code,
                started=started,
            )
        return _result(
            probe_id=probe_id,
            status=HealthProbeStatus.SUCCEEDED,
            trigger=trigger,
            channel_id=channel_id,
            account=account,
            deployment=deployment,
            response_status_code=upstream.response_status_code,
            started=started,
        )

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
    return ()


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
