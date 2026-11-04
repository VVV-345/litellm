"""单卡 Codex 重置传输，只读现有令牌，禁止 TOKEN 模板及隐式 OAuth 刷新。"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from account_pool.channels.cliproxyapi.protocol import APICallResponse, AuthFile, AuthFilesResponse
from account_pool.channels.cliproxyapi.transport import CLIProxyTransport
from account_pool.credential_ownership import CredentialIdentity, credential_identity
from account_pool.credential_refresh import CredentialRefreshResult
from account_pool.domain import (
    AccessTokenFallbackStatus,
    AccessTokenFallbackVerification,
    EnvironmentRecord,
    ModelQuotaSnapshot,
    QuotaSnapshot,
    utc_now,
)
from account_pool.providers.usage.codex import parse_codex_usage_quota
from account_pool.quota_reset_models import ResetObservation, ResetOperation, ResetReply, ResetVerification
from account_pool.shared.secrets import EnvironmentSecretDeriver

_ROOT: Final = "https://chatgpt.com/backend-api/wham/"


class _VerificationCapability(BaseModel):
    model_config = ConfigDict(strict=True)
    supported: Literal[True]
    version: int = Field(ge=1, le=1)
    token_refresh: Literal[False]


class _Credential(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    access_token: str = Field(min_length=1, repr=False)
    account_id: str | None = None


class _Credit(BaseModel):
    id: str
    status: str
    reset_type: str
    expires_at: datetime | None = None


class _Credits(BaseModel):
    available_count: int = Field(ge=0)
    credits: tuple[_Credit, ...]


@dataclass(frozen=True, slots=True)
class CodexResetClient:
    http: httpx.AsyncClient
    secrets: EnvironmentSecretDeriver

    async def verify(self, record: EnvironmentRecord, model: str, recover: bool) -> ResetVerification:
        transport: Final = CLIProxyTransport(self.http, self.secrets)
        try:
            capability: Final = await transport.request(record, "GET", "/v0/management/codex/quota-verification")
            _VerificationCapability.model_validate_json(capability.content)
        except httpx.HTTPStatusError as error:
            reason = (
                "runtime_upgrade_required" if error.response.status_code in (404, 405)
                else "management_auth_failed" if error.response.status_code in (401, 403)
                else "runtime_unavailable"
            )
            return ResetVerification(passed=False, restored=False, reason=reason)
        except httpx.HTTPError:
            return ResetVerification(passed=False, restored=False, reason="runtime_unreachable")
        except ValueError:
            return ResetVerification(passed=False, restored=False, reason="runtime_upgrade_required")
        try:
            _, credential = await self._credential(record, transport)
            response: Final = await transport.request(
                record,
                "POST",
                "/v0/management/codex/quota-verification",
                json={
                    "name": record.auth_file_name,
                    "auth_index": record.auth_index,
                    "model": model,
                    "recover": recover,
                    "token_fingerprint": hashlib.sha256(credential.access_token.encode()).hexdigest(),
                },
            )
            return ResetVerification.model_validate_json(response.content)
        except (httpx.HTTPError, ValueError):
            return ResetVerification(passed=False, restored=False, reason="verification_uncertain")

    async def refresh_credentials(self, record: EnvironmentRecord) -> CredentialRefreshResult:
        transport: Final = CLIProxyTransport(self.http, self.secrets)
        _, credential = await self._credential(record, transport)
        response: Final = await transport.request(record, "POST", "/v0/management/codex/credential-refresh", json={
            "name":record.auth_file_name,"auth_index":record.auth_index,
            "token_fingerprint":hashlib.sha256(credential.access_token.encode()).hexdigest(),
        })
        return CredentialRefreshResult.model_validate_json(response.content)

    async def access_quota(self, record: EnvironmentRecord) -> tuple[QuotaSnapshot, tuple[ModelQuotaSnapshot, ...]]:
        body: Final = await self._call(record, "GET", "usage")
        parsed: Final = parse_codex_usage_quota(body, utc_now())
        if parsed is None:
            raise ValueError("供应商未返回可核对额度")
        return parsed.quota.model_copy(update={"source": "provider_api", "refresh_status": "complete", "refresh_attempted_at": parsed.quota.observed_at}), parsed.model_quotas

    async def access_fallback_status(self, record: EnvironmentRecord) -> AccessTokenFallbackStatus:
        selected, _ = await self._credential(record, CLIProxyTransport(self.http, self.secrets))
        return selected.access_token_fallback

    async def verify_access_fallback(self, record: EnvironmentRecord, model: str) -> AccessTokenFallbackVerification:
        transport: Final = CLIProxyTransport(self.http, self.secrets)
        capability: Final = await transport.request(record, "GET", "/v0/management/codex/access-token-fallback")
        _VerificationCapability.model_validate_json(capability.content)
        _, credential = await self._credential(record, transport)
        response: Final = await transport.request(record, "POST", "/v0/management/codex/access-token-fallback", json={
            "name": record.auth_file_name, "auth_index": record.auth_index, "model": model,
            "token_fingerprint": hashlib.sha256(credential.access_token.encode()).hexdigest(),
        })
        return AccessTokenFallbackVerification.model_validate_json(response.content)

    async def _credential(
        self, record: EnvironmentRecord, transport: CLIProxyTransport
    ) -> tuple[AuthFile, _Credential]:
        selected, content = await self._download_credential(record, transport)
        identity: Final = credential_identity(content, record.supplier.value, self.secrets)
        if set(identity.fingerprints) != set(record.credential_fingerprints):
            raise ValueError("凭据已变化，请先核对卡片绑定")
        credential: Final = _Credential.model_validate_json(content)
        if "$TOKEN$" in credential.access_token:
            raise ValueError("不能使用令牌模板")
        return selected, credential

    async def read_credential_identity(self, record: EnvironmentRecord) -> CredentialIdentity:
        _, content = await self._download_credential(record, CLIProxyTransport(self.http, self.secrets))
        return credential_identity(content, record.supplier.value, self.secrets)

    async def _download_credential(
        self, record: EnvironmentRecord, transport: CLIProxyTransport
    ) -> tuple[AuthFile, bytes]:
        files_response: Final = await transport.request(record, "GET", "/v0/management/auth-files")
        files: Final = AuthFilesResponse.model_validate_json(files_response.content)
        selected: Final = next((item for item in files.files if item.name == record.auth_file_name), None)
        if selected is None or selected.auth_index != record.auth_index:
            raise ValueError("认证文件绑定已变化")
        response: Final = await transport.request(
            record,
            "GET",
            "/v0/management/auth-files/download",
            params={"name": selected.name},
        )
        return selected, response.content

    async def _call(
        self, record: EnvironmentRecord, method: str, endpoint: str, operation: ResetOperation | None = None
    ) -> str:
        transport: Final = CLIProxyTransport(self.http, self.secrets)
        selected, credential = await self._credential(record, transport)
        # 不使用 $TOKEN$，避免 CPA 在 401 后轮换令牌；不覆盖 auth_index 对应的出站代理。
        result: Final = await transport.request(
            record,
            "POST",
            "/v0/management/api-call",
            json={
                "auth_index": selected.auth_index,
                "method": method,
                "url": _ROOT + endpoint,
                "header": {
                    "Authorization": "Bearer " + credential.access_token,
                    "Content-Type": "application/json",
                    **({"Chatgpt-Account-Id": credential.account_id} if credential.account_id else {}),
                },
                "data": ""
                if operation is None
                else json.dumps(
                    {
                        "redeem_request_id": str(operation.request_id),
                        "credit_id": operation.credit_id,
                    }
                ),
            },
        )
        payload: Final = APICallResponse.model_validate_json(result.content)
        if payload.status_code != 200:
            raise ValueError("供应商认证失败" if payload.status_code == 401 else "供应商请求未成功")
        return payload.body

    async def observe(self, record: EnvironmentRecord) -> ResetObservation:
        usage: Final = await self._call(record, "GET", "usage")
        credits_body: Final = await self._call(record, "GET", "rate-limit-reset-credits")
        credits: Final = _Credits.model_validate_json(credits_body)
        now: Final = utc_now()
        quota: Final = parse_codex_usage_quota(usage, now, reset_credits_body=credits_body)
        if quota is None or not quota.quota.windows:
            raise ValueError("上游未返回可核对的额度窗口")
        credit: Final = min(
            (
                item
                for item in credits.credits
                if (
                    item.status == "available"
                    and item.reset_type == "codex_rate_limits"
                    and (item.expires_at is None or item.expires_at > now)
                )
            ),
            key=lambda item: float("inf") if item.expires_at is None else item.expires_at.timestamp(),
            default=None,
        )
        return ResetObservation(
            available=credits.available_count,
            credit_id=None if credit is None else credit.id,
            eligible=credit is not None and any(window.remaining_percent < 100 for window in quota.quota.windows),
            quota=quota.quota.model_copy(
                update={"source": "provider_api", "refresh_status": "complete", "refresh_attempted_at": now}
            ),
            model_quotas=quota.model_quotas,
        )

    async def consume(self, record: EnvironmentRecord, operation: ResetOperation) -> ResetReply:
        body: Final = await self._call(record, "POST", "rate-limit-reset-credits/consume", operation)
        return ResetReply.model_validate_json(body)
