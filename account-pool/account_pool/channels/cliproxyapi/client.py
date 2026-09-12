"""本模块封装 CLIProxyAPI 管理协议，并将返回值规范化为号池领域模型。"""

from __future__ import annotations

import asyncio
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
from account_pool.policies import AccountPolicy
from account_pool.quota import QuotaObservation
from account_pool.quota import effective_cooldown_until as effective_cooldown_until_value
from account_pool.quota import parse_quota as parse_quota_snapshot
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose
from account_pool.settings import AccountPoolSettings

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
    plan_type: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)
    attributes: Mapping[str, object] = Field(default_factory=dict)


class _AuthFilesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    files: tuple[_AuthFile, ...] = ()


_AUTH_FILES_ADAPTER: Final = TypeAdapter(_AuthFilesResponse)
_JSON_OBJECT_ADAPTER: Final = TypeAdapter(dict[str, object])
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

    async def cancel_oauth_session(self, record: EnvironmentRecord, state: str) -> None:
        await self._request(
            record,
            "DELETE",
            "/v0/management/oauth-session",
            params={"state": state},
        )

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
        metadata_signals: Final = {
            str(key): str(value)
            for source in (auth_file.metadata, auth_file.attributes)
            for key, value in source.items()
            if isinstance(value, (str, int, float))
        }
        observed_quota: Final = auth_file.quota.model_copy(
            update={"signals": {**metadata_signals, **auth_file.quota.signals}}
        )
        parsed_quota: Final = selected_supplier.quota_parser(observed_quota)
        quota: Final = (
            parsed_quota
            if parsed_quota.plan_type is not None or auth_file.plan_type is None
            else parsed_quota.model_copy(update={"plan_type": auth_file.plan_type})
        )
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

    async def upload_auth_file(
        self, record: EnvironmentRecord, filename: str, content: bytes, content_type: str | None
    ) -> None:
        response: Final = await self._request_multipart(
            record,
            "POST",
            "/v0/management/auth-files",
            filename,
            content,
            content_type,
        )
        response.raise_for_status()

    async def download_auth_file(self, record: EnvironmentRecord, filename: str) -> tuple[bytes, str]:
        response: Final = await self._request(
            record,
            "GET",
            "/v0/management/auth-files/download",
            params={"name": filename},
        )
        content_type: Final = response.headers.get("content-type", "application/json")
        return response.content, content_type

    async def delete_auth_file(self, record: EnvironmentRecord, filename: str) -> None:
        await self._request(
            record,
            "DELETE",
            "/v0/management/auth-files",
            params={"name": filename},
        )

    async def patch_auth_file_status(
        self, record: EnvironmentRecord, filename: str, auth_index: str | None, disabled: bool
    ) -> None:
        await self._request(
            record,
            "PATCH",
            "/v0/management/auth-files/status",
            json={"name": filename, "auth_index": auth_index or "", "disabled": disabled},
        )

    async def patch_auth_file_fields(
        self, record: EnvironmentRecord, filename: str, fields: Mapping[str, object]
    ) -> None:
        await self._request(
            record,
            "PATCH",
            "/v0/management/auth-files/fields",
            json={"name": filename, **fields},
        )

    async def get_auth_file_models(self, record: EnvironmentRecord, filename: str) -> tuple[str, ...]:
        response: Final = await self._request(
            record,
            "GET",
            "/v0/management/auth-files/models",
            params={"name": filename},
        )
        models: Final = _ModelsResponse.model_validate(response.json())
        return tuple(dict.fromkeys(model.id for model in (*models.models, *models.data)))

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

    async def set_oauth_excluded_models(self, record: EnvironmentRecord, models: Sequence[str]) -> None:
        excluded_models: Final = {
            definition.excluded_models_key: list(models)
            for definition in _DEFAULT_SUPPLIERS.definitions.values()
        }
        await self._request(record, "PUT", "/v0/management/oauth-excluded-models", json=excluded_models)

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

    async def apply_global_settings(self, record: EnvironmentRecord, settings: AccountPoolSettings) -> None:
        route_strategy: Final = {
            "auto": "round-robin",
            "priority": "fill-first",
            "random": "round-robin",
            "quota": "weighted-round-robin",
        }[settings.default_route]
        bool_fields: Final = (
            ("/v0/management/debug", settings.debug_logging_enabled),
            ("/v0/management/logging-to-file", settings.file_logging_enabled),
            ("/v0/management/usage-statistics-enabled", settings.usage_statistics_enabled),
            ("/v0/management/request-log", settings.request_log_enabled),
            ("/v0/management/ws-auth", settings.websocket_auth_enabled or settings.websocket_enabled),
            ("/v0/management/quota-exceeded/switch-project", settings.quota_switch_project),
            ("/v0/management/quota-exceeded/switch-preview-model", settings.quota_switch_preview_model),
        )
        await asyncio.gather(*(self._put_value(record, path, value) for path, value in bool_fields))
        int_fields: Final = (
            ("/v0/management/request-retry", settings.request_retry),
            ("/v0/management/max-retry-credentials", settings.max_retry_credentials),
            ("/v0/management/max-retry-interval", settings.max_retry_interval),
            ("/v0/management/logs-max-total-size-mb", settings.logs_max_total_size_mb),
            ("/v0/management/error-logs-max-files", settings.error_logs_max_files),
        )
        await asyncio.gather(*(self._put_value(record, path, value) for path, value in int_fields))
        await self._put_value(record, "/v0/management/force-model-prefix", settings.force_model_prefix)
        await self._put_value(record, "/v0/management/routing/strategy", route_strategy)
        await self.set_oauth_excluded_models(record, settings.oauth_excluded_models)
        await self._request(
            record,
            "PUT",
            "/v0/management/oauth-model-alias",
            json={
                key: [{"name": name, "alias": alias} for name, alias in value]
                for key, value in settings.oauth_model_aliases.items()
            },
        )

    async def apply_policy(self, record: EnvironmentRecord, policy: AccountPolicy) -> None:
        route_strategy: Final = {
            "auto": "round-robin",
            "priority": "fill-first",
            "random": "round-robin",
            "quota": "weighted-round-robin",
            "plan": "fill-first",
            "expiry": "fill-first",
            "custom": "round-robin",
        }[policy.routing.strategy]
        await self._put_value(record, "/v0/management/routing/strategy", route_strategy)
        await self._put_value(record, "/v0/management/request-retry", policy.routing.max_attempts)
        if record.auth_file_name is None:
            return
        fields: Final = _policy_auth_fields(policy)
        if fields:
            await self.patch_auth_file_fields(record, record.auth_file_name, fields)

    async def _put_value(self, record: EnvironmentRecord, path: str, value: object) -> None:
        await self._request(record, "PUT", path, json={"value": value})

    async def list_plugins(self, record: EnvironmentRecord) -> Mapping[str, object]:
        return _JSON_OBJECT_ADAPTER.validate_python((await self._request(record, "GET", "/v0/management/plugins")).json())

    async def list_plugin_store(self, record: EnvironmentRecord) -> Mapping[str, object]:
        return _JSON_OBJECT_ADAPTER.validate_python(
            (await self._request(record, "GET", "/v0/management/plugin-store")).json()
        )

    async def install_plugin(self, record: EnvironmentRecord, plugin_id: str, version: str) -> Mapping[str, object]:
        return _JSON_OBJECT_ADAPTER.validate_python(
            (
                await self._request(
                    record, "POST", f"/v0/management/plugin-store/{plugin_id}/install", json={"version": version}
                )
            ).json()
        )

    async def set_plugin_enabled(
        self, record: EnvironmentRecord, plugin_id: str, enabled: bool
    ) -> Mapping[str, object]:
        return _JSON_OBJECT_ADAPTER.validate_python(
            (
                await self._request(
                    record, "PATCH", f"/v0/management/plugins/{plugin_id}/enabled", json={"enabled": enabled}
                )
            ).json()
        )

    async def uninstall_plugin(self, record: EnvironmentRecord, plugin_id: str) -> Mapping[str, object]:
        return _JSON_OBJECT_ADAPTER.validate_python(
            (await self._request(record, "DELETE", f"/v0/management/plugins/{plugin_id}")).json()
        )

    async def get_plugin_config(self, record: EnvironmentRecord, plugin_id: str) -> Mapping[str, object]:
        return _JSON_OBJECT_ADAPTER.validate_python(
            (await self._request(record, "GET", f"/v0/management/plugins/{plugin_id}/config")).json()
        )

    async def put_plugin_config(
        self, record: EnvironmentRecord, plugin_id: str, config: Mapping[str, object]
    ) -> Mapping[str, object]:
        return _JSON_OBJECT_ADAPTER.validate_python(
            (
                await self._request(record, "PUT", f"/v0/management/plugins/{plugin_id}/config", json=config)
            ).json()
        )

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

    async def _request_multipart(
        self,
        record: EnvironmentRecord,
        method: str,
        path: str,
        filename: str,
        content: bytes,
        content_type: str | None,
    ) -> httpx.Response:
        headers: Final = {"X-Management-Key": self._secrets.derive(record.id, SecretPurpose.MANAGEMENT)}
        return await self._client.request(
            method,
            f"http://cliproxy-{record.id.hex}:8317{path}",
            headers=headers,
            files={"files": (filename, content, content_type or "application/json")},
        )


def parse_quota(observation: QuotaObservation) -> QuotaSnapshot:
    return parse_quota_snapshot(observation)


def _policy_auth_fields(policy: AccountPolicy) -> Mapping[str, object]:
    fields: dict[str, object] = {}
    if policy.codex is not None:
        fields.update(
            {
                "codex_cli_only": policy.codex.cli_only,
                "codex_cli_only_allow_app_server": policy.codex.allow_app_server,
                "codex_cli_only_allow_app_server_clients": list(policy.codex.allow_app_server_clients),
                "identity_confuse": policy.codex.identity_confuse,
                "disable_codex_cloaking": policy.codex.disable_codex_cloaking,
            }
        )
    if policy.claude is not None:
        fields.update(
            {
                "fingerprint_profile": policy.claude.fingerprint_profile,
                "experimental_cch_signing": policy.claude.experimental_cch_signing,
                "cloak": policy.claude.cloak,
                "rebuild_mid_system_message": policy.claude.rebuild_mid_system_message,
            }
        )
    if policy.xai is not None:
        fields["inject_x_search"] = policy.xai.inject_x_search
    if policy.openai_compatible is not None:
        fields["support_prompt_cache_key"] = policy.openai_compatible.support_prompt_cache_key
    if policy.antigravity is not None:
        fields.update(
            {
                "signature_cache": policy.antigravity.signature_cache,
                "strict_bypass_signature": policy.antigravity.strict_bypass_signature,
            }
        )
    return fields
