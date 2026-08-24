"""安全请求 New API 网关的模型列表与倍率价格，并返回类型化成功或失败结果。"""

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
from account_pool.provider_services.http_response import read_limited_response
from account_pool.provider_services.new_api.schemas import (
    NewApiLoginResponse,
    NewApiModelList,
    NewApiPricingEntry,
    NewApiPricingResponse,
)

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
            raise httpcore.ConnectError("New API host did not resolve to an address")
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
            raise httpcore.ProtocolError("New API response stream must be asynchronous")
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
class _NewApiSession:
    cookie_header: str
    user_id: int | None


@dataclass(frozen=True, slots=True)
class NewApiPricedModel:
    model: str
    entry: NewApiPricingEntry


@dataclass(frozen=True, slots=True)
class NewApiDiscoverySuccess:
    api_base: str
    models: tuple[str, ...]
    pricing: tuple[NewApiPricedModel, ...]
    pricing_failure_code: PricingDiscoveryFailureCode | None = None


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
    username: str | None = None,
    password: str | None = None,
    discovery_client_factory: HttpClientFactory | None = None,
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
    if username is not None or password is not None:
        if username is None or password is None:
            return NewApiDiscoveryFailure(
                api_base=normalized,
                message="管理员账号与密码必须同时提供",
                code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
            )
        return await _fetch_new_api_discovery_with_login(
            normalized=normalized,
            api_key=api_key,
            username=username,
            password=password,
            addresses=addresses,
            login_client_factory=_login_client_factory(client, discovery_client_factory),
        )
    return await _fetch_new_api_discovery_with_key(
        normalized=normalized,
        api_key=api_key,
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


async def _fetch_new_api_discovery_with_key(
    normalized: str,
    api_key: str,
    addresses: tuple[str, ...],
    client_factory: HttpClientFactory,
) -> NewApiDiscoveryResult:
    key_client: Final = client_factory(addresses)
    try:
        models: Final = await _fetch_models(client=key_client, url=f"{normalized}/models", api_key=api_key)
        if isinstance(models, NewApiDiscoveryFailure):
            return models
        pricing: Final = await _fetch_pricing(
            client=key_client,
            url=f"{new_api_admin_base(normalized)}/api/pricing",
            api_key=api_key,
            session=None,
        )
        return _models_with_pricing(normalized, models, pricing)
    finally:
        await key_client.aclose()


async def _fetch_new_api_discovery_with_login(
    normalized: str,
    api_key: str,
    username: str,
    password: str,
    addresses: tuple[str, ...],
    login_client_factory: HttpClientFactory,
) -> NewApiDiscoveryResult:
    login_client: Final = login_client_factory(addresses)
    try:
        models: Final = await _fetch_models(client=login_client, url=f"{normalized}/models", api_key=api_key)
        if isinstance(models, NewApiDiscoveryFailure):
            return models
        admin_base: Final = new_api_admin_base(normalized)
        session: Final = await _authenticate(
            client=login_client,
            admin_base=admin_base,
            username=username,
            password=password,
        )
        pricing: Final = await _fetch_pricing(
            client=login_client,
            url=f"{admin_base}/api/pricing",
            api_key=api_key,
            session=session,
        )
        return _models_with_pricing(normalized, models, pricing)
    finally:
        await login_client.aclose()


def _models_with_pricing(
    normalized: str,
    models: _ModelsPayload,
    pricing: _PricingPayload | NewApiDiscoveryFailure,
) -> NewApiDiscoveryResult:
    if isinstance(pricing, NewApiDiscoveryFailure):
        return NewApiDiscoverySuccess(
            api_base=normalized,
            models=models.models,
            pricing=(),
            pricing_failure_code=_pricing_failure_code(pricing.code),
        )
    return NewApiDiscoverySuccess(api_base=normalized, models=models.models, pricing=pricing.pricing)


def _pricing_failure_code(code: ProviderValidationFailureCode) -> PricingDiscoveryFailureCode:
    match code:
        case ProviderValidationFailureCode.AUTHENTICATION:
            return PricingDiscoveryFailureCode.AUTHENTICATION
        case ProviderValidationFailureCode.TRANSPORT:
            return PricingDiscoveryFailureCode.TRANSPORT
        case ProviderValidationFailureCode.UPSTREAM_RESPONSE:
            return PricingDiscoveryFailureCode.UPSTREAM_RESPONSE
        case _:
            raise ValueError(f"unsupported New API pricing failure code: {code}")


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


async def _fetch_pricing(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    session: _NewApiSession | NewApiDiscoveryFailure | None,
) -> _PricingPayload | NewApiDiscoveryFailure:
    if isinstance(session, NewApiDiscoveryFailure):
        return session
    headers: Final = _pricing_headers(api_key=api_key, session=session)
    try:
        async with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
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


def _pricing_headers(api_key: str, session: _NewApiSession | None) -> dict[str, str]:
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
) -> _NewApiSession | NewApiDiscoveryFailure:
    return await _login(client=client, url=f"{admin_base}/api/user/login", username=username, password=password)


async def _login(
    client: httpx.AsyncClient,
    url: str,
    username: str,
    password: str,
) -> _NewApiSession | NewApiDiscoveryFailure:
    return await _login_with_client(client, url, username, password)


async def _login_with_client(
    client: httpx.AsyncClient,
    url: str,
    username: str,
    password: str,
) -> _NewApiSession | NewApiDiscoveryFailure:
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
                return NewApiDiscoveryFailure(
                    api_base=url,
                    message="登录接口不允许重定向",
                    code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
                )
            if response.status_code in {401, 403}:
                return NewApiDiscoveryFailure(
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
                return NewApiDiscoveryFailure(
                    api_base=url,
                    message=f"登录接口返回 HTTP {response.status_code}",
                    code=failure_code,
                )
            content: Final = await read_limited_response(response, _MAX_LOGIN_RESPONSE_BYTES)
    except httpx.HTTPError:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="无法连接 New API 登录接口",
            code=ProviderValidationFailureCode.TRANSPORT,
        )
    if content is None:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="登录响应超过 64 KiB 限制",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    try:
        parsed: Final = NewApiLoginResponse.model_validate_json(content)
    except ValidationError:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="登录响应格式无法识别",
            code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        )
    if not parsed.success or not cookie_header:
        return NewApiDiscoveryFailure(
            api_base=url,
            message="管理员登录失败或未返回会话",
            code=ProviderValidationFailureCode.AUTHENTICATION,
        )
    return _NewApiSession(cookie_header=cookie_header, user_id=None if parsed.data is None else parsed.data.id)


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
