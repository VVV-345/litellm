"""启动隔离 OAuth 浏览器和只连接选定代理的出站转发器。"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import ssl
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Final, Protocol, cast
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class ProxyTarget:
    host: str
    port: int
    ssl_context: ssl.SSLContext | None


class BrowserPage(Protocol):
    def goto(self, url: str, *, wait_until: str, timeout: int) -> object: ...

    def wait_for_timeout(self, timeout: int) -> None: ...


class Browser(Protocol):
    def new_page(self) -> BrowserPage: ...

    def close(self) -> None: ...


class Chromium(Protocol):
    def launch(self, *, headless: bool, proxy: dict[str, str]) -> Browser: ...


class Playwright(Protocol):
    chromium: Chromium


class PlaywrightContext(Protocol):
    def __enter__(self) -> Playwright: ...

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None: ...


def _normalize_proxy_host(host: str) -> str:
    try:
        address: Final = ipaddress.ip_address(host)
    except ValueError:
        labels: Final = host.rstrip(".").split(".")
        if len(host) > 253 or not all(
            label
            and len(label) <= 63
            and all(character.isalnum() or character == "-" for character in label)
            and label[0].isalnum()
            and label[-1].isalnum()
            for label in labels
        ):
            raise ValueError("selected proxy hostname is invalid") from None
        return host
    return address.compressed


def _proxy_target(proxy_url: str) -> ProxyTarget:
    parsed: Final = urlsplit(proxy_url)
    try:
        port: Final = parsed.port
    except ValueError as error:
        raise ValueError("selected proxy URL is invalid") from error
    host: Final = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("selected proxy URL is invalid")
    normalized_host: Final = _normalize_proxy_host(host)
    return ProxyTarget(
        host=normalized_host,
        port=port or (443 if parsed.scheme == "https" else 80),
        ssl_context=ssl.create_default_context() if parsed.scheme == "https" else None,
    )


async def _copy_stream(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
    try:
        while chunk := await source.read(65536):
            destination.write(chunk)
            await destination.drain()
    except (ConnectionError, OSError):
        return


async def _handle_proxy_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target: ProxyTarget,
) -> None:
    try:
        proxy_reader, proxy_writer = await asyncio.open_connection(
            target.host,
            target.port,
            ssl=target.ssl_context,
            server_hostname=target.host if target.ssl_context is not None else None,
        )
    except (OSError, ssl.SSLError):
        client_writer.close()
        await client_writer.wait_closed()
        return

    transfers: Final = (
        asyncio.create_task(_copy_stream(client_reader, proxy_writer)),
        asyncio.create_task(_copy_stream(proxy_reader, client_writer)),
    )
    try:
        await asyncio.wait(transfers, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for transfer in transfers:
            transfer.cancel()
        await asyncio.gather(*transfers, return_exceptions=True)
        proxy_writer.close()
        client_writer.close()
        await asyncio.gather(proxy_writer.wait_closed(), client_writer.wait_closed(), return_exceptions=True)


async def _run_egress_relay(proxy_url: str) -> None:
    target: Final = _proxy_target(proxy_url)

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_proxy_client(reader, writer, target)

    server: Final = await asyncio.start_server(handle_client, host="0.0.0.0", port=8080)
    async with server:
        await server.serve_forever()


def _required_environment(name: str) -> str:
    value: Final = os.environ.get(name)
    if value is None or not value:
        raise RuntimeError(f"required environment setting {name} is missing")
    return value


def _start_process(
    arguments: tuple[str, ...], environment: dict[str, str], stack: ExitStack
) -> subprocess.Popen[bytes]:
    process: Final = subprocess.Popen(
        arguments,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    stack.callback(_stop_process, process)
    time.sleep(0.2)
    if process.poll() is not None:
        raise RuntimeError("OAuth browser display service failed to start")
    return process


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)


def _run_browser() -> None:
    authorization_url: Final = _required_environment("OAUTH_AUTHORIZATION_URL")
    proxy_server: Final = _required_environment("CHROME_PROXY")
    authorization: Final = urlsplit(authorization_url)
    proxy: Final = urlsplit(proxy_server)
    if (
        authorization.scheme != "https"
        or authorization.hostname is None
        or authorization.username is not None
        or authorization.password is not None
    ):
        raise RuntimeError("OAuth authorization URL is invalid")
    if proxy.scheme != "http" or proxy.hostname != "egress-relay" or proxy.port != 8080:
        raise RuntimeError("OAuth browser proxy is invalid")

    display: Final = ":99"
    process_environment: Final = {"DISPLAY": display, "HOME": "/tmp", "PATH": os.environ.get("PATH", "")}
    with ExitStack() as processes:
        _start_process(
            ("Xvfb", display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"), process_environment, processes
        )
        _start_process(
            ("x11vnc", "-display", display, "-forever", "-shared", "-nopw", "-localhost", "-rfbport", "5900"),
            process_environment,
            processes,
        )
        _start_process(
            ("websockify", "--web=/usr/share/novnc", "127.0.0.1:6080", "127.0.0.1:5900"),
            process_environment,
            processes,
        )
        from playwright.sync_api import (
            sync_playwright,  # pyright: ignore[reportMissingImports, reportUnknownVariableType]  # worker image supplies Playwright
        )

        playwright_factory: Final = cast(Callable[[], PlaywrightContext], sync_playwright)
        with playwright_factory() as playwright:
            browser: Final = playwright.chromium.launch(headless=False, proxy={"server": proxy_server})
            try:
                page: Final = browser.new_page()
                page.goto(authorization_url, wait_until="domcontentloaded", timeout=30_000)
                while True:
                    page.wait_for_timeout(30_000)
            except Exception:
                raise RuntimeError("OAuth browser failed while opening the authorization page") from None
            finally:
                browser.close()


def main() -> None:
    mode: Final = sys.argv[1] if len(sys.argv) > 1 else "browser"
    match mode:
        case "browser":
            _run_browser()
        case "egress-relay":
            asyncio.run(_run_egress_relay(_required_environment("PROXY_URL")))
        case _:
            raise SystemExit("unsupported browser worker mode")


if __name__ == "__main__":
    main()
