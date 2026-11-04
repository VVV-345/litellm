"""验证现有访问令牌的临时降级能力，不刷新凭据或清除独立限制。"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal, Protocol
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from account_pool.channels.cliproxyapi.quota_state import merge_model_quotas
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import (
    AccessTokenFallbackStatus,
    AccessTokenFallbackVerification,
    EnvironmentRecord,
    EnvironmentStatus,
    ModelQuotaSnapshot,
    QuotaSnapshot,
    SupplierKind,
    utc_now,
)
from account_pool.gateway_repository import LeaseRepository
from account_pool.policies import CodexPolicy, PolicyRepository
from account_pool.ports import EnvironmentRepository


class AccessFallbackUpstream(Protocol):
    async def access_quota(self, record: EnvironmentRecord) -> tuple[QuotaSnapshot, tuple[ModelQuotaSnapshot, ...]]: ...
    async def verify_access_fallback(
        self, record: EnvironmentRecord, model: str
    ) -> AccessTokenFallbackVerification: ...
    async def access_fallback_status(self, record: EnvironmentRecord) -> AccessTokenFallbackStatus: ...


def fallback_eligible(record: EnvironmentRecord, model: str) -> bool:
    return (
        record.supplier is SupplierKind.OPENAI_CODEX
        and record.enabled
        and not record.manual_cooldown
        and not record.auth_file_disabled
        and not record.configuration_pending
        and record.desired_configuration_version == record.observed_configuration_version
        and record.status in (EnvironmentStatus.READY, EnvironmentStatus.COOLING_DOWN)
        and record.last_error in (None, "上游认证失效，请重新认证")
        and (record.cooldown_until is None or record.cooldown_until <= utc_now())
        and model in record.enabled_models
        and bool(record.auth_file_name and record.auth_index and record.credential_fingerprints)
        and not any(item.model == model and item.retry_at > utc_now() for item in record.model_cooldowns)
    )


def _quota_available(quota: QuotaSnapshot, now: datetime) -> bool:
    return (
        quota.source == "provider_api"
        and quota.refresh_status in ("complete", "partial")
        and quota.observed_at is not None
        and now - timedelta(minutes=5) <= quota.observed_at <= now
        and bool(quota.windows)
        and all(w.remaining_percent > 0 and (w.resets_at is None or w.resets_at > now) for w in quota.windows)
    )


def _model_quota_exhausted(quotas: tuple[ModelQuotaSnapshot, ...], model: str, now: datetime) -> bool:
    return any(
        window.remaining_percent <= 0 and (window.resets_at is None or window.resets_at > now)
        for item in quotas if item.model == model
        for window in item.quota.windows
    )


@dataclass(frozen=True, slots=True)
class AccessTokenFallbackService:
    environments: EnvironmentRepository
    policies: PolicyRepository
    upstream: AccessFallbackUpstream
    ownership: CredentialOwnership
    leases: LeaseRepository

    async def discover(self) -> tuple[UUID, ...]:
        """按卡片串行验证已启用模型；失败不放行，也不删除用户配置。"""
        slots: Final = asyncio.Semaphore(4)

        async def discover_model(environment_id: UUID, model: str) -> bool:
            current: Final = await self.environments.get(environment_id)
            if current is None:
                return False
            fallback: Final = current.access_token_fallback
            if (
                not fallback.enabled or fallback.failure_source != "refresh"
                or fallback.expires_at is None or fallback.expires_at <= utc_now()
                or fallback.usable_for(model, utc_now()) or not fallback_eligible(current, model)
            ):
                return False
            try:
                result: Final = await self.verify(environment_id, model)
                return not result.restored
            except (ValueError, httpx.HTTPError, TimeoutError):
                return True

        async def discover_card(record: EnvironmentRecord) -> UUID | None:
            async with slots:
                failed = False  # rebind-ok: aggregate independent model probe outcomes
                for model in record.enabled_models:
                    failed = await discover_model(record.id, model) or failed
                return record.id if failed else None

        results: Final = await asyncio.gather(*(discover_card(record) for record in await self.environments.list()))
        return tuple(result for result in results if result is not None)

    async def verify(self, environment_id: UUID, model: str) -> AccessTokenFallbackVerification:
        async with self.ownership.operation(environment_id):
            record: Final = await self.environments.get(environment_id)
            policy: Final = await self.policies.get(environment_id)
            if (
                record is None
                or not fallback_eligible(record, model)
                or not (policy.policy.codex or CodexPolicy()).allow_valid_access_after_refresh_failure
                or model in policy.policy.excluded_models
                or policy.runtime_status != "synced"
                or environment_id in await self.leases.cooling()
                or not await self.ownership.owns(environment_id, record.credential_fingerprints)
            ):
                return AccessTokenFallbackVerification(passed=False, restored=False, reason="independent_block")
            now: Final = utc_now()
            if (
                record.access_token_fallback.usable_for(model, now)
                and _quota_available(record.quota, now)
                and not _model_quota_exhausted(record.model_quotas, model, now)
            ):
                return AccessTokenFallbackVerification(
                    passed=True, restored=True, reason="temporary_access",
                    expires_at=record.access_token_fallback.expires_at,
                )
            quota, models = await self.upstream.access_quota(record)
            merged_models: Final = merge_model_quotas(record.model_quotas, models)
            observed_now: Final = utc_now()
            # 实时账户额度是验证前提；历史模型缓存只保留尚未到期的耗尽限制，不能替代完整推理证据。
            if not _quota_available(quota, observed_now) or _model_quota_exhausted(merged_models, model, observed_now):
                return AccessTokenFallbackVerification(passed=False, restored=False, reason="quota_not_available")
            verified: Final = await self.upstream.verify_access_fallback(record, model)
            if not verified.passed or not verified.restored:
                return verified.model_copy(update={"restored": False})
            state: Final = await self.upstream.access_fallback_status(record)
            current: Final = await self.environments.get(environment_id)
            if (
                current != record
                or await self.policies.get(environment_id) != policy
                or environment_id in await self.leases.cooling()
                or not state.usable_for(model, utc_now())
                or not await self.ownership.owns(environment_id, record.credential_fingerprints)
            ):
                return verified.model_copy(update={"restored": False, "reason": "state_changed"})
            updated: Final = record.model_copy(
                update={
                    "access_token_fallback": state,
                    "status": EnvironmentStatus.READY,
                    "last_error": None,
                    "automatic_cooldown": False,
                    "quota": quota,
                    "model_quotas": merged_models,
                    "version": record.version + 1,
                    "updated_at": utc_now(),
                }
            )
            saved: Final = await self.environments.save_if_version(updated, record.version)
            return verified.model_copy(
                update={
                    "restored": saved is not None,
                    "reason": "temporary_access" if saved is not None else "state_changed",
                }
            )


class VerifyAccessTokenFallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=100)
    confirmed: Literal[True]


def create_access_token_fallback_router(
    service: AccessTokenFallbackService, require_manager: Callable[[], None]
) -> APIRouter:
    router: Final = APIRouter(
        prefix="/api/environments/{environment_id}/access-token-fallback", dependencies=[Depends(require_manager)]
    )

    async def verify(
        environment_id: UUID, request: VerifyAccessTokenFallbackRequest
    ) -> AccessTokenFallbackVerification:
        try:
            return await service.verify(environment_id, request.model)
        except (ValueError, httpx.HTTPError, TimeoutError):
            raise HTTPException(409, "访问令牌验证未完成，未确认可用；未刷新令牌或兑换额度") from None

    router.add_api_route("/verify", verify, methods=["POST"], response_model=AccessTokenFallbackVerification)
    return router
