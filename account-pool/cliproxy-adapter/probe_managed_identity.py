"""启动已适配的 CLIProxyAPI，确认托管安装身份探测返回 supported=true 且 version=1。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final

CAPABILITY_PATH: Final = "/v0/management/codex/managed-identity"
CAPABILITY_BODY: Final = b'{"supported":true,"version":1}'
MANAGEMENT_HEADER: Final = "X-Management-Key"
PROBE_PASSWORD: Final = "probe-managed-identity-key"
_HOST_PORT: Final = 18317
_CONTAINER_PORT: Final = 8317


def config_text(port: int, auth_dir: str) -> str:
    return (
        "host: 0.0.0.0\n"
        f"port: {port}\n"
        "remote-management:\n"
        "  allow-remote: true\n"
        f"  secret-key: {PROBE_PASSWORD}\n"
        "  disable-control-panel: true\n"
        f"auth-dir: {auth_dir}\n"
        "api-keys:\n"
        "  - probe-gateway-key\n"
        "debug: false\n"
        "logging-to-file: false\n"
        "usage-statistics-enabled: false\n"
        "request-retry: 0\n"
        "max-retry-credentials: 1\n"
        "max-retry-interval: 0\n"
        "streaming:\n"
        "  bootstrap-retries: 0\n"
    )


def fetch(port: int, key: str | None, *, timeout_seconds: float = 2) -> tuple[int, bytes]:
    request: Final = urllib.request.Request(f"http://127.0.0.1:{port}{CAPABILITY_PATH}")
    if key is not None:
        request.add_header(MANAGEMENT_HEADER, key)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def wait_until_http_ready(port: int, timeout_seconds: float) -> tuple[int, bytes]:
    deadline: Final = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            return fetch(port, None, timeout_seconds=min(2, max(0.001, deadline - time.monotonic())))
        except OSError:
            time.sleep(0.2)
    raise SystemExit(f"CLIProxyAPI HTTP readiness timed out on 127.0.0.1:{port}")


def assert_capability(port: int) -> None:
    unauthorized: Final = wait_until_http_ready(port, 60)
    sys.stdout.write(f"GET {CAPABILITY_PATH} without key -> {unauthorized[0]}\n")
    if unauthorized[0] != 401:
        raise SystemExit(f"unauthenticated probe status={unauthorized[0]} body={unauthorized[1]!r}")
    authorized: Final = fetch(port, PROBE_PASSWORD)
    sys.stdout.write(f"GET {CAPABILITY_PATH} -> {authorized[0]} {authorized[1].decode()}\n")
    if authorized != (200, CAPABILITY_BODY):
        raise SystemExit(f"managed-identity probe failed: status={authorized[0]} body={authorized[1]!r}")


def _probe_binary(binary: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="cliproxy-managed-probe-") as directory:
        root: Final = Path(directory)
        auth_dir: Final = root / "auths"
        auth_dir.mkdir(mode=0o700)
        config: Final = root / "config.yaml"
        config.write_text(config_text(_HOST_PORT, auth_dir.as_posix()), encoding="utf-8")
        log_path: Final = root / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            process: Final = subprocess.Popen(
                [os.fspath(binary), "-config", os.fspath(config), "-local-model"],
                cwd=root,
                env={**os.environ, "MANAGEMENT_PASSWORD": PROBE_PASSWORD},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                assert_capability(_HOST_PORT)
            except BaseException:
                sys.stderr.write(log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
                raise
            finally:
                process.kill()
                process.wait(timeout=10)


def _probe_image(image: str) -> None:
    name: Final = f"cliproxy-managed-probe-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="cliproxy-managed-probe-") as directory:
        config: Final = Path(directory) / "config.yaml"
        config.write_text(config_text(_CONTAINER_PORT, "/tmp/auths"), encoding="utf-8")
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
        started: Final = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-e",
                f"MANAGEMENT_PASSWORD={PROBE_PASSWORD}",
                "-p",
                f"127.0.0.1:{_HOST_PORT}:{_CONTAINER_PORT}",
                "-v",
                f"{config}:/tmp/probe-config.yaml:ro",
                image,
                "./CLIProxyAPI",
                "-config",
                "/tmp/probe-config.yaml",
                "-local-model",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if started.returncode != 0:
            raise SystemExit(started.stderr.strip() or started.stdout.strip() or "docker run failed")
        try:
            assert_capability(_HOST_PORT)
        except BaseException:
            logs: Final = subprocess.run(["docker", "logs", name], check=False, capture_output=True, text=True)
            sys.stderr.write((logs.stdout + logs.stderr)[-4000:])
            raise
        finally:
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in {"--binary", "--image"}:
        raise SystemExit("Usage: python probe_managed_identity.py --binary PATH | --image REF")
    if sys.argv[1] == "--binary":
        _probe_binary(Path(sys.argv[2]).resolve())
        return
    _probe_image(sys.argv[2])


if __name__ == "__main__":
    main()
