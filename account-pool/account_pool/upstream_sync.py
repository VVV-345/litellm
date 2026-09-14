"""本模块封装上游版本检查和 GitHub 工作流调度，不参与模型请求数据面。"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Final, Literal, overload
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from account_pool.config import Settings

UpstreamSyncState = Literal["idle", "queued", "running", "conflict", "failed", "passed", "promoted"]
UpstreamSyncAction = Literal["none", "analyze", "promote"]

_GITHUB_API_ROOT: Final = "https://api.github.com"
_STATUS_PATH: Final = ".codex/upstream-sync-status.json"
_REVIEW_PATH: Final = ".codex/upstream-sync-review.md"
_HANDOFF_PATH: Final = "CODEX_UPSTREAM_SYNC.md"
_TAG_PATTERN: Final = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)(?:[.-][0-9A-Za-z.-]+)?$")
_MAX_CONTENT_BYTES: Final = 2_000_000
_MAX_ENCODED_CONTENT_LENGTH: Final = 2_700_000


class UpstreamSyncReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    state: UpstreamSyncState = "idle"
    action: UpstreamSyncAction = "none"
    request_id: UUID | None = None
    target_tag: str | None = None
    base_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    candidate_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    conflict_files: tuple[str, ...] = ()
    failed_steps: tuple[str, ...] = ()
    message: str = ""
    workflow_url: HttpUrl | None = None
    updated_at: AwareDatetime | None = None


class UpstreamSyncView(BaseModel):
    model_config = ConfigDict(frozen=True)

    upstream_repository: str
    fork_repository: str
    sync_branch: str
    current_tag: str
    latest_tag: str
    latest_release_url: HttpUrl
    update_available: bool
    dispatch_configured: bool
    report: UpstreamSyncReport


class UpstreamSyncDispatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    action: Literal["analyze", "promote"]
    target_tag: str
    state: Literal["queued"] = "queued"


class CodexReviewPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    filename: str
    branch: str
    target_tag: str | None = None
    content: str


class _GitHubRelease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    tag_name: str
    html_url: HttpUrl


class _GitHubContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    encoding: Literal["base64"]
    content: str = Field(max_length=_MAX_ENCODED_CONTENT_LENGTH)


class _GitHubError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    message: str = Field(max_length=1000)


class UpstreamSyncError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code: Final = status_code
        self.message: Final = message


class GitHubUpstreamSyncService:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings: Final = settings
        self._token: Final = (
            ""
            if settings.upstream_sync_github_token is None
            else settings.upstream_sync_github_token.get_secret_value().strip()
        )
        self._client: Final = client or httpx.AsyncClient(
            base_url=_GITHUB_API_ROOT,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client: Final = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def status(self) -> UpstreamSyncView:
        release: Final = await self._latest_release()
        report: Final = await self._report()
        return UpstreamSyncView(
            upstream_repository=self._settings.upstream_sync_upstream_repository,
            fork_repository=self._settings.upstream_sync_fork_repository,
            sync_branch=self._settings.upstream_sync_branch,
            current_tag=self._settings.upstream_sync_current_tag,
            latest_tag=release.tag_name,
            latest_release_url=release.html_url,
            update_available=_version_key(release.tag_name) > _version_key(self._settings.upstream_sync_current_tag),
            dispatch_configured=bool(self._token),
            report=report,
        )

    async def analyze(self) -> UpstreamSyncDispatch:
        state: Final = await self.status()
        if not state.update_available:
            raise UpstreamSyncError(409, "No newer upstream release is available")
        return await self._dispatch("analyze", state.latest_tag)

    async def promote(self) -> UpstreamSyncDispatch:
        state: Final = await self.status()
        report: Final = state.report
        if report.state != "passed" or report.target_tag != state.latest_tag:
            raise UpstreamSyncError(409, "The latest upstream release has not passed compatibility validation")
        return await self._dispatch("promote", state.latest_tag)

    async def codex_review_package(self) -> CodexReviewPackage:
        report: Final = await self._report()
        handoff: Final = await self._repository_text(_HANDOFF_PATH, self._settings.upstream_sync_branch, required=True)
        review: Final = await self._repository_text(_REVIEW_PATH, self._settings.upstream_sync_branch, required=True)
        target: Final = report.target_tag or "pending"
        return CodexReviewPackage(
            filename=f"codex-upstream-review-{target}.md",
            branch=self._settings.upstream_sync_branch,
            target_tag=report.target_tag,
            content=f"{handoff.rstrip()}\n\n---\n\n{review.rstrip()}\n",
        )

    async def _dispatch(self, action: Literal["analyze", "promote"], target_tag: str) -> UpstreamSyncDispatch:
        if not self._token:
            raise UpstreamSyncError(503, "GitHub workflow authorization is not configured")
        request_id: Final = uuid4()
        workflow: Final = quote(self._settings.upstream_sync_workflow, safe="")
        try:
            response: Final = await self._client.post(
                f"/repos/{self._settings.upstream_sync_fork_repository}/actions/workflows/{workflow}/dispatches",
                headers=self._headers(),
                json={
                    "ref": self._settings.upstream_sync_workflow_ref,
                    "inputs": {
                        "action": action,
                        "target_tag": target_tag,
                        "request_id": str(request_id),
                    },
                },
            )
        except httpx.HTTPError as error:
            raise UpstreamSyncError(502, "Unable to dispatch the GitHub compatibility workflow") from error
        if response.status_code != 204:
            raise UpstreamSyncError(502, _github_error(response, "GitHub rejected the workflow dispatch"))
        return UpstreamSyncDispatch(request_id=request_id, action=action, target_tag=target_tag)

    async def _latest_release(self) -> _GitHubRelease:
        try:
            response: Final = await self._client.get(
                f"/repos/{self._settings.upstream_sync_upstream_repository}/releases/latest",
                headers=self._headers(),
            )
        except httpx.HTTPError as error:
            raise UpstreamSyncError(502, "Unable to check the upstream release") from error
        if response.is_error:
            raise UpstreamSyncError(502, _github_error(response, "Unable to check the upstream release"))
        try:
            release: Final = _GitHubRelease.model_validate_json(response.content)
        except ValidationError as error:
            raise UpstreamSyncError(502, "GitHub returned an invalid upstream release") from error
        _version_key(release.tag_name)
        return release

    async def _report(self) -> UpstreamSyncReport:
        payload: Final = await self._repository_text(_STATUS_PATH, self._settings.upstream_sync_branch, required=False)
        if payload is None:
            return UpstreamSyncReport(message="No upstream compatibility report is available")
        try:
            return UpstreamSyncReport.model_validate_json(payload)
        except ValidationError as error:
            raise UpstreamSyncError(502, "The compatibility branch contains an invalid status report") from error

    @overload
    async def _repository_text(self, path: str, ref: str, *, required: Literal[True]) -> str: ...

    @overload
    async def _repository_text(self, path: str, ref: str, *, required: Literal[False]) -> str | None: ...

    async def _repository_text(self, path: str, ref: str, *, required: bool) -> str | None:
        encoded_path: Final = quote(path, safe="/")
        try:
            response: Final = await self._client.get(
                f"/repos/{self._settings.upstream_sync_fork_repository}/contents/{encoded_path}",
                headers=self._headers(),
                params={"ref": ref},
            )
        except httpx.HTTPError as error:
            raise UpstreamSyncError(502, f"Unable to read {path} from the sync branch") from error
        if response.status_code == 404 and not required:
            return None
        if response.is_error:
            raise UpstreamSyncError(502, _github_error(response, f"Unable to read {path} from the sync branch"))
        try:
            content: Final = _GitHubContent.model_validate_json(response.content)
        except ValidationError as error:
            raise UpstreamSyncError(502, f"GitHub returned invalid content metadata for {path}") from error
        try:
            decoded: Final = base64.b64decode("".join(content.content.split()), validate=True)
        except (binascii.Error, ValueError) as error:
            raise UpstreamSyncError(502, f"GitHub returned invalid content for {path}") from error
        if len(decoded) > _MAX_CONTENT_BYTES:
            raise UpstreamSyncError(502, f"GitHub content for {path} is too large")
        try:
            return decoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise UpstreamSyncError(502, f"GitHub content for {path} is not UTF-8") from error

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "User-Agent": "litellm-account-pool",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
        }


def _version_key(tag: str) -> tuple[int, int, int, int, str]:
    match: Final = _TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise UpstreamSyncError(502, f"Unsupported upstream release tag: {tag}")
    suffix: Final = tag[match.end(3) :]
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), int(not suffix), suffix


def _github_error(response: httpx.Response, fallback: str) -> str:
    try:
        payload: Final = _GitHubError.model_validate_json(response.content)
    except ValidationError:
        return fallback
    return f"{fallback}: {payload.message[:240]}"


__all__ = (
    "CodexReviewPackage",
    "GitHubUpstreamSyncService",
    "UpstreamSyncDispatch",
    "UpstreamSyncError",
    "UpstreamSyncReport",
    "UpstreamSyncView",
)
