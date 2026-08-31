"""安全请求 通用解析器 网关的模型列表与倍率价格，并返回类型化成功或失败结果。"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.base import SOCKET_OPTION, AsyncNetworkStream
from pydantic import ValidationError

from account_pool.domain.provider_source import PricingDiscoveryFailureCode, ProviderValidationFailureCode
from account_pool.provider_services.generic.schemas import (
    CompatibleLoginResponse,
    CompatibleModelList,
    CompatiblePricingEntry,
    CompatiblePricingResponse,
)
from account_pool.provider_services.http_response import read_limited_response

HostResolver = Callable[[str], Awaitable[tuple[str, ...]]]
HttpClientFactory = Callable[[tuple[str, ...]], httpx.AsyncClient]
_MAX_MODELS_RESPONSE_BYTES: Final = 1_048_576
_MAX_PRICING_RESPONSE_BYTES: Final = 1_048_576
_MAX_LOGIN_RESPONSE_BYTES: Final = 65_536


class _ResolvedAddressBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, addresses: tuple[str, ...], backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._addresses: Final = addresses
        self._backend: Final = AnyIOBackend() if backend is None else backend

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        if not self._addresses:
            raise httpcore.ConnectError("解析目标主机未解析到地址")
        address: Final = self._addresses[0]
        return await self._backend.connect_tcp(
            host=address,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        return await self._backend.connect_unix_socket(path=path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _ResolvedAddressTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        addresses: tuple[str, ...],
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._pool: Final = httpcore.AsyncConnectionPool(
            network_backend=_ResolvedAddressBackend(addresses, backend),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response: Final = await self._pool.handle_async_request(
            httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        stream: Final = response.stream
        if not isinstance(stream, AsyncIterable):
            raise httpcore.ProtocolError("解析目标响应流必须为异步流")
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_AsyncResponseStream(stream),
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


@runtime_checkable
class _AsyncClosable(Protocol):
    async def aclose(self) -> None: ...


class _AsyncResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: AsyncIterable[bytes]) -> None:
        self._stream: Final = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for part in self._stream:
            yield part

    async def aclose(self) -> None:
        if isinstance(self._stream, _AsyncClosable):
            await self._stream.aclose()


@dataclass(frozen=True, slots=True)
class _GenericSession:
    cookie_header: str
    user_id: int | None


@dataclass(frozen=True, slots=True)
class CompatiblePricedModel:
    model: str
    entry: CompatiblePricingEntry


@dataclass(frozen=True, slots=True)
class GenericDiscoverySuccess:
    api_base: str
    models: tuple[str, ...]
    pricing: tuple[CompatiblePricedModel, ...]
    group_ratios: dict[str, float]
    pricing_failure_code: PricingDiscoveryFailureCode | None = None


@dataclass(frozen=True, slots=True)
class GenericDiscoveryFailure:
    api_base: str
    message: str
    code: ProviderValidationFailureCode


GenericDiscoveryResult = GenericDiscoverySuccess | GenericDiscoveryFailure


async def resolve_host_addresses(host: str) -> tuple[str, ...]:
    loop: Final = asyncio.get_running_loop()
    records: Final = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple(sorted(frozenset(str(record[4][0]) for record in records)))


def normalize_generic_api_base(value: str) -> str | None:
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


def generic_admin_base(api_base: str) -> str:
    parsed: Final = urlsplit(api_base)
    path: Final = parsed.path.rstrip("/")
    admin_path: Final = path.removesuffix("/v1")
    return urlunsplit((parsed.scheme, parsed.netloc, admin_path.rstrip("/"), "", ""))


async def fetch_generic_discovery(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    resolve_host: HostResolver = resolve_host_addresses,
    username: str | None = None,
    password: str | None = None,
    discovery_client_factory: HttpClientFactory | None = None,
) -> GenericDiscoveryResult:
    normalized: Final = normalize_generic_api_base(api_base)
    if normalized is None:
        return GenericDiscoveryFailure(
            api_base=api_base,
            message="通用解析器仅允许不含用户信息、查询参数或片段的 HTTPS URL",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    host: Final = urlsplit(normalized).hostname
    if host is None:
        return GenericDiscoveryFailure(
            api_base=api_base,
            message="通用解析器 URL 缺少主机名",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    addresses: Final = await _safe_resolve(resolve_host=resolve_host, host=host)
    if addresses is None or not addresses or any(not _is_public_address(address) for address in addresses):
        return GenericDiscoveryFailure(
            api_base=normalized,
            message="通用解析器地址必须解析到公网 IP",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    if (username is None) != (password is None):
        return GenericDiscoveryFailure(
            api_base=normalized,
            message="管理员账号与密码必须同时提供",
            code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
        )
    return await _fetch_generic_discovery(
        normalized=normalized,
        api_key=api_key,
        username=username,
        password=password,
        addresses=addresses,
        client_factory=_login_client_factory(client, discovery_client_factory),
    )


def _key_client_factory(client: httpx.AsyncClient) -> HttpClientFactory:
    timeout: Final = client.timeout
    return lambda addresses: httpx.AsyncClient(
        timeout=timeout,
        transport=_ResolvedAddressTransport(addresses),
        trust_env=False,
    )


def _login_client_factory(
    client: httpx.AsyncClient,
    discovery_client_factory: HttpClientFactory | None,
) -> HttpClientFactory:
    if discovery_client_factory is not None:
        return discovery_client_factory
    return _key_client_factory(client)


async def _fetch_generic_discovery(
    normalized: str,
    api_key: str,
    username: str | None,
    password: str | None,
    addresses: tuple[str, ...],
    client_factory: HttpClientFactory,
) -> GenericDiscoveryResult:
    key_client: Final = client_factory(addresses)
    try:
        models: Final = await _fetch_models(client=key_client, url=f"{normalized}/models", api_key=api_key)
        if isinstance(models, GenericDiscoveryFailure):
            return models
        pricing: Final = await _fetch_pricing(
            client=key_client,
            url=f"{generic_admin_base(normalized)}/api/pricing",
            api_key=api_key,
            session=None,
        )
        if not isinstance(pricing, GenericDiscoveryFailure) or username is None or password is None:
            return _models_with_pricing(normalized, models, pricing)
        session: Final = await _authenticate(
            client=key_client,
            admin_base=generic_admin_base(normalized),
            username=username,
            password=password,
        )
        retried_pricing: Final = await _fetch_pricing(
            client=key_client,
            url=f"{generic_admin_base(normalized)}/api/pricing",
            api_key=api_key,
            session=session,
        )
        return _models_with_pricing(normalized, models, retried_pricing)
    finally:
        await key_client.aclose()


def _models_with_pricing(
    normalized: str,
    models: _ModelsPayload,
    pricing: _PricingPayload | GenericDiscoveryFailure,
) -> GenericDiscoveryResult:
    if isinstance(pricing, GenericDiscoveryFailure):
        return GenericDiscoverySuccess(
            api_base=normalized,
            models=models.models,
            pricing=(),
            group_ratios={},
            pricing_failure_code=_pricing_failure_code(pricing.code),
        )
    return GenericDiscoverySuccess(
        api_base=normalized,
        models=models.models,
        pricing=pricing.pricing,
        group_ratios=pricing.group_ratios,
    )


def _pricing_failure_code(code: ProviderValidationFailureCode) -> PricingDiscoveryFailureCode:
    match code:
        case ProviderValidationFailureCode.AUTHENTICATION:
            return PricingDiscoveryFailureCode.AUTHENTICATION
        case ProviderValidationFailureCode.TRANSPORT:
            return PricingDiscoveryFailureCode.TRANSPORT
        case ProviderValidationFailureCode.UPSTREAM_RESPONSE:
            return PricingDiscoveryFailureCode.UPSTREAM_RESPONSE
        case _:
            raise ValueError(f"不支持的通用解析器价格失败代码: {code}")


@dataclass(frozen=True, slots=True)
class _ModelsPayload:
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PricingPayload:
    pricing: tuple[CompatiblePricedModel, ...]
    group_ratios: dict[str, float]


async def _fetch_models(client: httpx.AsyncClient, url: str, api_key: str) -> _ModelsPayload | GenericDiscoveryFailure:
    try:
        async with client.stream(
            "GET",
            url,
            headers={"authorization": f"Bearer {api_key}", "accept": "application/json"},
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                return GenericDiscoveryFailure(
                    api_base=url,
                    message="模型列表接口不允许重定向",
                    code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
                )
            if response.status_code in {401, 403}:
                return GenericDiscoveryFailure(
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
                return GenericDiscoveryFailure(
                    api_base=url,
                    message=f"模型列表返回 HTTP {response.status_code}",
                    code=failure_code,
                )
            content: Final = await read_limited_response(response, _MAX_MODELS_RESPONSE_BYTES)
    except httpx.HTTPError:
        return GenericDiscoveryFailure(
            api_base=url,
            message="无法连接通用解析器的模型列表接口",
            code=ProviderValidationFailureCode.TRANSPORT,
        )
    if content is None:
        return GenericDiscoveryFailure(
            api_base=url,
            message="模型列表响应超过 1 MiB 限制",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    try:
        parsed: Final = CompatibleModelList.model_validate_json(content)
        payload: Final = _ModelsPayload(models=tuple(sorted(frozenset(item.id for item in parsed.data))))
    except ValidationError:
        return GenericDiscoveryFailure(
            api_base=url,
            message="模型列表响应格式无法识别",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    if not payload.models:
        return GenericDiscoveryFailure(
            api_base=url,
            message="当前 API Key 没有可见模型",
            code=ProviderValidationFailureCode.NO_MODELS,
        )
    return payload


async def _fetch_pricing(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    session: _GenericSession | GenericDiscoveryFailure | None,
) -> _PricingPayload | GenericDiscoveryFailure:
    if isinstance(session, GenericDiscoveryFailure):
        return session
    headers: Final = _pricing_headers(api_key=api_key, session=session)
    try:
        async with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            if response.is_redirect:
                return GenericDiscoveryFailure(
                    api_base=url,
                    message="倍率价格接口不允许重定向",
                    code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
                )
            if response.status_code in {401, 403}:
                return GenericDiscoveryFailure(
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
                return GenericDiscoveryFailure(
                    api_base=url,
                    message=f"倍率价格接口返回 HTTP {response.status_code}",
                    code=failure_code,
                )
            content: Final = await read_limited_response(response, _MAX_PRICING_RESPONSE_BYTES)
    except httpx.HTTPError:
        return GenericDiscoveryFailure(
            api_base=url,
            message="无法连接通用解析器的倍率价格接口",
            code=ProviderValidationFailureCode.TRANSPORT,
        )
    if content is None:
        return GenericDiscoveryFailure(
            api_base=url,
            message="倍率价格响应超过 1 MiB 限制",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    try:
        payload: Final = CompatiblePricingResponse.model_validate_json(content)
    except ValidationError:
        return GenericDiscoveryFailure(
            api_base=url,
            message="倍率价格响应格式无法识别",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    if not payload.success:
        return GenericDiscoveryFailure(
            api_base=url,
            message="倍率价格接口返回失败",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    pricing: Final = tuple(CompatiblePricedModel(model=model, entry=entry) for model, entry in payload.entries())
    return _PricingPayload(pricing=pricing, group_ratios=payload.group_ratio)


def _pricing_headers(api_key: str, session: _GenericSession | None) -> dict[str, str]:
    base: Final = {"authorization": f"Bearer {api_key}", "accept": "application/json"}
    if session is None:
        return base
    user_headers: Final = {} if session.user_id is None else {"new-api-user": str(session.user_id)}
    return {**base, "cookie": session.cookie_header, **user_headers}


async def _authenticate(
    client: httpx.AsyncClient,
    admin_base: str,
    username: str,
    password: str,
) -> _GenericSession | GenericDiscoveryFailure:
    return await _login(client=client, url=f"{admin_base}/api/user/login", username=username, password=password)


async def _login(
    client: httpx.AsyncClient,
    url: str,
    username: str,
    password: str,
) -> _GenericSession | GenericDiscoveryFailure:
    return await _login_with_client(client, url, username, password)


async def _login_with_client(
    client: httpx.AsyncClient,
    url: str,
    username: str,
    password: str,
) -> _GenericSession | GenericDiscoveryFailure:
    try:
        async with client.stream(
            "POST",
            url,
            json={"username": username, "password": password},
            headers={"accept": "application/json", "content-type": "application/json"},
            follow_redirects=False,
        ) as response:
            cookie_header: Final = "; ".join(f"{name}={value}" for name, value in response.cookies.items())
            if response.is_redirect:
                return GenericDiscoveryFailure(
                    api_base=url,
                    message="登录接口不允许重定向",
                    code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
                )
            if response.status_code in {401, 403}:
                return GenericDiscoveryFailure(
                    api_base=url,
                    message="管理员账号或密码无效",
                    code=ProviderValidationFailureCode.AUTHENTICATION,
                )
            if response.status_code >= 400:
                failure_code: Final = (
                    ProviderValidationFailureCode.TRANSPORT
                    if response.status_code == 429 or response.status_code >= 500
                    else ProviderValidationFailureCode.UPSTREAM_RESPONSE
                )
                return GenericDiscoveryFailure(
                    api_base=url,
                    message=f"登录接口返回 HTTP {response.status_code}",
                    code=failure_code,
                )
            content: Final = await read_limited_response(response, _MAX_LOGIN_RESPONSE_BYTES)
    except httpx.HTTPError:
        return GenericDiscoveryFailure(
            api_base=url,
            message="无法连接通用解析器的登录接口",
            code=ProviderValidationFailureCode.TRANSPORT,
        )
    if content is None:
        return GenericDiscoveryFailure(
            api_base=url,
            message="登录响应超过 64 KiB 限制",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    try:
        parsed: Final = CompatibleLoginResponse.model_validate_json(content)
    except ValidationError:
        return GenericDiscoveryFailure(
            api_base=url,
            message="登录响应格式无法识别",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    if not parsed.success or not cookie_header:
        return GenericDiscoveryFailure(
            api_base=url,
            message="管理员登录失败或未返回会话",
            code=ProviderValidationFailureCode.AUTHENTICATION,
        )
    return _GenericSession(cookie_header=cookie_header, user_id=None if parsed.data is None else parsed.data.id)


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
