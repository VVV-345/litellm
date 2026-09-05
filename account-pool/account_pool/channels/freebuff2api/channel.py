"""FreeBuff2API channel composition root."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final
from uuid import UUID

import httpx

from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition, parse_empty_quota
from account_pool.channels.freebuff2api.client import (
    AuthorizationStart,
    CodeAuthorizationOperation,
    FreeBuff2APIRuntime,
    HttpCodebuffClient,
    _ModelsResponse,
)
from account_pool.compose_runtime import ComposeRuntime
from account_pool.config import Settings
from account_pool.domain import (
    AuthorizationFlow,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    GatewayEnvironment,
    OAuthCallback,
    SupplierKind,
)
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose, StateCipher

_FREEBUFF_SUPPLIER: Final = SupplierDefinition(
    kind=SupplierKind.FREEBUFF,
    authorization_flow=AuthorizationFlow.DEVICE_CODE,
    authorization_path="/api/auth/cli/code",
    callback_provider_key="freebuff",
    auth_file_provider_key="freebuff",
    excluded_models_key="freebuff",
    callback_port=None,
    callback_path=None,
    quota_parser=parse_empty_quota,
)

_STATE_PREFIX: Final = "freebuff:"


def freebuff_supplier() -> SupplierDefinition:
    return _FREEBUFF_SUPPLIER


def pack_authorization_state(cipher: StateCipher, environment_id: UUID, operation: CodeAuthorizationOperation) -> str:
    """加密后落库的授权操作凭据：数据库只见环境绑定的密文，不落日志。"""
    return _STATE_PREFIX + cipher.seal(
        environment_id,
        json.dumps(
            {
                "url": operation.authorization_url,
                "fingerprint_id": operation.fingerprint_id,
                "fingerprint_hash": operation.fingerprint_hash,
                "expires_at": operation.expires_at,
                "expires_in_seconds": operation.expires_in_seconds,
            },
            separators=(",", ":"),
        ),
    )


def unpack_authorization_state(cipher: StateCipher, environment_id: UUID, state: str) -> CodeAuthorizationOperation:
    if not state.startswith(_STATE_PREFIX):
        raise RuntimeError("FreeBuff2API authorization state is malformed")
    try:
        payload: Final = json.loads(cipher.open(environment_id, state.removeprefix(_STATE_PREFIX)))
        operation: Final = CodeAuthorizationOperation(
            authorization_url=payload["url"],
            fingerprint_id=payload["fingerprint_id"],
            fingerprint_hash=payload["fingerprint_hash"],
            expires_at=payload["expires_at"],
        )
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("FreeBuff2API authorization state is malformed") from error
    return operation


class FreeBuff2APIChannel:
    def __init__(
        self,
        settings: Settings,
        secrets: EnvironmentSecretDeriver,
        *,
        runtime: ComposeRuntime | None = None,
        client: HttpCodebuffClient | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings: Final = settings
        self._secrets: Final = secrets
        self._cipher: Final = StateCipher(secrets)
        self._runtime: Final = FreeBuff2APIRuntime(
            runtime or ComposeRuntime(settings, secrets),
            secrets,
        )
        self._client: Final = client or HttpCodebuffClient()
        self._http_client: Final = http_client or httpx.AsyncClient(
            timeout=15.0,
            transport=httpx.AsyncHTTPTransport(retries=20),
            trust_env=False,
        )
        self._owns_http_client: Final = http_client is None

    def supplier(self, kind: SupplierKind) -> SupplierDefinition:
        if kind is not SupplierKind.FREEBUFF:
            raise KeyError(kind.value)
        return _FREEBUFF_SUPPLIER

    async def close(self) -> None:
        await self._client.close()
        if self._owns_http_client:
            await self._http_client.aclose()

    async def provision(self, record: EnvironmentRecord) -> None:
        await self._runtime.provision(record)

    async def ensure_control_plane_connections(self, environment_id: UUID) -> None:
        await self._runtime.ensure_control_plane_connections(environment_id)

    async def set_running(self, record: EnvironmentRecord, running: bool) -> None:
        await self._runtime.set_running(record, running)

    async def remove(self, record: EnvironmentRecord) -> None:
        await self._runtime.remove(record)

    async def remove_compose(self, record: EnvironmentRecord) -> None:
        await self._runtime.remove_compose(record)

    async def remove_directory(self, environment_id: UUID) -> None:
        await self._runtime.remove_directory(environment_id)

    def environment_dir(self, environment_id: UUID) -> Path:
        return self._runtime.environment_dir(environment_id)

    async def start_authorization(self, record: EnvironmentRecord) -> AuthorizationStart:
        # fingerprintId 稳定派生自环境 UUID，重复授权复用同一指纹，与官方 CLI 行为一致。
        fingerprint_id: Final = f"codebuff-cli-litellm-{record.id.hex}"
        operation: Final = await self._client.start_authorization(fingerprint_id)
        return AuthorizationStart(
            authorization_url=operation.authorization_url,
            provider_state=pack_authorization_state(self._cipher, record.id, operation),
            user_code=None,
            expires_in_seconds=operation.expires_in_seconds,
        )

    async def authorization_status(self, record: EnvironmentRecord, state: str) -> str:
        operation: Final = unpack_authorization_state(self._cipher, record.id, state)
        token: Final = await self._client.authorization_token(operation)
        if token is None:
            return "wait"
        await self._runtime.write_credential(record, token)
        return "ok"

    async def submit_callback(self, record: EnvironmentRecord, callback: OAuthCallback) -> None:
        raise RuntimeError("FreeBuff2API authorization does not use OAuth callbacks")

    async def read_account(self, record: EnvironmentRecord) -> EnvironmentRecord:
        available: Final = await self._fetch_models(record)
        if not available:
            raise RuntimeError("FreeBuff2API container did not report any models")
        enabled: Final = (
            tuple(model for model in record.enabled_models if model in available)
            if record.available_models
            else available
        )
        return record.model_copy(
            update={
                "auth_file_name": "freebuff_credentials.json",
                "auth_index": "default",
                "available_models": available,
                "enabled_models": enabled,
                "cooldown_until": None,
                "automatic_cooldown": False,
                "status": _refreshed_status(record),
                "last_error": None,
            }
        )

    async def data_plane_health_check(self, record: EnvironmentRecord) -> bool:
        return await self._runtime.health_check(record, self._http_client)

    async def apply_configuration(self, record: EnvironmentRecord, configuration: EnvironmentConfiguration) -> None:
        # 容器上游是 Node v20 的 fetch，不读 HTTP(S)_PROXY 环境变量，HTTP 代理对容器内流量无效；
        # 非美区部署需在宿主机做透明代理，或用上游支持的 CODEBUFF_API 中继地址（当前不暴露）。
        # 模型启停由 reconciler 按 enabled_models 快照收敛 LiteLLM Deployment，无需容器操作。
        return None

    def gateway(self, record: EnvironmentRecord) -> GatewayEnvironment:
        return GatewayEnvironment(
            id=record.id,
            routable=record.status.value == "ready"
            and record.enabled
            and not record.manual_cooldown
            and record.cooldown_until is None
            and not record.configuration_pending
            and record.desired_configuration_version <= record.observed_configuration_version,
            concurrency_limit=record.concurrency_limit,
            enabled_models=record.enabled_models,
            api_base=f"http://freebuff-{record.id.hex}:8787",
            api_key=self._secrets.derive(record.id, SecretPurpose.GATEWAY),
            custom_llm_provider="openai",
        )

    async def _fetch_models(self, record: EnvironmentRecord) -> tuple[str, ...]:
        try:
            response: Final = await self._http_client.get(
                f"http://freebuff-{record.id.hex}:8787/v1/models",
                headers={"Authorization": f"Bearer {self._secrets.derive(record.id, SecretPurpose.GATEWAY)}"},
            )
            response.raise_for_status()
            models: Final = _ModelsResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            return ()
        return tuple(dict.fromkeys(model.id for model in models.data))


def _refreshed_status(record: EnvironmentRecord) -> EnvironmentStatus:
    if not record.enabled:
        return EnvironmentStatus.DISABLED
    if record.manual_cooldown:
        return EnvironmentStatus.COOLING_DOWN
    return EnvironmentStatus.READY


__all__ = ("FreeBuff2APIChannel", "freebuff_supplier")
