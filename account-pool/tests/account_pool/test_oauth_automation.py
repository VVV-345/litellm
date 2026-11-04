"""从管理员入口验证指纹独占、加密任务与多进程领取；使用专用隔离数据库。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Final
from uuid import uuid4

import httpx
import psycopg
import pytest
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import EnvironmentStatus
from account_pool.oauth_automation.models import JobInput, ProfileInput
from account_pool.oauth_automation.repository import PostgresAutomationRepository
from account_pool.oauth_automation.service import AutomationRegistry
from account_pool.shared.result import Failure, Success
from account_pool.shared.secrets import EnvironmentSecretDeriver
from pydantic import JsonValue
from test_account_pool import FakeCLIProxy, MemoryRepository, _record, _service

IMAGE: Final = "registry.example/worker@sha256:" + "d" * 64
CREDENTIAL: Final = "one@example.test----test-only-password----GEZDGNBVGY3TQOJQ"


@pytest.mark.asyncio
async def test_worker_control_hostname_is_a_valid_dns_label(tmp_path: Path) -> None:
    from account_pool.compose_runtime import ComposeRuntime
    from account_pool.oauth_automation.worker import AutomationWorker
    from account_pool.shared.secrets import EnvironmentSecretDeriver
    from test_account_pool import _settings

    def control(request: httpx.Request) -> httpx.Response:
        assert len(request.url.host.encode("idna")) <= 63
        return httpx.Response(200, json={"status": "running", "expires_at": "2099-01-01T00:00:00Z"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(control)) as http:
        worker: Final = AutomationWorker(ComposeRuntime(_settings(tmp_path), EnvironmentSecretDeriver("s" * 32)), http)
        assert (await worker.command(uuid4(), "job-scoped-token-only-" * 2, "status")).status == "running"


class ProfileWorker:
    image: str = IMAGE

    async def generate(self, locale: str) -> dict[str, JsonValue]:
        return {"version": "camoufox-0.5.9", "locale": locale, "fingerprint": {"fixture": "external-container-double"}}

    async def self_check(self) -> bool:
        return True


@pytest.fixture
async def registry():
    url: Final = os.environ.get("OAUTH_AUTOMATION_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("requires dedicated OAUTH_AUTOMATION_TEST_DATABASE_URL")
    assert url.endswith("/oauth_automation_test"), "refuse to clear a non-test database"
    store: Final = PostgresAutomationRepository(url)
    await store.initialize()
    async with await psycopg.AsyncConnection.connect(url) as connection:
        await connection.execute("DELETE FROM account_pool_oauth_automation")
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None).model_copy(
        update={"proxy_profile_id": "ordinary-proxy", "credential_fingerprints": (), "oauth_state": None},
    )
    environments: Final = MemoryRepository(record)
    registry: Final = AutomationRegistry(
        store, environments, ProfileWorker(), EnvironmentSecretDeriver("s" * 32), CredentialOwnership(),
    )
    return registry, record


@pytest.mark.asyncio
async def test_retries_keep_the_account_fingerprint_and_never_take_another_accounts_environment(registry) -> None:
    service, card = registry
    profile: Final = await service.create_profile(ProfileInput(name="Linux 01", locale="en-US"))
    assert isinstance(profile, Success)
    submitted: Final = await service.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(submitted, Success)
    assert submitted.value.profile_id == profile.value.id
    assert "test-only-password" not in (await service.view()).model_dump_json()
    await service.cancel(submitted.value.id)
    retry: Final = await service.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(retry, Success)
    assert retry.value.profile_id == profile.value.id
    other: Final = card.model_copy(update={"id": uuid4()})
    service.environments.records[other.id] = other
    blocked: Final = await service.submit(JobInput(
        id=uuid4(), environment_id=other.id,
        credentials="two@example.test----test-only-password----GEZDGNBVGY3TQOJQ",
    ))
    assert isinstance(blocked, Failure)
    assert "指纹" in blocked.message
    assert len((await service.view()).jobs) == 2


@pytest.mark.asyncio
async def test_worker_ownership_excludes_another_python_process(registry) -> None:
    service, _ = registry
    child: Final = """
import asyncio, os, sys
from account_pool.oauth_automation.repository import PostgresAutomationRepository
async def main():
    store = PostgresAutomationRepository(os.environ['OAUTH_AUTOMATION_TEST_DATABASE_URL'])
    async with store.worker_lock() as acquired:
        print('acquired' if acquired else 'blocked', flush=True)
        await asyncio.to_thread(sys.stdin.readline)
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())
"""
    process: Final = subprocess.Popen(
        [sys.executable, "-c", child], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert process.stdout is not None and process.stdin is not None
    try:
        assert (await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=15)).strip() == "acquired"
        async with service.repository.worker_lock() as acquired:
            assert not acquired
        process.stdin.write("release\n")
        process.stdin.flush()
        assert await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=10) == 0
        async with service.repository.worker_lock() as acquired:
            assert acquired
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait)


@pytest.mark.asyncio
async def test_duplicate_job_id_does_not_enqueue_or_rebind_twice(registry) -> None:
    service, card = registry
    await service.create_profile(ProfileInput(name="Linux"))
    request: Final = JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL)
    first: Final = await service.submit(request)
    second: Final = await service.submit(request)
    assert isinstance(first, Success) and isinstance(second, Success) and first.value == second.value
    assert len((await service.view()).jobs) == 1
    assert isinstance(await service.submit(request.model_copy(update={"environment_id": uuid4()})), Failure)


@pytest.mark.asyncio
async def test_manual_authorization_cannot_clear_a_card_owned_by_a_running_automation(tmp_path: Path) -> None:
    operation: Final = "automation:00000000000000000000000000000001:1"
    card: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None).model_copy(
        update={"oauth_automation_operation": operation},
    )
    cli: Final = FakeCLIProxy()
    service: Final = _service(card, cli, tmp_path)
    result: Final = await service.authorize_environment(card.id)
    assert isinstance(result, Failure)
    assert "自动化" in result.message
    assert (await service.get_environment(card.id)).value.id == card.id


@pytest.fixture
async def execution(registry, tmp_path: Path):
    from account_pool.compose_runtime import ComposeRuntime
    from account_pool.controlled_oauth_browser import ControlledOAuthBrowser
    from account_pool.oauth_automation.runner import AutomationRunner
    from account_pool.oauth_automation.worker import AutomationWorker
    from account_pool.oauth_browser import OAuthBrowserSessionService
    from account_pool.service import EnvironmentService
    from test_account_pool import FakeRuntime, StaticProfiles, _fake_channels, _settings
    from test_oauth_browser_api import MemoryBrowserSessions

    registry_service, card = registry
    profiles: Final = StaticProfiles("http://ordinary-proxy.test:8080")
    class CredentialCLI(FakeCLIProxy):
        async def read_account(self, record, *, refresh_quota=False):
            observed = await super().read_account(record, refresh_quota=refresh_quota)
            return observed.model_copy(update={
                "credential_email": "one@example.test", "credential_fingerprints": ("fixture-credential-owner",),
                "enabled_models": record.enabled_models or observed.enabled_models,
                "model_cooldowns": record.model_cooldowns,
            })

    cli: Final = CredentialCLI()
    runtime: Final = FakeRuntime()
    manager: Final = EnvironmentService(
        settings=_settings(tmp_path), repository=registry_service.environments, runtime=runtime,
        cli_proxy=cli, proxy_profiles=profiles, secrets=registry_service.secrets,
        channels=_fake_channels(runtime, cli), ownership=registry_service.ownership,
    )
    starts: Final[list[object]] = []
    removals: Final[list[object]] = []

    class BrowserProcess:
        async def start_oauth_browser(self, session, **kwargs):
            assert kwargs["automation"].fingerprint_json
            starts.append(session.id)

        async def remove_oauth_browser(self, session_id):
            removals.append(session_id)

    browser: Final = ControlledOAuthBrowser(
        registry_service.environments, profiles,
        OAuthBrowserSessionService(MemoryBrowserSessions.create(), fingerprint_key=b"test-only-automation-browser-key"),
        BrowserProcess(), manager, registry_service.ownership,
    )
    commands: Final[list[str]] = []

    async def external_worker(request: httpx.Request) -> httpx.Response:
        import json
        if request.url.path.endswith("/chat/completions"):
            commands.append("chat")
            while "hold-chat" in commands:
                await asyncio.sleep(0.02)
            if "inference-down" in commands:
                return httpx.Response(503, json={"error": {"message": "private-upstream-body-must-not-be-shown"}})
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Hello"}}]})
        if request.url.path.endswith("/responses"):
            commands.append("responses")
            if "inference-down" in commands:
                return httpx.Response(503, json={"error": {"message": "private-upstream-body-must-not-be-shown"}})
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]})
        action = json.loads(request.content)["action"]
        commands.append(action)
        return httpx.Response(200, json={"status": "manual" if action == "takeover" else "running", "reason": None, "expires_at": "2099-01-01T00:00:00Z"})

    await registry_service.create_profile(ProfileInput(name="test Linux"))
    submitted: Final = await registry_service.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(submitted, Success)
    async with httpx.AsyncClient(transport=httpx.MockTransport(external_worker)) as http:
        worker: Final = AutomationWorker(ComposeRuntime(_settings(tmp_path).model_copy(update={"oauth_browser_image": IMAGE}), registry_service.secrets), http)
        runner: Final = AutomationRunner(registry_service, browser, worker)
        yield registry_service, card, manager, runner, browser, commands, starts, removals, submitted


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ("cancel", "callback"))
async def test_runner_is_disabled_by_default_and_restarts_without_resubmitting_login(execution, finish: str) -> None:
    from account_pool.domain import OAuthCallback
    from account_pool.oauth_automation.models import AutomationSettings
    from account_pool.oauth_automation.runner import AutomationRunner
    registry_service, card, manager, runner, browser, commands, starts, removals, submitted = execution
    await runner.run_once()
    assert starts == []
    await registry_service.self_check()
    view: Final = await registry_service.view()
    assert isinstance(await registry_service.save_settings(AutomationSettings(enabled=True), view.version), Success)
    await asyncio.gather(runner.run_once(), runner.run_once())
    assert len(starts) == 1
    assert commands.count("start") == 1


    assert (await registry_service.view()).jobs[0].stage == "running"
    await AutomationRunner(registry_service, browser, runner.worker).run_once()
    assert commands.count("start") == 1
    if finish == "callback":
        pending: Final = await registry_service.environments.get(card.id)
        assert isinstance(await manager.submit_oauth_callback(OAuthCallback(state=pending.oauth_state, code="one-use-fixture-code"), card.id), Success)
        await runner.run_once()
        completed: Final = (await registry_service.view()).jobs[0]
        assert completed.stage == "completed"
        assert completed.callback_consumed and completed.credentials_saved
        assert completed.chat_verified and completed.responses_verified
        assert commands.count("start") == commands.count("chat") == commands.count("responses") == 1
        assert (await registry_service.environments.get(card.id)).oauth_automation_operation is None
        return
    await registry_service.cancel(submitted.value.id)
    await runner.run_once()
    assert (await registry_service.view()).jobs[0].stage == "cancelled"
    assert starts[0] in removals
    assert (await registry_service.environments.get(card.id)).oauth_automation_operation is None


@pytest.mark.asyncio
async def test_cancel_during_inference_aborts_without_starting_responses(execution) -> None:
    from account_pool.domain import OAuthCallback
    from account_pool.oauth_automation.models import AutomationSettings
    service, card, manager, runner, browser, commands, starts, removals, submitted = execution
    await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), (await service.view()).version)
    await runner.run_once()
    pending: Final = await service.environments.get(card.id)
    await manager.submit_oauth_callback(OAuthCallback(state=pending.oauth_state, code="one-use-test-code"), card.id)
    commands.append("hold-chat")
    advancing: Final = asyncio.create_task(runner.run_once())
    async with asyncio.timeout(2):
        while "chat" not in commands:
            await asyncio.sleep(0.02)
    await service.cancel(submitted.value.id)
    await asyncio.wait_for(advancing, timeout=2)
    assert (await service.view()).jobs[0].stage == "cancelled"
    assert "responses" not in commands
    assert (await service.environments.get(card.id)).oauth_automation_operation is None


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", ("total", "human"))
async def test_expired_run_cleans_up_without_replaying_login(execution, deadline: str) -> None:
    from account_pool.oauth_automation.models import AutomationSettings
    from account_pool.oauth_automation.runner import AutomationRunner

    service, card, manager, runner, browser, commands, starts, removals, submitted = execution
    await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), (await service.view()).version)
    await runner.run_once()
    running: Final = (await service.view()).jobs[0]
    assert running.expires_at is not None
    if deadline == "human":
        await runner.action(running.id, "takeover")
        waiting: Final = (await service.view()).jobs[0]
        assert waiting.human_expires_at is not None
        expiry: Final = waiting.human_expires_at
    else:
        expiry = running.expires_at
    after: Final = AutomationRunner(service, browser, runner.worker, clock=lambda: expiry + timedelta(seconds=1))
    await after.run_once()
    assert (await service.view()).jobs[0].stage == "expired"
    assert commands.count("start") == len(starts) == 1 and starts[0] in removals
    assert (await service.environments.get(card.id)).oauth_automation_operation is None


@pytest.mark.asyncio
async def test_restart_with_unknown_inference_result_requires_explicit_verification_retry(execution) -> None:
    from account_pool.domain import OAuthCallback
    from account_pool.oauth_automation.models import AutomationSettings
    from account_pool.oauth_automation.runner import AutomationRunner

    service, card, manager, runner, browser, commands, starts, removals, submitted = execution
    await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), (await service.view()).version)
    await runner.run_once()
    pending: Final = await service.environments.get(card.id)
    await manager.submit_oauth_callback(OAuthCallback(state=pending.oauth_state, code="one-use-test-code"), card.id)
    async with service.repository.transaction() as tx:
        row: Final = tx.snapshot.jobs[0]
        interrupted: Final = row.model_copy(update={"verification_started": True, "item": row.item.model_copy(update={"stage": "verifying"})})
        await tx.save(tx.snapshot.model_copy(update={"jobs": (interrupted,)}))
    await AutomationRunner(service, browser, runner.worker).run_once()
    failed: Final = (await service.view()).jobs[0]
    assert failed.stage == "verification_failed" and failed.credentials_saved
    assert commands.count("start") == len(starts) == 1
    assert "chat" not in commands and "responses" not in commands


@pytest.mark.asyncio
async def test_admin_api_is_default_off_authenticated_and_never_echoes_credentials(execution) -> None:
    from account_pool.oauth_automation.api import create_automation_router
    from fastapi import FastAPI, Header, HTTPException

    registry_service, card, manager, runner, browser, commands, starts, removals, submitted = execution

    def authorize(authorization: str = Header(default="")) -> None:
        if authorization != "Bearer isolated-admin-only":
            raise HTTPException(401, "unauthorized")

    app: Final = FastAPI()
    app.include_router(create_automation_router(runner, authorize))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://manager.test") as client:
        assert (await client.get("/api/oauth-automation")).status_code == 401
        headers: Final = {"Authorization": "Bearer isolated-admin-only"}
        view: Final = await client.get("/api/oauth-automation", headers=headers)
        assert view.status_code == 200 and view.json()["settings"]["enabled"] is False
        assert view.headers["cache-control"] == "no-store"
        assert "test-only-password" not in view.text and "account_digest" not in view.text
        invalid: Final = await client.post("/api/oauth-automation/jobs", headers=headers, json={"id": "invalid", "credentials": CREDENTIAL})
        assert invalid.status_code == 422 and "test-only-password" not in invalid.text
        blocked: Final = await client.put("/api/oauth-automation/settings", headers=headers, json={"version": view.json()["version"], "settings": {"enabled": True}})
        assert blocked.status_code == 409
        checked: Final = await client.post("/api/oauth-automation/self-check", headers=headers)
        assert checked.status_code == 200 and checked.json()["image_checked"] is True
        enabled: Final = await client.put("/api/oauth-automation/settings", headers=headers, json={"version": checked.json()["version"], "settings": {"enabled": True}})
        assert enabled.status_code == 200 and enabled.json()["settings"]["enabled"] is True


@pytest.mark.asyncio
async def test_saved_credentials_retry_only_inference_and_keep_the_same_fingerprint(execution) -> None:
    from account_pool.domain import OAuthCallback
    from account_pool.oauth_automation.models import AutomationSettings
    service, card, manager, runner, browser, commands, starts, removals, submitted = execution
    await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), (await service.view()).version)
    await runner.run_once()
    pending: Final = await service.environments.get(card.id)
    await manager.submit_oauth_callback(OAuthCallback(state=pending.oauth_state, code="one-use-test-code"), card.id)
    commands.append("inference-down")
    await runner.run_once()
    failed: Final = (await service.view()).jobs[0]
    assert failed.stage == "verification_failed" and failed.credentials_saved
    assert "Chat: HTTP 503" in failed.message and "Responses: HTTP 503" in failed.message
    assert "private-upstream-body" not in (await service.view()).model_dump_json()
    commands.remove("inference-down")
    assert isinstance(await runner.action(failed.id, "retry", model="not-on-this-card"), Failure)
    assert isinstance(await runner.action(failed.id, "retry", model="gpt-5"), Success)
    await runner.run_once()
    completed: Final = (await service.view()).jobs[0]
    assert completed.stage == "completed" and completed.profile_id == submitted.value.profile_id
    assert commands.count("start") == len(starts) == 1
    assert commands.count("chat") == commands.count("responses") == 2


@pytest.mark.asyncio
async def test_automatic_model_verification_skips_an_active_model_cooldown(execution) -> None:
    from account_pool.domain import ModelCooldown, OAuthCallback
    from account_pool.oauth_automation.models import AutomationSettings

    service, card, manager, runner, browser, commands, starts, removals, submitted = execution
    await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), (await service.view()).version)
    await runner.run_once()
    pending = await service.environments.get(card.id)
    await manager.submit_oauth_callback(OAuthCallback(state=pending.oauth_state, code="one-use-test-code"), card.id)
    ready = await service.environments.get(card.id)
    service.environments.records[card.id] = ready.model_copy(update={
        "enabled_models": ("gpt-5-cooling", "gpt-5"),
        "model_cooldowns": (ModelCooldown(model="gpt-5-cooling", retry_at=runner.clock() + timedelta(hours=1)),),
    })

    await runner.run_once()

    completed = (await service.view()).jobs[0]
    assert completed.stage == "completed" and completed.model == "gpt-5"
    assert commands.count("start") == commands.count("chat") == commands.count("responses") == 1


@pytest.mark.asyncio
async def test_cooling_model_is_not_probed_and_verification_retry_never_repeats_login(execution) -> None:
    from account_pool.domain import ModelCooldown, OAuthCallback
    from account_pool.oauth_automation.models import AutomationSettings

    service, card, manager, runner, browser, commands, starts, removals, submitted = execution
    await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), (await service.view()).version)
    await runner.run_once()
    pending = await service.environments.get(card.id)
    await manager.submit_oauth_callback(OAuthCallback(state=pending.oauth_state, code="one-use-test-code"), card.id)
    ready = await service.environments.get(card.id)
    service.environments.records[card.id] = ready.model_copy(update={
        "model_cooldowns": (ModelCooldown(model="gpt-5", retry_at=runner.clock() + timedelta(hours=1)),),
    })

    await runner.run_once()
    failed = (await service.view()).jobs[0]
    assert failed.stage == "verification_failed" and failed.credentials_saved
    assert "冷却" in failed.message
    assert isinstance(await runner.action(failed.id, "retry", model="gpt-5"), Success)
    await runner.run_once()
    assert (await service.view()).jobs[0].stage == "verification_failed"
    assert commands.count("chat") == commands.count("responses") == 0

    latest = await service.environments.get(card.id)
    service.environments.records[card.id] = latest.model_copy(update={
        "model_cooldowns": (ModelCooldown(model="gpt-5", retry_at=runner.clock() - timedelta(seconds=1)),),
    })
    assert isinstance(await runner.action(failed.id, "retry", model="gpt-5"), Success)
    await runner.run_once()
    assert (await service.view()).jobs[0].stage == "completed"
    assert commands.count("start") == len(starts) == 1
    assert commands.count("chat") == commands.count("responses") == 1


@pytest.mark.asyncio
async def test_takeover_waits_for_worker_ack_and_running_credentials_are_immutable(execution) -> None:
    from account_pool.oauth_automation.models import AutomationSettings
    service, card, manager, runner, browser, commands, starts, removals, submitted = execution
    await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), (await service.view()).version)
    await runner.run_once()
    assert isinstance(await runner.action(submitted.value.id, "takeover"), Success)
    taken: Final = (await service.view()).jobs[0]
    assert taken.stage == "manual" and taken.human_expires_at <= taken.expires_at
    assert taken.human_expires_at <= runner.clock() + timedelta(seconds=300)
    assert commands[-1] == "takeover"
    assert isinstance(await manager.delete_auth_file(card.id), Failure)
    assert isinstance(await runner.action(submitted.value.id, "resume"), Success)
    assert (await service.view()).jobs[0].stage == "running"
    assert commands.count("start") == 1
