"""本模块定义持久化批量任务协议，任务仅接受账号标识及明确的版本快照。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator

from account_pool.domain import AuthorizationFlow
from account_pool.policies import AccountPolicy

BatchAction = Literal["refresh", "authorize", "enable", "disable", "cooldown", "release", "policy", "delete"]


class BatchTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    account_id: UUID
    version: int = Field(ge=0)
    policy_version: int = Field(default=0, ge=0)


class BatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    job_id: UUID
    action: BatchAction
    targets: tuple[BatchTarget, ...] = Field(min_length=1, max_length=100)
    policy: AccountPolicy | None = None

    @model_validator(mode="after")
    def valid_action(self) -> BatchRequest:
        if len({target.account_id for target in self.targets}) != len(self.targets):
            raise ValueError("Duplicate accounts are not allowed")
        if (self.action == "policy") != (self.policy is not None):
            raise ValueError("Policy is required only for policy jobs")
        return self


class BatchAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True)
    flow: AuthorizationFlow
    authorization_url: HttpUrl
    ssh_command: str | None
    user_code: str | None
    expires_at: AwareDatetime


class BatchItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    account_id: UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int = 0
    message: str | None = None
    authorization: BatchAuthorization | None = None
    finished_at: AwareDatetime | None = None


class BatchJob(BaseModel):
    model_config = ConfigDict(frozen=True)
    job_id: UUID
    action: BatchAction
    created_at: AwareDatetime
    items: tuple[BatchItem, ...]


class BatchClaim(BaseModel):
    model_config = ConfigDict(frozen=True)
    request: BatchRequest
    target: BatchTarget
    token: UUID
    attempts: int = Field(default=1, ge=1)
