"""本文件验证上游版本检查、工作流调度和 Codex 审查包边界。"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Final

import httpx
import pytest
from account_pool.config import Settings
from account_pool.upstream_sync import GitHubUpstreamSyncService, UpstreamSyncError
from pydantic import SecretStr, TypeAdapter


def _settings(token: str | None = None) -> Settings:
    return Settings(
        database_url="postgresql://account_pool:test@db:5432/account_pool",
        manager_token="m" * 32,
        secret_seed="s" * 32,
        ssh_host="127.0.0.1",
        ssh_user="litellm",
        upstream_sync_github_token=None if token is None else SecretStr(token),
    )


def _content_response(request: httpx.Request, text: str) -> httpx.Response:
    encoded: Final = base64.b64encode(text.encode()).decode()
    return httpx.Response(200, json={"encoding": "base64", "content": encoded}, request=request)


def _report(state: str = "passed", tag: str = "v7.3.2") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "state": state,
            "action": "analyze",
            "request_id": "af094d6b-f0da-4ad6-aa59-7704393a81a4",
            "target_tag": tag,
            "base_sha": "e851070a0d08fb631d5ee7c64ecfab9a30ecbd2a",
            "candidate_sha": "92589ae0e0592e5469fb5f2e7859ab9664155419",
            "conflict_files": [],
            "failed_steps": [],
            "message": "passed",
            "workflow_url": "https://github.com/VVV-345/CLIProxyAPI/actions/runs/1",
            "updated_at": "2026-09-14T12:00:00Z",
        }
    )


def _ready(request: httpx.Request) -> httpx.Response | None:
    if "/branches/" in request.url.path:
        return httpx.Response(200, json={"commit": {"sha": "e851070a0d08fb631d5ee7c64ecfab9a30ecbd2a"}})
    if "/actions/workflows/" in request.url.path and request.method == "GET":
        return httpx.Response(200, json={"state": "active"})
    if "/contents/.github/workflows/" in request.url.path:
        return _content_response(request, "name: upstream-sync")
    return None


@pytest.mark.asyncio
async def test_status_reports_latest_release_and_fixed_branch_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (ready := _ready(request)) is not None:
            return ready
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v7.3.2",
                    "html_url": "https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.2",
                },
                request=request,
            )
        assert request.url.params["ref"] == "codex/upstream-sync"
        return _content_response(request, _report())

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    service: Final = GitHubUpstreamSyncService(_settings(), client)

    state: Final = await service.status()

    assert state.current_tag == "v7.2.146"
    assert state.latest_tag == "v7.3.2"
    assert state.update_available is True
    assert state.dispatch_configured is False
    assert state.report.state == "passed"
    await client.aclose()


@pytest.mark.asyncio
async def test_status_reuses_release_and_readiness_checks_for_thirty_seconds() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if (ready := _ready(request)) is not None:
            return ready
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v7.3.2",
                    "html_url": "https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.2",
                },
                request=request,
            )
        return _content_response(request, _report())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        service = GitHubUpstreamSyncService(_settings("test-token"), client)
        await service.status()
        await service.status()

    assert sum(path.endswith("/releases/latest") for path in paths) == 1
    assert sum("/branches/" in path for path in paths) == 2
    assert sum("/contents/.github/workflows/" in path for path in paths) == 1
    assert sum("/actions/workflows/" in path for path in paths) == 1
    assert sum(path.endswith("upstream-sync-status.json") for path in paths) == 2


@pytest.mark.asyncio
async def test_manual_status_refresh_bypasses_the_short_cache() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if (ready := _ready(request)) is not None:
            return ready
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v7.3.2",
                    "html_url": "https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.2",
                },
                request=request,
            )
        return _content_response(request, _report())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        service = GitHubUpstreamSyncService(_settings("test-token"), client)
        await service.status()
        await service.status()
        await service.status(refresh=True)

    assert sum(path.endswith("/releases/latest") for path in paths) == 2
    assert sum("/branches/" in path for path in paths) == 4
    assert sum("/contents/.github/workflows/" in path for path in paths) == 2
    assert sum("/actions/workflows/" in path for path in paths) == 2


@pytest.mark.asyncio
async def test_independent_readiness_requests_start_concurrently() -> None:
    readiness_paths: list[str] = []
    all_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if (
            "/branches/" in request.url.path
            or "/contents/.github/workflows/" in request.url.path
            or "/actions/workflows/" in request.url.path
        ):
            readiness_paths.append(request.url.path)
            if len(readiness_paths) == 4:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=1)
        if (ready := _ready(request)) is not None:
            return ready
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v7.3.2",
                    "html_url": "https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.2",
                },
                request=request,
            )
        return _content_response(request, _report())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        state = await GitHubUpstreamSyncService(_settings("test-token"), client).status()

    assert state.dispatch_configured is True
    assert len(readiness_paths) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["status", "codex_review_package", "analyze"])
@pytest.mark.parametrize(
    "status_code,headers,expected", [(403, {"X-RateLimit-Remaining": "0"}, 429), (429, {}, 429), (403, {}, 502)]
)
async def test_github_rate_limits_are_distinct_from_permission_failures(
    operation: str, status_code: int, headers: dict[str, str], expected: int
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, headers=headers, json={"message": "GitHub error"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        service = GitHubUpstreamSyncService(_settings(), client, target="litellm")
        with pytest.raises(UpstreamSyncError) as caught:
            await getattr(service, operation)()
    assert caught.value.status_code == expected
    if expected == 429:
        assert (
            caught.value.message
            == "GitHub API rate limit exceeded; retry later or configure this target's GitHub token"
        )


@pytest.mark.asyncio
async def test_analyze_dispatches_only_the_latest_release_to_the_fixed_workflow() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (ready := _ready(request)) is not None:
            return ready
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v7.3.2",
                    "html_url": "https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.2",
                },
                request=request,
            )
        if request.method == "GET":
            return _content_response(request, _report("idle", "v7.3.2"))
        payload: Final = TypeAdapter(dict[str, object]).validate_json(request.content)
        inputs: Final = TypeAdapter(dict[str, str]).validate_python(payload["inputs"])
        assert request.headers["authorization"] == "Bearer github-secret"
        assert request.url.path.endswith("/actions/workflows/upstream-sync.yml/dispatches")
        assert payload["ref"] == "main"
        assert inputs["action"] == "analyze"
        assert inputs["target_tag"] == "v7.3.2"
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    service: Final = GitHubUpstreamSyncService(_settings("github-secret"), client)

    dispatched: Final = await service.analyze()

    assert dispatched.action == "analyze"
    assert dispatched.target_tag == "v7.3.2"
    assert dispatched.state == "queued"
    await client.aclose()


@pytest.mark.asyncio
async def test_promote_rejects_a_report_for_an_older_release() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (ready := _ready(request)) is not None:
            return ready
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v7.3.2",
                    "html_url": "https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.2",
                },
                request=request,
            )
        return _content_response(request, _report("passed", "v7.3.1"))

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    service: Final = GitHubUpstreamSyncService(_settings("github-secret"), client)

    with pytest.raises(UpstreamSyncError, match="has not passed"):
        await service.promote()
    await client.aclose()


@pytest.mark.asyncio
async def test_codex_review_package_combines_the_stable_handoff_and_latest_report() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("upstream-sync-status.json"):
            return _content_response(request, _report("failed"))
        if request.url.path.endswith("CODEX_UPSTREAM_SYNC.md"):
            return _content_response(request, "# Stable handoff\n")
        return _content_response(request, "# Latest failure\n")

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    service: Final = GitHubUpstreamSyncService(_settings(), client)

    package: Final = await service.codex_review_package()

    assert package.branch == "codex/upstream-sync"
    assert package.target_tag == "v7.3.2"
    assert "# Stable handoff" in package.content
    assert "# Latest failure" in package.content
    await client.aclose()


@pytest.mark.asyncio
async def test_litellm_sync_uses_a_separate_repository_and_branch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (ready := _ready(request)) is not None:
            return ready
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v1.101.0",
                    "html_url": "https://github.com/BerriAI/litellm/releases/tag/v1.101.0",
                },
                request=request,
            )
        assert request.url.path.startswith("/repos/VVV-345/litellm/contents/")
        assert request.url.params["ref"] == "codex/litellm-upstream-sync"
        return _content_response(request, _report("idle", "v1.101.0"))

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    service: Final = GitHubUpstreamSyncService(_settings(), client, target="litellm")

    state: Final = await service.status()

    assert state.target == "litellm"
    assert state.current_tag == "v1.100.0"
    assert state.upstream_repository == "BerriAI/litellm"
    assert state.fork_repository == "VVV-345/litellm"
    assert state.sync_branch == "codex/litellm-upstream-sync"
    await client.aclose()


@pytest.mark.asyncio
async def test_litellm_token_does_not_hide_missing_workflow_and_branch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={"tag_name": "v1.105.0", "html_url": "https://github.com/BerriAI/litellm/releases/tag/v1.105.0"},
            )
        return httpx.Response(404, json={"message": "Not Found"})

    settings = _settings().model_copy(update={"litellm_upstream_sync_github_token": SecretStr("test-token")})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        service = GitHubUpstreamSyncService(settings, client, target="litellm")
        state = await service.status()
        assert state.dispatch_configured is False
        assert "sync_branch_missing" in state.configuration_issues
        assert "workflow_missing" in state.configuration_issues
        with pytest.raises(UpstreamSyncError, match="configured"):
            await service.analyze()


def test_litellm_workflow_defaults_to_business_branch_not_main() -> None:
    assert _settings().litellm_upstream_sync_workflow_ref == "CLIProxyAPI分支"


@pytest.mark.asyncio
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "success"])
async def test_tracked_workflow_without_published_report_reaches_terminal_state(conclusion):
    from uuid import UUID

    request_id = UUID("11111111-1111-4111-8111-111111111111")

    def handler(request):
        if request.url.path.endswith("/runs"):
            return httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "display_title": f"litellm-upstream:analyze:v1.105.0:{request_id}",
                            "status": "completed",
                            "conclusion": conclusion,
                            "html_url": "https://github.com/VVV-345/litellm/actions/runs/123",
                            "updated_at": "2026-10-12T12:00:00Z",
                        }
                    ]
                },
            )
        if (ready := _ready(request)) is not None:
            return ready
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={"tag_name": "v1.105.0", "html_url": "https://github.com/BerriAI/litellm/releases/tag/v1.105.0"},
            )
        return httpx.Response(404)

    settings = _settings().model_copy(update={"litellm_upstream_sync_github_token": SecretStr("test-token")})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        state = await GitHubUpstreamSyncService(settings, client, target="litellm").status(request_id)
    assert state.report.request_id == request_id
    assert state.report.state == "failed"
    assert str(state.report.workflow_url).endswith("/123")


@pytest.mark.asyncio
async def test_readiness_rejects_explicit_wrong_litellm_workflow_ref():
    from account_pool.upstream_readiness import inspect_readiness

    async with httpx.AsyncClient(transport=httpx.MockTransport(_ready), base_url="https://api.github.com") as client:
        result = await inspect_readiness(
            client,
            {},
            "VVV-345/litellm",
            "codex/litellm-upstream-sync",
            "CLIProxyAPI分支",
            "litellm-upstream-sync.yml",
            "main",
            True,
            None,
        )
    assert "workflow_target_mismatch" in result.issues


@pytest.mark.asyncio
async def test_readiness_does_not_call_rate_limit_a_missing_branch():
    from account_pool.upstream_readiness import inspect_readiness

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(429)), base_url="https://api.github.com"
    ) as client:
        result = await inspect_readiness(
            client,
            {},
            "VVV-345/litellm",
            "codex/litellm-upstream-sync",
            "CLIProxyAPI分支",
            "litellm-upstream-sync.yml",
            "CLIProxyAPI分支",
            True,
            None,
        )
    assert result.issues == ("configuration_unavailable",)


@pytest.mark.asyncio
async def test_matching_legacy_report_finishes_tracking_without_run_name_uuid():
    from uuid import UUID

    def handler(request):
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json={"workflow_runs": []})
        if (ready := _ready(request)) is not None:
            return ready
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v7.3.2",
                    "html_url": "https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.2",
                },
            )
        return _content_response(request, _report("passed"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        result = await GitHubUpstreamSyncService(_settings("test-token"), client).status(
            UUID("af094d6b-f0da-4ad6-aa59-7704393a81a4")
        )
    assert result.report.state == "passed"


@pytest.mark.asyncio
async def test_workflow_poll_only_reads_the_matching_run_and_compatibility_report() -> None:
    from uuid import UUID

    request_id = UUID("af094d6b-f0da-4ad6-aa59-7704393a81a4")
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v7.3.2",
                    "html_url": "https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.2",
                },
                request=request,
            )
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json={"workflow_runs": []}, request=request)
        if (ready := _ready(request)) is not None:
            return ready
        return _content_response(request, _report())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        service = GitHubUpstreamSyncService(_settings("test-token"), client)
        await service.status()
        paths.clear()

        await service.status(request_id)

    assert len(paths) == 2
    assert any(path.endswith("/runs") for path in paths)
    assert any(path.endswith("upstream-sync-status.json") for path in paths)
    assert not any(path.endswith("/releases/latest") or "/branches/" in path for path in paths)
    assert not any(
        "/contents/.github/workflows/" in path or ("/actions/workflows/" in path and not path.endswith("/runs"))
        for path in paths
    )
