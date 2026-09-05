"""本模块封装 codebuff CLI 授权协议与 FreeBuff2API 容器管理，返回值规范化为号池领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict

from account_pool.compose_runtime import ComposeRuntime
from account_pool.config import Settings
from account_pool.domain import EnvironmentRecord
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose

_UPSTREAM_BASE_URL: Final = "https://www.codebuff.com"
_UPSTREAM_USER_AGENT: Final = "ai-sdk/openai-compatible/1.0.25/codebuff"
_CREDENTIAL_FILE_NAME: Final = "freebuff_credentials.json"


def _freebuff_compose(record: EnvironmentRecord, settings: Settings, gateway_key: str) -> str:
    from account_pool.compose_renderer import render_freebuff_compose

    return render_freebuff_compose(record, settings, gateway_key)


def _remaining_seconds(expires_at: str | None) -> int | None:
    """把上游 ISO 过期时间换算成剩余秒数；解析失败或已过期返回 None。"""
    if not expires_at:
        return None
    try:
        deadline: Final = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    remaining: Final = (deadline - datetime.now(timezone.utc)).total_seconds()
    return int(remaining) if remaining > 0 else None


@dataclass(frozen=True, slots=True)
class AuthorizationStart:
    authorization_url: str
    provider_state: str
    user_code: str | None
    expires_in_seconds: int | None


@dataclass(frozen=True, slots=True)
class CodeAuthorizationOperation:
    """codebuff 授权操作的完整凭据，state 与 hash 不落日志。"""

    authorization_url: str
    fingerprint_id: str
    fingerprint_hash: str
    expires_at: str
    expires_in_seconds: int | None = None


class _CodeStartResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    loginUrl: str
    fingerprintHash: str
    expiresAt: str | None = None


class _CodeStatusUser(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    authToken: str | None = None


class _CodeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    user: _CodeStatusUser | None = None


class _HealthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status: str
    accounts: int = 0
    alive_accounts: int = 0
    unknown_accounts: int = 0


class _ModelResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str


class _ModelsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    data: tuple[_ModelResponse, ...] = ()


class HttpCodebuffClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client: Final = client or httpx.AsyncClient(timeout=15.0, trust_env=False)
        self._owns_client: Final = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def start_authorization(self, fingerprint_id: str) -> CodeAuthorizationOperation:
        response: Final = await self._client.post(
            f"{_UPSTREAM_BASE_URL}/api/auth/cli/code",
            headers={"User-Agent": _UPSTREAM_USER_AGENT},
            json={"fingerprintId": fingerprint_id},
        )
        response.raise_for_status()
        payload: Final = _CodeStartResponse.model_validate(response.json())
        return CodeAuthorizationOperation(
            authorization_url=payload.loginUrl,
            fingerprint_id=fingerprint_id,
            fingerprint_hash=payload.fingerprintHash,
            expires_at=payload.expiresAt or "",
            expires_in_seconds=_remaining_seconds(payload.expiresAt),
        )

    async def authorization_token(
        self,
        operation: CodeAuthorizationOperation,
    ) -> str | None:
        """返回 authToken；用户尚未完成授权时返回 None。"""
        response: Final = await self._client.get(
            f"{_UPSTREAM_BASE_URL}/api/auth/cli/status",
            headers={"User-Agent": _UPSTREAM_USER_AGENT},
            params={
                "fingerprintId": operation.fingerprint_id,
                "fingerprintHash": operation.fingerprint_hash,
                "expiresAt": operation.expires_at,
            },
        )
        if response.status_code == httpx.codes.UNAUTHORIZED:
            return None
        response.raise_for_status()
        payload: Final = _CodeStatusResponse.model_validate(response.json())
        token: Final = payload.user.authToken if payload.user is not None else None
        return token if token is not None and token.strip() else None


class FreeBuff2APIRuntime:
    def __init__(self, runtime: ComposeRuntime, secrets: EnvironmentSecretDeriver) -> None:
        self._runtime: Final = runtime
        self._secrets: Final = secrets

    async def provision(self, record: EnvironmentRecord) -> None:
        gateway_key: Final = self._secrets.derive(record.id, SecretPurpose.GATEWAY)
        await self._runtime.provision_freebuff(
            record,
            compose=_freebuff_compose(record, self._runtime.settings, gateway_key),
        )

    async def ensure_control_plane_connections(self, environment_id: str) -> None:
        await self._runtime.ensure_control_plane_connections(environment_id)

    async def set_running(self, record: EnvironmentRecord, running: bool) -> None:
        await self._runtime.set_running(record, running)

    async def remove(self, record: EnvironmentRecord) -> None:
        await self._runtime.remove(record)

    async def remove_compose(self, record: EnvironmentRecord) -> None:
        await self._runtime.remove_compose(record)

    async def remove_directory(self, environment_id: str) -> None:
        await self._runtime.remove_directory(environment_id)

    async def write_credential(self, record: EnvironmentRecord, auth_token: str) -> None:
        """把 authToken 写进数据卷的凭据文件并重启容器。内容经 stdin 进入一次性容器，不进命令行。"""
        from account_pool.compose_renderer import render_freebuff_credentials

        payload: Final = render_freebuff_credentials(auth_token)
        script: Final = f"cat > /data/{_CREDENTIAL_FILE_NAME} && chmod 600 /data/{_CREDENTIAL_FILE_NAME}"
        await self._runtime.write_volume_files(
            record.id,
            image=self._runtime.settings.freebuff2api_image,
            script=script,
            stdin_content=payload,
            user="1000:1000",
        )
        await self._runtime.restart(record.id)

    async def health_check(self, record: EnvironmentRecord, client: httpx.AsyncClient) -> bool:
        try:
            health: Final = await client.get(
                f"{self._base_url(record.id)}/healthz",
                headers=self._headers(record),
            )
            if health.status_code != httpx.codes.OK:
                return False
            # critical 只代表账号池为空或全部失效；刚写入凭据、还没有真实流量的账号
            # 会以 unknown 计数，属于授权完成后的正常状态，不能据此判死。
            parsed: Final = _HealthResponse.model_validate(health.json())
            if parsed.accounts == 0:
                return False
            if parsed.accounts != parsed.alive_accounts + parsed.unknown_accounts:
                return False
            models: Final = await client.get(
                f"{self._base_url(record.id)}/v1/models",
                headers=self._headers(record),
            )
            if models.status_code != httpx.codes.OK:
                return False
            return bool(_ModelsResponse.model_validate(models.json()).data)
        except (httpx.HTTPError, ValueError):
            return False

    def _headers(self, record: EnvironmentRecord) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secrets.derive(record.id, SecretPurpose.GATEWAY)}"}

    def _base_url(self, environment_id: object) -> str:
        return f"http://freebuff-{environment_id}:8787"
