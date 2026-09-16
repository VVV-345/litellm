"""本模块管理卡片绑定、租约及请求事件，真实请求由 LiteLLM 网关直接发送到账号容器。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Final, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from account_pool.card_keys import CardKeyService
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import EnvironmentRecord, GatewayEnvironment, utc_now
from account_pool.error_logs import MODEL_REQUEST_OPERATION, ErrorLogRecord, ErrorLogService
from account_pool.gateway_contracts import (
    AcquireRejected,
    AcquireRequest,
    Candidate,
    CandidateModelQuota,
    FinishRequest,
    Lease,
    Resolution,
    ResolveRequest,
)
from account_pool.gateway_repository import LeaseRepository
from account_pool.policies import AccountPolicy, PolicyRepository
from account_pool.ports import EnvironmentRepository
from account_pool.settings import (
    AccountPoolSettings,
    AccountPoolSettingsRepository,
    settings_for_card,
    streaming_mode_for_card,
)


class GatewayService:
    def __init__(
        self,
        keys: CardKeyService,
        environments: EnvironmentRepository,
        policies: PolicyRepository,
        leases: LeaseRepository,
        logs: ErrorLogService,
        gateway: Callable[[EnvironmentRecord], GatewayEnvironment],
        settings: AccountPoolSettingsRepository | None = None,
        ownership: CredentialOwnership | None = None,
    ) -> None:
        self.keys: Final = keys
        self.environments: Final = environments
        self.policies: Final = policies
        self.leases: Final = leases
        self.logs: Final = logs
        self.gateway: Final = gateway
        self.settings: Final = settings
        self.ownership: Final = ownership

    async def resolve(self, request: ResolveRequest) -> Resolution:
        key: Final = await self.keys.authenticate(request.card_key)
        if key is None:
            raise HTTPException(401, "Invalid or revoked account pool key")
        card: Final = await self.environments.get(key.card_id)
        if (
            card is None
            or not card.enabled
            or card.manual_cooldown
            or card.status.value == "deleting"
            or card.configuration_pending
        ):
            raise HTTPException(403, "Account pool card is disabled or unavailable")
        policy: Final = await self.policies.get(card.id)
        global_settings: Final = None if self.settings is None else (await self.settings.get()).values
        effective_settings: Final = None if global_settings is None else settings_for_card(global_settings, card.id)
        effective_card_policy: Final = _policy_with_settings(
            policy.policy,
            effective_settings if policy.version == 0 else None,
        )
        cooling: Final = await self.leases.cooling()
        owned: Final = self.ownership is None or await self.ownership.owns(card.id, card.credential_fingerprints)
        candidates: Final = (
            (await self.candidate(card, global_settings),)
            if owned and card.id not in cooling and self.gateway(card).routable
            else ()
        )
        binding: Final = (
            binding_hash(key.key_id, request.session_hash) if policy.policy.routing.session_affinity else None
        )
        streaming_mode: Final = (
            "inherit" if global_settings is None else streaming_mode_for_card(global_settings, card.id)
        )
        websocket_enabled: Final = _websocket_enabled(
            policy.policy.transport.websocket,
            effective_settings.websocket_enabled if effective_settings is not None else False,
        )
        return Resolution(
            card_id=card.id,
            key_id=key.key_id,
            card_version=card.version,
            policy_version=policy.version,
            policy=effective_card_policy,
            candidates=candidates,
            full_logging_enabled=global_settings.full_logging_enabled if global_settings else False,
            full_log_retention_days=global_settings.full_log_retention_days if global_settings else 30,
            sticky_account_id=await self.leases.sticky(binding),
            streaming_mode=streaming_mode,
            websocket_enabled=websocket_enabled,
        )

    async def candidate(
        self,
        record: EnvironmentRecord,
        global_settings: AccountPoolSettings | None = None,
    ) -> Candidate:
        endpoint: Final = self.gateway(record)
        policy: Final = await self.policies.get(record.id)
        configured_policy: Final = _policy_with_settings(
            policy.policy,
            (
                settings_for_card(global_settings, record.id)
                if global_settings is not None and policy.version == 0
                else None
            ),
        )
        effective_policy: Final = (
            configured_policy.model_copy(
                update={
                    "routing": configured_policy.routing.model_copy(
                        update={"priority": record.openai_compatible.priority}
                    )
                }
            )
            if policy.version == 0 and record.openai_compatible is not None
            else configured_policy
        )
        effective_settings: Final = (
            settings_for_card(global_settings, record.id) if global_settings is not None else None
        )
        return Candidate(
            id=record.id,
            channel=record.channel,
            supplier=record.supplier,
            environment_version=record.version,
            policy_version=policy.version,
            enabled_models=endpoint.enabled_models,
            proxy_endpoint=record.proxy_profile_id or "default",
            api_base=endpoint.api_base,
            api_key=endpoint.api_key,
            credentials=endpoint.credentials,
            headers=endpoint.headers,
            model_prefix=endpoint.model_prefix,
            concurrency_limit=endpoint.concurrency_limit,
            policy=effective_policy,
            remaining_percent=min((window.remaining_percent for window in record.quota.windows), default=None),
            quota_observed_at=record.quota.observed_at,
            model_quotas=tuple(
                CandidateModelQuota(
                    model=item.model,
                    remaining_percent=min(window.remaining_percent for window in item.quota.windows),
                    observed_at=item.quota.observed_at,
                )
                for item in record.model_quotas
                if item.quota.windows
            ),
            plan_type=record.quota.plan_type,
            auth_file_plan_type=record.quota.auth_file_plan_type,
            subscription_active_until=record.quota.subscription_active_until,
            websocket_enabled=_websocket_enabled(
                policy.policy.transport.websocket,
                effective_settings.websocket_enabled if effective_settings is not None else False,
            ),
        )

    async def acquire(self, request: AcquireRequest) -> Lease:
        resolution: Final = await self.resolve(
            ResolveRequest(card_key=request.card_key, session_hash=request.session_hash)
        )
        candidate: Final = next((item for item in resolution.candidates if item.id == request.account_id), None)
        if candidate is None or request.model not in candidate.enabled_models:
            raise HTTPException(403, "Account is outside the card scope or unavailable")
        if request.model in resolution.policy.excluded_models or request.model in candidate.policy.excluded_models:
            raise HTTPException(403, "Model is excluded")
        if resolution.card_version != request.card_version or resolution.policy_version != request.policy_version:
            raise HTTPException(409, "Card configuration changed")
        if (
            candidate.environment_version != request.account_version
            or candidate.policy_version != request.account_policy_version
        ):
            raise HTTPException(409, "Account configuration changed")
        binding: Final = (
            binding_hash(resolution.key_id, request.session_hash)
            if resolution.policy.routing.session_affinity
            else None
        )
        lease: Final = await self.leases.acquire(request, resolution, candidate, binding)
        if isinstance(lease, AcquireRejected):
            raise HTTPException(409, detail=lease.model_dump(mode="json"))
        return lease

    async def finish(self, request: FinishRequest) -> None:
        lease: Final = await self.leases.get(request.lease_id)
        if lease is None:
            return
        failed: Final = request.http_status >= 400
        category: Final = (
            "authentication"
            if request.http_status == 401
            else "authorization"
            if request.http_status == 403
            else "rate_limit"
            if request.http_status == 429
            else "timeout"
            if request.http_status == 504
            else "connection"
            if request.stage == "connection"
            else "upstream"
            if failed
            else None
        )
        now: Final = utc_now()
        input_total: Final = (
            (request.input_tokens or 0)
            + (request.cache_read_input_tokens or 0)
            + (request.cache_creation_input_tokens or 0)
            if request.endpoint.endswith("/messages")
            else request.input_tokens
        )
        event: Final = ErrorLogRecord(
            event_id=lease.lease_id,
            occurred_at=lease.started_at,
            finished_at=now,
            channel=lease.channel,
            supplier=lease.supplier,
            card_id=lease.card_id,
            environment_id=lease.account_id,
            account_id=lease.account_id,
            card_key_id=lease.key_id,
            request_id=lease.request_id,
            attempt=lease.attempt,
            operation=MODEL_REQUEST_OPERATION,
            stage=request.stage,
            model=lease.model,
            endpoint=request.endpoint,
            method=request.method,
            error_category=category,
            severity="error" if failed else "info",
            http_status=request.http_status,
            upstream_code=request.upstream_code,
            retryable=request.retryable,
            retry_count=lease.attempt - 1,
            switched_account=request.switched_account,
            next_account_id=request.next_account_id,
            message=request.message,
            detail=request.detail,
            duration_ms=max(0, int((now - lease.started_at).total_seconds() * 1000)),
            final_status="retrying" if request.retryable else "failed" if failed else "succeeded",
            input_tokens=request.input_tokens,
            output_tokens=request.output_tokens,
            cache_read_input_tokens=request.cache_read_input_tokens,
            cache_creation_input_tokens=request.cache_creation_input_tokens,
            # 原生 Messages 的输入不含缓存，OpenAI-compatible 的输入已经包含缓存。
            cache_rate=(
                None
                if failed
                or request.retryable
                or request.input_tokens is None
                or not input_total
                or request.cache_read_input_tokens is None
                or request.cache_read_input_tokens > input_total
                else request.cache_read_input_tokens / input_total
            ),
            routing_reason=lease.routing_reason,
            cost_usd=request.cost_usd,
            session_id=request.session_id,
            proxy_endpoint=request.proxy_endpoint,
            cost_source=request.cost_source,
            cost_details=request.cost_details,
            full_log_state=request.full_log_state,
            spend_sync_state=request.spend_sync_state,
        )
        cooldown: Final = 60 if request.http_status == 429 else 300 if request.http_status in (401, 403) else 0
        actual_tokens: Final = (
            0
            if failed
            else request.input_tokens + request.output_tokens
            if request.input_tokens is not None and request.output_tokens is not None
            else lease.reserved_tokens
            if lease.budget_enabled
            else None
        )
        try:
            await self.logs.repository.append(event)
        finally:
            # 日志故障不能占住并发租约，否则账号会持续误判为满载。
            await self.leases.release(lease, cooldown, actual_tokens)


def binding_hash(key_id: UUID, session_hash: str | None) -> str | None:
    if session_hash is None:
        return None
    return hashlib.sha256(f"{key_id}:{session_hash}".encode()).hexdigest()


def _policy_with_settings(policy: AccountPolicy, settings: AccountPoolSettings | None) -> AccountPolicy:
    if settings is None:
        return policy
    return policy.model_copy(
        update={
            "routing": policy.routing.model_copy(
                update={"strategy": settings.default_route, "max_attempts": settings.max_attempts}
            ),
            "transport": policy.transport.model_copy(
                update={"request_timeout_seconds": settings.request_timeout_seconds}
            ),
        }
    )


def _websocket_enabled(mode: Literal["inherit", "enabled", "disabled"], inherited: bool) -> bool:
    return inherited if mode == "inherit" else mode == "enabled"


def create_gateway_router(service: GatewayService, authorize: Callable[..., None]) -> APIRouter:
    router: Final = APIRouter(
        prefix="/api/internal/gateway", dependencies=[Depends(authorize)], include_in_schema=False
    )

    async def resolve(request: ResolveRequest, response: Response) -> Resolution:
        response.headers["Cache-Control"] = "no-store"
        return await service.resolve(request)

    async def acquire(request: AcquireRequest) -> Lease:
        return await service.acquire(request)

    async def finish(request: FinishRequest) -> None:
        await service.finish(request)

    router.add_api_route("/resolve", resolve, methods=["POST"])
    router.add_api_route("/acquire", acquire, methods=["POST"])
    router.add_api_route("/finish", finish, methods=["POST"], status_code=204)
    return router
