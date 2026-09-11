"""本模块实现 OpenAI 兼容直连渠道，负责凭据探活、模型发现和脱敏网关投影。"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter

from account_pool.channels.cliproxyapi.client import AuthorizationStart
from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition, parse_empty_quota
from account_pool.domain import (
    AuthorizationFlow,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    GatewayCredential,
    GatewayEnvironment,
    OAuthCallback,
    OpenAICompatibleCredential,
    QuotaSnapshot,
    SupplierKind,
    configured_proxy_url,
    utc_now,
)
from account_pool.secrets import EnvironmentSecretDeriver, StateCipher


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str


class _ModelList(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    data: tuple[_Model, ...] = ()


_SUPPLIER: Final = SupplierDefinition(
    kind=SupplierKind.OPENAI_COMPATIBLE,
    authorization_flow=AuthorizationFlow.BROWSER_OAUTH,
    authorization_path="",
    callback_provider_key="",
    auth_file_provider_key="",
    excluded_models_key="",
    callback_port=None,
    callback_path=None,
    quota_parser=parse_empty_quota,
)
_HEADERS: Final = TypeAdapter(tuple[tuple[str, str], ...])
_AZURE_WIRE_SERVER: Final = ipaddress.ip_address("168.63.129.16")
HostResolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def _resolve_host(hostname: str, port: int) -> tuple[str, ...]:
    addresses: Final = await asyncio.get_running_loop().getaddrinfo(
        hostname,
        port,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(dict.fromkeys(str(item[4][0]) for item in addresses))


async def _validate_destination(base_url: str, resolver: HostResolver) -> None:
    parsed: Final = urlsplit(base_url)
    hostname: Final = parsed.hostname
    if hostname is None:
        raise ValueError("OpenAI-compatible destination has no hostname")
    port: Final = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses: Final = await resolver(hostname, port)
    except OSError as error:
        raise ValueError("OpenAI-compatible destination cannot be resolved") from error
    if not addresses:
        raise ValueError("OpenAI-compatible destination cannot be resolved")
    for value in addresses:
        try:
            address: Final = ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError("OpenAI-compatible destination resolved to an invalid address") from error
        effective: Final = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        checked: Final = effective or address
        if not checked.is_global or checked.is_multicast or checked == _AZURE_WIRE_SERVER:
            raise ValueError("OpenAI-compatible destination resolved to a blocked address")


class OpenAICompatibleChannel:
    def __init__(
        self,
        secrets: EnvironmentSecretDeriver,
        client: httpx.AsyncClient | None = None,
        resolver: HostResolver = _resolve_host,
    ) -> None:
        self._cipher: Final = StateCipher(secrets)
        self._client: Final = client or httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False)
        self._owns_client: Final = client is None
        self._resolver: Final = resolver

    def supplier(self, kind: SupplierKind) -> SupplierDefinition:
        if kind is not SupplierKind.OPENAI_COMPATIBLE:
            raise KeyError(kind.value)
        return _SUPPLIER

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def provision(self, record: EnvironmentRecord) -> None:
        return None

    async def ensure_control_plane_connections(self, environment_id: UUID) -> None:
        return None

    async def set_running(self, record: EnvironmentRecord, running: bool) -> None:
        return None

    async def remove(self, record: EnvironmentRecord) -> None:
        return None

    async def remove_compose(self, record: EnvironmentRecord) -> None:
        return None

    async def remove_directory(self, environment_id: UUID) -> None:
        return None

    def environment_dir(self, environment_id: UUID) -> Path:
        raise RuntimeError("OpenAI-compatible cards do not have a local runtime directory")

    async def start_authorization(self, record: EnvironmentRecord) -> AuthorizationStart:
        raise RuntimeError("OpenAI-compatible cards use API keys instead of OAuth")

    async def authorization_status(self, record: EnvironmentRecord, state: str) -> str:
        raise RuntimeError("OpenAI-compatible cards use API keys instead of OAuth")

    async def submit_callback(self, record: EnvironmentRecord, callback: OAuthCallback) -> None:
        raise RuntimeError("OpenAI-compatible cards do not accept OAuth callbacks")

    async def read_account(self, record: EnvironmentRecord) -> EnvironmentRecord:
        models: Final = await self._discover_models(record)
        if not models:
            raise RuntimeError("OpenAI-compatible upstream did not report any models")
        enabled: Final = (
            tuple(model for model in record.enabled_models if model in models)
            if record.enabled_models
            else models
        )
        status: Final = (
            EnvironmentStatus.DISABLED
            if not record.enabled
            else EnvironmentStatus.COOLING_DOWN
            if record.manual_cooldown
            else EnvironmentStatus.READY
        )
        return record.model_copy(
            update={
                "auth_file_name": "encrypted-api-keys",
                "auth_index": "openai-compatible",
                "available_models": models,
                "enabled_models": enabled,
                "quota": QuotaSnapshot(observed_at=utc_now()),
                "status": status,
                "last_error": None,
            }
        )

    async def data_plane_health_check(self, record: EnvironmentRecord) -> bool:
        return bool(await self._discover_models(record))

    async def apply_configuration(self, record: EnvironmentRecord, configuration: EnvironmentConfiguration) -> None:
        return None

    def gateway(self, record: EnvironmentRecord) -> GatewayEnvironment:
        configuration: Final = self._configuration(record)
        credentials: Final = tuple(
            GatewayCredential(
                api_key=self._cipher.open(record.id, credential.api_key_ciphertext),
                proxy_url=credential.proxy_url or configured_proxy_url(record) or None,
                weight=credential.weight,
            )
            for credential in configuration.credentials
        )
        headers: Final = (
            _HEADERS.validate_json(self._cipher.open(record.id, configuration.headers_ciphertext))
            if configuration.headers_ciphertext
            else ()
        )
        return GatewayEnvironment(
            id=record.id,
            routable=record.status is EnvironmentStatus.READY
            and record.enabled
            and not record.manual_cooldown
            and record.cooldown_until is None
            and not record.configuration_pending
            and record.desired_configuration_version <= record.observed_configuration_version,
            concurrency_limit=record.concurrency_limit,
            enabled_models=record.enabled_models,
            api_base=configuration.base_url,
            api_key=credentials[0].api_key,
            credentials=credentials,
            headers=headers,
            model_prefix=configuration.prefix,
            custom_llm_provider="openai",
        )

    async def _discover_models(self, record: EnvironmentRecord) -> tuple[str, ...]:
        configuration: Final = self._configuration(record)
        await _validate_destination(configuration.base_url, self._resolver)
        headers: Final = (
            dict(_HEADERS.validate_json(self._cipher.open(record.id, configuration.headers_ciphertext)))
            if configuration.headers_ciphertext
            else {}
        )
        discoveries: Final = await asyncio.gather(
            *(self._discover_models_for_credential(record, credential, headers) for credential in configuration.credentials)
        )
        discovered: Final = tuple(dict.fromkeys(model for models in discoveries for model in models))
        raw: Final = configuration.custom_models or discovered
        return tuple(
            dict.fromkeys(
                f"{configuration.prefix}{model}" if configuration.prefix else model for model in raw
            )
        )

    async def _discover_models_for_credential(
        self,
        record: EnvironmentRecord,
        credential: OpenAICompatibleCredential,
        headers: dict[str, str],
    ) -> tuple[str, ...]:
        configuration: Final = self._configuration(record)
        api_key: Final = self._cipher.open(record.id, credential.api_key_ciphertext)
        client: Final = (
            self._client
            if (proxy_url := credential.proxy_url or configured_proxy_url(record) or None) is None
            else httpx.AsyncClient(proxy=proxy_url, timeout=20, trust_env=False, follow_redirects=False)
        )
        try:
            response: Final = await client.get(
                f"{configuration.base_url.rstrip('/')}/models",
                headers={**headers, "Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            return tuple(model.id for model in _ModelList.model_validate(response.json()).data)
        except (httpx.HTTPError, ValueError):
            return ()
        finally:
            if client is not self._client:
                await client.aclose()

    @staticmethod
    def _configuration(record: EnvironmentRecord):
        if record.openai_compatible is None:
            raise RuntimeError("OpenAI-compatible configuration is missing")
        return record.openai_compatible


__all__ = ("OpenAICompatibleChannel",)
