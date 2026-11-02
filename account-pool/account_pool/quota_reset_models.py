"""定义单卡额度兑换记录及安全的公开结果，不保存供应商令牌。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from account_pool.domain import ModelQuotaSnapshot, QuotaSnapshot, utc_now

ResetCode = Literal["reset", "nothing_to_reset", "no_credit", "already_redeemed"]


class ResetObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    available: int = Field(ge=0)
    credit_id: str | None = None
    eligible: bool
    quota: QuotaSnapshot = Field(default_factory=QuotaSnapshot)
    model_quotas: tuple[ModelQuotaSnapshot, ...] = ()


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


class ResetView(BaseModel):
    request_id: UUID
    environment_id: UUID
    status: Literal["prepared", "uncertain", "reset", "nothing_to_reset", "no_credit", "already_redeemed"]
    before: ResetObservation
    after: ResetObservation | None = None
    windows_reset: int
    verification: Literal["not_run", "failed", "passed", "restored", "blocked"]
    message: str | None = None


class ResetStatus(BaseModel):
    observation: ResetObservation | None
    operation: ResetView | None = None
    last_verification: ResetVerificationRecord | None = None
    last_api_call: VerifiedAPICall | None = None


class VerifyResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=100)
    recover: bool = False
    confirmed: Literal[True]


class ResetVerification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    passed: bool
    restored: bool
    reason: str


class ResetVerificationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model: str
    observed_at: datetime = Field(default_factory=utc_now)
    result: ResetVerification
    source: Literal["manual", "natural"] = "manual"


class VerifiedAPICall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model: str
    request_id: UUID
    endpoint: Literal["/v1/responses", "/v1/chat/completions"]
    verification_at: datetime
    observed_at: datetime


ResetStatus.model_rebuild()
