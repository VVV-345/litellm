"""按厂商协议安全请求资源侧模型列表。"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, TypeAlias
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from account_pool.provider_services.http_response import read_limited_response
from account_pool.provider_services.safe_http import (
    ValidatedAddressClientPolicy,
    create_validated_address_client,
)
from account_pool.upstream_providers.catalog import ModelListingProtocol
from account_pool.upstream_providers.models import UpstreamModelDiscoveryFailureCode
from account_pool.upstream_providers.schemas import (
    AnthropicModelList,
    GeminiModelList,
    OllamaModelList,
    OpenAICompatibleModelList,
)

HostResolver: TypeAlias = Callable[[str], Awaitable[tuple[str, ...]]]
HttpClientFactory: TypeAlias = Callable[
    [tuple[str, ...], httpx.Timeout, ValidatedAddressClientPolicy | None], httpx.AsyncClient
]
_MAX_MODELS_RESPONSE_BYTES: Final = 1_048_576
_MAX_PAGES: Final = 20


@dataclass(frozen=True, slots=True)
class UpstreamModelsSuccess:
    api_base: str
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UpstreamModelsFailure:
    api_base: str
    message: str
    code: UpstreamModelDiscoveryFailureCode


UpstreamModelsResult: TypeAlias = UpstreamModelsSuccess | UpstreamModelsFailure


async def resolve_host_addresses(host: str) -> tuple[str, ...]:
    loop: Final = asyncio.get_running_loop()
    records: Final = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple(sorted(frozenset(str(record[4][0]) for record in records)))


def normalize_api_base(value: str, default_api_base: str) -> str | None:
    candidate: Final = value.strip().rstrip("/") or default_api_base.rstrip("/")
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
    return urlunsplit(("https", authority, parsed.path.rstrip("/"), "", ""))


async def fetch_upstream_models(
    protocol: ModelListingProtocol,
    api_base: str,
    default_api_base: str,
    api_key: str,
    resolve_host: HostResolver = resolve_host_addresses,
    client_factory: HttpClientFactory | None = None,
    client_policy: ValidatedAddressClientPolicy | None = None,
    timeout: httpx.Timeout | None = None,
) -> UpstreamModelsResult:
    normalized: Final = normalize_api_base(api_base, default_api_base)
    if normalized is None:
        return UpstreamModelsFailure(
            api_base=api_base,
            message="上游地址必须是无用户信息、查询参数或片段的 HTTPS URL",
            code=UpstreamModelDiscoveryFailureCode.INVALID_CONFIGURATION,
        )
    host: Final = urlsplit(normalized).hostname
    if host is None:
        return UpstreamModelsFailure(
            api_base=api_base,
            message="上游地址缺少主机名",
            code=UpstreamModelDiscoveryFailureCode.INVALID_CONFIGURATION,
        )
    addresses: Final = await _safe_resolve(resolve_host, host)
    if addresses is None or not addresses or any(not _is_public_address(address) for address in addresses):
        return UpstreamModelsFailure(
            api_base=normalized,
            message="上游地址必须解析到公网 IP",
            code=UpstreamModelDiscoveryFailureCode.INVALID_CONFIGURATION,
        )
    factory: Final = client_factory or _default_client_factory
    discovery_timeout: Final = httpx.Timeout(120, connect=5) if timeout is None else timeout
    client: Final = factory(addresses, discovery_timeout, client_policy)
    try:
        return await _fetch_by_protocol(client, protocol, normalized, api_key)
    finally:
        await client.aclose()


def _default_client_factory(
    addresses: tuple[str, ...],
    timeout: httpx.Timeout,
    policy: ValidatedAddressClientPolicy | None,
) -> httpx.AsyncClient:
    return create_validated_address_client(timeout, addresses, policy)


async def _fetch_by_protocol(
    client: httpx.AsyncClient,
    protocol: ModelListingProtocol,
    api_base: str,
    api_key: str,
) -> UpstreamModelsResult:
    match protocol:
        case ModelListingProtocol.OPENAI_COMPATIBLE:
            return await _fetch_openai_compatible_models(client, api_base, api_key)
        case ModelListingProtocol.ANTHROPIC:
            return await _fetch_anthropic_models(client, api_base, api_key)
        case ModelListingProtocol.GEMINI:
            return await _fetch_gemini_models(client, api_base, api_key)
        case ModelListingProtocol.OLLAMA:
            return await _fetch_ollama_models(client, api_base)


async def _fetch_openai_compatible_models(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
) -> UpstreamModelsResult:
    response: Final = await _get_json(
        client,
        f"{api_base}/models",
        headers={"authorization": f"Bearer {api_key}", "accept": "application/json"},
    )
    if isinstance(response, UpstreamModelsFailure):
        return response
    try:
        payload: Final = OpenAICompatibleModelList.model_validate_json(response)
    except ValidationError:
        return _invalid_response(api_base)
    return _models_or_failure(api_base, tuple(item.id for item in payload.data))


async def _fetch_anthropic_models(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    after_id: str | None = None,
    pages_remaining: int = _MAX_PAGES,
) -> UpstreamModelsResult:
    params: Final = {"limit": "1000", **({"after_id": after_id} if after_id is not None else {})}
    response: Final = await _get_json(
        client,
        f"{api_base}/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "accept": "application/json",
        },
        params=params,
    )
    if isinstance(response, UpstreamModelsFailure):
        return response
    try:
        payload: Final = AnthropicModelList.model_validate_json(response)
    except ValidationError:
        return _invalid_response(api_base)
    page_models: Final = tuple(item.id for item in payload.data)
    if not payload.has_more:
        return _models_or_failure(api_base, page_models)
    if payload.last_id is None or pages_remaining <= 1:
        return UpstreamModelsFailure(
            api_base=api_base,
            message="Anthropic 模型列表分页响应不完整",
            code=UpstreamModelDiscoveryFailureCode.UPSTREAM_RESPONSE,
        )
    remaining: Final = await _fetch_anthropic_models(
        client,
        api_base,
        api_key,
        after_id=payload.last_id,
        pages_remaining=pages_remaining - 1,
    )
    if isinstance(remaining, UpstreamModelsFailure):
        return remaining
    return _models_or_failure(api_base, (*page_models, *remaining.models))


async def _fetch_gemini_models(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    page_token: str | None = None,
    pages_remaining: int = _MAX_PAGES,
) -> UpstreamModelsResult:
    params: Final = {"key": api_key, **({"pageToken": page_token} if page_token is not None else {})}
    response: Final = await _get_json(client, f"{api_base}/models", params=params)
    if isinstance(response, UpstreamModelsFailure):
        return response
    try:
        payload: Final = GeminiModelList.model_validate_json(response)
    except ValidationError:
        return _invalid_response(api_base)
    page_models: Final = tuple(item.name.removeprefix("models/") for item in payload.models)
    if payload.next_page_token is None:
        return _models_or_failure(api_base, page_models)
    if pages_remaining <= 1:
        return UpstreamModelsFailure(
            api_base=api_base,
            message="Gemini 模型列表分页超出安全上限",
            code=UpstreamModelDiscoveryFailureCode.UPSTREAM_RESPONSE,
        )
    remaining: Final = await _fetch_gemini_models(
        client,
        api_base,
        api_key,
        page_token=payload.next_page_token,
        pages_remaining=pages_remaining - 1,
    )
    if isinstance(remaining, UpstreamModelsFailure):
        return remaining
    return _models_or_failure(api_base, (*page_models, *remaining.models))


async def _fetch_ollama_models(client: httpx.AsyncClient, api_base: str) -> UpstreamModelsResult:
    response: Final = await _get_json(client, f"{api_base}/api/tags")
    if isinstance(response, UpstreamModelsFailure):
        return response
    try:
        payload: Final = OllamaModelList.model_validate_json(response)
    except ValidationError:
        return _invalid_response(api_base)
    return _models_or_failure(api_base, tuple(item.name for item in payload.models))


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> bytes | UpstreamModelsFailure:
    try:
        async with client.stream("GET", url, headers=headers, params=params, follow_redirects=False) as response:
            if response.is_redirect:
                return UpstreamModelsFailure(
                    api_base=url,
                    message="模型列表接口不允许重定向",
                    code=UpstreamModelDiscoveryFailureCode.UPSTREAM_RESPONSE,
                )
            if response.status_code in {401, 403}:
                return UpstreamModelsFailure(
                    api_base=url,
                    message="API Key 无效或没有模型列表权限",
                    code=UpstreamModelDiscoveryFailureCode.AUTHENTICATION,
                )
            if response.status_code >= 400:
                return UpstreamModelsFailure(
                    api_base=url,
                    message=f"模型列表返回 HTTP {response.status_code}",
                    code=(
                        UpstreamModelDiscoveryFailureCode.TRANSPORT
                        if response.status_code == 429 or response.status_code >= 500
                        else UpstreamModelDiscoveryFailureCode.UPSTREAM_RESPONSE
                    ),
                )
            content: Final = await read_limited_response(response, _MAX_MODELS_RESPONSE_BYTES)
    except httpx.HTTPError:
        return UpstreamModelsFailure(
            api_base=url,
            message="无法连接上游模型列表接口",
            code=UpstreamModelDiscoveryFailureCode.TRANSPORT,
        )
    if content is None:
        return UpstreamModelsFailure(
            api_base=url,
            message="模型列表响应超过 1 MiB 限制",
            code=UpstreamModelDiscoveryFailureCode.UPSTREAM_RESPONSE,
        )
    return content


def _invalid_response(api_base: str) -> UpstreamModelsFailure:
    return UpstreamModelsFailure(
        api_base=api_base,
        message="模型列表响应格式无法识别",
        code=UpstreamModelDiscoveryFailureCode.UPSTREAM_RESPONSE,
    )


def _models_or_failure(api_base: str, values: tuple[str, ...]) -> UpstreamModelsResult:
    models: Final = tuple(sorted(frozenset(model for model in values if model)))
    if models:
        return UpstreamModelsSuccess(api_base=api_base, models=models)
    return UpstreamModelsFailure(
        api_base=api_base,
        message="当前 API Key 没有可见模型",
        code=UpstreamModelDiscoveryFailureCode.NO_MODELS,
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
