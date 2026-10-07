"""启动隔离 OAuth 浏览器和只连接选定代理的出站转发器。"""

from __future__ import annotations

import asyncio
import http.server
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from http.client import HTTPResponse
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

_BROWSER_READY_FILE: Final = Path("/tmp/oauth-browser-ready")


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


class _CallbackServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[http.server.BaseHTTPRequestHandler],
        *,
        callback_path: str,
        manager_callback_url: str | None = None,
        callback_token: str | None = None,
    ) -> None:
        super().__init__(address, handler)
        self.callback_path: Final = callback_path
        self.manager_callback_url: Final = manager_callback_url
        self.callback_token: Final = callback_token


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


def _callback_fields(query: str) -> Mapping[str, str] | None:
    if len(query) > 16_384:
        return None
    try:
        values: Final = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=16)
    except ValueError:
        return None
    allowed: Final = frozenset(("code", "state", "error", "error_description"))
    if "state" not in values or len(values["state"]) != 1:
        return None
    if any(key in allowed and len(items) != 1 for key, items in values.items()):
        return None
    fields: Final = MappingProxyType({key: values[key][0] for key in allowed if key in values})
    if len(fields["state"]) < 16 or len(fields["state"]) > 512:
        return None
    code: Final = fields.get("code")
    error: Final = fields.get("error") or fields.get("error_description")
    if (code is None or not code.strip()) == (error is None or not error.strip()):
        return None
    if code is not None and len(code) > 8192:
        return None
    if any(len(fields.get(name, "")) > limit for name, limit in (("error", 512), ("error_description", 2048))):
        return None
    return fields


class _BrowserCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server: Final = cast(_CallbackServer, self.server)
        parsed: Final = urlsplit(self.path)
        fields: Final = _callback_fields(parsed.query) if parsed.path == server.callback_path else None
        relay_url: Final = os.environ.get("CALLBACK_RELAY_URL", "")
        if fields is None or not relay_url:
            self._respond(400)
            return
        request: Final = Request(
            relay_url,
            data=json.dumps(dict(fields)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        code: Final = _callback_response_status(request)
        self._respond(200 if 200 <= code < 300 else code)

    def _respond(self, code: int) -> None:
        content: Final = (
            b"OAuth callback received. You can close this page."
            if 200 <= code < 300
            else b"OAuth callback could not be completed. Return to LiteLLM and retry."
        )
        self.send_response(200 if 200 <= code < 300 else code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return None

    def log_error(self, format: str, *args: object) -> None:
        return None


class _ManagerCallbackRelayHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server: Final = cast(_CallbackServer, self.server)
        manager_url: Final = server.manager_callback_url
        token: Final = server.callback_token
        content_length: Final = self.headers.get("Content-Length", "")
        if self.path != "/callback" or manager_url is None or token is None:
            self._respond(404)
            return
        try:
            body_length: Final = int(content_length)
        except ValueError:
            self._respond(400)
            return
        if body_length < 1 or body_length > 12_000:
            self._respond(413)
            return
        try:
            decoded: Final = cast(object, json.loads(self.rfile.read(body_length)))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400)
            return
        if not isinstance(decoded, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in cast(dict[object, object], decoded).items()
        ):
            self._respond(400)
            return
        fields: Final[dict[str, str]] = cast(dict[str, str], decoded)
        if not _valid_relay_fields(fields):
            self._respond(400)
            return
        forwarded: Final = {
            key: value for key, value in fields.items() if key in {"code", "state", "error", "error_description"}
        }
        request: Final = Request(
            manager_url,
            data=json.dumps(forwarded).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        code: Final = _callback_response_status(request)
        self._respond(200 if 200 <= code < 300 else code)

    def _respond(self, code: int) -> None:
        self.send_response(code)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return None

    def log_error(self, format: str, *args: object) -> None:
        return None


def _callback_response_status(request: Request) -> int:
    try:
        with cast(HTTPResponse, urlopen(request, timeout=30)) as response:
            return response.status
    except HTTPError as error:
        return error.code
    except (OSError, URLError, TimeoutError):
        return 502


def _valid_relay_fields(fields: Mapping[str, str]) -> bool:
    allowed: Final = frozenset(("code", "state", "error", "error_description"))
    state: Final = fields.get("state", "")
    code: Final = fields.get("code")
    error: Final = fields.get("error") or fields.get("error_description")
    return (
        set(fields).issubset(allowed)
        and 16 <= len(state) <= 512
        and (code is None or (bool(code.strip()) and len(code) <= 8192))
        and (error is None or bool(error.strip()))
        and ((code is None) != (error is None))
        and len(fields.get("error", "")) <= 512
        and len(fields.get("error_description", "")) <= 2048
    )


def _run_callback_relay(manager_callback_url: str, callback_token: str) -> None:
    server: Final = _CallbackServer(
        ("0.0.0.0", 8092),
        _ManagerCallbackRelayHandler,
        callback_path="/callback",
        manager_callback_url=manager_callback_url,
        callback_token=callback_token,
    )
    process_environment: Final = {"HOME": "/tmp", "PATH": os.environ.get("PATH", "")}
    with ExitStack() as processes:
        _start_process(
            ("websockify", "--web=/usr/share/novnc", "0.0.0.0:8093", "browser:5900"),
            process_environment,
            processes,
        )
        with server:
            server.serve_forever()


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


def _check_port(port: int) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=2):
        pass


def _check_health(
    mode: str,
    *,
    ready_file: Path = _BROWSER_READY_FILE,
    check_port: Callable[[int], None] = _check_port,
    callback_port: int | None = None,
) -> None:
    match mode:
        case "browser":
            if not ready_file.is_file():
                raise RuntimeError("OAuth browser is not ready")
            check_port(5900)
            check_port(
                callback_port if callback_port is not None else int(_required_environment("OAUTH_CALLBACK_LISTEN_PORT"))
            )
        case "callback-relay":
            check_port(8092)
            check_port(8093)
        case "egress-relay":
            check_port(8080)
        case _:
            raise ValueError("unsupported browser healthcheck")


def _run_browser_page(chromium: Chromium, authorization_url: str, proxy_server: str, ready_file: Path) -> None:
    ready_file.unlink(missing_ok=True)
    browser: Final = chromium.launch(
        headless=False,
        proxy={"server": proxy_server, "bypass": "localhost,127.0.0.1,[::1]"},
    )
    try:
        page: Final = browser.new_page()
        page.goto(authorization_url, wait_until="domcontentloaded", timeout=30_000)
        ready_file.touch()
        while True:
            page.wait_for_timeout(30_000)
    except Exception:
        raise RuntimeError("OAuth browser failed while opening the authorization page") from None
    finally:
        ready_file.unlink(missing_ok=True)
        browser.close()


def _run_browser() -> None:
    authorization_url: Final = _required_environment("OAUTH_AUTHORIZATION_URL")
    proxy_server: Final = _required_environment("CHROME_PROXY")
    authorization: Final = urlsplit(authorization_url)
    proxy: Final = urlsplit(proxy_server)
    callback_path: Final = _required_environment("OAUTH_CALLBACK_LISTEN_PATH")
    callback_relay_url: Final = _required_environment("CALLBACK_RELAY_URL")
    try:
        callback_port: Final = int(_required_environment("OAUTH_CALLBACK_LISTEN_PORT"))
    except ValueError as error:
        raise RuntimeError("OAuth callback port is invalid") from error
    if (
        authorization.scheme != "https"
        or authorization.hostname is None
        or authorization.username is not None
        or authorization.password is not None
    ):
        raise RuntimeError("OAuth authorization URL is invalid")
    if proxy.scheme != "http" or proxy.hostname != "egress-relay" or proxy.port != 8080:
        raise RuntimeError("OAuth browser proxy is invalid")
    if not callback_path.startswith("/") or "?" in callback_path or "#" in callback_path:
        raise RuntimeError("OAuth callback path is invalid")
    relay: Final = urlsplit(callback_relay_url)
    if relay.scheme != "http" or relay.hostname != "callback-relay" or relay.port != 8092 or relay.path != "/callback":
        raise RuntimeError("OAuth callback relay is invalid")

    display: Final = _required_environment("DISPLAY")
    process_environment: Final = {"DISPLAY": display, "HOME": "/tmp", "PATH": os.environ.get("PATH", "")}
    with ExitStack() as processes:
        callback_server: Final = _CallbackServer(
            ("127.0.0.1", callback_port),
            _BrowserCallbackHandler,
            callback_path=callback_path,
        )
        callback_thread: Final = threading.Thread(target=callback_server.serve_forever, daemon=True)
        callback_thread.start()
        processes.callback(callback_server.shutdown)
        processes.callback(callback_server.server_close)
        _start_process(
            ("Xvfb", display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"), process_environment, processes
        )
        _start_process(
            ("x11vnc", "-display", display, "-forever", "-shared", "-nopw", "-rfbport", "5900"),
            process_environment,
            processes,
        )
        from playwright.sync_api import (
            sync_playwright,  # pyright: ignore[reportMissingImports, reportUnknownVariableType]  # worker image supplies Playwright
        )

        playwright_factory: Final = cast(Callable[[], PlaywrightContext], sync_playwright)
        with playwright_factory() as playwright:
            _run_browser_page(playwright.chromium, authorization_url, proxy_server, _BROWSER_READY_FILE)


def main() -> None:
    mode: Final = sys.argv[1] if len(sys.argv) > 1 else "browser"
    match mode:
        case "browser":
            _run_browser()
        case "egress-relay":
            asyncio.run(_run_egress_relay(_required_environment("PROXY_URL")))
        case "callback-relay":
            _run_callback_relay(_required_environment("MANAGER_CALLBACK_URL"), _required_environment("CALLBACK_TOKEN"))
        case "healthcheck":
            try:
                _check_health(sys.argv[2])
            except (IndexError, OSError, RuntimeError, ValueError):
                raise SystemExit("OAuth browser service is not ready") from None
        case _:
            raise SystemExit("unsupported browser worker mode")


if __name__ == "__main__":
    main()
