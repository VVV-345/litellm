"""本模块封装 CLIProxyAPI 管理协议，并将返回值规范化为号池领域模型。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.domain import (
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    ModelQuotaSnapshot,
    OAuthCallback,
    QuotaSnapshot,
    SupplierKind,
)
from account_pool.quota import QuotaObservation
from account_pool.quota import effective_cooldown_until as effective_cooldown_until_value
from account_pool.quota import parse_quota as parse_quota_snapshot
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose

_QuotaObservation = QuotaObservation


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


class _AuthFile(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    auth_index: str | None = None
    provider: str | None = None
    type: str | None = None
    disabled: bool = False
    unavailable: bool = False
    next_retry_after: datetime | None = None
    quota: QuotaObservation = QuotaObservation()
    model_quotas: Mapping[str, QuotaObservation] = Field(default_factory=dict)


class _AuthFilesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    files: tuple[_AuthFile, ...] = ()


_AUTH_FILES_ADAPTER: Final = TypeAdapter(_AuthFilesResponse)
_DEFAULT_SUPPLIERS: Final = SupplierRegistry.default()


def _legacy_openai_supplier() -> SupplierDefinition:
    return _DEFAULT_SUPPLIERS.get(SupplierKind.OPENAI_CODEX)


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

    async def start_authorization(
        self, record: EnvironmentRecord, supplier: SupplierDefinition
    ) -> AuthorizationStart:
        response: Final = await self._request(record, "GET", supplier.authorization_path)
        payload: Final = _AuthorizationResponse.model_validate(response.json())
        return AuthorizationStart(
            authorization_url=payload.url or "",
            provider_state=payload.state,
            user_code=payload.user_code,
            expires_in_seconds=payload.expires_in,
        )

    async def start_openai_authorization(self, record: EnvironmentRecord) -> tuple[str, str]:
        result: Final = await self.start_authorization(record, _legacy_openai_supplier())
        return result.authorization_url, result.provider_state

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

    async def submit_callback(
        self,
        record: EnvironmentRecord,
        supplier: SupplierDefinition | OAuthCallback,
        callback: OAuthCallback | None = None,
    ) -> None:
        selected_supplier: Final
        selected_callback: Final
        if callback is None:
            selected_supplier = _legacy_openai_supplier()
            selected_callback = supplier
        else:
            selected_supplier = supplier
            selected_callback = callback
        payload: Final = {
            "provider": selected_supplier.callback_provider_key,
            "state": selected_callback.state,
            "code": selected_callback.code or "",
            "error": selected_callback.error or selected_callback.error_description or "",
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

    async def read_account(
        self, record: EnvironmentRecord, supplier: SupplierDefinition | None = None
    ) -> EnvironmentRecord:
        selected_supplier: Final = supplier or _legacy_openai_supplier()
        auth_response: Final = await self._request(record, "GET", "/v0/management/auth-files")
        auth_files: Final = _AUTH_FILES_ADAPTER.validate_python(auth_response.json())
        auth_file: Final = next(
            (
                item
                for item in auth_files.files
                if (item.provider is not None and item.provider.lower() == selected_supplier.auth_file_provider_key)
                or (item.type is not None and item.type.lower() == selected_supplier.auth_file_provider_key)
            ),
            None,
        )
        if auth_file is None:
            raise RuntimeError(f"CLIProxyAPI did not persist a {selected_supplier.kind.value} credential")
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
        quota: Final = selected_supplier.quota_parser(auth_file.quota)
        model_quotas: Final = tuple(
            ModelQuotaSnapshot(model=model, quota=selected_supplier.quota_parser(observation))
            for model, observation in sorted(auth_file.model_quotas.items())
        )
        now: Final = datetime.now().astimezone()
        cooldown_until: Final = effective_cooldown_until_value(record, auth_file.next_retry_after, now)
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
                "automatic_cooldown": automatically_cooling,
                "status": status,
                "last_error": None,
            }
        )

    async def read_account_legacy(self, record: EnvironmentRecord) -> EnvironmentRecord:
        return await self.read_account(record, _legacy_openai_supplier())

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

    async def set_enabled_models(
        self, record: EnvironmentRecord, supplier: SupplierDefinition, enabled_models: Sequence[str]
    ) -> None:
        excluded: Final = tuple(model for model in record.available_models if model not in frozenset(enabled_models))
        await self._request(
            record,
            "PUT",
            "/v0/management/oauth-excluded-models",
            json={supplier.excluded_models_key: excluded},
        )

    async def apply_configuration(
        self,
        record: EnvironmentRecord,
        supplier: SupplierDefinition | EnvironmentConfiguration,
        configuration: EnvironmentConfiguration | None = None,
    ) -> None:
        selected_supplier: Final
        selected_configuration: Final
        if configuration is None:
            selected_supplier = _legacy_openai_supplier()
            selected_configuration = supplier
        else:
            selected_supplier = supplier
            selected_configuration = configuration
        await self.set_proxy_url(record, selected_configuration.proxy_url)
        await self.set_enabled_models(record, selected_supplier, selected_configuration.enabled_models)
        await self.set_credential_enabled(record, selected_configuration.credential_enabled)

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


def parse_quota(observation: QuotaObservation) -> QuotaSnapshot:
    return parse_quota_snapshot(observation)
