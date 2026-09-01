"""本模块编排环境创建、授权、配置更新和状态恢复，不直接处理 HTTP 细节。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Generic, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from pydantic import HttpUrl, TypeAdapter

from account_pool.config import Settings
from account_pool.domain import (
    AuthorizationView,
    CreateEnvironmentRequest,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    GatewayEnvironment,
    OAuthCallback,
    Provider,
    ProxyMode,
    ProxyProfile,
    QuotaSnapshot,
    UpdateEnvironmentRequest,
    to_view,
    utc_now,
)
from account_pool.ports import CLIProxyClient, EnvironmentRepository, EnvironmentRuntime, ProxyProfileRepository
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose

T = TypeVar("T")
_HTTP_URL_ADAPTER: Final = TypeAdapter(HttpUrl)


class FailureCode(StrEnum):
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID = "invalid"
    UPSTREAM = "upstream"


class _AutomaticCooldownState(StrEnum):
    NONE = "none"
    ACTIVE = "active"
    RECOVERED = "recovered"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class Success(Generic[T]):
    value: T


@dataclass(frozen=True, slots=True)
class Failure:
    code: FailureCode
    message: str


Result = Success[T] | Failure


class EnvironmentService:
    def __init__(
        self,
        settings: Settings,
        repository: EnvironmentRepository,
        runtime: EnvironmentRuntime,
        cli_proxy: CLIProxyClient,
        proxy_profiles: ProxyProfileRepository,
        secrets: EnvironmentSecretDeriver,
    ) -> None:
        self._settings: Final = settings
        self._repository: Final = repository
        self._runtime: Final = runtime
        self._cli_proxy: Final = cli_proxy
        self._proxy_profiles: Final = proxy_profiles
        self._secrets: Final = secrets
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._locks_guard: Final = asyncio.Lock()

    async def list_environments(self) -> tuple[EnvironmentView, ...]:
        records: Final = await self._repository.list()
        refreshed: Final = await asyncio.gather(*(self._refresh_if_needed(record) for record in records))
        return tuple(to_view(record) for record in refreshed)

    async def get_environment(self, environment_id: UUID) -> Result[EnvironmentView]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        refreshed: Final = await self._refresh_if_needed(record)
        return Success(to_view(refreshed))

    async def list_proxy_profiles(self) -> tuple[ProxyProfile, ...]:
        return await self._proxy_profiles.list()

    async def list_gateway_environments(self) -> tuple[GatewayEnvironment, ...]:
        records: Final = await self._repository.list()
        refreshed: Final = await asyncio.gather(*(self._refresh_if_needed(record) for record in records))
        return tuple(self._gateway_environment(record) for record in refreshed)

    async def create_environment(self, request: CreateEnvironmentRequest) -> Result[AuthorizationView]:
        now: Final = utc_now()
        record: Final = EnvironmentRecord(
            id=uuid4(),
            version=0,
            name=request.name,
            provider=Provider.OPENAI,
            status=EnvironmentStatus.PROVISIONING,
            enabled=True,
            manual_cooldown=False,
            concurrency_limit=1,
            proxy_mode=ProxyMode.DEFAULT_GATEWAY,
            proxy_profile_id=None,
            available_models=(),
            enabled_models=(),
            auth_file_name=None,
            auth_index=None,
            quota=QuotaSnapshot(),
            model_quotas=(),
            cooldown_until=None,
            oauth_state=None,
            oauth_expires_at=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        await self._repository.save(record)
        try:
            await self._runtime.provision(record)
            authorization_url, state = await self._cli_proxy.start_openai_authorization(record)
        except Exception as error:
            failed: Final = record.model_copy(
                update={
                    "status": EnvironmentStatus.ERROR,
                    "last_error": _safe_error(error),
                    "updated_at": utc_now(),
                }
            )
            await self._repository.save(failed)
            return Failure(FailureCode.UPSTREAM, "environment provisioning failed")
        expires_at: Final = utc_now() + timedelta(minutes=5)
        awaiting: Final = record.model_copy(
            update={
                "status": EnvironmentStatus.AWAITING_AUTHORIZATION,
                "oauth_state": state,
                "oauth_expires_at": expires_at,
                "updated_at": utc_now(),
            }
        )
        await self._repository.save(awaiting)
        command: Final = (
            f"ssh -N -L 1455:127.0.0.1:{self._settings.callback_port} "
            f"{self._settings.ssh_user}@{self._settings.ssh_host}"
        )
        return Success(
            AuthorizationView(
                environment=to_view(awaiting),
                authorization_url=_HTTP_URL_ADAPTER.validate_python(authorization_url),
                ssh_command=command,
                expires_at=expires_at,
            )
        )

    async def submit_oauth_callback(self, callback: OAuthCallback) -> Result[EnvironmentView]:
        record: Final = await self._repository.find_by_oauth_state(callback.state)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "unknown or expired OAuth state")
        if record.oauth_expires_at is None or record.oauth_expires_at <= utc_now():
            expired: Final = record.model_copy(
                update={
                    "status": EnvironmentStatus.ERROR,
                    "last_error": "OAuth authorization expired",
                    "oauth_state": None,
                    "oauth_expires_at": None,
                    "updated_at": utc_now(),
                }
            )
            await self._repository.save(expired)
            return Failure(FailureCode.CONFLICT, "OAuth authorization expired")
        try:
            await self._cli_proxy.submit_callback(record, callback)
        except Exception as error:
            return Failure(FailureCode.UPSTREAM, _safe_error(error))
        return Success(to_view(record))

    async def update_environment(
        self,
        environment_id: UUID,
        request: UpdateEnvironmentRequest,
    ) -> Result[EnvironmentView]:
        lock: Final = await self._lock_for(environment_id)
        async with lock:
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.auth_file_name is None:
                return Failure(FailureCode.CONFLICT, "environment authorization is not complete")
            if request.version != record.version:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            if record.configuration_pending:
                return Failure(FailureCode.CONFLICT, "environment configuration is still being applied")
            unknown_models: Final = frozenset(request.enabled_models).difference(record.available_models)
            if unknown_models:
                return Failure(FailureCode.INVALID, "enabled_models contains unsupported models")
            profile_result: Final = await self._resolve_proxy(request)
            if isinstance(profile_result, Failure):
                return profile_result
            automatic_cooldown: Final = await self._automatic_cooldown_before_update(record, request.manual_cooldown)
            credential_enabled: Final = (
                request.enabled
                and not request.manual_cooldown
                and automatic_cooldown
                in (
                    _AutomaticCooldownState.NONE,
                    _AutomaticCooldownState.RECOVERED,
                )
            )
            status: Final = _status_after_update(record, request, automatic_cooldown)
            cooldown_until: Final = _cooldown_until_after_update(record, request.manual_cooldown, automatic_cooldown)
            updated: Final = record.model_copy(
                update={
                    "name": request.name,
                    "version": record.version + 1,
                    "configuration_pending": True,
                    "concurrency_limit": request.concurrency_limit,
                    "enabled": request.enabled,
                    "manual_cooldown": request.manual_cooldown,
                    "proxy_mode": request.proxy_mode,
                    "proxy_profile_id": request.proxy_profile_id,
                    "enabled_models": request.enabled_models,
                    "status": status,
                    "cooldown_until": cooldown_until,
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            claimed: Final = await self._repository.save_if_version(updated, record.version)
            if claimed is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            try:
                await self._cli_proxy.set_credential_enabled(claimed, credential_enabled)
                await self._cli_proxy.set_proxy_url(claimed, profile_result.value)
                await self._cli_proxy.set_enabled_models(claimed, request.enabled_models)
            except Exception as error:
                failed: Final = claimed.model_copy(
                    update={
                        "configuration_pending": False,
                        "status": EnvironmentStatus.ERROR,
                        "last_error": _safe_error(error),
                        "updated_at": utc_now(),
                    }
                )
                await self._repository.save_if_version(failed, claimed.version)
                return Failure(FailureCode.UPSTREAM, "environment configuration failed")
            completed: Final = claimed.model_copy(
                update={
                    "configuration_pending": False,
                    "updated_at": utc_now(),
                }
            )
            saved: Final = await self._repository.save_if_version(completed, claimed.version)
            if saved is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            return Success(to_view(saved))

    async def _automatic_cooldown_before_update(
        self,
        record: EnvironmentRecord,
        manual_cooldown: bool,
    ) -> _AutomaticCooldownState:
        # 自动冷却必须先通过真实数据面探活，配置保存不能成为绕过额度保护的入口。
        if manual_cooldown:
            return _AutomaticCooldownState.ACTIVE if record.cooldown_until is not None else _AutomaticCooldownState.NONE
        if record.cooldown_until is not None:
            if record.cooldown_until > utc_now():
                return _AutomaticCooldownState.ACTIVE
            return (
                _AutomaticCooldownState.RECOVERED
                if await self._cli_proxy.data_plane_health_check(record)
                else _AutomaticCooldownState.BLOCKED
            )
        if record.manual_cooldown:
            return (
                _AutomaticCooldownState.RECOVERED
                if await self._cli_proxy.data_plane_health_check(record)
                else _AutomaticCooldownState.BLOCKED
            )
        return (
            _AutomaticCooldownState.ACTIVE
            if record.status == EnvironmentStatus.COOLING_DOWN
            else _AutomaticCooldownState.NONE
        )

    async def _refresh_if_needed(self, record: EnvironmentRecord) -> EnvironmentRecord:
        if record.status not in (
            EnvironmentStatus.AWAITING_AUTHORIZATION,
            EnvironmentStatus.VALIDATING,
            EnvironmentStatus.READY,
            EnvironmentStatus.COOLING_DOWN,
            EnvironmentStatus.DISABLED,
        ):
            return record
        lock: Final = await self._lock_for(record.id)
        if lock.locked():
            return record
        async with lock:
            current: Final = await self._repository.get(record.id) or record
            if current.status == EnvironmentStatus.AWAITING_AUTHORIZATION:
                return await self._refresh_authorization(current)
            if current.auth_file_name is None:
                return current
            if _cooldown_active(current):
                return current
            if _cooldown_elapsed(current) and not await self._cli_proxy.data_plane_health_check(current):
                return current
            try:
                observed: Final = await self._cli_proxy.read_account(current)
            except Exception:
                return current
            refreshed: Final = observed.model_copy(update={"updated_at": utc_now()})
            return await self._repository.save(refreshed)

    async def _refresh_authorization(self, record: EnvironmentRecord) -> EnvironmentRecord:
        if record.oauth_state is None or record.oauth_expires_at is None:
            return record
        if record.oauth_expires_at <= utc_now():
            expired: Final = record.model_copy(
                update={
                    "status": EnvironmentStatus.ERROR,
                    "last_error": "OAuth authorization expired",
                    "oauth_state": None,
                    "oauth_expires_at": None,
                    "updated_at": utc_now(),
                }
            )
            return await self._repository.save(expired)
        try:
            status: Final = await self._cli_proxy.authorization_status(record, record.oauth_state)
        except Exception:
            return record
        if status == "wait":
            return record
        if status.startswith("error:"):
            failed: Final = record.model_copy(
                update={
                    "status": EnvironmentStatus.ERROR,
                    "last_error": status.removeprefix("error:"),
                    "oauth_state": None,
                    "oauth_expires_at": None,
                    "updated_at": utc_now(),
                }
            )
            return await self._repository.save(failed)
        if status != "ok":
            return record
        validating: Final = record.model_copy(
            update={
                "status": EnvironmentStatus.VALIDATING,
                "oauth_state": None,
                "oauth_expires_at": None,
                "updated_at": utc_now(),
            }
        )
        await self._repository.save(validating)
        try:
            observed: Final = await self._cli_proxy.read_account(validating)
        except Exception as error:
            validation_failed: Final = validating.model_copy(
                update={
                    "status": EnvironmentStatus.ERROR,
                    "last_error": _safe_error(error),
                    "updated_at": utc_now(),
                }
            )
            return await self._repository.save(validation_failed)
        if not await self._cli_proxy.data_plane_health_check(observed):
            health_failed: Final = observed.model_copy(
                update={
                    "status": EnvironmentStatus.ERROR,
                    "last_error": "CLIProxyAPI data plane validation failed",
                    "updated_at": utc_now(),
                }
            )
            return await self._repository.save(health_failed)
        validated: Final = observed.model_copy(update={"updated_at": utc_now()})
        return await self._repository.save(validated)

    async def _resolve_proxy(self, request: UpdateEnvironmentRequest) -> Result[str]:
        if request.proxy_mode == ProxyMode.DEFAULT_GATEWAY:
            return Success("")
        if request.proxy_profile_id is None:
            return Failure(FailureCode.INVALID, "proxy profile is required")
        profile_url: Final = await self._proxy_profiles.get_url(request.proxy_profile_id)
        if profile_url is None:
            return Failure(FailureCode.INVALID, "proxy profile is unavailable")
        return Success(profile_url)

    def _gateway_environment(self, record: EnvironmentRecord) -> GatewayEnvironment:
        routable: Final = (
            record.status == EnvironmentStatus.READY
            and record.enabled
            and not record.manual_cooldown
            and record.cooldown_until is None
        )
        return GatewayEnvironment(
            id=record.id,
            routable=routable,
            concurrency_limit=record.concurrency_limit,
            enabled_models=record.enabled_models,
            api_base=f"http://cliproxy-{record.id.hex}:8317/v1",
            api_key=self._secrets.derive(record.id, SecretPurpose.GATEWAY),
        )

    async def _lock_for(self, environment_id: UUID) -> asyncio.Lock:
        async with self._locks_guard:
            existing: Final = self._locks.get(environment_id)
            if existing is not None:
                return existing
            created: Final = asyncio.Lock()
            self._locks[environment_id] = created
            return created


def _safe_error(error: Exception) -> str:
    message: Final = str(error).strip() or error.__class__.__name__
    without_urls: Final = _redact_urls(message)
    without_credentials: Final = re.sub(
        r"(?i)(bearer\s+|basic\s+|(?:access|refresh|id)?[_-]?token\s*[:=]\s*|(?:api[_-]?key|secret(?:[-_]?key)?|client[_-]?secret|password|code|state|proxy[_-]?url)\s*[:=]\s*)[^\s,;]+",
        r"\1[redacted]",
        without_urls,
    )
    return without_credentials[:500]


def _redact_urls(message: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw_url: Final = match.group(0)
        try:
            parsed: Final = urlsplit(raw_url)
            safe_query: Final = urlencode(
                tuple((key, "[redacted]") for key, _ in parse_qsl(parsed.query, keep_blank_values=True))
            )
            safe_netloc: Final = parsed.hostname or ""
            if parsed.port is not None:
                safe_netloc = f"{safe_netloc}:{parsed.port}"
            return urlunsplit((parsed.scheme, safe_netloc, parsed.path, safe_query, ""))
        except ValueError:
            return "[redacted-url]"

    return re.sub(r"https?://[^\s\]\[)>,;]+", replace, message)


def _cooldown_elapsed(record: EnvironmentRecord) -> bool:
    return (
        record.enabled
        and not record.manual_cooldown
        and record.cooldown_until is not None
        and record.cooldown_until <= utc_now()
    )


def _cooldown_active(record: EnvironmentRecord) -> bool:
    return record.cooldown_until is not None and record.cooldown_until > utc_now()


def _status_after_update(
    record: EnvironmentRecord,
    request: UpdateEnvironmentRequest,
    automatic_cooldown: _AutomaticCooldownState,
) -> EnvironmentStatus:
    if not request.enabled:
        return EnvironmentStatus.DISABLED
    if request.manual_cooldown:
        return EnvironmentStatus.COOLING_DOWN
    if automatic_cooldown in (_AutomaticCooldownState.ACTIVE, _AutomaticCooldownState.BLOCKED):
        return EnvironmentStatus.COOLING_DOWN
    if record.status in (EnvironmentStatus.AWAITING_AUTHORIZATION, EnvironmentStatus.VALIDATING):
        return record.status
    return EnvironmentStatus.READY


def _cooldown_until_after_update(
    record: EnvironmentRecord,
    manual_cooldown: bool,
    automatic_cooldown: _AutomaticCooldownState,
) -> datetime | None:
    if manual_cooldown:
        return record.cooldown_until
    return None if automatic_cooldown is _AutomaticCooldownState.RECOVERED else record.cooldown_until
