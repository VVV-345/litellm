"""本模块封装 CLIProxyAPI 管理协议，并将返回值规范化为号池领域模型。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, TypeAlias

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
from account_pool.quota import (
    ProviderQuotaRefresh,
    QuotaObservation,
    parse_antigravity_assist,
    parse_antigravity_quota,
    parse_xai_billing_quota,
)
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


class _CodexIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    plan_type: str | None = None
    chatgpt_subscription_active_until: datetime | None = None


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
    auth_file_plan_type: str | None = None
    project_id: str | None = None
    id_token: _CodexIdentity | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)
    attributes: Mapping[str, object] = Field(default_factory=dict)


class _AuthFilesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    files: tuple[_AuthFile, ...] = ()


class _APICallResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status_code: int
    body: str


_AUTH_FILES_ADAPTER: Final = TypeAdapter(_AuthFilesResponse)
_JSON_OBJECT_ADAPTER: Final = TypeAdapter(dict[str, object])
_DEFAULT_SUPPLIERS: Final = SupplierRegistry.default()
JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]


def _codex_auth_file_plan_type(auth_file: _AuthFile) -> str | None:
    explicit: Final = _normalize_codex_auth_file_plan_type(auth_file.auth_file_plan_type)
    if explicit is not None:
        return explicit
    normalized_name: Final = auth_file.name.rsplit(".", 1)[0].strip().lower().replace("_", "-").replace(" ", "-")
    if normalized_name.endswith(("-prolite", "-pro-lite")):
        return "prolite"
    if normalized_name.endswith(("-promax", "-pro-max")):
        return "promax"
    return None


def _normalize_codex_auth_file_plan_type(value: str | None) -> str | None:
    normalized: Final = (value or "").strip().lower().replace("_", "-").replace(" ", "-")
    if normalized in ("prolite", "pro-lite"):
        return "prolite"
    if normalized in ("promax", "pro-max"):
        return "promax"
    return None


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

    async def start_authorization(self, record: EnvironmentRecord, supplier: SupplierDefinition) -> AuthorizationStart:
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
        self,
        record: EnvironmentRecord,
        supplier: SupplierDefinition | None = None,
        *,
        refresh_quota: bool = False,
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
        refreshed_quota: Final = (
            await self._refresh_provider_quota(record, selected_supplier, auth_file) if refresh_quota else None
        )
        refreshed_windows: Final = (
            refreshed_quota.quota.windows
            if refreshed_quota is not None and refreshed_quota.quota.windows
            else parsed_quota.windows
        )
        quota_observed_at: Final = (
            refreshed_quota.quota.observed_at
            if refreshed_quota is not None and refreshed_quota.quota.windows
            else parsed_quota.observed_at
        )
        identity: Final = auth_file.id_token if selected_supplier.kind is SupplierKind.OPENAI_CODEX else None
        plan_type: Final = auth_file.plan_type or (identity.plan_type if identity is not None else None)
        quota: Final = parsed_quota.model_copy(
            update={
                "observed_at": quota_observed_at,
                "plan_type": (
                    refreshed_quota.quota.plan_type
                    if refreshed_quota is not None and refreshed_quota.quota.plan_type is not None
                    else parsed_quota.plan_type or plan_type
                ),
                "windows": refreshed_windows,
                **(
                    {
                        "auth_file_plan_type": _codex_auth_file_plan_type(auth_file),
                        "subscription_active_until": identity.chatgpt_subscription_active_until
                        if identity is not None
                        else None,
                    }
                    if selected_supplier.kind is SupplierKind.OPENAI_CODEX
                    else {}
                ),
            }
        )
        passive_model_quotas: Final = tuple(
            ModelQuotaSnapshot(model=model, quota=selected_supplier.quota_parser(observation))
            for model, observation in sorted(auth_file.model_quotas.items())
        )
        model_quotas: Final = (
            refreshed_quota.model_quotas
            if refreshed_quota is not None and refreshed_quota.model_quotas
            else passive_model_quotas
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

    async def _refresh_provider_quota(
        self,
        record: EnvironmentRecord,
        supplier: SupplierDefinition,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh | None:
        if supplier.kind is SupplierKind.XAI:
            return await self._refresh_xai_quota(record, auth_file)
        if supplier.kind is SupplierKind.GOOGLE_ANTIGRAVITY:
            return await self._refresh_antigravity_quota(record, auth_file)
        return None

    async def _refresh_xai_quota(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh:
        headers: Final = {
            "Authorization": "Bearer $TOKEN$",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-grok-client-version": "0.2.120",
            "x-grok-client-identifier": "grok-shell",
            "x-authenticateresponse": "authenticate-response",
            "User-Agent": "xai-grok-workspace/0.2.120",
        }
        weekly_body: Final = await self._optional_provider_api_call(
            record,
            auth_file,
            "GET",
            "https://cli-chat-proxy.grok.com/v1/billing?format=credits",
            headers,
        )
        monthly_body: Final = await self._optional_provider_api_call(
            record,
            auth_file,
            "GET",
            "https://cli-chat-proxy.grok.com/v1/billing",
            headers,
        )
        refreshed: Final = parse_xai_billing_quota(weekly_body, monthly_body, datetime.now(timezone.utc))
        if refreshed is None:
            raise RuntimeError("xAI quota endpoints did not return usable quota data")
        return refreshed

    async def _refresh_antigravity_quota(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh:
        headers: Final = {
            "Authorization": "Bearer $TOKEN$",
            "Accept": "*/*",
            "Content-Type": "application/json",
            "User-Agent": "antigravity/hub/2.9.1 darwin/arm64",
        }
        assist_body: Final = await self._optional_provider_api_call(
            record,
            auth_file,
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
            headers,
            data={"metadata": {"ideType": "ANTIGRAVITY"}},
        )
        assist: Final = parse_antigravity_assist(assist_body)
        project_id: Final = auth_file.project_id or (None if assist is None else assist.project_id)
        if project_id is None:
            raise RuntimeError("Antigravity quota refresh requires a project ID")
        models_body: Final = await self._provider_api_call(
            record,
            auth_file,
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
            headers,
            data={"project": project_id},
        )
        refreshed: Final = parse_antigravity_quota(models_body, assist, datetime.now(timezone.utc))
        if refreshed is None:
            raise RuntimeError("Antigravity quota endpoint did not return usable quota data")
        return refreshed

    async def _optional_provider_api_call(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        method: str,
        url: str,
        headers: Mapping[str, str],
        *,
        data: JSONValue | None = None,
    ) -> str | None:
        try:
            return await self._provider_api_call(record, auth_file, method, url, headers, data=data)
        except (httpx.HTTPError, RuntimeError, ValueError):
            return None

    async def _provider_api_call(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        method: str,
        url: str,
        headers: Mapping[str, str],
        *,
        data: JSONValue | None = None,
    ) -> str:
        if auth_file.auth_index is None:
            raise RuntimeError("CLIProxyAPI credential is missing auth_index")
        response: Final = await self._request(
            record,
            "POST",
            "/v0/management/api-call",
            json={
                "auth_index": auth_file.auth_index,
                "method": method,
                "url": url,
                "header": dict(headers),
                "data": "" if data is None else json.dumps(data, separators=(",", ":")),
            },
        )
        payload: Final = _APICallResponse.model_validate(response.json())
        if payload.status_code < 200 or payload.status_code >= 300:
            raise RuntimeError(f"provider quota endpoint returned HTTP {payload.status_code}")
        return payload.body

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
        if supplier.authorization_flow.value == "direct_credential":
            path: Final = {
                SupplierKind.GEMINI: "/v0/management/gemini-api-key",
                SupplierKind.GEMINI_INTERACTIONS: "/v0/management/interactions-api-key",
            }.get(supplier.kind)
            if path is not None:
                await self._request(
                    record,
                    "PATCH",
                    path,
                    json={"index": 0, "value": {"excluded-models": list(excluded)}},
                )
            return
        await self._request(
            record,
            "PUT",
            "/v0/management/oauth-excluded-models",
            json={supplier.excluded_models_key: excluded},
        )

    async def write_direct_api_key(
        self,
        record: EnvironmentRecord,
        supplier: SupplierDefinition,
        *,
        api_key: str,
        prefix: str,
        priority: int,
        weight: int,
        base_url: str | None,
        headers: Mapping[str, str],
        proxy_url: str,
    ) -> None:
        path: Final = {
            SupplierKind.GEMINI: "/v0/management/gemini-api-key",
            SupplierKind.GEMINI_INTERACTIONS: "/v0/management/interactions-api-key",
        }.get(supplier.kind)
        if path is None:
            raise ValueError("supplier does not accept a direct API key")
        payload: Final[dict[str, JSONValue]] = {
            "api-key": api_key,
            "prefix": prefix,
            "priority": priority,
            "weight": weight,
            "headers": dict(headers),
            **({"base-url": base_url} if base_url else {}),
            **({"proxy-url": proxy_url} if proxy_url else {}),
        }
        await self._request(record, "PUT", path, json=[payload])

    async def import_vertex_credential(
        self,
        record: EnvironmentRecord,
        filename: str,
        content: bytes,
        location: str,
    ) -> None:
        response: Final = await self._request_multipart(
            record,
            "POST",
            "/v0/management/vertex/import",
            filename,
            content,
            "application/json",
            field_name="file",
            fields={"location": location},
        )
        response.raise_for_status()

    async def get_config_yaml(self, record: EnvironmentRecord) -> str:
        response: Final = await self._request(record, "GET", "/v0/management/config.yaml")
        return response.text

    async def put_config_yaml(self, record: EnvironmentRecord, content: str) -> None:
        await self._request(
            record,
            "PUT",
            "/v0/management/config.yaml",
            content=content,
            headers={"Content-Type": "application/yaml"},
        )

    async def put_json(self, record: EnvironmentRecord, path: str, payload: JSONValue) -> None:
        await self._request(record, "PUT", path, json=payload)

    async def put_value(self, record: EnvironmentRecord, path: str, value: JSONValue) -> None:
        await self.put_json(record, path, {"value": value})

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

    async def list_plugins(self, record: EnvironmentRecord) -> Mapping[str, object]:
        return _JSON_OBJECT_ADAPTER.validate_python(
            (await self._request(record, "GET", "/v0/management/plugins")).json()
        )

    async def list_plugin_store(self, record: EnvironmentRecord) -> Mapping[str, object]:
        return _JSON_OBJECT_ADAPTER.validate_python(
            (await self._request(record, "GET", "/v0/management/plugin-store")).json()
        )

    async def install_plugin(
        self, record: EnvironmentRecord, plugin_id: str, version: str, source: str | None
    ) -> Mapping[str, object]:
        params: Final = {"version": version, **({"source": source} if source else {})}
        return _JSON_OBJECT_ADAPTER.validate_python(
            (
                await self._request(
                    record,
                    "POST",
                    f"/v0/management/plugin-store/{plugin_id}/install",
                    params=params,
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
            (await self._request(record, "PUT", f"/v0/management/plugins/{plugin_id}/config", json=config)).json()
        )

    async def _request(
        self,
        record: EnvironmentRecord,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: JSONValue | None = None,
        content: str | None = None,
        headers: Mapping[str, str] | None = None,
        management: bool = True,
        gateway: bool = False,
    ) -> httpx.Response:
        host: Final = f"cliproxy-{record.id.hex}"
        auth_headers: Final = (
            {"X-Management-Key": self._secrets.derive(record.id, SecretPurpose.MANAGEMENT)}
            if management
            else {"Authorization": f"Bearer {self._secrets.derive(record.id, SecretPurpose.GATEWAY)}"}
            if gateway
            else None
        )
        request_headers: Final = {**(auth_headers or {}), **(headers or {})}
        response: Final = await self._client.request(
            method,
            f"http://{host}:8317{path}",
            headers=request_headers,
            params=params,
            json=json,
            content=content,
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
        *,
        field_name: str = "files",
        fields: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        headers: Final = {"X-Management-Key": self._secrets.derive(record.id, SecretPurpose.MANAGEMENT)}
        return await self._client.request(
            method,
            f"http://cliproxy-{record.id.hex}:8317{path}",
            headers=headers,
            data=fields,
            files={field_name: (filename, content, content_type or "application/json")},
        )


def parse_quota(observation: QuotaObservation) -> QuotaSnapshot:
    return parse_quota_snapshot(observation)
