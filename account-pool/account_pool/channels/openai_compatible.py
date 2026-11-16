"""本模块实现 OpenAI 兼容直连渠道，负责凭据探活、模型发现和脱敏网关投影。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable, Mapping, Sequence
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import Final
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter

from account_pool.channels.cliproxyapi.client import AuthorizationStart
from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition, parse_empty_quota
from account_pool.credential_ownership import CredentialConflict, CredentialOwnership, credential_identity
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
from account_pool.provider_families import OPENCODE_GO_BASES, OPENCODE_GO_CATALOG_BASE
from account_pool.shared.secrets import EnvironmentSecretDeriver, StateCipher


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str


class _ModelList(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    data: tuple[_Model, ...]


class ModelListDiscoveryError(RuntimeError):
    pass


def _model_list_error(error: httpx.HTTPError | TimeoutError | ValueError) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"model_list_http_{error.response.status_code}"
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "model_list_timeout"
    if isinstance(error, httpx.HTTPError):
        return "model_list_network"
    return "model_list_format"


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
_MAX_MODEL_LIST_BYTES: Final = 2 * 1024 * 1024
_MAX_PREVIEW_MODELS: Final = 500
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


def _validated_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address: Final = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("OpenAI-compatible destination resolved to an invalid address") from error
    effective: Final = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    checked: Final = effective or address
    if not checked.is_global or checked.is_multicast or checked == _AZURE_WIRE_SERVER:
        raise ValueError("OpenAI-compatible destination resolved to a blocked address")
    return address


async def _validate_destination(base_url: str, resolver: HostResolver) -> tuple[str, str | None]:
    parsed: Final = urlsplit(base_url)
    hostname: Final = parsed.hostname
    if hostname is None or parsed.scheme not in {"http", "https"}:
        raise ValueError("OpenAI-compatible destination has no hostname")
    default_port: Final = 443 if parsed.scheme == "https" else 80
    port: Final = parsed.port or default_port
    try:
        addresses: Final = await resolver(hostname, port)
    except OSError as error:
        raise ValueError("OpenAI-compatible destination cannot be resolved") from error
    if not addresses:
        raise ValueError("OpenAI-compatible destination cannot be resolved")
    validated: Final = tuple(_validated_address(value) for value in addresses)
    if parsed.scheme == "https":
        return base_url, None
    selected: Final = validated[0]
    ip_host: Final = f"[{selected.compressed}]" if isinstance(selected, ipaddress.IPv6Address) else selected.compressed
    netloc: Final = ip_host if parsed.port is None else f"{ip_host}:{parsed.port}"
    original_host: Final = f"[{hostname}]" if ":" in hostname else hostname
    host_header: Final = (
        original_host if parsed.port is None or parsed.port == default_port else f"{original_host}:{parsed.port}"
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, "")), host_header


class OpenAICompatibleChannel:
    def __init__(
        self,
        secrets: EnvironmentSecretDeriver,
        client: httpx.AsyncClient | None = None,
        resolver: HostResolver = _resolve_host,
        ownership: CredentialOwnership | None = None,
    ) -> None:
        self._cipher: Final = StateCipher(secrets)
        self._secrets: Final = secrets
        self._ownership: Final = ownership
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

    async def read_account(self, record: EnvironmentRecord, *, refresh_quota: bool = False) -> EnvironmentRecord:
        _ = refresh_quota
        configuration: Final = self._configuration(record)
        if len(configuration.credentials) != 1:
            raise CredentialConflict("一张卡片只能使用一个 API Key，请先删除多余凭证")
        identity: Final = credential_identity(
            json.dumps(
                {"api_key": self._cipher.open(record.id, configuration.credentials[0].api_key_ciphertext)}
            ).encode(),
            record.supplier.value,
            self._secrets,
        )
        if self._ownership is not None:
            await self._ownership.claim(record.id, identity.fingerprints)
        models: Final = await self._discover_models(record)
        if not models:
            raise RuntimeError("OpenAI-compatible upstream did not report any models")
        enabled: Final = (
            tuple(model for model in record.enabled_models if model in models) if record.enabled_models else models
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
                "credential_fingerprints": identity.fingerprints,
                "credential_account_id": configuration.base_url,
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
            and len(configuration.credentials) == 1
            and (self._ownership is None or bool(record.credential_fingerprints))
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
        destination, host_header = await _validate_destination(configuration.base_url, self._resolver)
        headers: Final = (
            dict(_HEADERS.validate_json(self._cipher.open(record.id, configuration.headers_ciphertext)))
            if configuration.headers_ciphertext
            else {}
        )
        discoveries: Final = await asyncio.gather(
            *(
                self._discover_models_for_credential(record, credential, headers, destination, host_header)
                for credential in configuration.credentials
            )
        )
        discovered: Final = tuple(dict.fromkeys(model for models in discoveries for model in models))
        raw: Final = configuration.custom_models or discovered
        return tuple(
            dict.fromkeys(f"{configuration.prefix}{model}" if configuration.prefix else model for model in raw)
        )

    async def _discover_models_for_credential(
        self,
        record: EnvironmentRecord,
        credential: OpenAICompatibleCredential,
        headers: Mapping[str, str],
        destination: str,
        host_header: str | None,
    ) -> tuple[str, ...]:
        api_key: Final = self._cipher.open(record.id, credential.api_key_ciphertext)
        models: Final = await self._request_models(
            api_key,
            credential.proxy_url or configured_proxy_url(record) or None,
            headers,
            destination,
            host_header,
        )
        return models or ()

    async def discover_models_from_source(
        self, base_url: str, api_key: str, headers: tuple[tuple[str, str], ...], proxy_url: str | None
    ) -> tuple[str, ...] | None:
        destination, host_header = await _validate_destination(base_url, self._resolver)
        models: Final = await self._request_models(
            api_key, proxy_url, MappingProxyType(dict(headers)), destination, host_header, bounded=True
        )
        if models is None:
            return None
        valid: Final = (
            model for value in models if (model := value.strip()) and len(model) <= 256
            and not any(character in model for character in (",", "\r", "\n"))
        )
        return tuple(dict.fromkeys(valid))[:_MAX_PREVIEW_MODELS]

    async def _request_models(
        self,
        api_key: str,
        proxy_url: str | None,
        headers: Mapping[str, str],
        destination: str,
        host_header: str | None,
        *,
        bounded: bool = False,
    ) -> tuple[str, ...] | None:
        client: Final = (
            self._client
            if proxy_url is None
            else httpx.AsyncClient(proxy=proxy_url, timeout=20, trust_env=False, follow_redirects=False)
        )
        source: Final = destination.rstrip("/")
        catalog: Final = OPENCODE_GO_CATALOG_BASE if source in OPENCODE_GO_BASES else source
        try:
            async with asyncio.timeout(20 if bounded else None), client.stream(
                "GET",
                f"{catalog}/models",
                headers={
                    **headers,
                    **({"Host": host_header} if host_header is not None else {}),
                    "Authorization": f"Bearer {api_key}",
                },
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                body: Final = BytesIO()
                async for chunk in response.aiter_bytes():
                    if bounded and body.tell() + len(chunk) > _MAX_MODEL_LIST_BYTES:
                        raise ModelListDiscoveryError("model_list_format")
                    body.write(chunk)
                return tuple(model.id for model in _ModelList.model_validate_json(body.getvalue()).data)
        except (httpx.HTTPError, TimeoutError, ValueError) as error:
            if bounded:
                raise ModelListDiscoveryError(_model_list_error(error)) from None
            return None
        finally:
            if client is not self._client:
                await client.aclose()

    @staticmethod
    def _configuration(record: EnvironmentRecord):
        if record.openai_compatible is None:
            raise RuntimeError("OpenAI-compatible configuration is missing")
        return record.openai_compatible


__all__ = ("ModelListDiscoveryError", "OpenAICompatibleChannel")
