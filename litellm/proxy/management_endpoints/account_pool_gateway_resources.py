"""本模块统一管理网关连接池、请求上限和取消后的有界租约清理。"""

from __future__ import annotations

import asyncio
import os
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


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw: Final = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, min(maximum, int(raw)))
    except ValueError:
        return default


def http_client(proxy: str | None = None) -> httpx.AsyncClient:
    max_connections: Final = _env_int("ACCOUNT_POOL_GATEWAY_MAX_CONNECTIONS", 100, 20, 2000)
    max_keepalive: Final = min(max_connections, max(20, max_connections // 2))
    return httpx.AsyncClient(
        proxy=proxy,
        timeout=120,
        trust_env=False,
        follow_redirects=False,
        cookies=CookieJar(policy=_RejectCookies()),
        limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_keepalive),
    )


_RESOURCES: Final[ContextVar[GatewayHTTPResources | None]] = ContextVar("account_pool_http_resources", default=None)


async def _wait_cleanup(task: asyncio.Task[None]) -> None:
    cancelled: asyncio.CancelledError | None = None  # rebind-ok: preserve the owning cancel scope while cleanup drains
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                cancelled = error
        task.result()
    if cancelled is not None:
        raise cancelled


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
        proxy_limit: int | None = None,
    ) -> None:
        self.control: Final = client_factory()
        self.direct: Final = client_factory()
        for client in (self.control, self.direct):
            client.cookies.clear()
            client.cookies.jar.set_policy(_RejectCookies())
        self.proxy_factory: Final = proxy_factory
        self.proxy_limit: Final = (
            proxy_limit if proxy_limit is not None else _env_int("ACCOUNT_POOL_GATEWAY_PROXY_LIMIT", 64, 4, 256)
        )
        self.proxies: Final[dict[str, tuple[httpx.AsyncClient, int]]] = {}  # mutable-ok: live proxy leases
        self.available: Final = asyncio.Condition()
        self.admission: Final = asyncio.Semaphore(_env_int("ACCOUNT_POOL_GATEWAY_MAX_ADMISSION", 100, 10, 2000))
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
        retired_clients: list[httpx.AsyncClient] = []
        async with self.available:
            while proxy not in self.proxies and len(self.proxies) >= self.proxy_limit:
                idle: Final = next((key for key, (_, count) in self.proxies.items() if count == 0), None)
                if idle is not None:
                    retired, _ = self.proxies.pop(idle)
                    retired_clients.append(retired)
                    self.available.notify_all()
                    break
                await self.available.wait()
            if proxy not in self.proxies:
                created: Final = self.proxy_factory(proxy)
                created.cookies.clear()
                created.cookies.jar.set_policy(_RejectCookies())
                self.proxies[proxy] = (created, 0)
            client, count = self.proxies.pop(proxy)
            self.proxies[proxy] = (client, count + 1)
        # 在释放条件锁之后关闭淘汰的代理客户端，避免 5 秒关闭超时阻塞所有并发代理获取。
        if retired_clients:
            with self.bind():
                await managed_cleanup(close_clients(tuple(retired_clients)))
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
