"""安全请求 New API 网关的模型列表与倍率价格，并返回类型化成功或失败结果。"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from account_pool.domain.provider_source import ProviderValidationFailureCode
from account_pool.provider_services.http_response import read_limited_response
from account_pool.provider_services.new_api.schemas import NewApiModelList, NewApiPricingEntry, NewApiPricingResponse

HostResolver = Callable[[str], Awaitable[tuple[str, ...]]]
_MAX_MODELS_RESPONSE_BYTES: Final = 1_048_576
_MAX_PRICING_RESPONSE_BYTES: Final = 1_048_576


@dataclass(frozen=True, slots=True)
class NewApiPricedModel:
    model: str
    entry: NewApiPricingEntry


@dataclass(frozen=True, slots=True)
class NewApiDiscoverySuccess:
    api_base: str
    models: tuple[str, ...]
    pricing: tuple[NewApiPricedModel, ...]


@dataclass(frozen=True, slots=True)
class NewApiDiscoveryFailure:
    api_base: str
    message: str
    code: ProviderValidationFailureCode


NewApiDiscoveryResult = NewApiDiscoverySuccess | NewApiDiscoveryFailure


async def resolve_host_addresses(host: str) -> tuple[str, ...]:
    loop: Final = asyncio.get_running_loop()
    records: Final = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple(sorted(frozenset(str(record[4][0]) for record in records)))


def normalize_new_api_api_base(value: str) -> str | None:
    candidate: Final = value.strip().rstrip("/")
    if not candidate:
        return None
    try:
        parsed: Final = urlsplit(candidate)
        port: Final = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname is None:
        return None
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        return None
    host: Final = parsed.hostname.lower()
    formatted_host: Final = f"[{host}]" if ":" in host else host
    authority: Final = f"{formatted_host}:{port}" if port is not None else formatted_host
    path: Final = parsed.path.rstrip("/")
    return urlunsplit(("https", authority, path, "", ""))


def new_api_admin_base(api_base: str) -> str:
    parsed: Final = urlsplit(api_base)
    path: Final = parsed.path.rstrip("/")
    admin_path: Final = path.removesuffix("/v1")
    return urlunsplit((parsed.scheme, parsed.netloc, admin_path.rstrip("/"), "", ""))


async def fetch_new_api_discovery(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    resolve_host: HostResolver = resolve_host_addresses,
) -> NewApiDiscoveryResult:
    normalized: Final = normalize_new_api_api_base(api_base)
    if normalized is None:
        return NewApiDiscoveryFailure(
            api_base=api_base,
            message="New API 模块只允许不含用户信息、查询参数或片段的 HTTPS URL",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    host: Final = urlsplit(normalized).hostname
    if host is None:
        return NewApiDiscoveryFailure(
            api_base=api_base,
            message="New API URL 缺少主机名",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    addresses: Final = await _safe_resolve(resolve_host=resolve_host, host=host)
    if addresses is None or not addresses or any(not _is_public_address(address) for address in addresses):
        return NewApiDiscoveryFailure(
            api_base=normalized,
            message="New API 地址必须解析到公网 IP",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    models: Final = await _fetch_models(client=client, url=f"{normalized}/models", api_key=api_key)
    if isinstance(models, NewApiDiscoveryFailure):
        return models
    pricing: Final = await _fetch_pricing(client=client, url=f"{new_api_admin_base(normalized)}/api/pricing", api_key=api_key)
    if isinstance(pricing, NewApiDiscoveryFailure):
        return pricing
    return NewApiDiscoverySuccess(api_base=normalized, models=models.models, pricing=pricing.pricing)


@dataclass(frozen=True, slots=True)
class _ModelsPayload:
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PricingPayload:
    pricing: tuple[NewApiPricedModel, ...]


async def _fetch_models(client: httpx.AsyncClient, url: str, api_key: str) -> _ModelsPayload | NewApiDiscoveryFailure:
    try:
        async with client.stream(
            "GET",
            url,
            headers={"authorization": f"Bearer {api_key}", "accept": "application/json"},
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                return NewApiDiscoveryFailure(
                    api_base=url,
                    message="模型列表接口不允许重定向",
                    code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
                )
            if response.status_code in {401, 403}:
                return NewApiDiscoveryFailure(
                    api_base=url,
                    message="API Key 无效或没有模型列表权限",
                    code=ProviderValidationFailureCode.AUTHENTICATION,
                )
            if response.status_code >= 400:
                failure_code: Final = (
                    ProviderValidationFailureCode.TRANSPORT
                    if response.status_code == 429 or response.status_code >= 500
                    else ProviderValidationFailureCode.UPSTREAM_RESPONSE
                )
                return NewApiDiscoveryFailure(
                    api_base=url,
                    message=f"模型列表返回 HTTP {response.status_code}",
                    code=failure_code,
                )
            content: Final = await read_limited_response(response, _MAX_MODELS_RESPONSE_BYTES)
    except httpx.HTTPError:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="无法连接 New API 模型列表接口",
            code=ProviderValidationFailureCode.TRANSPORT,
        )
    if content is None:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="模型列表响应超过 1 MiB 限制",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    try:
        parsed: Final = NewApiModelList.model_validate_json(content)
        payload: Final = _ModelsPayload(models=tuple(sorted(frozenset(item.id for item in parsed.data))))
    except ValidationError:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="模型列表响应格式无法识别",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    if not payload.models:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="当前 API Key 没有可见模型",
            code=ProviderValidationFailureCode.NO_MODELS,
        )
    return payload


async def _fetch_pricing(client: httpx.AsyncClient, url: str, api_key: str) -> _PricingPayload | NewApiDiscoveryFailure:
    try:
        async with client.stream(
            "GET",
            url,
            headers={"authorization": f"Bearer {api_key}", "accept": "application/json"},
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                return NewApiDiscoveryFailure(
                    api_base=url,
                    message="倍率价格接口不允许重定向",
                    code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
                )
            if response.status_code in {401, 403}:
                return NewApiDiscoveryFailure(
                    api_base=url,
                    message="API Key 无效或没有倍率价格读取权限",
                    code=ProviderValidationFailureCode.AUTHENTICATION,
                )
            if response.status_code >= 400:
                failure_code: Final = (
                    ProviderValidationFailureCode.TRANSPORT
                    if response.status_code == 429 or response.status_code >= 500
                    else ProviderValidationFailureCode.UPSTREAM_RESPONSE
                )
                return NewApiDiscoveryFailure(
                    api_base=url,
                    message=f"倍率价格接口返回 HTTP {response.status_code}",
                    code=failure_code,
                )
            content: Final = await read_limited_response(response, _MAX_PRICING_RESPONSE_BYTES)
    except httpx.HTTPError:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="无法连接 New API 倍率价格接口",
            code=ProviderValidationFailureCode.TRANSPORT,
        )
    if content is None:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="倍率价格响应超过 1 MiB 限制",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    try:
        payload: Final = NewApiPricingResponse.model_validate_json(content)
    except ValidationError:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="倍率价格响应格式无法识别",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    if not payload.success:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="倍率价格接口返回失败",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    pricing: Final = tuple(NewApiPricedModel(model=model, entry=entry) for model, entry in payload.data.items())
    return _PricingPayload(pricing=pricing)


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
