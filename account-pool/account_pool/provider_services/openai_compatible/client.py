"""安全请求 OpenAI 兼容模型列表，并返回类型化成功或失败结果。"""

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
from account_pool.provider_services.openai_compatible.manifest import OPENAI_COMPATIBLE_API_BASE
from account_pool.provider_services.openai_compatible.schemas import OpenAICompatibleModelList

HostResolver = Callable[[str], Awaitable[tuple[str, ...]]]
_MAX_MODELS_RESPONSE_BYTES: Final = 1_048_576


@dataclass(frozen=True, slots=True)
class OpenAICompatibleModelsSuccess:
    api_base: str
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpenAICompatibleModelsFailure:
    api_base: str
    message: str
    code: ProviderValidationFailureCode


OpenAICompatibleModelsResult = OpenAICompatibleModelsSuccess | OpenAICompatibleModelsFailure


async def resolve_host_addresses(host: str) -> tuple[str, ...]:
    loop: Final = asyncio.get_running_loop()
    records: Final = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple(sorted(frozenset(str(record[4][0]) for record in records)))


def normalize_openai_compatible_api_base(value: str) -> str | None:
    candidate: Final = value.strip().rstrip("/") or OPENAI_COMPATIBLE_API_BASE
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


async def fetch_openai_compatible_models(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    resolve_host: HostResolver = resolve_host_addresses,
) -> OpenAICompatibleModelsResult:
    normalized: Final = normalize_openai_compatible_api_base(api_base)
    if normalized is None:
        return OpenAICompatibleModelsFailure(
            api_base=api_base,
            message="OpenAI 兼容模块只允许不含用户信息、查询参数或片段的 HTTPS URL",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    host: Final = urlsplit(normalized).hostname
    if host is None:
        return OpenAICompatibleModelsFailure(
            api_base=api_base,
            message="OpenAI 兼容 URL 缺少主机名",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    # 在携带渠道密钥前拒绝内网解析结果，避免用户配置把校验请求引向内部服务。
    addresses: Final = await _safe_resolve(resolve_host=resolve_host, host=host)
    if addresses is None or not addresses or any(not _is_public_address(address) for address in addresses):
        return OpenAICompatibleModelsFailure(
            api_base=normalized,
            message="OpenAI 兼容地址必须解析到公网 IP",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    try:
        async with client.stream(
            "GET",
            f"{normalized}/models",
            headers={"authorization": f"Bearer {api_key}", "accept": "application/json"},
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                return OpenAICompatibleModelsFailure(
                    api_base=normalized,
                    message="模型列表接口不允许重定向",
                    code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
                )
            if response.status_code in {401, 403}:
                return OpenAICompatibleModelsFailure(
                    api_base=normalized,
                    message="API Key 无效或没有模型列表权限",
                    code=ProviderValidationFailureCode.AUTHENTICATION,
                )
            if response.status_code >= 400:
                failure_code: Final = (
                    ProviderValidationFailureCode.TRANSPORT
                    if response.status_code == 429 or response.status_code >= 500
                    else ProviderValidationFailureCode.UPSTREAM_RESPONSE
                )
                return OpenAICompatibleModelsFailure(
                    api_base=normalized,
                    message=f"模型列表返回 HTTP {response.status_code}",
                    code=failure_code,
                )
            content: Final = await read_limited_response(response, _MAX_MODELS_RESPONSE_BYTES)
    except httpx.HTTPError:
        return OpenAICompatibleModelsFailure(
            api_base=normalized,
            message="无法连接 OpenAI 兼容模型列表接口",
            code=ProviderValidationFailureCode.TRANSPORT,
        )
    if content is None:
        return OpenAICompatibleModelsFailure(
            api_base=normalized,
            message="模型列表响应超过 1 MiB 限制",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    try:
        payload: Final = OpenAICompatibleModelList.model_validate_json(content)
    except ValidationError:
        return OpenAICompatibleModelsFailure(
            api_base=normalized,
            message="模型列表响应格式无法识别",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    models: Final = tuple(sorted(frozenset(item.id for item in payload.data)))
    if not models:
        return OpenAICompatibleModelsFailure(
            api_base=normalized,
            message="当前 API Key 没有可见模型",
            code=ProviderValidationFailureCode.NO_MODELS,
        )
    return OpenAICompatibleModelsSuccess(api_base=normalized, models=models)


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
