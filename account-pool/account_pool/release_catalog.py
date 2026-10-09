"""本模块只读查询固定分支的成功发布和配套平台镜像，不下载源码、拉取或构建镜像。"""

from __future__ import annotations

import re
from typing import Final, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from account_pool.release_models import Commit, ReleaseCandidate, ReleaseImage, ReleasePair, RemoteReleaseImage
from account_pool.release_runtime import REPOSITORIES
from account_pool.release_store import ReleaseError

BRANCH: Final = "CLIProxyAPI分支"
GITHUB_REPOSITORY: Final = "https://api.github.com/repos/VVV-345/litellm"
WORKFLOW: Final = "publish-deployment-images.yml"


class ClientFactory(Protocol):
    def __call__(self, *, timeout: float, follow_redirects: bool) -> httpx.Client: ...


class ManifestRuntime(Protocol):
    def run(self, *args: str, timeout: int = 120) -> bytes: ...


class _HeadCommit(BaseModel):
    sha: Commit


class _Branch(BaseModel):
    name: str
    commit: _HeadCommit


class _WorkflowRun(BaseModel):
    head_sha: Commit
    head_branch: str
    status: str
    conclusion: str | None


class _WorkflowRuns(BaseModel):
    workflow_runs: tuple[_WorkflowRun, ...]


class _Platform(BaseModel):
    os: str
    architecture: str


class _Descriptor(BaseModel):
    platform: _Platform
    digest: str = ""


class _Config(BaseModel):
    digest: str


class _ManifestBody(BaseModel):
    config: _Config


class _Manifest(BaseModel):
    descriptor: _Descriptor = Field(alias="Descriptor")
    schema_v2: _ManifestBody | None = Field(default=None, alias="SchemaV2Manifest")
    oci: _ManifestBody | None = Field(default=None, alias="OCIManifest")


class ReleaseCatalog:
    def __init__(self, runtime: ManifestRuntime, client_factory: ClientFactory | None = None) -> None:
        self.runtime: Final = runtime
        self.client_factory: Final = client_factory or httpx.Client

    def latest(self, current: ReleasePair) -> ReleaseCandidate | None:
        try:
            return self._latest(current)
        except (httpx.HTTPError, ValueError, OSError, ReleaseError):
            # 远程返回和 Docker 错误可能含认证信息，只公开固定错误文案。
            raise ReleaseError("检查新版本失败，请检查 GitHub 发布记录及镜像仓库状态") from None

    def _latest(self, current: ReleasePair) -> ReleaseCandidate | None:
        with self.client_factory(timeout=30, follow_redirects=False) as client:
            head: Final = _Branch.model_validate_json(self._get(client, "/branches/" + quote(BRANCH, safe="")))
            if head.name != BRANCH:
                raise ReleaseError("分支信息不匹配")
            runs: Final = _WorkflowRuns.model_validate_json(
                self._get(
                    client,
                    "/actions/workflows/" + WORKFLOW + "/runs",
                    {"branch": BRANCH, "head_sha": head.commit.sha, "status": "success", "per_page": "100"},
                )
            )
        if any(run.head_branch != BRANCH or run.head_sha != head.commit.sha for run in runs.workflow_runs):
            raise ReleaseError("发布记录与当前分支 HEAD 不匹配")
        if not any(run.status == "completed" and run.conclusion == "success" for run in runs.workflow_runs):
            return None
        images: Final = {image.service: image for image in current.images}
        if set(images) != set(REPOSITORIES):
            raise ReleaseError("当前版本缺少配套服务")
        return ReleaseCandidate(
            commit=head.commit.sha,
            images=(
                self._image(images["litellm"], head.commit.sha),
                self._image(images["account-pool"], head.commit.sha),
            ),
        )

    @staticmethod
    def _get(client: httpx.Client, path: str, params: dict[str, str] | None = None) -> bytes:
        response: Final = client.get(
            GITHUB_REPOSITORY + path,
            params=params,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        response.raise_for_status()
        return response.content

    def _image(self, image: ReleaseImage, commit: str) -> RemoteReleaseImage:
        platform: Final = (
            self.runtime.run("image", "inspect", image.image_id, "--format", "{{.Os}}/{{.Architecture}}")
            .decode()
            .strip()
        )
        if not re.fullmatch(r"[a-z0-9_-]+/[a-z0-9_-]+", platform) or "unknown" in platform.split("/"):
            raise ReleaseError("无法确定当前服务平台")
        raw: Final = self.runtime.run(
            "manifest", "inspect", "--verbose", REPOSITORIES[image.service] + ":" + commit[:10], timeout=30
        )
        parsed: Final = TypeAdapter[_Manifest | tuple[_Manifest, ...]](_Manifest | tuple[_Manifest, ...]).validate_json(
            raw
        )
        manifests: Final = (parsed,) if isinstance(parsed, _Manifest) else parsed
        matches: Final = tuple(
            item
            for item in manifests
            if item.descriptor.platform.os + "/" + item.descriptor.platform.architecture == platform
        )
        if len(matches) != 1:
            raise ReleaseError("远程镜像没有唯一匹配的运行平台")
        bodies: Final = tuple(body for body in (matches[0].schema_v2, matches[0].oci) if body is not None)
        if len(bodies) != 1:
            raise ReleaseError("远程镜像缺少唯一配置")
        return RemoteReleaseImage(
            service=image.service, digest=matches[0].descriptor.digest, image_id=bodies[0].config.digest
        )
