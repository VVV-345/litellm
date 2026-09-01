"""本模块封装 CLIProxyAPI 管理协议，并将返回值规范化为号池领域模型。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from account_pool.domain import (
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    ModelQuotaSnapshot,
    OAuthCallback,
    QuotaSnapshot,
    QuotaWindow,
    utc_now,
)
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose


class _AuthorizationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status: str
    url: str
    state: str


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


class _QuotaObservation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    observed_at: datetime | None = None
    signals: Mapping[str, str] = Field(default_factory=dict)


class _AuthFile(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    auth_index: str | None = None
    provider: str | None = None
    type: str | None = None
    disabled: bool = False
    unavailable: bool = False
    next_retry_after: datetime | None = None
    quota: _QuotaObservation = _QuotaObservation()
    model_quotas: Mapping[str, _QuotaObservation] = Field(default_factory=dict)


class _AuthFilesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    files: tuple[_AuthFile, ...] = ()


_AUTH_FILES_ADAPTER: Final = TypeAdapter(_AuthFilesResponse)


class HttpCLIProxyClient:
    def __init__(self, secrets: EnvironmentSecretDeriver, client: httpx.AsyncClient | None = None) -> None:
        self._secrets: Final = secrets
        self._client: Final = client or httpx.AsyncClient(
            timeout=15.0,
            transport=httpx.AsyncHTTPTransport(retries=20),
            trust_env=False,
        )
        self._owns_client: Final = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def start_openai_authorization(self, record: EnvironmentRecord) -> tuple[str, str]:
        response: Final = await self._request(record, "GET", "/v0/management/codex-auth-url")
        payload: Final = _AuthorizationResponse.model_validate(response.json())
        return payload.url, payload.state

    async def authorization_status(self, record: EnvironmentRecord, state: str) -> str:
        response: Final = await self._request(
            record,
            "GET",
            "/v0/management/get-auth-status",
            params={"state": state},
        )
        payload: Final = _StatusResponse.model_validate(response.json())
        if payload.status == "error":
            return f"error:{payload.error or 'authentication failed'}"
        return payload.status

    async def submit_callback(self, record: EnvironmentRecord, callback: OAuthCallback) -> None:
        payload: Final = {
            "provider": "codex",
            "state": callback.state,
            "code": callback.code or "",
            "error": callback.error or callback.error_description or "",
        }
        await self._request(record, "POST", "/v0/management/oauth-callback", json=payload)

    async def data_plane_health_check(self, record: EnvironmentRecord) -> bool:
        try:
            response: Final = await self._request(
                record,
                "GET",
                "/v1/models",
                management=False,
                gateway=True,
            )
            models: Final = _ModelsResponse.model_validate(response.json())
            if not models.models and not models.data:
                return False
        except (httpx.HTTPError, ValueError):
            return False
        return True

    async def read_account(self, record: EnvironmentRecord) -> EnvironmentRecord:
        auth_response: Final = await self._request(record, "GET", "/v0/management/auth-files")
        auth_files: Final = _AUTH_FILES_ADAPTER.validate_python(auth_response.json())
        auth_file: Final = next(
            (item for item in auth_files.files if (item.provider or item.type or "").lower() == "codex"),
            None,
        )
        if auth_file is None:
            raise RuntimeError("CLIProxyAPI did not persist an OpenAI credential")
        model_response: Final = await self._request(
            record,
            "GET",
            "/v0/management/auth-files/models",
            params={"name": auth_file.name},
        )
        models: Final = _ModelsResponse.model_validate(model_response.json())
        available_models: Final = tuple(dict.fromkeys(model.id for model in (*models.models, *models.data)))
        enabled_models: Final = (
            available_models
            if not record.available_models
            else tuple(model for model in record.enabled_models if model in available_models)
        )
        quota: Final = parse_quota(auth_file.quota)
        model_quotas: Final = tuple(
            ModelQuotaSnapshot(model=model, quota=parse_quota(observation))
            for model, observation in sorted(auth_file.model_quotas.items())
        )
        now: Final = utc_now()
        cooldown_until: Final = _effective_cooldown_until(record, auth_file.next_retry_after, now)
        automatically_cooling: Final = (
            auth_file.disabled or auth_file.unavailable or (cooldown_until is not None and cooldown_until > now)
        )
        status: Final = (
            EnvironmentStatus.DISABLED
            if not record.enabled
            else EnvironmentStatus.COOLING_DOWN
            if record.manual_cooldown or automatically_cooling
            else EnvironmentStatus.READY
        )
        return record.model_copy(
            update={
                "auth_file_name": auth_file.name,
                "auth_index": auth_file.auth_index,
                "available_models": available_models,
                "enabled_models": enabled_models,
                "quota": quota,
                "model_quotas": model_quotas,
                "cooldown_until": cooldown_until,
                "status": status,
                "last_error": None,
            }
        )

    async def set_credential_enabled(self, record: EnvironmentRecord, enabled: bool) -> None:
        if record.auth_file_name is None:
            return
        await self._request(
            record,
            "PATCH",
            "/v0/management/auth-files/status",
            json={
                "name": record.auth_file_name,
                "auth_index": record.auth_index or "",
                "disabled": not enabled,
            },
        )

    async def set_proxy_url(self, record: EnvironmentRecord, proxy_url: str) -> None:
        await self._request(record, "PUT", "/v0/management/proxy-url", json={"value": proxy_url})

    async def set_enabled_models(self, record: EnvironmentRecord, enabled_models: Sequence[str]) -> None:
        excluded: Final = tuple(model for model in record.available_models if model not in frozenset(enabled_models))
        await self._request(
            record,
            "PUT",
            "/v0/management/oauth-excluded-models",
            json={"codex": excluded},
        )

    async def set_concurrency_limit(self, record: EnvironmentRecord, concurrency_limit: int) -> None:
        await self._request(
            record,
            "PUT",
            "/v0/management/concurrency-limit",
            json={"value": concurrency_limit},
        )

    async def apply_configuration(self, record: EnvironmentRecord, configuration: EnvironmentConfiguration) -> None:
        """按固定顺序应用完整快照，重复执行每一步都保持幂等。"""
        await self.set_proxy_url(record, configuration.proxy_url)
        await self.set_enabled_models(record, configuration.enabled_models)
        await self.set_credential_enabled(record, configuration.enabled and not configuration.manual_cooldown)
        await self.set_concurrency_limit(record, configuration.concurrency_limit)

    async def _request(
        self,
        record: EnvironmentRecord,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, object] | None = None,
        management: bool = True,
        gateway: bool = False,
    ) -> httpx.Response:
        host: Final = f"cliproxy-{record.id.hex}"
        headers: Final = (
            {"X-Management-Key": self._secrets.derive(record.id, SecretPurpose.MANAGEMENT)}
            if management
            else {"Authorization": f"Bearer {self._secrets.derive(record.id, SecretPurpose.GATEWAY)}"}
            if gateway
            else None
        )
        response: Final = await self._client.request(
            method,
            f"http://{host}:8317{path}",
            headers=headers,
            params=params,
            json=json,
        )
        response.raise_for_status()
        return response


def parse_quota(observation: _QuotaObservation) -> QuotaSnapshot:
    signals: Final = {key.lower(): value for key, value in observation.signals.items()}
    plan_type: Final = signals.get("x-codex-plan-type")
    namespaces: Final = tuple(
        dict.fromkeys(
            key.removesuffix("-used-percent")
            for key in signals
            if key.startswith("x-codex-") and key.endswith("-used-percent")
        )
    )
    windows: Final = tuple(
        window
        for namespace in namespaces
        if (window := _quota_window(namespace, signals, observation.observed_at)) is not None
    )
    return QuotaSnapshot(observed_at=observation.observed_at, plan_type=plan_type, windows=windows)


def _effective_cooldown_until(
    record: EnvironmentRecord,
    upstream_cooldown_until: datetime | None,
    now: datetime,
) -> datetime | None:
    if upstream_cooldown_until is not None and upstream_cooldown_until > now:
        return upstream_cooldown_until
    if record.cooldown_until is not None and (record.manual_cooldown or not record.enabled):
        return record.cooldown_until
    return None


def _quota_window(
    namespace: str,
    signals: Mapping[str, str],
    observed_at: datetime | None,
) -> QuotaWindow | None:
    used_raw: Final = signals.get(f"{namespace}-used-percent")
    minutes_raw: Final = signals.get(f"{namespace}-window-minutes")
    if used_raw is None or minutes_raw is None:
        return None
    try:
        used: Final = float(used_raw)
        minutes: Final = int(minutes_raw)
    except ValueError:
        return None
    if used < 0 or used > 100 or minutes <= 0:
        return None
    resets_at: Final = _reset_at(namespace, signals, observed_at)
    name: Final = namespace.removeprefix("x-codex-").replace("-", " ").title()
    return QuotaWindow(
        name=name,
        used_percent=used,
        remaining_percent=100 - used,
        window_minutes=minutes,
        resets_at=resets_at,
    )


def _reset_at(
    namespace: str,
    signals: Mapping[str, str],
    observed_at: datetime | None,
) -> datetime | None:
    reset_epoch: Final = signals.get(f"{namespace}-reset-at")
    if reset_epoch is not None:
        try:
            return datetime.fromtimestamp(int(reset_epoch), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    reset_after: Final = signals.get(f"{namespace}-reset-after-seconds")
    if reset_after is None or observed_at is None:
        return None
    try:
        return observed_at + timedelta(seconds=int(reset_after))
    except ValueError:
        return None
