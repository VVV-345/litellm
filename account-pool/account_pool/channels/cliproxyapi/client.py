"""本模块封装 CLIProxyAPI 管理协议，并将返回值规范化为号池领域模型。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Final
from uuid import UUID
from weakref import WeakValueDictionary

import httpx
from pydantic import TypeAdapter

from account_pool.channels.cliproxyapi.protocol import (
    AuthorizationStart as AuthorizationStart,
)
from account_pool.channels.cliproxyapi.protocol import (
    _AuthFile as _AuthFile,
)
from account_pool.channels.cliproxyapi.protocol import (
    _AuthFilesResponse,
    _AuthorizationResponse,
    _ModelsResponse,
    _ProviderAPICallResult,
    _StatusResponse,
    model_cooldowns_from_auth,
)
from account_pool.channels.cliproxyapi.provider_requests import (
    _codex_auth_file_plan_type,
    _upstream_code,
)
from account_pool.channels.cliproxyapi.quota_state import (
    _merge_quota_snapshots,
)
from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.credential_ownership import (
    CredentialConflict,
    CredentialIdentity,
    CredentialOwnership,
    credential_identity,
)
from account_pool.domain import (
    AuthorizationFlow,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    ModelCooldown,
    ModelQuotaSnapshot,
    OAuthCallback,
    ProviderEndpointFailure,
    ProviderHTTPMethod,
    QuotaSnapshot,
    SupplierKind,
)
from account_pool.quota import (
    ProviderQuotaError,
    ProviderQuotaRefresh,
    QuotaObservation,
)
from account_pool.quota import effective_cooldown_until as effective_cooldown_until_value
from account_pool.quota import parse_quota as parse_quota_snapshot
from account_pool.shared.secrets import EnvironmentSecretDeriver

_QuotaObservation = QuotaObservation


_AUTH_FILES_ADAPTER: Final = TypeAdapter(_AuthFilesResponse)
_JSON_OBJECT_ADAPTER: Final = TypeAdapter(dict[str, object])
_DEFAULT_SUPPLIERS: Final = SupplierRegistry.default()


def _legacy_openai_supplier() -> SupplierDefinition:
    return _DEFAULT_SUPPLIERS.get(SupplierKind.OPENAI_CODEX)


from account_pool.channels.cliproxyapi.provider_quota import ProviderQuotaClient
from account_pool.channels.cliproxyapi.transport import CLIProxyTransport
from account_pool.channels.cliproxyapi.transport import JSONValue as JSONValue


class HttpCLIProxyClient:
    def __init__(
        self,
        secrets: EnvironmentSecretDeriver,
        client: httpx.AsyncClient | None = None,
        ownership: CredentialOwnership | None = None,
    ) -> None:
        self._secrets: Final = secrets
        self._ownership: Final = ownership
        self._client: Final = client or httpx.AsyncClient(
            timeout=15.0,
            transport=httpx.AsyncHTTPTransport(retries=20),
            trust_env=False,
        )
        self._owns_client: Final = client is None
        self._credential_locks: Final[WeakValueDictionary[tuple[UUID, str], asyncio.Lock]] = WeakValueDictionary()
        self._transport: Final = CLIProxyTransport(self._client, self._secrets)
        self._provider_quota: Final = ProviderQuotaClient(self._request, self._credential_locks)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def start_authorization(self, record: EnvironmentRecord, supplier: SupplierDefinition) -> AuthorizationStart:
        if self._ownership is not None:
            if record.oauth_provider_state is not None:
                await self.cancel_oauth_session(record, record.oauth_provider_state)
            await self.delete_auth_file(record, record.auth_file_name or "")
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
        matching_auth_files: Final = tuple(
            item
            for item in auth_files.files
            if (item.provider is not None and item.provider.lower() == selected_supplier.auth_file_provider_key)
            or (item.type is not None and item.type.lower() == selected_supplier.auth_file_provider_key)
        )
        auth_file: Final = (
            next((item for item in matching_auth_files if item.name == record.auth_file_name), None)
            if record.auth_file_name is not None
            else matching_auth_files[-1]
            if matching_auth_files
            else None
        )
        if auth_file is None:
            if self._ownership is not None:
                try:
                    for item in auth_files.files:
                        await self.patch_auth_file_status(record, item.name, item.auth_index, True)
                except Exception as error:
                    raise CredentialConflict("卡片绑定的认证文件不存在且残留文件禁用失败，请重试清理") from error
                raise CredentialConflict("卡片绑定的认证文件不存在，请重新上传唯一凭证或重新授权")
            raise RuntimeError(f"CLIProxyAPI did not persist a {selected_supplier.kind.value} credential")
        if self._ownership is not None:
            try:
                if len(auth_files.files) != 1:
                    raise CredentialConflict("卡片内存在多个认证文件，请删除多余凭证或重新上传唯一凭证")
                if record.authorization_flow is AuthorizationFlow.DIRECT_CREDENTIAL and record.credential_fingerprints:
                    credential = CredentialIdentity(
                        record.credential_fingerprints, record.credential_email, record.credential_account_id
                    )
                else:
                    content, _ = await self.download_auth_file(record, auth_file.name)
                    credential = credential_identity(content, record.supplier.value, self._secrets)
                await self._ownership.claim(record.id, credential.fingerprints)
            except CredentialConflict as conflict:
                try:
                    for item in auth_files.files:
                        await self.patch_auth_file_status(record, item.name, item.auth_index, True)
                except Exception as error:
                    raise conflict from error
                raise
        else:
            credential = None
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
        identity: Final = auth_file.id_token if selected_supplier.kind is SupplierKind.OPENAI_CODEX else None
        auth_plan_type: Final = auth_file.plan_type or (identity.plan_type if identity is not None else None)
        passive_quota: Final = parsed_quota.model_copy(
            update={
                "source": "cliproxyapi_cache",
                "plan_type": parsed_quota.plan_type or auth_plan_type,
                **(
                    {
                        "auth_file_plan_type": _codex_auth_file_plan_type(auth_file),
                        "subscription_active_start": identity.chatgpt_subscription_active_start,
                        "subscription_active_until": identity.chatgpt_subscription_active_until,
                    }
                    if identity is not None
                    else {}
                ),
            }
        )
        refreshed_quota: Final = (
            await self._quota_refresh_with_fallback(record, selected_supplier, auth_file) if refresh_quota else None
        )
        quota: Final = _merge_quota_snapshots(
            record.quota,
            passive_quota,
            None if refreshed_quota is None else refreshed_quota.quota,
        )
        passive_model_quotas: Final = tuple(
            ModelQuotaSnapshot(model=model, quota=selected_supplier.quota_parser(observation))
            for model, observation in sorted(auth_file.model_quotas.items())
        )
        model_quotas: Final = (
            refreshed_quota.model_quotas
            if refreshed_quota is not None and refreshed_quota.model_quotas
            else passive_model_quotas
            if passive_model_quotas
            else record.model_quotas
        )
        now: Final = datetime.now().astimezone()
        model_cooldowns: Final = model_cooldowns_from_auth(auth_file)
        model_aggregate: Final = any(item.retry_at == auth_file.next_retry_after for item in model_cooldowns)
        upstream_code: Final = _upstream_code(auth_file.status_message or "")
        authentication_failed: Final = auth_file.status == "error" and (
            upstream_code
            in {
                "auth_unavailable",
                "authentication_error",
                "invalid_api_key",
                "refresh_token_invalidated",
                "refresh_token_reused",
                "unauthorized",
            }
            or (auth_file.status_message or "").strip().lower() == "unauthorized"
        )
        overload_elapsed: Final = (
            auth_file.next_retry_after is not None
            and auth_file.next_retry_after <= now
            and upstream_code == "server_is_overloaded"
        )
        cooldown_until: Final = effective_cooldown_until_value(
            record, None if model_aggregate else auth_file.next_retry_after, now
        )
        automatically_cooling: Final = (
            auth_file.disabled
            or authentication_failed
            or (auth_file.unavailable and not model_aggregate and not overload_elapsed)
            or (cooldown_until is not None and cooldown_until > now)
        )
        status: Final = (
            EnvironmentStatus.DISABLED
            if not record.enabled
            else EnvironmentStatus.COOLING_DOWN
            if record.manual_cooldown or automatically_cooling
            else EnvironmentStatus.READY
        )
        if self._ownership is not None and credential is not None:
            await self._ownership.retain(record.id, credential.fingerprints)
        return record.model_copy(
            update={
                "auth_file_name": auth_file.name,
                "auth_index": auth_file.auth_index,
                "credential_fingerprints": () if credential is None else credential.fingerprints,
                "credential_email": auth_file.email if credential is None else credential.email or auth_file.email,
                "credential_account_id": (
                    auth_file.account_id if credential is None else credential.account_id or auth_file.account_id
                ),
                "auth_file_disabled": auth_file.disabled,
                "available_models": available_models,
                "enabled_models": enabled_models,
                "quota": quota,
                "model_quotas": model_quotas,
                "model_cooldowns": tuple(
                    max(
                        (item for item in (*record.model_cooldowns, *model_cooldowns) if item.model == model),
                        key=lambda item: item.retry_at,
                    )
                    for model in sorted({item.model for item in (*record.model_cooldowns, *model_cooldowns)})
                ),
                "cooldown_until": cooldown_until,
                "automatic_cooldown": automatically_cooling,
                "status": status,
                "last_error": "上游认证失效，请重新认证" if authentication_failed else None,
            }
        )

    async def read_model_cooldowns(self, record: EnvironmentRecord) -> tuple[ModelCooldown, ...]:
        response: Final = await self._request(record, "GET", "/v0/management/auth-files")
        files: Final = _AUTH_FILES_ADAPTER.validate_python(response.json())
        selected: Final = next((item for item in files.files if item.name == record.auth_file_name), None)
        return () if selected is None else model_cooldowns_from_auth(selected)

    async def _quota_refresh_with_fallback(
        self,
        record: EnvironmentRecord,
        supplier: SupplierDefinition,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh | None:
        attempted_at: Final = datetime.now(timezone.utc)
        try:
            refreshed: Final = await self._refresh_provider_quota(record, supplier, auth_file)
        except ProviderQuotaError as error:
            message: Final = "; ".join(failure.summary() for failure in error.failures)[:500] or str(error)[:500]
            return ProviderQuotaRefresh(
                quota=QuotaSnapshot(
                    refresh_attempted_at=attempted_at,
                    refresh_status="failed",
                    refresh_error=message,
                    refresh_failures=error.failures,
                )
            )
        if refreshed is None:
            return None
        return ProviderQuotaRefresh(
            quota=refreshed.quota.model_copy(
                update={"refresh_attempted_at": refreshed.quota.refresh_attempted_at or attempted_at}
            ),
            model_quotas=refreshed.model_quotas,
        )

    async def read_account_legacy(self, record: EnvironmentRecord) -> EnvironmentRecord:
        return await self.read_account(record, _legacy_openai_supplier())

    async def _refresh_provider_quota(
        self, record: EnvironmentRecord, supplier: SupplierDefinition, auth_file: _AuthFile
    ) -> ProviderQuotaRefresh | None:
        return await self._provider_quota.refresh_provider_quota(record, supplier, auth_file)

    async def _refresh_codex_quota(self, record: EnvironmentRecord, auth_file: _AuthFile) -> ProviderQuotaRefresh:
        return await self._provider_quota.refresh_codex_quota(record, auth_file)

    async def _refresh_claude_quota(self, record: EnvironmentRecord, auth_file: _AuthFile) -> ProviderQuotaRefresh:
        return await self._provider_quota.refresh_claude_quota(record, auth_file)

    async def _refresh_xai_quota(self, record: EnvironmentRecord, auth_file: _AuthFile) -> ProviderQuotaRefresh:
        return await self._provider_quota.refresh_xai_quota(record, auth_file)

    async def _refresh_antigravity_quota(self, record: EnvironmentRecord, auth_file: _AuthFile) -> ProviderQuotaRefresh:
        return await self._provider_quota.refresh_antigravity_quota(record, auth_file)

    async def _onboard_antigravity_user(
        self, record: EnvironmentRecord, auth_file: _AuthFile, base_url: str, tier_id: str, metadata: JSONValue
    ) -> tuple[str | None, tuple[ProviderEndpointFailure, ...]]:
        return await self._provider_quota.onboard_antigravity_user(record, auth_file, base_url, tier_id, metadata)

    async def _poll_antigravity_onboard_operation(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        base_url: str,
        operation: str,
        headers: Mapping[str, str],
        attempts_remaining: int = 60,
    ) -> tuple[str | None, tuple[ProviderEndpointFailure, ...]]:
        return await self._provider_quota.poll_antigravity_onboard_operation(
            record, auth_file, base_url, operation, headers, attempts_remaining
        )

    async def _provider_api_call_first_success(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        method: ProviderHTTPMethod,
        base_urls: tuple[str, ...],
        path: str,
        headers: Mapping[str, str],
        *,
        data: JSONValue | None = None,
    ) -> tuple[str, _ProviderAPICallResult]:
        return await self._provider_quota.provider_api_call_first_success(
            record, auth_file, method, base_urls, path, headers, data=data
        )

    async def _provider_api_call(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        method: ProviderHTTPMethod,
        url: str,
        headers: Mapping[str, str],
        *,
        data: JSONValue | None = None,
    ) -> _ProviderAPICallResult:
        return await self._provider_quota.provider_api_call(record, auth_file, method, url, headers, data=data)

    async def _provider_api_call_serialized(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        method: ProviderHTTPMethod,
        url: str,
        headers: Mapping[str, str],
        *,
        data: JSONValue | None = None,
    ) -> _ProviderAPICallResult:
        return await self._provider_quota.provider_api_call_serialized(
            record, auth_file, method, url, headers, data=data
        )

    async def set_credential_enabled(self, record: EnvironmentRecord, enabled: bool) -> None:
        if record.auth_file_name is None:
            return
        if enabled and self._ownership is not None:
            await self.read_account(record, SupplierRegistry.default().get(record.supplier))
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
        if self._ownership is not None:
            identity: Final = credential_identity(content, record.supplier.value, self._secrets)
            await self._ownership.claim(record.id, identity.fingerprints)
        existing: Final = await self._request(record, "GET", "/v0/management/auth-files")
        files: Final = _AUTH_FILES_ADAPTER.validate_python(existing.json()).files
        # 显式替换前移除旧文件，失败时由服务保持不可路由，不能让新旧令牌同时工作。
        for item in files:
            await self._request(record, "DELETE", "/v0/management/auth-files", params={"name": item.name})
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
        existing: Final = await self._request(record, "GET", "/v0/management/auth-files")
        files: Final = _AUTH_FILES_ADAPTER.validate_python(existing.json()).files
        for item in files:
            await self._request(record, "DELETE", "/v0/management/auth-files", params={"name": item.name})
        remaining: Final = await self._request(record, "GET", "/v0/management/auth-files")
        if _AUTH_FILES_ADAPTER.validate_python(remaining.json()).files:
            raise CredentialConflict("认证文件尚未清理完成，请重试删除")

    async def patch_auth_file_status(
        self, record: EnvironmentRecord, filename: str, auth_index: str | None, disabled: bool
    ) -> None:
        if not disabled and self._ownership is not None:
            await self.read_account(record, SupplierRegistry.default().get(record.supplier))
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
        if (
            record.authorization_flow is AuthorizationFlow.DIRECT_CREDENTIAL
            or supplier.authorization_flow is AuthorizationFlow.DIRECT_CREDENTIAL
        ):
            path: Final = {
                SupplierKind.GEMINI: "/v0/management/gemini-api-key",
                SupplierKind.GEMINI_INTERACTIONS: "/v0/management/interactions-api-key",
                SupplierKind.XAI: "/v0/management/xai-api-key",
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
            SupplierKind.XAI: "/v0/management/xai-api-key",
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
        return await self._transport.request(
            record,
            method,
            path,
            params=params,
            json=json,
            content=content,
            headers=headers,
            management=management,
            gateway=gateway,
        )

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
        return await self._transport.request_multipart(
            record, method, path, filename, content, content_type, field_name=field_name, fields=fields
        )


def parse_quota(observation: QuotaObservation) -> QuotaSnapshot:
    return parse_quota_snapshot(observation)
