"""从 LMU 固定公开页面无凭证提取经结构校验的模型清单。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from account_pool.domain.provider_source import ProviderValidationFailureCode
from account_pool.provider_services.http_response import read_limited_response
from account_pool.provider_services.lmu_static_metadata.manifest import (
    LMU_STATIC_METADATA_ORIGIN,
    LMU_STATIC_METADATA_PATH,
)
from account_pool.provider_services.lmu_static_metadata.schemas import LmuJsonLdModelList
from account_pool.provider_services.safe_http import create_validated_address_client

HostResolver = Callable[[str], Awaitable[tuple[str, ...]]]
HttpClientFactory = Callable[[tuple[str, ...]], httpx.AsyncClient]
_MAX_RESPONSE_BYTES: Final = 262_144
_JSON_LD_SCRIPT: Final = re.compile(
    rb'<script\s+[^>]*type=["\']application/ld\+json["\'][^>]*>(?P<content>.*?)</script\s*>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class LmuStaticMetadataSuccess:
    api_base: str
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LmuStaticMetadataFailure:
    api_base: str
    message: str
    code: ProviderValidationFailureCode


LmuStaticMetadataResult = LmuStaticMetadataSuccess | LmuStaticMetadataFailure


async def resolve_host_addresses(host: str) -> tuple[str, ...]:
    loop: Final = asyncio.get_running_loop()
    records: Final = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple(sorted(frozenset(str(record[4][0]) for record in records)))


def normalize_lmu_static_metadata_base(value: str) -> str | None:
    candidate: Final = value.strip().rstrip("/")
    if candidate != LMU_STATIC_METADATA_ORIGIN:
        return None
    parsed: Final = urlsplit(candidate)
    if parsed.scheme != "https" or parsed.hostname != "api.lmuai.com":
        return None
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.path:
        return None
    return LMU_STATIC_METADATA_ORIGIN


async def fetch_lmu_static_metadata(
    api_base: str,
    resolve_host: HostResolver = resolve_host_addresses,
    client_factory: HttpClientFactory | None = None,
) -> LmuStaticMetadataResult:
    normalized: Final = normalize_lmu_static_metadata_base(api_base)
    if normalized is None:
        return LmuStaticMetadataFailure(
            api_base=api_base,
            message="LMU 静态元数据仅允许精确的 HTTPS 根地址",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    addresses: Final = await _safe_resolve(resolve_host, "api.lmuai.com")
    if addresses is None or not addresses or any(not _is_public_address(address) for address in addresses):
        return LmuStaticMetadataFailure(
            api_base=normalized,
            message="LMU 静态元数据地址必须解析到公网 IP",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    factory: Final = client_factory or _default_client_factory
    client: Final = factory(addresses)
    try:
        return await _fetch_page(client, normalized)
    finally:
        await client.aclose()


def _default_client_factory(addresses: tuple[str, ...]) -> httpx.AsyncClient:
    return create_validated_address_client(httpx.Timeout(30, connect=5), addresses)


async def _fetch_page(client: httpx.AsyncClient, api_base: str) -> LmuStaticMetadataResult:
    try:
        async with client.stream(
            "GET",
            f"{api_base}{LMU_STATIC_METADATA_PATH}",
            headers={"accept": "text/html"},
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                return _failure(api_base, "LMU 公开页面不允许重定向", ProviderValidationFailureCode.UPSTREAM_RESPONSE)
            if response.status_code >= 400:
                return _failure(api_base, "LMU 公开页面不可用", _failure_code(response.status_code))
            content_type: Final = response.headers.get("content-type", "")
            if not content_type.lower().startswith("text/html"):
                return _failure(api_base, "LMU 公开页面不是 HTML", ProviderValidationFailureCode.UPSTREAM_RESPONSE)
            content: Final = await read_limited_response(response, _MAX_RESPONSE_BYTES)
    except httpx.HTTPError:
        return _failure(api_base, "无法连接 LMU 公开页面", ProviderValidationFailureCode.TRANSPORT)
    if content is None:
        return _failure(api_base, "LMU 公开页面超过响应限制", ProviderValidationFailureCode.UPSTREAM_RESPONSE)
    models: Final = _json_ld_models(content)
    if models is None:
        return _failure(api_base, "LMU 公开页面未提供可验证的静态模型清单", ProviderValidationFailureCode.NO_MODELS)
    return LmuStaticMetadataSuccess(api_base=api_base, models=models)


def _json_ld_models(content: bytes) -> tuple[str, ...] | None:
    payloads: Final = tuple(match.group("content") for match in _JSON_LD_SCRIPT.finditer(content))
    parsed: Final = tuple(_parse_model_list(payload) for payload in payloads)
    models: Final = tuple(model for model_list in parsed if model_list is not None for model in model_list)
    return tuple(sorted(frozenset(models))) or None


def _parse_model_list(payload: bytes) -> tuple[str, ...] | None:
    try:
        parsed: Final = LmuJsonLdModelList.model_validate(json.loads(payload))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
        return None
    return tuple(item.identifier for item in parsed.item_list_element)


def _failure(api_base: str, message: str, code: ProviderValidationFailureCode) -> LmuStaticMetadataFailure:
    return LmuStaticMetadataFailure(api_base=api_base, message=message, code=code)


def _failure_code(status_code: int) -> ProviderValidationFailureCode:
    return (
        ProviderValidationFailureCode.TRANSPORT
        if status_code == 429 or status_code >= 500
        else ProviderValidationFailureCode.UPSTREAM_RESPONSE
    )


async def _safe_resolve(resolve_host: HostResolver, host: str) -> tuple[str, ...] | None:
    try:
        return await resolve_host(host)
    except (OSError, ValueError):
        return None


def _is_public_address(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False
