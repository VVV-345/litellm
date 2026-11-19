"""本模块统一管理网关连接池、请求上限和取消后的有界租约清理。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from typing import Final
from urllib.request import Request

import anyio
import httpx

from litellm._logging import verbose_proxy_logger


class _RejectCookies(DefaultCookiePolicy):
    def set_ok(self, cookie: Cookie, request: Request) -> bool:
        return False

    def return_ok(self, cookie: Cookie, request: Request) -> bool:
        return False


def http_client(proxy: str | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        proxy=proxy,
        timeout=120,
        trust_env=False,
        follow_redirects=False,
        cookies=CookieJar(policy=_RejectCookies()),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )


_RESOURCES: Final[ContextVar[GatewayHTTPResources | None]] = ContextVar("account_pool_http_resources", default=None)


async def _wait_cleanup(task: asyncio.Task[None]) -> None:
    cancelled = False  # rebind-ok: remember cancellation until the owned cleanup task drains
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
    if cancelled:
        raise asyncio.CancelledError


async def managed_cleanup(operation: Coroutine[object, object, None]) -> None:
    task: Final = asyncio.create_task(operation)
    resources: Final = _RESOURCES.get()
    if resources is not None:
        resources.cleanup_tasks.add(task)
        task.add_done_callback(resources.cleanup_tasks.discard)
    await _wait_cleanup(task)


async def close_clients(clients: tuple[httpx.AsyncClient, ...]) -> None:
    try:
        async with asyncio.timeout(5):
            results: Final = await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                verbose_proxy_logger.warning("Account pool HTTP client cleanup failed: %s", result.__class__.__name__)
    except TimeoutError:
        verbose_proxy_logger.warning("Account pool HTTP client cleanup timed out")


class GatewayHTTPResources:
    def __init__(
        self,
        client_factory: Callable[[], httpx.AsyncClient] = http_client,
        proxy_factory: Callable[[str], httpx.AsyncClient] = http_client,
        proxy_limit: int = 16,
    ) -> None:
        self.control: Final = client_factory()
        self.direct: Final = client_factory()
        for client in (self.control, self.direct):
            client.cookies.clear()
            client.cookies.jar.set_policy(_RejectCookies())
        self.proxy_factory: Final = proxy_factory
        self.proxy_limit: Final = proxy_limit
        self.proxies: Final[dict[str, tuple[httpx.AsyncClient, int]]] = {}  # mutable-ok: live proxy leases
        self.available: Final = asyncio.Condition()
        self.admission: Final = asyncio.Semaphore(100)
        self.cleanup_tasks: Final[set[asyncio.Task[None]]] = set()  # mutable-ok: pending cleanup task ownership
        self._close_task: asyncio.Task[None] | None = None

    @contextmanager
    def bind(self) -> Iterator[GatewayHTTPResources]:
        token: Final = _RESOURCES.set(self)
        try:
            yield self
        finally:
            _RESOURCES.reset(token)

    @asynccontextmanager
    async def upstream(self, proxy: str | None) -> AsyncIterator[httpx.AsyncClient]:
        if proxy is None:
            yield self.direct
            return
        async with self.available:
            while proxy not in self.proxies and len(self.proxies) >= self.proxy_limit:
                idle: Final = next((key for key, (_, count) in self.proxies.items() if count == 0), None)
                if idle is not None:
                    retired, _ = self.proxies.pop(idle)
                    self.available.notify_all()
                    with self.bind():
                        await managed_cleanup(close_clients((retired,)))
                    break
                await self.available.wait()
            if proxy not in self.proxies:
                created: Final = self.proxy_factory(proxy)
                created.cookies.clear()
                created.cookies.jar.set_policy(_RejectCookies())
                self.proxies[proxy] = (created, 0)
            client, count = self.proxies.pop(proxy)
            self.proxies[proxy] = (client, count + 1)
        try:
            yield client
        finally:
            _, borrowed = self.proxies[proxy]
            self.proxies[proxy] = (client, borrowed - 1)
            with anyio.CancelScope(shield=True):
                async with self.available:
                    self.available.notify_all()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await _wait_cleanup(self._close_task)

    async def _close(self) -> None:
        with anyio.CancelScope(shield=True):
            while self.cleanup_tasks:
                for task in tuple(self.cleanup_tasks):
                    await _wait_cleanup(task)
            await close_clients((self.control, self.direct, *(client for client, _ in self.proxies.values())))


@asynccontextmanager
async def upstream_client(client: httpx.AsyncClient, proxy: str | None) -> AsyncIterator[httpx.AsyncClient]:
    resources: Final = _RESOURCES.get()
    if resources is not None:
        async with resources.upstream(proxy) as selected:
            yield selected
    elif proxy is None:
        yield client
    else:
        async with http_client(proxy) as selected:
            yield selected
