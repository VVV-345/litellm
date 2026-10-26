#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from http.client import HTTPResponse
from pathlib import Path
from typing import (
    Final,
    Protocol,
    cast,  # noqa: TID251  # stdlib-only host CLI validates decoded fields at the boundary
)

PACKAGE: Final = "ghcr.io/vvv-345/litellm-cockpit"
RUNTIME: Final = Path("/opt/zlia-pool/litellm-cockpit-test/runtime")
PROJECT: Final = "litellm-cockpit-test"
BUNDLE_FILES: Final = ("releasectl.py", "compose.yaml")
RELEASE_API: Final = "https://api.github.com/repos/VVV-345/litellm/releases"


@dataclass(frozen=True, slots=True)
class Release:
    format: int
    channel: str
    version: str
    litellm_revision: str
    cockpit_revision: str
    compatibility: str
    runtime: str
    litellm: str
    cockpit: str


class Runner(Protocol):
    def __call__(self, command: tuple[str, ...], *, stdin: Path | None = None, stdout: Path | None = None) -> str: ...


def json_object(data: bytes) -> Mapping[str, object]:
    value: Final = cast(object, json.loads(data))
    if not isinstance(value, dict):
        raise TypeError("expected JSON object")
    return cast(Mapping[str, object], value)


def matched(value: object, pattern: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError("invalid release field")
    return value


def parse_release(data: bytes, version: str) -> Release:
    value: Final = json_object(data)
    if value.get("format") != 1 or value.get("channel") != "cockpit-test" or value.get("version") != version:
        raise ValueError("wrong release format, channel or version")
    revision: Final = matched(value.get("litellm_revision"), "[0-9a-f]{40}")
    if revision[:10] != matched(version, "[0-9a-f]{10}"):
        raise ValueError("release revision mismatch")
    return Release(
        1,
        "cockpit-test",
        version,
        revision,
        matched(value.get("cockpit_revision"), "[0-9a-f]{40}"),
        matched(value.get("compatibility"), "[0-9a-f]{64}"),
        matched(value.get("runtime"), "[0-9a-f]{64}"),
        matched(value.get("litellm"), re.escape(PACKAGE) + r"@sha256:[0-9a-f]{64}"),
        matched(value.get("cockpit"), re.escape(PACKAGE) + r"@sha256:[0-9a-f]{64}"),
    )


def read_release(path: Path) -> Release:
    data: Final = path.read_bytes()
    return parse_release(data, matched(json_object(data).get("version"), "[0-9a-f]{10}"))


def atomic(path: Path, data: bytes) -> None:
    temporary: Final = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name == "posix":
        descriptor: Final = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def save(path: Path, value: object) -> None:
    atomic(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def runtime_hash(root: Path) -> str:
    return hashlib.sha256(
        b"".join(name.encode() + b"\0" + (root / name).read_bytes() for name in BUNDLE_FILES)
    ).hexdigest()


def check_root(root: Path) -> None:
    if root.resolve() != RUNTIME:
        raise ValueError(f"only isolated runtime {RUNTIME} is allowed")
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("run on Linux with sudo; production runtime is never supported")


def own_data(path: Path) -> None:
    if os.name == "posix" and os.geteuid() == 0:
        for entry in (path, *path.rglob("*")):
            if entry.is_symlink():
                raise ValueError("symlinks are not supported in credential snapshots")
            os.chown(entry, 10001, 10001)


def initialize(root: Path) -> None:
    if any((root / name).exists() for name in (".env", "cockpit-data", "current.json", "pending.json")):
        raise ValueError("runtime already initialized; never overwrite existing secrets")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    values: Final = {
        "DB_PASSWORD": secrets.token_hex(32),
        "LITELLM_MASTER_KEY": "sk-" + secrets.token_hex(32),
        "UI_PASSWORD": secrets.token_hex(32),
        "COCKPIT_ADMIN_TOKEN": secrets.token_hex(32),
        "RECEIPT_KEY": secrets.token_hex(32),
        "ROUTING_SEED": secrets.token_hex(32),
    }
    atomic(root / ".env", "".join(f"{key}={value}\n" for key, value in values.items()).encode())
    data: Final = root / "cockpit-data"
    data.mkdir(mode=0o700)
    (data / "auth").mkdir(mode=0o700)
    atomic(data / "config.yaml", b"host: 0.0.0.0\nport: 8317\nauth-dir: /data/auth\nusage-statistics-enabled: true\n")
    save(data / "manifest.json", {"locale": "en", "apiKeys": [], "accounts": [], "modelIds": []})
    own_data(data)


def run(command: tuple[str, ...], *, stdin: Path | None = None, stdout: Path | None = None) -> str:
    from contextlib import ExitStack

    with ExitStack() as stack:
        source: Final = stack.enter_context(stdin.open("rb")) if stdin else None
        destination: Final = stack.enter_context(stdout.open("wb")) if stdout else subprocess.PIPE
        result: Final = subprocess.run(
            command, stdin=source, stdout=destination, stderr=subprocess.PIPE, timeout=900, check=False
        )
        if result.returncode:
            raise RuntimeError(
                f"command exited {result.returncode}: {' '.join(command)}\n"
                + result.stderr.decode("utf-8", errors="replace")
            )
        return result.stdout.decode("utf-8") if result.stdout else ""


def compose(root: Path, *args: str) -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "--project-name",
        PROJECT,
        "--project-directory",
        str(root),
        "--env-file",
        str(root / ".env"),
        "--env-file",
        str(root / "images.env"),
        "-f",
        str(root / "compose.yaml"),
        *args,
    )


def images(root: Path, release: Release) -> None:
    atomic(root / "images.env", f"LITELLM_IMAGE={release.litellm}\nCOCKPIT_IMAGE={release.cockpit}\n".encode())


def image_labels(release: Release, component: str) -> Mapping[str, str]:
    return {
        "org.opencontainers.image.revision": release.litellm_revision
        if component == "litellm"
        else release.cockpit_revision,
        "ai.zlia.channel": "cockpit-test",
        "ai.zlia.component": component,
        "ai.zlia.release": release.version,
        "ai.zlia.compatibility": release.compatibility,
    }


def pull_pair(release: Release, runner: Runner) -> None:
    for component, reference in (("litellm", release.litellm), ("cockpit", release.cockpit)):
        pull_image(release, component, reference, runner)


def pull_image(release: Release, component: str, reference: str, runner: Runner) -> None:
    runner(("docker", "pull", reference))
    labels: Final = json_object(
        runner(("docker", "image", "inspect", reference, "--format", "{{json .Config.Labels}}")).encode()
    )
    if any(labels.get(key) != value for key, value in image_labels(release, component).items()):
        raise ValueError(f"{component} image labels do not match release")


def verify_running(root: Path, release: Release, runner: Runner) -> None:
    for component, reference in (("litellm", release.litellm), ("cockpit", release.cockpit)):
        verify_component(root, component, reference, runner)


def verify_component(root: Path, component: str, reference: str, runner: Runner) -> None:
    container: Final = runner(compose(root, "ps", "-q", component)).strip()
    if not container or "\n" in container:
        raise ValueError(f"expected one running {component} container")
    actual: Final = runner(("docker", "inspect", "--format", "{{.Image}}", container)).strip()
    expected: Final = runner(("docker", "image", "inspect", reference, "--format", "{{.Id}}")).strip()
    if actual != expected or not actual:
        raise ValueError(f"{component} running image differs from verified digest")


def tree_hashes(root: Path) -> Mapping[str, str]:
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("symlinks are not supported in snapshots")
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != root / "checksums.json"
    }


def complete(root: Path, release: Release) -> None:
    save(root / "current.json", asdict(release))
    os.replace(root / "pending.json", root / "last-operation.json")


def deploy(root: Path, release: Release, runner: Runner, probe: Callable[[], None]) -> None:
    if not (root / ".env").is_file() or not (root / "cockpit-data/manifest.json").is_file():
        raise ValueError("initialize the isolated runtime with init before deploying")
    if (root / "pending.json").exists():
        raise ValueError("unfinished operation; run recover first")
    previous: Final = read_release(root / "current.json") if (root / "current.json").exists() else None
    if previous and previous.compatibility != release.compatibility:
        raise ValueError("compatibility changed; a separately reviewed data migration is required")
    pull_pair(release, runner)
    operation: Final = secrets.token_hex(12)
    snapshot: Final = root / "backups" / operation
    snapshot.mkdir(parents=True, mode=0o700)
    save(snapshot / "candidate.json", asdict(release))
    if previous:
        save(snapshot / "previous.json", asdict(previous))
    save(root / "pending.json", {"operation": operation, "stage": "prepared"})
    try:
        if previous:
            images(root, previous)
            runner(compose(root, "stop", "litellm", "cockpit"))
            runner(
                compose(root, "exec", "-T", "db", "pg_dump", "-U", "llmproxy", "--no-owner", "litellm"),
                stdout=snapshot / "database.sql",
            )
        shutil.copytree(root / "cockpit-data", snapshot / "cockpit-data")
        shutil.copy2(root / ".env", snapshot / ".env")
        save(snapshot / "checksums.json", tree_hashes(snapshot))
        save(root / "pending.json", {"operation": operation, "stage": "switching"})
        images(root, release)
        runner(compose(root, "up", "-d", "--wait", "--wait-timeout", "300"))
        verify_running(root, release, runner)
        probe()
        complete(root, release)
    except BaseException:
        if (root / "images.env").exists():
            try:
                runner(compose(root, "stop", "litellm", "cockpit"))
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as stop_error:
                sys.stderr.write(f"STOP FAILED: {stop_error}; isolate test ingress and run recover\n")
        raise


def recover(root: Path, runner: Runner, probe: Callable[[], None]) -> None:
    pending: Final = json_object((root / "pending.json").read_bytes())
    operation: Final = matched(pending.get("operation"), "[0-9a-f]{24}")
    snapshot: Final = root / "backups" / operation
    candidate: Final = read_release(snapshot / "candidate.json")
    previous: Final = read_release(snapshot / "previous.json") if (snapshot / "previous.json").exists() else None
    target: Final = previous or candidate
    pull_pair(target, runner)
    if pending.get("stage") not in ("prepared", "switching"):
        raise ValueError("unknown recovery stage")
    if pending.get("stage") == "switching" and previous:
        if tree_hashes(snapshot) != json_object((snapshot / "checksums.json").read_bytes()):
            raise ValueError("snapshot checksum mismatch; refusing data restore")
        images(root, candidate)
        runner(compose(root, "stop", "litellm", "cockpit"))
        runner(compose(root, "rm", "-f", "litellm", "cockpit"))
        runner(compose(root, "up", "-d", "--wait", "db"))
        runner(compose(root, "exec", "-T", "db", "dropdb", "-U", "llmproxy", "--if-exists", "--force", "litellm"))
        runner(compose(root, "exec", "-T", "db", "createdb", "-U", "llmproxy", "litellm"))
        runner(
            compose(root, "exec", "-T", "db", "psql", "-U", "llmproxy", "-d", "litellm", "-v", "ON_ERROR_STOP=1"),
            stdin=snapshot / "database.sql",
        )
        if (root / "cockpit-data").exists():
            os.replace(root / "cockpit-data", root / ("failed-data-" + secrets.token_hex(12)))
        shutil.copytree(snapshot / "cockpit-data", root / "cockpit-data")
        own_data(root / "cockpit-data")
        atomic(root / ".env", (snapshot / ".env").read_bytes())
    images(root, target)
    try:
        runner(compose(root, "up", "-d", "--wait", "--wait-timeout", "300"))
        verify_running(root, target, runner)
        probe()
        complete(root, target)
    except BaseException:
        runner(compose(root, "stop", "litellm", "cockpit"))
        raise


def probe_http() -> None:
    for path in ("/health/readiness", "/ui/"):
        with cast(HTTPResponse, urllib.request.urlopen("http://127.0.0.1:18400" + path, timeout=20)) as response:
            if response.status != 200:
                raise ValueError(f"HTTP probe failed: {path}")


def github_bytes(url: str, accept: str) -> bytes:
    request: Final = urllib.request.Request(url, headers={"Accept": accept})
    try:
        with cast(HTTPResponse, urllib.request.urlopen(request, timeout=30)) as response:
            data: Final = response.read(65537)
    except TimeoutError as error:
        raise ValueError(f"GitHub API request timed out: {url}") from error
    if len(data) > 65536:
        raise ValueError("GitHub release response is too large")
    return data


def fetch_release(version: str) -> Release:
    matched(version, "[0-9a-f]{10}")
    metadata: Final = json_object(
        github_bytes(f"{RELEASE_API}/tags/cockpit-test-{version}", "application/vnd.github+json")
    )
    if metadata.get("tag_name") != f"cockpit-test-{version}" or metadata.get("draft") is not False:
        raise ValueError("wrong GitHub release identity or unpublished draft")
    assets: Final = metadata.get("assets")
    if not isinstance(assets, list):
        raise TypeError("GitHub release assets must be a list")
    manifests: Final = tuple(
        cast(Mapping[str, object], asset)
        for asset in assets
        if isinstance(asset, dict) and asset.get("name") == "release.json"
    )
    if len(manifests) != 1:
        raise ValueError("expected exactly one release.json asset")
    asset: Final = manifests[0]
    asset_id: Final = asset.get("id")
    if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
        raise ValueError("invalid GitHub asset identity")
    digest: Final = matched(asset.get("digest"), r"sha256:[0-9a-f]{64}")
    data: Final = github_bytes(f"{RELEASE_API}/assets/{asset_id}", "application/octet-stream")
    if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("release.json checksum mismatch")
    return parse_release(data, version)


def main() -> int:
    parser: Final = argparse.ArgumentParser(
        description="Isolated LiteLLM + Cockpit deployer; never calls production 8092"
    )
    parser.add_argument("action", choices=("init", "status", "deploy", "recover"))
    parser.add_argument("version", nargs="?", help="deploy: LiteLLM commit first 10 lowercase hex characters")
    args: Final = cast(Mapping[str, object], vars(parser.parse_args()))
    action: Final = matched(args.get("action"), "init|status|deploy|recover")
    root: Final = Path(__file__).resolve().parent
    try:
        check_root(root)
        import fcntl

        with (root / ".release.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if action == "init":
                initialize(root)
                sys.stdout.write("Initialized isolated secrets in .env (0600); UI username: admin\n")
            elif action == "status":
                for name in ("current.json", "pending.json"):
                    sys.stdout.write(f"{name}: " + ((root / name).read_text() if (root / name).exists() else "none\n"))
            elif action == "deploy":
                sys.stdout.write("Fetching verified release manifest via GitHub API\n")
                release: Final = fetch_release(matched(args.get("version"), "[0-9a-f]{10}"))
                if runtime_hash(root) != release.runtime:
                    raise ValueError("runtime bundle checksum mismatch; install reviewed matching runtime.tar.gz")
                sys.stdout.write(
                    f"Target: {RUNTIME} / {PROJECT} / {release.version}; target services will be updated\n"
                )
                if input("Type CONFIRM to stop service writes, snapshot and deploy: ") != "CONFIRM":
                    raise ValueError("cancelled")
                deploy(root, release, run, probe_http)
                sys.stdout.write(
                    f"DEPLOYED {release.version}; http://127.0.0.1:18400/ui/; provider acceptance still required\n"
                )
            else:
                if (
                    input("Type CONFIRM to restore the interrupted test deployment (test data may be rewound): ")
                    != "CONFIRM"
                ):
                    raise ValueError("cancelled")
                recover(root, run, probe_http)
                sys.stdout.write("RECOVERED; inspect status and recheck test accounts\n")
    except (OSError, ValueError, TypeError, RuntimeError, EOFError, subprocess.TimeoutExpired) as error:
        sys.stderr.write(f"ERROR: {error}\n")
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
