"""提供连接到已验证公网地址的无代理 HTTPS 客户端。"""

from __future__ import annotations

import ssl
import time
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Final, Protocol, TypeAlias, runtime_checkable

import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.base import SOCKET_OPTION, AsyncNetworkStream
from httpx._transports.default import map_httpcore_exceptions


class _ValidatedAddressBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        addresses: tuple[str, ...],
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
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
            raise httpcore.ConnectError("provider host did not resolve to an address")
        last_failure: httpcore.ConnectError | httpcore.ConnectTimeout | None = (
            None  # rebind-ok: tracks final address failure
        )
        deadline = (
            None if timeout is None else time.monotonic() + timeout
        )  # rebind-ok: deadline is immutable after initialization
        for address in self._addresses:
            remaining_timeout = (
                None if deadline is None else deadline - time.monotonic()
            )  # rebind-ok: immutable remaining timeout per address
            if remaining_timeout is not None and remaining_timeout <= 0:
                raise httpcore.ConnectTimeout("provider host connection timed out")
            try:
                return await self._backend.connect_tcp(
                    host=address,
                    port=port,
                    timeout=remaining_timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as failure:
                last_failure = failure
        if last_failure is None:
            raise httpcore.ConnectError("provider host did not resolve to an address")
        raise last_failure

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        return await self._backend.connect_unix_socket(path=path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


@dataclass(frozen=True, slots=True)
class ValidatedAddressClientPolicy:
    ssl_context: ssl.SSLContext
    http1: bool
    http2: bool
    max_connections: int
    max_keepalive_connections: int
    keepalive_expiry: float | None
    retries: int
    local_address: str | None
    socket_options: Iterable[SOCKET_OPTION] | None


HttpClientPolicyResult: TypeAlias = ValidatedAddressClientPolicy | str


def validated_address_client_policy(client: httpx.AsyncClient) -> HttpClientPolicyResult:
    mounts: Final = client._mounts  # pyright: ignore[reportPrivateUsage]  # HTTPX has no public mount policy API
    if any(transport is not None for transport in mounts.values()):
        return "HTTP client mounts or proxies cannot be used for DNS-pinned discovery"
    if not isinstance(client._transport, httpx.AsyncHTTPTransport):  # pyright: ignore[reportPrivateUsage]  # HTTPX has no public transport API
        return "HTTP client transport cannot be used for DNS-pinned discovery"
    pool: Final = client._transport._pool  # pyright: ignore[reportPrivateUsage]  # HTTPX exposes no transport configuration API
    if pool._proxy is not None or pool._uds is not None:  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no transport configuration API
        return "HTTP client proxy or Unix socket transport cannot be used for DNS-pinned discovery"
    if pool._ssl_context is None:  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no TLS policy API
        return "HTTP client TLS policy cannot be used for DNS-pinned discovery"
    return ValidatedAddressClientPolicy(
        ssl_context=pool._ssl_context,  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no TLS policy API
        http1=pool._http1,  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no protocol policy API
        http2=pool._http2,  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no protocol policy API
        max_connections=pool._max_connections,  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no limits API
        max_keepalive_connections=pool._max_keepalive_connections,  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no limits API
        keepalive_expiry=pool._keepalive_expiry,  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no keepalive API
        retries=pool._retries,  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no retry policy API
        local_address=pool._local_address,  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no bind API
        socket_options=pool._socket_options,  # pyright: ignore[reportPrivateUsage]  # HTTP core exposes no socket policy API
    )


class ValidatedAddressTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        addresses: tuple[str, ...],
        backend: httpcore.AsyncNetworkBackend | None = None,
        ssl_context: ssl.SSLContext | None = None,
        *,
        policy: ValidatedAddressClientPolicy | None = None,
    ) -> None:
        effective_policy: Final = policy
        self._pool: Final = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context if effective_policy is None else effective_policy.ssl_context,
            max_connections=10 if effective_policy is None else effective_policy.max_connections,
            max_keepalive_connections=None if effective_policy is None else effective_policy.max_keepalive_connections,
            keepalive_expiry=None if effective_policy is None else effective_policy.keepalive_expiry,
            http1=True if effective_policy is None else effective_policy.http1,
            http2=False if effective_policy is None else effective_policy.http2,
            retries=0 if effective_policy is None else effective_policy.retries,
            local_address=None if effective_policy is None else effective_policy.local_address,
            network_backend=_ValidatedAddressBackend(addresses, backend),
            socket_options=None if effective_policy is None else effective_policy.socket_options,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        with map_httpcore_exceptions():
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
            if not isinstance(response.stream, AsyncIterable):
                raise httpx.ProtocolError("provider response stream must be asynchronous")
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_AsyncResponseStream(response.stream),
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
        with map_httpcore_exceptions():
            async for part in self._stream:
                yield part

    async def aclose(self) -> None:
        if isinstance(self._stream, _AsyncClosable):
            await self._stream.aclose()


def create_validated_address_client(
    timeout: httpx.Timeout,
    addresses: tuple[str, ...],
    policy: ValidatedAddressClientPolicy | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        transport=ValidatedAddressTransport(
            addresses,
            policy=policy,
        ),
        trust_env=False,
    )
