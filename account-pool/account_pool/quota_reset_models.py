"""定义单卡额度兑换记录及安全的公开结果，不保存供应商令牌。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from account_pool.domain import QuotaSnapshot, utc_now

ResetCode = Literal["reset", "nothing_to_reset", "no_credit", "already_redeemed"]


class ResetObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    available: int = Field(ge=0)
    credit_id: str | None = None
    eligible: bool
    quota: QuotaSnapshot = Field(default_factory=QuotaSnapshot)


class ResetReply(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    code: ResetCode
    windows_reset: int = Field(default=0, ge=0)


class ResetOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    request_id: UUID
    environment_id: UUID
    created_at: datetime = Field(default_factory=utc_now)
    status: Literal["prepared", "uncertain", "reset", "nothing_to_reset", "no_credit", "already_redeemed"] = "prepared"
    credit_id: str
    credential_fingerprints: tuple[str, ...] = Field(repr=False)
    before: ResetObservation
    after: ResetObservation | None = None
    windows_reset: int = 0
    verification: Literal["not_run", "failed", "passed", "restored", "blocked"] = "not_run"
    message: str | None = None


class PrepareResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previous_request_id: UUID | None = None


class ConsumeResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    confirmed: Literal[True]
