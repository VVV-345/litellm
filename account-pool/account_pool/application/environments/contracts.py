"""共享生命周期常量与回调协议；不导入服务编排或创建运行时资源。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Final, Literal, Protocol, TypeVar
from uuid import UUID

from pydantic import HttpUrl, TypeAdapter

from account_pool.domain import (
    AuthorizationInstructionFlow,
    AuthorizationView,
    CleanupProgress,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    GatewayEnvironment,
    ProxyMode,
    SettingsProfileBaselines,
    UpdateEnvironmentRequest,
)
from account_pool.error_logs import LogStage
from account_pool.ports import (
    EnvironmentChannel,
)
from account_pool.settings import AccountPoolSettings
from account_pool.shared.result import Result

T = TypeVar("T")


HTTP_URL_ADAPTER: Final = TypeAdapter(HttpUrl)


async def constant_async(value: T) -> T:
    return value


class AuthorizationConflict(Exception):
    """授权回调未能持久化到可路由状态时阻止成功响应。"""


AUTHORIZATION_COMPLETE_STATUSES: Final = frozenset(
    (EnvironmentStatus.READY, EnvironmentStatus.DISABLED, EnvironmentStatus.COOLING_DOWN)
)


AUTHORIZATION_VALIDATION_TIMEOUT: Final = timedelta(minutes=2)


DIRECT_CREDENTIAL_VALIDATION_TIMEOUT_SECONDS: Final = 20.0


DIRECT_CREDENTIAL_VALIDATION_INTERVAL_SECONDS: Final = 0.5


class AccountPoolSettingsOperation(Protocol):
    async def __call__(self) -> AccountPoolSettings: ...


class ApplyAndPersistConfigurationOperation(Protocol):
    async def __call__(
        self, record: EnvironmentRecord, desired: EnvironmentConfiguration
    ) -> Result[EnvironmentView]: ...


class AuthorizationViewOperation(Protocol):
    def __call__(self, record: EnvironmentRecord) -> AuthorizationView: ...


class CallbackStateOperation(Protocol):
    def __call__(self, record: EnvironmentRecord) -> str: ...


class ChannelOperation(Protocol):
    def __call__(self, record: EnvironmentRecord) -> EnvironmentChannel: ...


class DefaultProxyOperation(Protocol):
    async def __call__(self, settings: AccountPoolSettings) -> Result[tuple[ProxyMode, str | None, str]]: ...


class FindByOperationIdOperation(Protocol):
    async def __call__(self, operation_id: str) -> EnvironmentRecord | None: ...


class GatewayEnvironmentOperation(Protocol):
    def __call__(self, record: EnvironmentRecord) -> GatewayEnvironment: ...


class LockForOperation(Protocol):
    async def __call__(self, environment_id: UUID) -> asyncio.Lock: ...


class LogEventOperation(Protocol):
    async def __call__(
        self, record: EnvironmentRecord, stage: LogStage, error: Exception | None, *, retryable: bool = False
    ) -> None: ...


class PersistCleanupProgressOperation(Protocol):
    async def __call__(self, record: EnvironmentRecord, progress: CleanupProgress) -> EnvironmentRecord | None: ...


class RefreshIfNeededOperation(Protocol):
    async def __call__(
        self,
        record: EnvironmentRecord,
        *,
        refresh_quota: bool = False,
        credential_state_changed: bool = False,
        raise_on_error: bool = False,
        wait_for_lock: bool = False,
    ) -> EnvironmentRecord: ...


class StartAuthorizationOperation(Protocol):
    async def __call__(
        self, record: EnvironmentRecord
    ) -> tuple[str, str, str, AuthorizationInstructionFlow, str | None, int | None]: ...


class WaitForDirectCredentialOperation(Protocol):
    async def __call__(self, channel: EnvironmentChannel, record: EnvironmentRecord) -> EnvironmentRecord: ...


class AuthorizeEnvironmentOperation(Protocol):
    async def __call__(self, environment_id: UUID, operation_id: str | None = None) -> Result[AuthorizationView]: ...


class DeleteEnvironmentOperation(Protocol):
    async def __call__(self, environment_id: UUID, operation_id: str | None = None) -> Result[None]: ...


class UpdateEnvironmentOperation(Protocol):
    async def __call__(
        self,
        environment_id: UUID,
        request: UpdateEnvironmentRequest,
        *,
        settings_profile_baselines: SettingsProfileBaselines | Literal["preserve"] = "preserve",
    ) -> Result[EnvironmentView]: ...


class UploadAuthFileOperation(Protocol):
    async def __call__(
        self, environment_id: UUID, filename: str, content: bytes, content_type: str | None, *, replace: bool = False
    ) -> Result[EnvironmentView]: ...
