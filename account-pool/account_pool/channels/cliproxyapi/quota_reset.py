"""单卡 Codex 重置传输，只读现有令牌，禁止 TOKEN 模板及隐式 OAuth 刷新。"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field

from account_pool.channels.cliproxyapi.protocol import _APICallResponse, _AuthFilesResponse
from account_pool.channels.cliproxyapi.transport import CLIProxyTransport
from account_pool.credential_ownership import credential_identity
from account_pool.domain import EnvironmentRecord, utc_now
from account_pool.providers.usage.codex import parse_codex_usage_quota
from account_pool.quota_reset_models import ResetObservation, ResetOperation, ResetReply
from account_pool.shared.secrets import EnvironmentSecretDeriver

_ROOT: Final = "https://chatgpt.com/backend-api/wham/"


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

    async def _call(
        self,
        record: EnvironmentRecord,
        method: str,
        endpoint: str,
        operation: ResetOperation | None = None,
    ) -> str:
        transport: Final = CLIProxyTransport(self.http, self.secrets)
        files_response: Final = await transport.request(record, "GET", "/v0/management/auth-files")
        files: Final = _AuthFilesResponse.model_validate(files_response.json())
        selected: Final = next((item for item in files.files if item.name == record.auth_file_name), None)
        if selected is None or selected.auth_index != record.auth_index:
            raise ValueError("认证文件绑定已变化")
        response: Final = await transport.request(
            record,
            "GET",
            "/v0/management/auth-files/download",
            params={"name": selected.name},
        )
        identity: Final = credential_identity(response.content, record.supplier.value, self.secrets)
        if set(identity.fingerprints) != set(record.credential_fingerprints):
            raise ValueError("凭据已变化，请先核对卡片绑定")
        credential: Final = _Credential.model_validate_json(response.content)
        if "$TOKEN$" in credential.access_token:
            raise ValueError("不能使用令牌模板")
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
        payload: Final = _APICallResponse.model_validate(result.json())
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
        credit: Final = next(
            (
                item
                for item in credits.credits
                if (
                    item.status == "available"
                    and item.reset_type == "codex_rate_limits"
                    and (item.expires_at is None or item.expires_at > now)
                )
            ),
            None,
        )
        return ResetObservation(
            available=credits.available_count,
            credit_id=None if credit is None else credit.id,
            eligible=credit is not None and any(window.remaining_percent < 100 for window in quota.quota.windows),
            quota=quota.quota,
        )

    async def consume(self, record: EnvironmentRecord, operation: ResetOperation) -> ResetReply:
        body: Final = await self._call(record, "POST", "rate-limit-reset-credits/consume", operation)
        return ResetReply.model_validate_json(body)
