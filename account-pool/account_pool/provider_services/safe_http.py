"""提供连接到已验证公网地址的无代理 HTTPS 客户端。"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterable
from typing import Final, Protocol, runtime_checkable

import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.base import SOCKET_OPTION, AsyncNetworkStream


class _ValidatedAddressBackend(httpcore.AsyncNetworkBackend):
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
            raise httpcore.ConnectError("provider host did not resolve to an address")
        return await self._backend.connect_tcp(
            host=self._addresses[0],
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


class ValidatedAddressTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        addresses: tuple[str, ...],
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._pool: Final = httpcore.AsyncConnectionPool(
            network_backend=_ValidatedAddressBackend(addresses, backend),
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
        if not isinstance(response.stream, AsyncIterable):
            raise httpcore.ProtocolError("provider response stream must be asynchronous")
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
        async for part in self._stream:
            yield part

    async def aclose(self) -> None:
        if isinstance(self._stream, _AsyncClosable):
            await self._stream.aclose()


def create_validated_address_client(
    timeout: httpx.Timeout,
    addresses: tuple[str, ...],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        transport=ValidatedAddressTransport(addresses),
        trust_env=False,
    )
