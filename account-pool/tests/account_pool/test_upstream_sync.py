"""本文件验证上游版本检查、工作流调度和 Codex 审查包边界。"""

from __future__ import annotations

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


@pytest.mark.asyncio
async def test_status_reports_latest_release_and_fixed_branch_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
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
async def test_analyze_dispatches_only_the_latest_release_to_the_fixed_workflow() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
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
