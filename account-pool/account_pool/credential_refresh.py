"""编排单卡凭据刷新，复用所有权锁与 CPA 协调器，不将保存成功当作推理成功。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, Protocol
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from account_pool.credential_ownership import CredentialIdentity, CredentialOwnership
from account_pool.domain import CredentialRefreshStatus, EnvironmentRecord, EnvironmentStatus, SupplierKind, utc_now
from account_pool.ports import EnvironmentRepository


class CredentialRefreshResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    saved: bool
    reason: str
    status: CredentialRefreshStatus = Field(default_factory=CredentialRefreshStatus)


class RefreshCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    confirmed: Literal[True]


class CredentialRefreshUpstream(Protocol):
    async def refresh_credentials(self, record: EnvironmentRecord) -> CredentialRefreshResult: ...

    async def read_credential_identity(self, record: EnvironmentRecord) -> CredentialIdentity: ...


@dataclass(frozen=True, slots=True)
class CredentialRefreshService:
    environments: EnvironmentRepository
    ownership: CredentialOwnership
    upstream: CredentialRefreshUpstream

    async def refresh(self, environment_id: UUID, version: int) -> CredentialRefreshResult:
        async with self.ownership.operation(environment_id):
            record: Final = await self.environments.get(environment_id)
            if (
                record is None
                or record.version != version
                or record.status is EnvironmentStatus.DELETING
                or record.supplier is not SupplierKind.OPENAI_CODEX
                or not record.enabled
                or record.auth_file_disabled
                or record.configuration_pending
                or record.oauth_automation_operation is not None
                or not record.auth_file_name
                or not record.auth_index
                or not await self.ownership.owns(environment_id, record.credential_fingerprints)
            ):
                return CredentialRefreshResult(saved=False, reason="state_changed")
            # 先推进版本，串行排队的重复请求不能在本次轮换后再次使用新 refresh token。
            claimed: Final = await self.environments.save_if_version(
                record.model_copy(update={"version": record.version + 1, "updated_at": utc_now()}),
                record.version,
            )
            if claimed is None:
                return CredentialRefreshResult(saved=False, reason="state_changed")
            result: Final = await self.upstream.refresh_credentials(claimed)
            identity: Final = await self.upstream.read_credential_identity(claimed) if result.saved else None
            if identity is not None:
                await self.ownership.claim(environment_id, identity.fingerprints)
            saved: Final = await self.environments.save_if_version(
                claimed.model_copy(
                    update={
                        "credential_refresh": result.status,
                        "updated_at": utc_now(),
                        **(
                            {
                                "credential_fingerprints": identity.fingerprints,
                                "credential_email": identity.email,
                                "credential_account_id": identity.account_id,
                            }
                            if identity is not None
                            else {}
                        ),
                    }
                ),
                claimed.version,
            )
            if saved is not None and identity is not None:
                await self.ownership.retain(environment_id, identity.fingerprints)
            return (
                result if saved is not None else result.model_copy(update={"saved": False, "reason": "state_changed"})
            )


def create_credential_refresh_router(
    service: CredentialRefreshService, require_manager: Callable[[], None]
) -> APIRouter:
    router: Final = APIRouter(
        prefix="/api/environments/{environment_id}/credentials", dependencies=[Depends(require_manager)]
    )

    async def refresh(environment_id: UUID, request: RefreshCredentialRequest) -> CredentialRefreshResult:
        try:
            return await service.refresh(environment_id, request.version)
        except (ValueError, httpx.HTTPError, TimeoutError):
            raise HTTPException(409, "凭据刷新结果未确认；请检查最新状态，不要连续重试") from None

    router.add_api_route("/refresh", refresh, methods=["POST"], response_model=CredentialRefreshResult)
    return router
