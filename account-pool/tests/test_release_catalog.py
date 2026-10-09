"""本文件验证远程发布目录的 HEAD 约束、平台选择和只读 Docker 边界。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import httpx
import pytest
from account_pool.release_catalog import ReleaseCatalog
from account_pool.release_models import ReleaseImage, ReleasePair
from account_pool.release_runtime import DockerReleaseRuntime, ReleaseSettings, image_pair
from account_pool.release_store import ReleaseError
from pydantic import JsonValue

HEAD: Final = "c" * 40
BRANCH: Final = "CLIProxyAPI分支"


def current_pair(commit: str = "a" * 40) -> ReleasePair:
    return image_pair(
        (
            ReleaseImage(
                service="litellm",
                image_id="sha256:" + "a" * 64,
                repository="ghcr.io/vvv-345/litellm",
                revision=commit,
                size=100,
            ),
            ReleaseImage(
                service="account-pool",
                image_id="sha256:" + "b" * 64,
                repository="ghcr.io/vvv-345/account-pool-manager",
                revision=commit,
                size=100,
            ),
        )
    )


def manifest(arch: str, digest: str, config: str, body: str = "SchemaV2Manifest") -> dict[str, JsonValue]:
    return {
        "Ref": "ghcr.io/vvv-345/litellm@sha256:" + digest * 64,
        "Descriptor": {
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "digest": "sha256:" + digest * 64,
            "size": 100,
            "platform": {"architecture": arch, "os": "linux"},
        },
        body: {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {"digest": "sha256:" + config * 64, "size": 100},
            "layers": [],
        },
    }


class GitHub:
    def __init__(
        self,
        runs: tuple[dict[str, JsonValue], ...] | None = None,
        branch: str = BRANCH,
        head: str = HEAD,
        status: int = 200,
        fail: bool = False,
    ) -> None:
        self.runs: Final = (
            runs
            if runs is not None
            else ({"head_sha": HEAD, "head_branch": BRANCH, "status": "completed", "conclusion": "success"},)
        )
        self.branch: Final = branch
        self.head: Final = head
        self.status: Final = status
        self.fail: Final = fail
        self.requests: tuple[httpx.Request, ...] = ()

    def __call__(self, *, timeout: float, follow_redirects: bool) -> httpx.Client:
        assert timeout == 30 and follow_redirects is False
        return httpx.Client(
            transport=httpx.MockTransport(self.respond), timeout=timeout, follow_redirects=follow_redirects
        )

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests += (request,)
        assert request.url.scheme == "https" and request.url.host == "api.github.com"
        if self.fail:
            raise httpx.ReadTimeout("secret-registry-password", request=request)
        if self.status != 200:
            return httpx.Response(
                self.status, text="secret-registry-password", headers={"Location": "https://evil.test"}
            )
        if request.url.path == "/repos/VVV-345/litellm/branches/" + BRANCH:
            return httpx.Response(200, json={"name": self.branch, "commit": {"sha": self.head}})
        assert request.url.path == "/repos/VVV-345/litellm/actions/workflows/publish-deployment-images.yml/runs"
        assert request.url.params["branch"] == BRANCH
        assert request.url.params["head_sha"] == HEAD
        assert request.url.params["status"] == "success"
        return httpx.Response(200, json={"total_count": len(self.runs), "workflow_runs": self.runs})


class Docker:
    def __init__(
        self,
        arch: str = "amd64",
        entries: JsonValue | None = None,
        fail_service: str = "",
        raw: bytes | None = None,
        configs: tuple[str, str] | None = None,
    ) -> None:
        self.arch: Final = arch
        self.entries: Final = entries
        self.fail_service: Final = fail_service
        self.raw: Final = raw
        self.configs: Final = configs
        self.calls: tuple[tuple[tuple[str, ...], int], ...] = ()

    def __call__(self, *args: str, timeout: int = 120) -> bytes:
        self.calls += ((args, timeout),)
        if args[:2] == ("image", "inspect"):
            assert args[2] in ("sha256:" + "a" * 64, "sha256:" + "b" * 64)
            assert args[3:] == ("--format", "{{.Os}}/{{.Architecture}}")
            return ("linux/" + self.arch + "\n").encode()
        assert args[:3] == ("manifest", "inspect", "--verbose")
        assert timeout == 30
        assert args[3] in (
            "ghcr.io/vvv-345/litellm:" + HEAD[:10],
            "ghcr.io/vvv-345/account-pool-manager:" + HEAD[:10],
        )
        if self.fail_service and self.fail_service in args[3]:
            raise ReleaseError("secret-registry-password")
        if self.raw is not None:
            return self.raw
        if self.entries is not None:
            return json.dumps(self.entries).encode()
        if self.configs is not None:
            return json.dumps(
                manifest("amd64", "d", self.configs[0] if "/litellm:" in args[3] else self.configs[1])
            ).encode()
        return json.dumps(
            [
                manifest("amd64", "d", "e"),
                manifest("arm64", "f", "0"),
                {"Descriptor": {"platform": {"os": "unknown", "architecture": "unknown"}}},
            ]
        ).encode()


def catalog(tmp_path: Path, docker: Docker, github: GitHub) -> ReleaseCatalog:
    return ReleaseCatalog(
        DockerReleaseRuntime(
            ReleaseSettings.model_validate(
                {
                    "token": "t" * 32,
                    "root": tmp_path,
                    "deployment": tmp_path.parent / "deployment",
                }
            ),
            docker,
        ),
        client_factory=github,
    )


@pytest.mark.parametrize(("arch", "digest", "config"), (("amd64", "d", "e"), ("arm64", "f", "0")))
def test_selects_current_platform_digests_without_pulling(
    tmp_path: Path,
    arch: str,
    digest: str,
    config: str,
) -> None:
    docker: Final = Docker(arch=arch)
    github: Final = GitHub()
    result: Final = catalog(tmp_path, docker, github).latest(current_pair())
    assert result is not None and result.commit == HEAD
    assert tuple(image.service for image in result.images) == ("litellm", "account-pool")
    assert tuple(image.digest for image in result.images) == ("sha256:" + digest * 64,) * 2
    assert tuple(image.image_id for image in result.images) == ("sha256:" + config * 64,) * 2
    assert len(github.requests) == 2
    assert len(docker.calls) == 4


@pytest.mark.parametrize("body", ("SchemaV2Manifest", "OCIManifest"))
def test_accepts_single_platform_manifest(tmp_path: Path, body: str) -> None:
    result: Final = catalog(tmp_path, Docker(entries=manifest("amd64", "d", "e", body)), GitHub()).latest(
        current_pair()
    )
    assert result is not None and result.images[0].image_id == "sha256:" + "e" * 64


@pytest.mark.parametrize(
    "runs",
    (
        (),
        ({"head_sha": HEAD, "head_branch": BRANCH, "status": "in_progress", "conclusion": None},),
        ({"head_sha": HEAD, "head_branch": BRANCH, "status": "completed", "conclusion": "failure"},),
    ),
)
def test_head_without_successful_publish_does_not_inspect_registry(
    tmp_path: Path,
    runs: tuple[dict[str, JsonValue], ...],
) -> None:
    docker: Final = Docker()
    assert catalog(tmp_path, docker, GitHub(runs=runs)).latest(current_pair()) is None
    assert not docker.calls


@pytest.mark.parametrize("configs", (("e", "f"), ("a", "b")))
def test_current_head_still_returns_verified_remote_images(
    tmp_path: Path,
    configs: tuple[str, str],
) -> None:
    docker: Final = Docker(configs=configs)
    github: Final = GitHub()
    result: Final = catalog(tmp_path, docker, github).latest(current_pair(HEAD))
    assert result is not None and result.commit == HEAD
    assert tuple(image.image_id for image in result.images) == tuple("sha256:" + value * 64 for value in configs)
    assert len(github.requests) == 2 and len(docker.calls) == 4


@pytest.mark.parametrize(("branch", "head"), (("other", HEAD), (BRANCH, "bad-commit")))
def test_rejects_wrong_branch_or_invalid_head(tmp_path: Path, branch: str, head: str) -> None:
    docker: Final = Docker()
    with pytest.raises(ReleaseError):
        catalog(tmp_path, docker, GitHub(branch=branch, head=head)).latest(current_pair())
    assert not docker.calls


@pytest.mark.parametrize(("branch", "head"), (("other", HEAD), (BRANCH, "b" * 40)))
def test_rejects_successful_publish_for_different_branch_or_commit(tmp_path: Path, branch: str, head: str) -> None:
    docker: Final = Docker()
    runs: Final[tuple[dict[str, JsonValue], ...]] = (
        {"head_sha": head, "head_branch": branch, "status": "completed", "conclusion": "success"},
    )
    with pytest.raises(ReleaseError):
        catalog(tmp_path, docker, GitHub(runs=runs)).latest(current_pair())
    assert not docker.calls


@pytest.mark.parametrize(
    "entries",
    (
        [],
        [manifest("arm64", "d", "e")],
        [manifest("amd64", "d", "e"), manifest("amd64", "f", "0")],
        manifest("amd64", "z", "e"),
        manifest("amd64", "d", "z"),
        {"Descriptor": {"platform": {"architecture": "amd64", "os": "linux"}, "digest": "sha256:" + "d" * 64}},
    ),
)
def test_rejects_missing_ambiguous_or_malformed_platform_images(tmp_path: Path, entries: JsonValue) -> None:
    with pytest.raises(ReleaseError):
        catalog(tmp_path, Docker(entries=entries), GitHub()).latest(current_pair())


@pytest.mark.parametrize("status", (302, 403, 429, 500))
def test_sanitizes_http_errors_and_never_follows_redirects(tmp_path: Path, status: int) -> None:
    github: Final = GitHub(status=status)
    with pytest.raises(ReleaseError) as error:
        catalog(tmp_path, Docker(), github).latest(current_pair())
    assert "secret-registry-password" not in str(error.value)
    assert len(github.requests) == 1


def test_sanitizes_github_timeout(tmp_path: Path) -> None:
    with pytest.raises(ReleaseError) as error:
        catalog(tmp_path, Docker(), GitHub(fail=True)).latest(current_pair())
    assert "secret-registry-password" not in str(error.value)


def test_partial_registry_failure_returns_no_candidate_and_redacts_error(tmp_path: Path) -> None:
    docker: Final = Docker(fail_service="account-pool-manager")
    with pytest.raises(ReleaseError) as error:
        catalog(tmp_path, docker, GitHub()).latest(current_pair())
    assert "secret-registry-password" not in str(error.value)
    assert sum(args[:2] == ("manifest", "inspect") for args, _ in docker.calls) == 2


@pytest.mark.parametrize("raw", (b"secret-registry-password", b"\xff", b'{"Descriptor": {}}'))
def test_sanitizes_malformed_registry_output(tmp_path: Path, raw: bytes) -> None:
    with pytest.raises(ReleaseError) as error:
        catalog(tmp_path, Docker(raw=raw), GitHub()).latest(current_pair())
    assert "secret-registry-password" not in str(error.value)
