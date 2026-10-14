"""本模块封装上游版本检查和 GitHub 工作流调度，不参与模型请求数据面。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
import time
from dataclasses import dataclass
from typing import Final, Literal, overload
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from account_pool.config import Settings
from account_pool.upstream_readiness import Readiness, inspect_readiness

UpstreamSyncState = Literal["idle", "queued", "running", "conflict", "failed", "passed", "promoted", "pr_created"]
UpstreamSyncAction = Literal["none", "analyze", "promote"]
UpstreamSyncTarget = Literal["cliproxyapi", "litellm"]

_GITHUB_API_ROOT: Final = "https://api.github.com"
_STATUS_PATH: Final = ".codex/upstream-sync-status.json"
_REVIEW_PATH: Final = ".codex/upstream-sync-review.md"
_HANDOFF_PATH: Final = "CODEX_UPSTREAM_SYNC.md"
_TAG_PATTERN: Final = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)(?:[.-][0-9A-Za-z.-]+)?$")
_MAX_CONTENT_BYTES: Final = 2_000_000
_MAX_ENCODED_CONTENT_LENGTH: Final = 2_700_000
_STATUS_CACHE_SECONDS: Final = 30


class UpstreamSyncReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    state: UpstreamSyncState = "idle"
    action: UpstreamSyncAction = "none"
    request_id: UUID | None = None
    target_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    pull_request_url: HttpUrl | None = None
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

    target: UpstreamSyncTarget = "cliproxyapi"
    upstream_repository: str
    fork_repository: str
    sync_branch: str
    production_branch: str = "main"
    workflow_ref: str = "main"
    configuration_issues: tuple[str, ...] = ()
    report_stale: bool = False
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


class _GitHubRun(BaseModel):
    display_title: str
    status: str
    conclusion: str | None = None
    html_url: HttpUrl
    updated_at: AwareDatetime


class _GitHubRuns(BaseModel):
    workflow_runs: tuple[_GitHubRun, ...]


class _GitHubError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    message: str = Field(max_length=1000)


@dataclass(frozen=True, slots=True)
class _TargetConfig:
    target: UpstreamSyncTarget
    upstream_repository: str
    fork_repository: str
    current_tag: str
    branch: str
    workflow: str
    workflow_ref: str
    token: str


@dataclass(frozen=True, slots=True)
class _StatusSnapshot:
    cached_at: float
    release: _GitHubRelease
    readiness: Readiness


class UpstreamSyncError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code: Final = status_code
        self.message: Final = message


class GitHubUpstreamSyncService:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        target: UpstreamSyncTarget = "cliproxyapi",
    ) -> None:
        self._config: Final = _target_config(settings, target)
        self._client: Final = client or httpx.AsyncClient(
            base_url=_GITHUB_API_ROOT,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client: Final = client is None
        self._status_cache: _StatusSnapshot | None = None
        self._status_cache_lock: Final = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def status(self, request_id: UUID | None = None, *, refresh: bool = False) -> UpstreamSyncView:
        if request_id is None:
            report, snapshot = await asyncio.gather(self._report(), self._status_snapshot(refresh=refresh))
            return self._status_view(snapshot, report)
        if (cached_snapshot := self._status_cache) is None:
            report, snapshot, runs = await asyncio.gather(
                self._report(), self._status_snapshot(), self._workflow_runs(request_id)
            )
            return self._status_view(snapshot, _tracked_report_result(request_id, report, runs))
        return self._status_view(cached_snapshot, await self._tracked_report(request_id))

    def _status_view(self, snapshot: _StatusSnapshot, report: UpstreamSyncReport) -> UpstreamSyncView:
        release: Final = snapshot.release
        readiness: Final = snapshot.readiness
        return UpstreamSyncView(
            target=self._config.target,
            upstream_repository=self._config.upstream_repository,
            fork_repository=self._config.fork_repository,
            sync_branch=self._config.branch,
            current_tag=self._config.current_tag,
            latest_tag=release.tag_name,
            latest_release_url=release.html_url,
            update_available=_version_key(release.tag_name) > _version_key(self._config.current_tag),
            dispatch_configured=not readiness.issues,
            production_branch=self._production_branch,
            workflow_ref=self._config.workflow_ref,
            configuration_issues=readiness.issues,
            report_stale=readiness.stale
            or bool(report.base_sha and readiness.production_sha and readiness.production_sha != report.base_sha),
            report=report,
        )

    async def analyze(self) -> UpstreamSyncDispatch:
        state: Final = await self.status()
        if not state.dispatch_configured:
            raise UpstreamSyncError(409, "Upstream workflow is not configured or accessible")
        if not state.update_available:
            raise UpstreamSyncError(409, "No newer upstream release is available")
        return await self._dispatch("analyze", state.latest_tag)

    async def promote(self) -> UpstreamSyncDispatch:
        state: Final = await self.status()
        report: Final = state.report
        if not state.dispatch_configured:
            raise UpstreamSyncError(409, "Upstream workflow is not configured or accessible")
        if state.report_stale or report.state != "passed" or report.target_tag != state.latest_tag:
            raise UpstreamSyncError(409, "The latest upstream release has not passed compatibility validation")
        return await self._dispatch("promote", state.latest_tag)

    async def codex_review_package(self) -> CodexReviewPackage:
        report: Final = await self._report()
        handoff: Final = await self._repository_text(_HANDOFF_PATH, self._config.branch, required=True)
        review: Final = await self._repository_text(_REVIEW_PATH, self._config.branch, required=True)
        target: Final = report.target_tag or "pending"
        return CodexReviewPackage(
            filename=f"codex-upstream-review-{target}.md",
            branch=self._config.branch,
            target_tag=report.target_tag,
            content=f"{handoff.rstrip()}\n\n---\n\n{review.rstrip()}\n",
        )

    async def _dispatch(self, action: Literal["analyze", "promote"], target_tag: str) -> UpstreamSyncDispatch:
        if not self._token:
            raise UpstreamSyncError(503, "GitHub workflow authorization is not configured")
        request_id: Final = uuid4()
        workflow: Final = quote(self._config.workflow, safe="")
        try:
            response: Final = await self._client.post(
                f"/repos/{self._config.fork_repository}/actions/workflows/{workflow}/dispatches",
                headers=self._headers(),
                json={
                    "ref": self._config.workflow_ref,
                    "inputs": {
                        "action": action,
                        "target_tag": target_tag,
                        "request_id": str(request_id),
                    },
                },
            )
        except httpx.HTTPError as error:
            raise UpstreamSyncError(502, "Unable to dispatch the GitHub compatibility workflow") from error
        _raise_github_rate_limit(response)
        if response.status_code != 204:
            raise UpstreamSyncError(502, _github_error(response, "GitHub rejected the workflow dispatch"))
        return UpstreamSyncDispatch(request_id=request_id, action=action, target_tag=target_tag)

    async def _latest_release(self) -> _GitHubRelease:
        try:
            response: Final = await self._client.get(
                f"/repos/{self._config.upstream_repository}/releases/latest",
                headers=self._headers(),
            )
        except httpx.HTTPError as error:
            raise UpstreamSyncError(502, "Unable to check the upstream release") from error
        _raise_github_rate_limit(response)
        if response.is_error:
            raise UpstreamSyncError(502, _github_error(response, "Unable to check the upstream release"))
        try:
            release: Final = _GitHubRelease.model_validate_json(response.content)
        except ValidationError as error:
            raise UpstreamSyncError(502, "GitHub returned an invalid upstream release") from error
        _version_key(release.tag_name)
        return release

    async def _tracked_report(self, request_id: UUID) -> UpstreamSyncReport:
        report, runs = await asyncio.gather(self._report(), self._workflow_runs(request_id))
        return _tracked_report_result(request_id, report, runs)

    async def _workflow_runs(self, request_id: UUID) -> _GitHubRuns:
        workflow: Final = quote(self._config.workflow, safe="")
        try:
            response: Final = await self._client.get(
                f"/repos/{self._config.fork_repository}/actions/workflows/{workflow}/runs",
                headers=self._headers(),
                params={"event": "workflow_dispatch", "branch": self._config.workflow_ref, "per_page": 100},
            )
            _raise_github_rate_limit(response)
            response.raise_for_status()
            runs: Final = _GitHubRuns.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError) as error:
            raise UpstreamSyncError(
                502, "Unable to track the GitHub workflow; inspect Actions before retrying"
            ) from error
        return runs

    async def _status_snapshot(self, *, refresh: bool = False) -> _StatusSnapshot:
        if not refresh:
            initial_snapshot: Final = self._valid_status_snapshot()
            if initial_snapshot is not None:
                return initial_snapshot
        async with self._status_cache_lock:
            if not refresh:
                locked_snapshot: Final = self._valid_status_snapshot()
                if locked_snapshot is not None:
                    return locked_snapshot
            release, readiness = await asyncio.gather(
                self._latest_release(),
                inspect_readiness(
                    self._client,
                    self._headers(),
                    self._config.fork_repository,
                    self._config.branch,
                    self._production_branch,
                    self._config.workflow,
                    self._config.workflow_ref,
                    bool(self._token),
                    None,
                ),
            )
            snapshot: Final = _StatusSnapshot(time.monotonic(), release, readiness)
            self._status_cache = snapshot
            return snapshot

    def _valid_status_snapshot(self) -> _StatusSnapshot | None:
        snapshot: Final = self._status_cache
        if snapshot is None or time.monotonic() - snapshot.cached_at >= _STATUS_CACHE_SECONDS:
            return None
        return snapshot

    async def _report(self) -> UpstreamSyncReport:
        payload: Final = await self._repository_text(_STATUS_PATH, self._config.branch, required=False)
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
                f"/repos/{self._config.fork_repository}/contents/{encoded_path}",
                headers=self._headers(),
                params={"ref": ref},
            )
        except httpx.HTTPError as error:
            raise UpstreamSyncError(502, f"Unable to read {path} from the sync branch") from error
        _raise_github_rate_limit(response)
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

    @property
    def _production_branch(self) -> str:
        return "CLIProxyAPI分支" if self._config.target == "litellm" else "main"

    @property
    def _token(self) -> str:
        return self._config.token


def _tracked_report_result(request_id: UUID, report: UpstreamSyncReport, runs: _GitHubRuns) -> UpstreamSyncReport:
    run: Final = next((item for item in runs.workflow_runs if str(request_id) in item.display_title), None)
    if run is None:
        if report.request_id == request_id and report.state not in ("idle", "queued", "running"):
            return report
        return UpstreamSyncReport(
            request_id=request_id,
            state="queued",
            message="No matching run in the latest 100 dispatches; inspect GitHub Actions before retrying",
        )
    if run.status != "completed":
        return UpstreamSyncReport(
            request_id=request_id,
            state="running",
            workflow_url=run.html_url,
            updated_at=run.updated_at,
            message="GitHub compatibility workflow is running",
        )
    if report.request_id == request_id and (run.conclusion == "success" or report.state in ("failed", "conflict")):
        return report
    return UpstreamSyncReport(
        request_id=request_id,
        state="failed",
        workflow_url=run.html_url,
        updated_at=run.updated_at,
        message=f"Workflow {run.conclusion or 'completed'}; inspect Actions and the last branch report before retrying",
        failed_steps=("workflow_or_report_publication",),
    )


def _target_config(settings: Settings, target: UpstreamSyncTarget) -> _TargetConfig:
    if target == "cliproxyapi":
        secret: Final = settings.upstream_sync_github_token
        return _TargetConfig(
            target=target,
            upstream_repository=settings.upstream_sync_upstream_repository,
            fork_repository=settings.upstream_sync_fork_repository,
            current_tag=settings.upstream_sync_current_tag,
            branch=settings.upstream_sync_branch,
            workflow=settings.upstream_sync_workflow,
            workflow_ref=settings.upstream_sync_workflow_ref,
            token="" if secret is None else secret.get_secret_value().strip(),
        )
    secret = settings.litellm_upstream_sync_github_token
    return _TargetConfig(
        target=target,
        upstream_repository=settings.litellm_upstream_sync_upstream_repository,
        fork_repository=settings.litellm_upstream_sync_fork_repository,
        current_tag=settings.litellm_upstream_sync_current_tag,
        branch=settings.litellm_upstream_sync_branch,
        workflow=settings.litellm_upstream_sync_workflow,
        workflow_ref=settings.litellm_upstream_sync_workflow_ref,
        token="" if secret is None else secret.get_secret_value().strip(),
    )


def _version_key(tag: str) -> tuple[int, int, int, int, str]:
    match: Final = _TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise UpstreamSyncError(502, f"Unsupported upstream release tag: {tag}")
    suffix: Final = tag[match.end(3) :]
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), int(not suffix), suffix


def _raise_github_rate_limit(response: httpx.Response) -> None:
    if response.status_code == 429 or (
        response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0"
    ):
        raise UpstreamSyncError(
            429, "GitHub API rate limit exceeded; retry later or configure this target's GitHub token"
        )


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
    "UpstreamSyncTarget",
    "UpstreamSyncView",
)
