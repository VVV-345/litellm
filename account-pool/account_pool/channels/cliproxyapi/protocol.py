"""定义 CLIProxyAPI 管理协议的响应模型，不执行网络或生命周期操作。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from account_pool.domain import ModelCooldown, ProviderEndpointFailure
from account_pool.quota import QuotaObservation


@dataclass(frozen=True, slots=True)
class AuthorizationStart:
    authorization_url: str
    provider_state: str
    user_code: str | None
    expires_in_seconds: int | None


class _AuthorizationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status: str
    url: str | None = None
    state: str
    user_code: str | None = None
    expires_in: int | None = None


class _StatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status: str
    error: str | None = None


class _ModelResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str


class _ModelsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    models: tuple[_ModelResponse, ...] = ()
    data: tuple[_ModelResponse, ...] = ()


class _CodexIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    chatgpt_account_id: str | None = None
    plan_type: str | None = None
    chatgpt_subscription_active_start: datetime | None = None
    chatgpt_subscription_active_until: datetime | None = None


class _ModelState(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    unavailable: bool = False
    next_retry_after: datetime | None = None
    reason: str = "upstream_error"


class _AuthFile(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    auth_index: str | None = None
    email: str | None = None
    account_id: str | None = None
    provider: str | None = None
    type: str | None = None
    disabled: bool = False
    unavailable: bool = False
    status: str | None = None
    status_message: str | None = None
    next_retry_after: datetime | None = None
    quota: QuotaObservation = QuotaObservation()
    model_quotas: Mapping[str, QuotaObservation] = Field(default_factory=dict)
    model_states: Mapping[str, _ModelState] = Field(default_factory=dict)
    plan_type: str | None = None
    auth_file_plan_type: str | None = None
    project_id: str | None = None
    id_token: _CodexIdentity | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)
    attributes: Mapping[str, object] = Field(default_factory=dict)


class _AuthFilesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    files: tuple[_AuthFile, ...] = ()


def model_cooldowns_from_auth(auth_file: _AuthFile) -> tuple[ModelCooldown, ...]:
    return tuple(
        ModelCooldown(model=model, retry_at=state.next_retry_after, reason=state.reason)
        for model, state in sorted(auth_file.model_states.items())
        if state.unavailable and state.next_retry_after is not None
    )


class _APICallResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status_code: int
    header: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    body: str


class _UpstreamError(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    code: str | None = None
    type: str | None = None


class _UpstreamErrorPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    error: _UpstreamError | str | None = None
    detail: _UpstreamError | str | None = None
    code: str | None = None
    type: str | None = None


@dataclass(frozen=True, slots=True)
class _ProviderAPICallResult:
    body: str | None = None
    failure: ProviderEndpointFailure | None = None
