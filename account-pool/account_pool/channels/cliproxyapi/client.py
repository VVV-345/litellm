"""本模块封装 CLIProxyAPI 管理协议，并将返回值规范化为号池领域模型。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, TypeAlias
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.domain import (
    AuthorizationFlow,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    ModelQuotaSnapshot,
    OAuthCallback,
    ProviderEndpointFailure,
    ProviderHTTPMethod,
    QuotaSnapshot,
    SupplierKind,
)
from account_pool.provider_quota import (
    CodexAccountInfo,
    parse_claude_usage_quota,
    parse_codex_account_info,
    parse_codex_usage_quota,
    parse_xai_quota,
    parse_xai_user_id,
)
from account_pool.quota import (
    ProviderQuotaError,
    ProviderQuotaRefresh,
    QuotaObservation,
    parse_antigravity_assist,
    parse_antigravity_onboard_project,
    parse_antigravity_quota,
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

    chatgpt_account_id: str | None = None
    plan_type: str | None = None
    chatgpt_subscription_active_start: datetime | None = None
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


_AUTH_FILES_ADAPTER: Final = TypeAdapter(_AuthFilesResponse)
_JSON_OBJECT_ADAPTER: Final = TypeAdapter(dict[str, object])
_DEFAULT_SUPPLIERS: Final = SupplierRegistry.default()
_ANTIGRAVITY_DAILY_BASE_URL: Final = "https://daily-cloudcode-pa.googleapis.com"
_ANTIGRAVITY_PROD_BASE_URL: Final = "https://cloudcode-pa.googleapis.com"
_CHATGPT_WEB_USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
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


def _merge_quota_snapshots(
    previous: QuotaSnapshot,
    passive: QuotaSnapshot,
    active: QuotaSnapshot | None,
) -> QuotaSnapshot:
    return QuotaSnapshot(
        observed_at=(
            active.observed_at
            if active is not None and active.observed_at is not None
            else passive.observed_at or previous.observed_at
        ),
        plan_type=(
            active.plan_type
            if active is not None and active.plan_type is not None
            else passive.plan_type or previous.plan_type
        ),
        auth_file_plan_type=passive.auth_file_plan_type or previous.auth_file_plan_type,
        subscription_status=(
            active.subscription_status
            if active is not None and active.subscription_status is not None
            else passive.subscription_status or previous.subscription_status
        ),
        subscription_active_start=(
            active.subscription_active_start
            if active is not None and active.subscription_active_start is not None
            else passive.subscription_active_start or previous.subscription_active_start
        ),
        subscription_active_until=(
            active.subscription_active_until
            if active is not None and active.subscription_active_until is not None
            else passive.subscription_active_until or previous.subscription_active_until
        ),
        reset_credits_available=(
            active.reset_credits_available
            if active is not None and active.reset_credits_available is not None
            else passive.reset_credits_available
            if passive.reset_credits_available is not None
            else previous.reset_credits_available
        ),
        prepaid_balance=(
            active.prepaid_balance
            if active is not None and active.prepaid_balance is not None
            else passive.prepaid_balance
            if passive.prepaid_balance is not None
            else previous.prepaid_balance
        ),
        extra_usage_enabled=(
            active.extra_usage_enabled
            if active is not None and active.extra_usage_enabled is not None
            else passive.extra_usage_enabled
            if passive.extra_usage_enabled is not None
            else previous.extra_usage_enabled
        ),
        has_grok_code_access=(
            active.has_grok_code_access
            if active is not None and active.has_grok_code_access is not None
            else passive.has_grok_code_access
            if passive.has_grok_code_access is not None
            else previous.has_grok_code_access
        ),
        refresh_status=(
            active.refresh_status
            if active is not None and active.refresh_status is not None
            else previous.refresh_status
        ),
        refresh_error=(
            active.refresh_error if active is not None and active.refresh_status is not None else previous.refresh_error
        ),
        refresh_failures=() if active is None else active.refresh_failures,
        windows=(
            active.windows
            if active is not None and active.windows
            else passive.windows
            if passive.windows
            else previous.windows
        ),
        balances=(
            active.balances
            if active is not None and active.balances
            else passive.balances
            if passive.balances
            else previous.balances
        ),
    )


def _annotate_refresh(
    refreshed: ProviderQuotaRefresh,
    failures: tuple[ProviderEndpointFailure, ...],
) -> ProviderQuotaRefresh:
    message: Final = "; ".join(failure.summary() for failure in failures)[:500] or None
    status: Final = "partial" if failures else "complete"
    return ProviderQuotaRefresh(
        quota=refreshed.quota.model_copy(
            update={"refresh_status": status, "refresh_error": message, "refresh_failures": failures}
        ),
        model_quotas=refreshed.model_quotas,
    )


def _safe_endpoint(url: str) -> str:
    parsed: Final = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _request_id(headers: Mapping[str, tuple[str, ...]]) -> str | None:
    normalized: Final = {key.lower(): values for key, values in headers.items()}
    return next(
        (values[0] for key in ("request-id", "x-request-id", "cf-ray") if (values := normalized.get(key))),
        None,
    )


def _upstream_code(body: str) -> str | None:
    try:
        payload: Final = _UpstreamErrorPayload.model_validate_json(body)
    except ValueError:
        return None
    candidate: Final = next(
        (
            value
            for value in (
                payload.error.code if isinstance(payload.error, _UpstreamError) else None,
                payload.error.type if isinstance(payload.error, _UpstreamError) else None,
                payload.detail.code if isinstance(payload.detail, _UpstreamError) else None,
                payload.detail.type if isinstance(payload.detail, _UpstreamError) else None,
                payload.code,
                payload.type,
            )
            if value is not None and value.strip()
        ),
        None,
    )
    return None if candidate is None else candidate.strip()[:120]


def _codex_usage_headers(account_id: str | None = None) -> Mapping[str, str]:
    return {
        "Authorization": "Bearer $TOKEN$",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": "https://chatgpt.com/",
        "User-Agent": _CHATGPT_WEB_USER_AGENT,
        "OpenAI-Beta": "codex-1",
        "oai-language": "zh-CN",
        "originator": "Codex Desktop",
        "sec-fetch-site": "none",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-dest": "empty",
        "priority": "u=4, i",
        **({"ChatGPT-Account-Id": account_id} if account_id is not None else {}),
    }


def _codex_subscription_headers(target_path: str, account_id: str | None = None) -> Mapping[str, str]:
    return {
        "Authorization": "Bearer $TOKEN$",
        "Accept": "application/json",
        "Referer": "https://chatgpt.com/",
        "User-Agent": _CHATGPT_WEB_USER_AGENT,
        "x-openai-target-path": target_path,
        "x-openai-target-route": target_path,
        **({"ChatGPT-Account-Id": account_id} if account_id is not None else {}),
    }


def _chatgpt_timezone_offset_minutes() -> int:
    offset: Final = datetime.now().astimezone().utcoffset()
    return 0 if offset is None else -round(offset.total_seconds() / 60)


def _codex_subscription_needs_fallback(
    account_info: CodexAccountInfo | None,
    identity: _CodexIdentity | None,
    observed_at: datetime,
) -> bool:
    expires_at: Final = (
        account_info.subscription_active_until
        if account_info is not None and account_info.subscription_active_until is not None
        else None
        if identity is None
        else identity.chatgpt_subscription_active_until
    )
    return expires_at is None or expires_at <= observed_at


def _antigravity_explicit_base_url(auth_file: _AuthFile) -> str | None:
    allowed: Final = frozenset((_ANTIGRAVITY_DAILY_BASE_URL, _ANTIGRAVITY_PROD_BASE_URL))
    candidate: Final = next(
        (
            value.strip().rstrip("/")
            for source in (auth_file.attributes, auth_file.metadata)
            if isinstance((value := source.get("base_url")), str) and value.strip()
        ),
        None,
    )
    return candidate if candidate in allowed else None


def _antigravity_base_urls(auth_file: _AuthFile) -> tuple[str, ...]:
    explicit: Final = _antigravity_explicit_base_url(auth_file)
    return (explicit,) if explicit is not None else (_ANTIGRAVITY_DAILY_BASE_URL, _ANTIGRAVITY_PROD_BASE_URL)


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
        identity: Final = auth_file.id_token if selected_supplier.kind is SupplierKind.OPENAI_CODEX else None
        auth_plan_type: Final = auth_file.plan_type or (identity.plan_type if identity is not None else None)
        passive_quota: Final = parsed_quota.model_copy(
            update={
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
            await self._refresh_provider_quota(record, selected_supplier, auth_file) if refresh_quota else None
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
                "auth_file_disabled": auth_file.disabled,
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
        if supplier.kind is SupplierKind.OPENAI_CODEX:
            return await self._refresh_codex_quota(record, auth_file)
        if supplier.kind is SupplierKind.ANTHROPIC_CLAUDE:
            return await self._refresh_claude_quota(record, auth_file)
        if supplier.kind is SupplierKind.XAI:
            return await self._refresh_xai_quota(record, auth_file)
        if supplier.kind is SupplierKind.GOOGLE_ANTIGRAVITY:
            return await self._refresh_antigravity_quota(record, auth_file)
        unsupported: Final = {
            SupplierKind.KIMI: "Kimi does not expose a documented subscription quota API",
            SupplierKind.GEMINI: "Gemini API keys do not expose personal subscription quota windows",
            SupplierKind.GEMINI_INTERACTIONS: "Gemini API keys do not expose personal subscription quota windows",
            SupplierKind.VERTEX: "Vertex service accounts do not expose personal subscription quota windows",
        }.get(supplier.kind)
        return (
            None
            if unsupported is None
            else ProviderQuotaRefresh(quota=QuotaSnapshot(refresh_status="unsupported", refresh_error=unsupported))
        )

    async def _refresh_codex_quota(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh:
        observed_at: Final = datetime.now(timezone.utc)
        identity: Final = auth_file.id_token
        identity_account_id: Final = None if identity is None else identity.chatgpt_account_id
        account_check_result: Final = await self._provider_api_call(
            record,
            auth_file,
            "GET",
            (
                "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
                f"?timezone_offset_min={_chatgpt_timezone_offset_minutes()}"
            ),
            _codex_subscription_headers("/backend-api/accounts/check/v4-2023-04-27"),
        )
        account_info: Final = parse_codex_account_info(
            account_check_result.body,
            identity_account_id,
            observed_at,
        )
        account_parse_failure: Final = (
            ProviderEndpointFailure(
                method="GET",
                endpoint="https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
                message="response did not contain a usable active account",
                status_code=200,
            )
            if account_check_result.body is not None and account_info is None
            else None
        )
        account_id: Final = (
            account_info.account_id
            if account_info is not None and account_info.account_id is not None
            else identity_account_id
        )
        headers: Final = _codex_usage_headers(account_id)
        usage_result, reset_credits_result = await asyncio.gather(
            self._provider_api_call(
                record,
                auth_file,
                "GET",
                "https://chatgpt.com/backend-api/wham/usage",
                headers,
            ),
            self._provider_api_call(
                record,
                auth_file,
                "GET",
                "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits",
                headers,
            ),
        )
        if usage_result.failure is not None:
            fatal_failures: Final = tuple(
                failure
                for failure in (
                    usage_result.failure,
                    account_check_result.failure,
                    account_parse_failure,
                    reset_credits_result.failure,
                )
                if failure is not None
            )
            raise ProviderQuotaError(fatal_failures, "Codex quota refresh failed")
        subscription_result: Final = (
            await self._provider_api_call(
                record,
                auth_file,
                "GET",
                f"https://chatgpt.com/backend-api/subscriptions?account_id={quote(account_id, safe='')}",
                _codex_subscription_headers("/backend-api/subscriptions", account_id),
            )
            if account_id is not None and _codex_subscription_needs_fallback(account_info, identity, observed_at)
            else None
        )
        failures: Final = tuple(
            failure
            for failure in (
                account_check_result.failure,
                account_parse_failure,
                reset_credits_result.failure,
                None if subscription_result is None else subscription_result.failure,
            )
            if failure is not None
        )
        refreshed: Final = parse_codex_usage_quota(
            usage_result.body or "",
            observed_at,
            account_info=account_info,
            subscription_body=None if subscription_result is None else subscription_result.body,
            reset_credits_body=reset_credits_result.body,
        )
        if refreshed is None:
            parse_failure: Final = ProviderEndpointFailure(
                method="GET",
                endpoint="https://chatgpt.com/backend-api/wham/usage",
                message="response did not contain usable quota fields",
                status_code=200,
            )
            raise ProviderQuotaError((*failures, parse_failure), "Codex quota refresh failed")
        usage_shape_failure: Final = (
            ProviderEndpointFailure(
                method="GET",
                endpoint="https://chatgpt.com/backend-api/wham/usage",
                message="response did not contain usable quota windows",
                status_code=200,
            )
            if not refreshed.quota.windows
            else None
        )
        annotated_failures: Final = failures if usage_shape_failure is None else (*failures, usage_shape_failure)
        return _annotate_refresh(refreshed, annotated_failures)

    async def _refresh_claude_quota(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh:
        headers: Final = {
            "Authorization": "Bearer $TOKEN$",
            "Accept": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code",
        }
        usage_result, profile_result = await asyncio.gather(
            self._provider_api_call(
                record,
                auth_file,
                "GET",
                "https://api.anthropic.com/api/oauth/usage",
                headers,
            ),
            self._provider_api_call(
                record,
                auth_file,
                "GET",
                "https://api.anthropic.com/api/oauth/profile",
                headers,
            ),
        )
        failures: Final = tuple(
            failure for result in (usage_result, profile_result) if (failure := result.failure) is not None
        )
        if usage_result.body is None and profile_result.body is None:
            raise ProviderQuotaError(failures, "Claude quota refresh failed")
        refreshed: Final = parse_claude_usage_quota(
            usage_result.body,
            profile_result.body,
            datetime.now(timezone.utc),
        )
        if refreshed is None:
            parse_failure: Final = ProviderEndpointFailure(
                method="GET",
                endpoint="https://api.anthropic.com/api/oauth/usage",
                message="responses did not contain usable subscription or quota fields",
                status_code=200,
            )
            raise ProviderQuotaError((*failures, parse_failure), "Claude quota refresh failed")
        return _annotate_refresh(refreshed, failures)

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
            "x-grok-cli-version": "0.2.120",
            "x-grok-client-surface": "grok-cli",
            "x-grok-client-identifier": "grok-shell",
            "User-Agent": "grok-cli/0.2.120",
        }
        weekly_result, monthly_result, user_result, task_result = await asyncio.gather(
            self._provider_api_call(
                record,
                auth_file,
                "GET",
                "https://cli-chat-proxy.grok.com/v1/billing?format=credits",
                headers,
            ),
            self._provider_api_call(
                record,
                auth_file,
                "GET",
                "https://cli-chat-proxy.grok.com/v1/billing",
                headers,
            ),
            self._provider_api_call(
                record,
                auth_file,
                "GET",
                "https://cli-chat-proxy.grok.com/v1/user?include=subscription",
                headers,
            ),
            self._provider_api_call(
                record,
                auth_file,
                "GET",
                "https://grok.com/rest/tasks/usage",
                {**headers, "User-Agent": "Grok Build"},
            ),
        )
        user_id: Final = parse_xai_user_id(user_result.body)
        subscription_result: Final = await self._provider_api_call(
            record,
            auth_file,
            "GET",
            "https://grok.com/rest/subscriptions",
            {**headers, **({"x-userid": user_id} if user_id is not None else {})},
        )
        results: Final = (weekly_result, monthly_result, user_result, subscription_result, task_result)
        failures: Final = tuple(result.failure for result in results if result.failure is not None)
        refreshed: Final = parse_xai_quota(
            weekly_result.body,
            monthly_result.body,
            datetime.now(timezone.utc),
            user_body=user_result.body,
            subscriptions_body=subscription_result.body,
            task_usage_body=task_result.body,
        )
        if refreshed is None:
            parse_failure: Final = ProviderEndpointFailure(
                method="GET",
                endpoint="https://cli-chat-proxy.grok.com/v1/billing",
                message="responses did not contain usable subscription or quota fields",
                status_code=200 if any(result.body is not None for result in results) else None,
            )
            raise ProviderQuotaError((*failures, parse_failure), "xAI quota refresh failed")
        return _annotate_refresh(refreshed, failures)

    async def _refresh_antigravity_quota(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh:
        headers: Final = {
            "Authorization": "Bearer $TOKEN$",
            "Accept": "*/*",
            "Content-Type": "application/json",
            "User-Agent": "antigravity/1.20.5 windows/amd64",
        }
        load_headers: Final = {
            **headers,
            "User-Agent": "antigravity/1.20.5 windows/amd64 google-api-nodejs-client/10.3.0",
            "Accept-Encoding": "gzip, deflate, br",
            "x-goog-api-client": "gl-node/22.21.1",
        }
        preferred_project: Final = auth_file.project_id
        load_payload: Final[dict[str, JSONValue]] = {
            "metadata": {
                "ideName": "antigravity",
                "ideType": "ANTIGRAVITY",
                "ideVersion": "1.20.5",
                "pluginVersion": "1.20.5",
                "platform": "WINDOWS_AMD64",
                "updateChannel": "stable",
                "pluginType": "GEMINI",
                **({"duetProject": preferred_project} if preferred_project is not None else {}),
            },
            "mode": "FULL_ELIGIBILITY_CHECK",
            **({"cloudaicompanionProject": preferred_project} if preferred_project is not None else {}),
        }
        candidate_base_urls: Final = _antigravity_base_urls(auth_file)
        assist_base_url, assist_result = await self._provider_api_call_first_success(
            record,
            auth_file,
            "POST",
            candidate_base_urls,
            "/v1internal:loadCodeAssist",
            load_headers,
            data=load_payload,
        )
        assist: Final = parse_antigravity_assist(assist_result.body)
        quota_base_url: Final = (
            _ANTIGRAVITY_PROD_BASE_URL
            if assist is not None and assist.uses_gcp_tos is True and len(candidate_base_urls) > 1
            else assist_base_url
        )
        initial_project_id: Final = auth_file.project_id or (None if assist is None else assist.project_id)
        onboard_result: Final = (
            await self._onboard_antigravity_user(
                record,
                auth_file,
                assist_base_url,
                assist.onboard_tier_id,
                load_payload["metadata"],
            )
            if initial_project_id is None and assist is not None and assist.onboard_tier_id is not None
            else (None, ())
        )
        project_id: Final = initial_project_id or onboard_result[0]
        onboard_failures: Final = onboard_result[1]
        if project_id is None:
            project_failures: Final = (
                (*onboard_failures, assist_result.failure)
                if assist_result.failure is not None
                else onboard_failures
                if onboard_failures
                else (
                    ProviderEndpointFailure(
                        method="POST",
                        endpoint=f"{assist_base_url}/v1internal:loadCodeAssist",
                        message="response did not contain a project ID",
                        status_code=200,
                    ),
                )
            )
            raise ProviderQuotaError(project_failures, "Antigravity quota refresh failed")
        models_result, summary_result = await asyncio.gather(
            self._provider_api_call(
                record,
                auth_file,
                "POST",
                f"{quota_base_url}/v1internal:fetchAvailableModels",
                headers,
                data={"project": project_id},
            ),
            self._provider_api_call(
                record,
                auth_file,
                "POST",
                f"{quota_base_url}/v1internal:retrieveUserQuotaSummary",
                headers,
                data={"project": project_id},
            ),
        )
        if models_result.failure is not None:
            model_failures: Final = tuple(
                failure
                for failure in (models_result.failure, assist_result.failure, summary_result.failure)
                if failure is not None
            )
            raise ProviderQuotaError(model_failures, "Antigravity quota refresh failed")
        refreshed: Final = parse_antigravity_quota(
            models_result.body or "",
            assist,
            datetime.now(timezone.utc),
            summary_body=summary_result.body,
        )
        if refreshed is None:
            parse_failure: Final = ProviderEndpointFailure(
                method="POST",
                endpoint=f"{quota_base_url}/v1internal:fetchAvailableModels",
                message="response did not contain usable model quota fields",
                status_code=200,
            )
            raise ProviderQuotaError((parse_failure,), "Antigravity quota refresh failed")
        optional_failures: Final = tuple(
            failure for failure in (*onboard_failures, assist_result.failure, summary_result.failure) if failure is not None
        )
        return _annotate_refresh(refreshed, optional_failures)

    async def _onboard_antigravity_user(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        base_url: str,
        tier_id: str,
        metadata: JSONValue,
    ) -> tuple[str | None, tuple[ProviderEndpointFailure, ...]]:
        headers: Final = {
            "Authorization": "Bearer $TOKEN$",
            "Accept": "*/*",
            "Content-Type": "application/json",
            "User-Agent": "antigravity/1.20.5 windows/amd64 google-api-nodejs-client/10.3.0",
            "Accept-Encoding": "gzip",
        }
        start_result: Final = await self._provider_api_call(
            record,
            auth_file,
            "POST",
            f"{base_url}/v1internal:onboardUser",
            headers,
            data={"tierId": tier_id, "metadata": metadata},
        )
        if start_result.failure is not None:
            return None, (start_result.failure,)
        project_id, operation, done = parse_antigravity_onboard_project(start_result.body)
        if project_id is not None:
            return project_id, ()
        if done or operation is None:
            return None, (
                ProviderEndpointFailure(
                    method="POST",
                    endpoint=f"{base_url}/v1internal:onboardUser",
                    message="response did not contain a project ID or pollable operation",
                    status_code=200,
                ),
            )
        return await self._poll_antigravity_onboard_operation(record, auth_file, base_url, operation, headers)

    async def _poll_antigravity_onboard_operation(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        base_url: str,
        operation: str,
        headers: Mapping[str, str],
        attempts_remaining: int = 60,
    ) -> tuple[str | None, tuple[ProviderEndpointFailure, ...]]:
        endpoint: Final = f"{base_url}/v1internal/{operation.lstrip('/')}"
        if attempts_remaining <= 0:
            return None, (
                ProviderEndpointFailure(
                    method="GET",
                    endpoint=endpoint,
                    message="onboard operation did not complete before timeout",
                ),
            )
        result: Final = await self._provider_api_call(record, auth_file, "GET", endpoint, headers)
        if result.failure is not None:
            return None, (result.failure,)
        project_id, _, done = parse_antigravity_onboard_project(result.body)
        if project_id is not None:
            return project_id, ()
        if done:
            return None, (
                ProviderEndpointFailure(
                    method="GET",
                    endpoint=endpoint,
                    message="completed operation did not contain a project ID",
                    status_code=200,
                ),
            )
        await asyncio.sleep(0.5)
        return await self._poll_antigravity_onboard_operation(
            record,
            auth_file,
            base_url,
            operation,
            headers,
            attempts_remaining - 1,
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
        base_url, *remaining_base_urls = base_urls
        result: Final = await self._provider_api_call(
            record,
            auth_file,
            method,
            f"{base_url}{path}",
            headers,
            data=data,
        )
        if result.body is not None or not remaining_base_urls:
            return base_url, result
        return await self._provider_api_call_first_success(
            record,
            auth_file,
            method,
            tuple(remaining_base_urls),
            path,
            headers,
            data=data,
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
        if auth_file.auth_index is None:
            return _ProviderAPICallResult(
                failure=ProviderEndpointFailure(
                    method=method,
                    endpoint=_safe_endpoint(url),
                    message="credential is missing auth_index",
                )
            )
        try:
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
        except httpx.HTTPStatusError as error:
            return _ProviderAPICallResult(
                failure=ProviderEndpointFailure(
                    method=method,
                    endpoint=_safe_endpoint(url),
                    message="CLIProxyAPI management request failed",
                    status_code=error.response.status_code,
                )
            )
        except (httpx.HTTPError, ValueError) as error:
            return _ProviderAPICallResult(
                failure=ProviderEndpointFailure(
                    method=method,
                    endpoint=_safe_endpoint(url),
                    message=error.__class__.__name__,
                )
            )
        if payload.status_code < 200 or payload.status_code >= 300:
            return _ProviderAPICallResult(
                failure=ProviderEndpointFailure(
                    method=method,
                    endpoint=_safe_endpoint(url),
                    message="provider quota endpoint rejected the request",
                    status_code=payload.status_code,
                    request_id=_request_id(payload.header),
                    upstream_code=_upstream_code(payload.body),
                )
            )
        return _ProviderAPICallResult(body=payload.body)

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
