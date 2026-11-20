"""从管理员入口验证指纹独占、加密任务与多进程领取；使用专用隔离数据库。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import uuid4

import httpx
import psycopg
import pytest
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import ChannelKind, EnvironmentStatus
from account_pool.oauth_automation.models import JobInput, ProfileInput
from account_pool.oauth_automation.repository import PostgresAutomationRepository
from account_pool.oauth_automation.service import AutomationRegistry
from account_pool.shared.result import Failure, Success
from account_pool.shared.secrets import EnvironmentSecretDeriver
from pydantic import JsonValue
from test_account_pool import (
    FakeChannel,
    FakeCLIProxy,
    MemoryRepository,
    StaticProfiles,
    _record,
    _service,
)

IMAGE: Final = "registry.example/worker@sha256:" + "d" * 64
CREDENTIAL: Final = "one@example.test----test-only-password----GEZDGNBVGY3TQOJQ"


class MemoryAutomationStore:
    def __init__(self):
        from account_pool.oauth_automation.models import AutomationState
        self.snapshot = AutomationState()

    async def read(self):
        return self.snapshot

    async def save(self, state):
        self.snapshot = state.model_copy(update={"version": self.snapshot.version + 1})
        return self.snapshot

    @asynccontextmanager
    async def transaction(self):
        yield self

    @asynccontextmanager
    async def worker_lock(self):
        yield True


@pytest.fixture
def deletion_registry():
    card = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None).model_copy(
        update={"proxy_profile_id": "ordinary-proxy", "credential_fingerprints": (), "oauth_state": None},
    )
    return AutomationRegistry(
        MemoryAutomationStore(), MemoryRepository(card), ProfileWorker(), EnvironmentSecretDeriver("s" * 32),
        CredentialOwnership(), StaticProfiles("http://ordinary-proxy.test:8080"), FixtureLocationProbe(),
    ), card


@pytest.mark.asyncio
async def test_deleted_card_binding_can_be_removed_before_the_same_account_uses_a_new_profile(deletion_registry):
    service, card = deletion_registry
    profile = await service.create_profile(ProfileInput(name="original"))
    assert isinstance(profile, Success)
    submitted = await service.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(submitted, Success)
    await service.cancel(submitted.value.id)
    service.environments.records.pop(card.id)
    replacement = card.model_copy(update={"id": uuid4()})
    service.environments.records[replacement.id] = replacement
    new_profile = await service.create_profile(ProfileInput(name="replacement"))
    assert isinstance(new_profile, Success)
    blocked = await service.submit(JobInput(id=uuid4(), environment_id=replacement.id, credentials=CREDENTIAL))
    assert isinstance(blocked, Failure) and "原卡片已删除" in blocked.message
    removed = await service.delete_profile(profile.value.id)
    assert isinstance(removed, Success)
    assert all(p.id != profile.value.id for p in removed.value.profiles)
    assert len(removed.value.jobs) == 1
    retried = await service.submit(JobInput(id=uuid4(), environment_id=replacement.id, credentials=CREDENTIAL))
    assert isinstance(retried, Success) and retried.value.profile_id == new_profile.value.id


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,cleanup,card_exists", [
    ("queued", False, False), ("running", False, False), ("failed", True, False),
    ("completed", False, True), ("cancelled", False, True),
])
async def test_profile_deletion_preserves_live_bindings_and_unfinished_cleanup(
    deletion_registry, stage, cleanup, card_exists,
):
    service, card = deletion_registry
    profile = await service.create_profile(ProfileInput(name="protected"))
    submitted = await service.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(profile, Success) and isinstance(submitted, Success)
    async with service.repository.transaction() as tx:
        await tx.save(tx.snapshot.model_copy(update={"jobs": tuple(
            row.model_copy(update={"cleanup_pending": cleanup, "item": row.item.model_copy(update={"stage": stage})})
            for row in tx.snapshot.jobs
        )}))
    if not card_exists:
        service.environments.records.pop(card.id)
    assert isinstance(await service.delete_profile(profile.value.id), Failure)
    assert (await service.view()).profiles[0].id == profile.value.id


@pytest.mark.asyncio
async def test_orphaned_fingerprint_migrates_only_to_the_same_account(deletion_registry) -> None:
    service, card = deletion_registry
    profile = await service.create_profile(ProfileInput(name="preserved original"))
    assert isinstance(profile, Success)
    started = await service.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(started, Success)
    bound_digest = (await service.view()).profiles[0].digest
    await service.cancel(started.value.id)
    replacement = card.model_copy(update={"id": uuid4()})
    await service.environments.save(replacement)
    request = JobInput(
        id=uuid4(), environment_id=replacement.id, credentials=CREDENTIAL, reuse_profile_id=profile.value.id,
    )
    assert isinstance(await service.submit(request), Failure)
    await service.environments.delete(card.id)
    assert isinstance(await service.submit(request.model_copy(update={
        "credentials": CREDENTIAL.replace("one@", "other@"),
    })), Failure)
    accepted = await service.submit(request)
    assert isinstance(accepted, Success) and accepted.value.profile_id == profile.value.id
    migrated = (await service.view()).profiles[0]
    assert migrated.digest == bound_digest and migrated.bound_environment_id == replacement.id


@pytest.mark.asyncio
async def test_cancelled_prelogin_reservation_can_be_released_without_deleting_its_card(deletion_registry) -> None:
    service, card = deletion_registry
    profile = await service.create_profile(ProfileInput(name="laboratory"))
    assert isinstance(profile, Success)
    queued = await service.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(queued, Success)
    assert isinstance(await service.release_reservation(profile.value.id), Failure)
    await service.cancel(queued.value.id)
    released = await service.release_reservation(profile.value.id)
    assert isinstance(released, Success) and released.value.profiles[0].bound_environment_id is None
    assert await service.environments.get(card.id) is not None
    retargeted = await service.submit(JobInput(
        id=uuid4(), environment_id=card.id, credentials=CREDENTIAL.replace("one@", "corrected@"),
    ))
    assert isinstance(retargeted, Success) and retargeted.value.profile_id == profile.value.id


@pytest.mark.asyncio
async def test_unused_profile_can_still_be_deleted(deletion_registry):
    service, _ = deletion_registry
    profile = await service.create_profile(ProfileInput(name="unused"))
    assert isinstance(profile, Success)
    result = await service.delete_profile(profile.value.id)
    assert isinstance(result, Success) and result.value.profiles == ()


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

    async def generate(self, locale: str, os="linux") -> dict[str, JsonValue]:
        return {"version": "camoufox-0.5.9", "locale": "en-US" if locale == "US" else locale, "os": os, "fingerprint": {"fixture": "external-container-double"}}

    async def self_check(self) -> bool:
        return True


class FixtureLocationProbe:
    async def locate(self, proxy_url):
        from account_pool.isp_probe import ProxyLocation
        return ProxyLocation(
            exit_ip="8.8.8.8", country_code="US", country="United States", timezone="America/Los_Angeles",
        )


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
        StaticProfiles("http://ordinary-proxy.test:8080"), FixtureLocationProbe(),
    )
    return registry, record


@pytest.mark.asyncio
async def test_new_card_intent_is_durable_before_provisioning(memory_execution) -> None:
    from account_pool.oauth_automation.api import create_automation_router
    from fastapi import FastAPI

    registry, _, _, runner, _, _, starts, _, existing = memory_execution
    await registry.cancel(existing.value.id)
    await registry.create_profile(ProfileInput(name="available"))
    app = FastAPI()
    app.include_router(create_automation_router(runner, lambda: None))
    operation = str(uuid4())
    body = {
        "id": operation, "environment_id": operation,
        "credentials": CREDENTIAL.replace("one@", "draft@"),
        "draft": {"name": "queued draft", "proxy_profile_id": "ordinary-proxy"},
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://manager.test") as client:
        first = await client.post("/api/oauth-automation/jobs", json=body)
        assert first.status_code == 202, first.text
        duplicate = await client.post("/api/oauth-automation/jobs", json=body)
        assert first.json() == duplicate.json()
        changed = await client.post("/api/oauth-automation/jobs", json={
            **body, "draft": {**body["draft"], "name": "another intent"},
        })
        assert changed.status_code == 409
    assert first.json()["environment_id"] == operation
    assert first.json()["draft_pending"] is True
    assert len(await registry.environments.list()) == 1 and not starts


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
@pytest.mark.parametrize(("country", "locale", "timezone"), [
    ("US", "en-US", "America/Los_Angeles"),
    ("DE", "de-DE", "Europe/Berlin"),
    ("HK", "zh-HK", "Asia/Hong_Kong"),
    ("UA", "uk-UA", "Europe/Kyiv"),
    ("NZ", "NZ", "Pacific/Auckland"),
])
async def test_new_accounts_match_proxy_once_and_keep_the_location_on_retry(registry, country, locale, timezone) -> None:
    from account_pool.isp_probe import ProxyLocation
    from test_account_pool import StaticProfiles

    service, card = registry
    lookups = []

    class LocationProbe:
        async def locate(self, proxy_url):
            lookups.append(proxy_url)
            return ProxyLocation(
                exit_ip="8.8.8.8", country_code=country, country=country, timezone=timezone,
            )

    configured = replace(
        service, proxy_profiles=StaticProfiles("http://selected-proxy.test:8080"), location_probe=LocationProbe(),
    )
    await configured.create_profile(ProfileInput(name="auto"))
    first = await configured.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(first, Success)
    profile = (await configured.view()).profiles[0]
    assert profile.location.country_code == country
    assert profile.location.timezone == timezone
    assert profile.matched_locale == locale
    digest = profile.digest
    await configured.cancel(first.value.id)
    again = await configured.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(again, Success)
    assert (await configured.view()).profiles[0].digest == digest
    assert lookups == ["http://selected-proxy.test:8080"]
    saved = (await configured.repository.read()).profiles[0].data
    assert saved["locale"] == locale
    assert saved["timezone"] == timezone


@pytest.mark.asyncio
async def test_profile_language_presets_and_manual_locale_use_one_contract(deletion_registry) -> None:
    from pydantic import ValidationError

    service, _ = deletion_registry
    presets = (await service.view()).locale_presets
    assert set(presets) == {
        "en-US", "en-GB", "zh-CN", "ja-JP", "pt-BR", "es-AR", "en-AU", "en-CA",
        "zh-TW", "zh-HK", "de-DE", "fr-FR", "ru-RU", "ko-KR", "en-SG", "tr-TR", "uk-UA",
    }
    for locale in (*presets, "it-IT"):
        created = await service.create_profile(ProfileInput(name=locale, locale=locale, auto_match_proxy=False))
        assert isinstance(created, Success) and created.value.locale == locale
    for invalid in ("DE", "uk-ua", "de_DE", "ja-JP/extra"):
        with pytest.raises(ValidationError):
            ProfileInput(name="invalid", locale=invalid, auto_match_proxy=False)


@pytest.mark.asyncio
async def test_location_failure_does_not_bind_profile_save_credentials_or_queue_login(registry) -> None:
    service, card = registry

    class UnavailableLocation:
        async def locate(self, proxy_url):
            raise httpx.ConnectError("private-proxy-user:private-proxy-password")

    configured = replace(service, location_probe=UnavailableLocation())
    await configured.create_profile(ProfileInput(name="auto"))
    before = (await configured.view()).profiles[0]
    result = await configured.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(result, Failure)
    assert "匹配失败" in result.message
    assert "private-proxy" not in result.message
    after = await configured.view()
    assert after.jobs == ()
    assert after.profiles[0] == before
    assert (await configured.repository.read()).profiles[0].account_digest is None


@pytest.mark.asyncio
async def test_manual_and_legacy_profiles_keep_their_original_fingerprint(registry) -> None:
    from account_pool.oauth_automation.models import ProfileView

    service, card = registry

    class ForbiddenLookup:
        async def locate(self, proxy_url):
            pytest.fail("manual or legacy profiles must not perform a location lookup")

    configured = replace(service, location_probe=ForbiddenLookup())
    created = await configured.create_profile(ProfileInput(name="existing", locale="zh-CN", auto_match_proxy=False))
    assert isinstance(created, Success)
    legacy = ProfileView.model_validate(created.value.model_dump(exclude={"auto_match_proxy", "location", "matched_locale"}))
    assert legacy.auto_match_proxy is False
    assert isinstance(await configured.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL)), Success)
    bound = (await configured.view()).profiles[0]
    assert bound.digest == created.value.digest
    assert bound.locale == "zh-CN"
    assert bound.location is None


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ("linux", "windows", "macos"))
async def test_selected_browser_system_is_saved_and_kept_when_matching_proxy(registry, platform) -> None:
    service, card = registry
    created = await service.create_profile(ProfileInput(name="system", os=platform))
    assert isinstance(created, Success)
    assert created.value.os == platform
    submitted = await service.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(submitted, Success)
    saved = (await service.repository.read()).profiles[0]
    assert saved.item.os == platform
    assert saved.data["os"] == platform
    digest = saved.item.digest
    await service.cancel(submitted.value.id)
    assert isinstance(await service.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL)), Success)
    assert (await service.view()).profiles[0].digest == digest


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
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


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
def authorized_email() -> str:
    return "one@example.test"


@asynccontextmanager
async def _execution(registry_data, tmp_path: Path, authorized_email: str, *, use_staging: bool = False):
    from account_pool.channels.cliproxyapi.channel import CLIProxyAPIChannel
    from account_pool.compose_runtime import ComposeRuntime
    from account_pool.controlled_oauth_browser import ControlledOAuthBrowser
    from account_pool.oauth_automation.runner import AutomationRunner
    from account_pool.oauth_automation.worker import AutomationWorker
    from account_pool.oauth_browser import OAuthBrowserSessionService
    from account_pool.service import EnvironmentService
    from test_account_pool import FakeRuntime, StaticProfiles, _fake_channels, _settings
    from test_oauth_browser_api import MemoryBrowserSessions

    registry_service, card = registry_data
    profiles: Final = StaticProfiles("http://ordinary-proxy.test:8080")
    commands: Final[list[str]] = []

    class CredentialCLI(FakeCLIProxy):
        async def start_authorization(self, record):
            started = await super().start_authorization(record)
            return replace(started, staged=True) if use_staging else started

        async def submit_callback(self, record, callback):
            if use_staging:
                commands.append("oauth-callback")
            await super().submit_callback(record, callback)

        async def read_account(self, record, *, refresh_quota=False):
            observed = await super().read_account(record, refresh_quota=refresh_quota)
            return observed.model_copy(update={
                "credential_email": authorized_email, "credential_fingerprints": ("fixture-credential-owner",),
                "enabled_models": record.enabled_models or observed.enabled_models,
                "model_cooldowns": record.model_cooldowns,
            })

    cli: Final = CredentialCLI()
    class DraftRuntime(FakeRuntime):
        async def provision(self, record):
            if "fail-draft-once" in commands and "draft-provision-failed" not in commands:
                commands.append("draft-provision-failed")
                raise RuntimeError("fixture partial provisioning")
            await super().provision(record)

    runtime: Final = DraftRuntime()
    class StagedChannel(FakeChannel, CLIProxyAPIChannel):
        async def staged_oauth_content(self, record):
            commands.append("read-staged")
            return b"synthetic-stage" if "staged-on-disk" in commands else None

        async def activate_staged_oauth(self, record, content):
            assert content == b"synthetic-stage"
            commands.append("activate-staged")
            if "staged-token-rejected" in commands:
                from account_pool.channels.cliproxyapi.protocol import (
                    StagedOAuthProbeFailure,
                )

                raise StagedOAuthProbeFailure("authentication_failed", "fixture rejected")

        async def authorization_status(self, record, state):
            return "error:fixture-exchange-failed" if use_staging else await super().authorization_status(record, state)

        async def oauth_runtime_matches(self, record):
            commands.append("read-live")
            return "unexpected-live-credential" not in commands

        async def cancel_oauth_session(self, record, state):
            if use_staging:
                commands.append("fence-oauth")
                if "staged-on-disk" in commands:
                    commands.remove("staged-on-disk")
            await super().cancel_oauth_session(record, state)

    channels = _fake_channels(runtime, cli)
    if use_staging:
        channels = replace(channels, implementations={ChannelKind.CLIPROXYAPI: StagedChannel(runtime, cli)})
    manager: Final = EnvironmentService(
        settings=_settings(tmp_path), repository=registry_service.environments, runtime=runtime,
        cli_proxy=cli, proxy_profiles=profiles, secrets=registry_service.secrets,
        channels=channels, ownership=registry_service.ownership,
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
        payload = json.loads(request.content)
        action = payload["action"]
        if action == "start":
            assert payload["credentials"] == CREDENTIAL
        commands.append(action)
        if action == "script_check":
            assert set(payload) == {"action", "sha256"}
            if "reject-script" in commands:
                return httpx.Response(409, json={"error": "unverified"})
            return httpx.Response(200, json={"sha256": payload["sha256"]})
        if action == "status" and "fixture-waiting" in commands:
            assert request.headers["X-OAuth-Diagnostics"] == "1"
            return httpx.Response(200, json={
                "status": "waiting", "reason": "verification_required", "expires_at": "2099-01-01T00:00:00Z",
                "diagnostic": (
                    {"step": "password", "reason": "fixture-secret-not-a-reason"}
                    if "unsafe-diagnostic" in commands
                    else {"step": "password", "reason": reason, "http_status": 200}
                    if (reason := next((entry for entry in commands if entry.startswith("form_")), None))
                    else {"step": "email_verification", "reason": "email_verification", "http_status": 200}
                    if "email-verification" in commands
                    else {"step": "password", "reason": "browser_security_challenge", "http_status": 403}
                ),
            })
        return httpx.Response(200, json={"status": "manual" if action == "takeover" else "running", "reason": None, "expires_at": "2099-01-01T00:00:00Z"})

    await registry_service.create_profile(ProfileInput(name="test Linux"))
    submitted: Final = await registry_service.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(submitted, Success)
    async with httpx.AsyncClient(transport=httpx.MockTransport(external_worker)) as http:
        worker: Final = AutomationWorker(ComposeRuntime(_settings(tmp_path).model_copy(update={"oauth_browser_image": IMAGE}), registry_service.secrets), http)
        runner: Final = AutomationRunner(registry_service, browser, worker)
        yield registry_service, card, manager, runner, browser, commands, starts, removals, submitted


@pytest.fixture
async def execution(registry, tmp_path: Path, authorized_email: str):
    async with _execution(registry, tmp_path, authorized_email) as case:
        yield case


@pytest.fixture
async def memory_execution(deletion_registry, tmp_path: Path, authorized_email: str):
    async with _execution(deletion_registry, tmp_path, authorized_email) as case:
        yield case


@pytest.mark.asyncio
async def test_failed_draft_provisioning_retries_the_same_card(memory_execution) -> None:
    from account_pool.oauth_automation.models import AutomationDraft, AutomationSettings

    registry, _, _, runner, _, commands, starts, _, old = memory_execution
    await registry.cancel(old.value.id)
    assert isinstance(await registry.release_reservation(old.value.profile_id), Success)
    operation_id = uuid4()
    queued = await registry.submit(JobInput(
        id=operation_id, environment_id=operation_id, credentials=CREDENTIAL,
        draft=AutomationDraft(name="Recoverable draft", proxy_profile_id="ordinary-proxy"),
    ))
    assert isinstance(queued, Success) and queued.value.draft_pending
    checked = await registry.self_check()
    assert isinstance(checked, Success)
    await registry.save_settings(AutomationSettings(enabled=True), checked.value.version)
    commands.append("fail-draft-once")
    await runner.run_once()
    assert "draft-provision-failed" in commands and not starts
    assert (await registry.environments.get(operation_id)).status is EnvironmentStatus.ERROR
    assert isinstance(await runner.action(operation_id, "retry"), Success)
    await runner.run_once()
    assert starts and commands.count("start") == 1
    assert len(await registry.environments.list()) == 2
    job = next(job for job in (await registry.view()).jobs if job.id == operation_id)
    assert job.stage == "running" and job.environment_id == operation_id and not job.draft_pending


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ("missing", "saved", "rejected"))
async def test_consumed_callback_recovers_original_card_without_replaying_code(
    deletion_registry, tmp_path, outcome,
) -> None:
    from account_pool.domain import OAuthCallback
    from account_pool.oauth_automation.models import AutomationSettings

    async with _execution(deletion_registry, tmp_path, "one@example.test", use_staging=True) as case:
        registry, card, manager, runner, _, commands, starts, _, submitted = case
        checked = await registry.self_check()
        assert isinstance(checked, Success)
        await registry.save_settings(AutomationSettings(enabled=True), checked.value.version)
        await runner.run_once()
        first = await registry.environments.get(card.id)
        assert first.oauth_state and first.oauth_staged
        callback = OAuthCallback(state=first.oauth_state, code="fixture-once")
        assert isinstance(await manager.submit_oauth_callback(callback, card.id), Success)
        if outcome == "rejected":
            commands.extend(("staged-on-disk", "staged-token-rejected"))
        await runner.run_once()
        terminal = (await registry.view()).jobs[0]
        assert terminal.stage == "verification_failed" and terminal.callback_consumed and not terminal.credentials_saved
        assert isinstance(await manager.submit_oauth_callback(callback, card.id), Failure)
        if outcome == "saved":
            commands.append("staged-on-disk")
        elif outcome == "missing":
            commands.append("unexpected-live-credential")
            assert isinstance(await runner.action(submitted.value.id, "retry"), Failure)
            assert len(starts) == 1
            commands.remove("unexpected-live-credential")
        else:
            assert "fence-oauth" in commands and "staged-on-disk" not in commands
            assert (await registry.environments.get(card.id)).oauth_provider_state is None
        recovery = await runner.action(submitted.value.id, "retry")
        assert isinstance(recovery, Success)
        assert recovery.value.environment_id == card.id
        if outcome == "saved":
            assert recovery.value.stage == "validating" and recovery.value.credentials_saved
            assert commands.count("activate-staged") == 1 and len(starts) == 1
        else:
            assert recovery.value.stage == "queued" and not recovery.value.callback_consumed
            assert commands.count("read-live") == 3 and "fence-oauth" in commands
            await runner.run_once()
            second = await registry.environments.get(card.id)
            assert second.oauth_state and second.oauth_state != first.oauth_state and len(starts) == 2
        assert commands.count("oauth-callback") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("fixture", "expected", "hint"), [
    ("email-verification", ("email_verification", "email_verification", 200), "邮箱验证码"),
    ("security-challenge", ("password", "browser_security_challenge", 403), "人机验证"),
    ("unsafe-diagnostic", ("login", "verification_required", None), "查看画面"),
    ("form_fill_timeout", ("password", "form_fill_timeout", 200), "填写表单超时"),
    ("form_fill_failed", ("password", "form_fill_failed", 200), "填写表单失败"),
    ("form_submit_timeout", ("password", "form_submit_timeout", 200), "提交表单超时"),
    ("form_submit_failed", ("password", "form_submit_failed", 200), "提交表单失败"),
])
async def test_runner_records_waiting_phase_once_without_raw_worker_text(memory_execution, fixture, expected, hint) -> None:
    from account_pool.error_logs import ErrorLogRecord
    from account_pool.manager_logs import ManagerLogQuery, ManagerLogs
    from account_pool.oauth_automation.models import AutomationSettings

    registry, card, _, runner, _, commands, starts, _, _ = memory_execution
    class LogStore:
        events: tuple[ErrorLogRecord, ...] = ()

        async def append(self, event: ErrorLogRecord) -> None:
            self.events += (event,)

        async def query_browser_logs(self, query: ManagerLogQuery) -> tuple[ErrorLogRecord, ...]:
            return tuple(
                event for event in reversed(self.events)
                if event.session_id == str(query.session_id) and event.card_id == query.card_id
            )[: query.limit + 1]

        async def delete_browser_logs(self, before: datetime) -> int:
            raise AssertionError("recording should not prune events")

    logs: Final = ManagerLogs(LogStore(), registry.environments)
    checked: Final = await registry.self_check()
    assert isinstance(checked, Success)
    saved: Final = await registry.save_settings(AutomationSettings(enabled=True), checked.value.version)
    assert isinstance(saved, Success)
    observer: Final = replace(runner, record_diagnostic=logs.record)
    await observer.run_once()
    commands.extend(("fixture-waiting", fixture))
    await observer.run_once()
    await observer.run_once()
    job = (await registry.view()).jobs[0]
    assert job.stage == "waiting" and hint in job.message and "fixture-secret" not in job.message
    assert not job.credentials_saved and not job.callback_consumed and not {"chat", "responses"}.intersection(commands)
    result: Final = await logs.list(ManagerLogQuery(card_id=card.id, session_id=starts[0]))
    waiting: Final = [(item.step, item.reason, item.http_status) for item in result.items if item.state == "waiting"]
    assert waiting == [expected]
    assert commands.count("start") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("verified_worker", [False, True])
async def test_publish_login_folder_and_attest_before_sending_login(memory_execution, tmp_path, verified_worker):
    from base64 import b64encode
    from account_pool.oauth_automation.api import create_automation_router
    from account_pool.oauth_automation.login_scripts import LoginScriptStore
    from account_pool.oauth_automation.models import AutomationSettings
    from fastapi import FastAPI, Header, HTTPException

    registry, _, _, runner, _, commands, _, _, _ = memory_execution
    key = "fixture-operator-only-" * 2
    store = LoginScriptStore(tmp_path / "scripts.json", key)
    registry = replace(registry, login_scripts=store)
    runner = replace(runner, registry=registry)
    app = FastAPI()

    def authorize(authorization: str = Header(default="")):
        if authorization != "Bearer local-admin":
            raise HTTPException(403)

    app.include_router(create_automation_router(runner, authorize))
    url = "/api/oauth-automation/login-script"
    headers = {"Authorization": "Bearer local-admin"}
    bundle = {
        "entrypoint": "nested/entry.mjs",
        "files": [
            {"path": "nested/entry.mjs", "data": b64encode(b"export function automateLogin() {}").decode()},
            {"path": "nested/new_helper.mjs", "data": b64encode(b"export const value = 1;").decode()},
            {"path": "extra.json", "data": "e30="},
        ],
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://manager.test") as client:
        unauthorized = await client.put(url, json={"publisher_key": key, "package": bundle})
        assert unauthorized.status_code == 403
        rejected = await client.put(url, headers=headers, json={"publisher_key": "x" * 40, "package": bundle})
        assert rejected.status_code == 409 and not store.path.exists()
        uploaded = await client.put(url, headers=headers, json={"publisher_key": key, "package": bundle})
        assert uploaded.status_code == 200 and uploaded.headers["cache-control"] == "no-store"
        active = uploaded.json()["active"]
        assert active["entrypoint"] == "nested/entry.mjs" and active["file_count"] == 3
        assert key not in uploaded.text and "extra.json" not in uploaded.text and "data" not in uploaded.text
        traversal = dict(bundle, files=[*bundle["files"], {"path": "../escape", "data": "e30="}])
        malformed = await client.put(url, headers=headers, json={"publisher_key": key, "package": traversal})
        assert malformed.status_code == 422
        assert (await client.get(url, headers=headers)).json()["active"] == active

        await registry.self_check()
        await registry.save_settings(AutomationSettings(enabled=True), (await registry.view()).version)
        if not verified_worker:
            commands.append("reject-script")
        await runner.run_once()
        job = (await registry.view()).jobs[0]
        assert "script_check" in commands
        if verified_worker:
            assert commands.index("script_check") < commands.index("start") and job.stage == "running"
        else:
            assert "start" not in commands and not job.credentials_saved and job.stage == "failed"
        reset = await client.put(url, headers=headers, json={"publisher_key": key, "package": None})
        assert reset.status_code == 200 and reset.json()["active"] is None and not store.path.exists()


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
@pytest.mark.parametrize("authorized_email", ("other@example.test",))
async def test_mismatched_callback_account_disables_card_before_finishing(execution, tmp_path: Path) -> None:
    from account_pool.channels.cliproxyapi.channel import CLIProxyAPIChannel
    from account_pool.domain import OAuthCallback
    from account_pool.oauth_automation.models import AutomationSettings
    from test_account_pool import _settings

    service, card, manager, runner, browser, commands, starts, removals, submitted = execution
    await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), (await service.view()).version)
    await runner.run_once()
    pending: Final = await service.environments.get(card.id)
    assert isinstance(
        await manager.submit_oauth_callback(OAuthCallback(state=pending.oauth_state, code="one-use-test-code"), card.id),
        Success,
    )
    native: Final = CLIProxyAPIChannel(_settings(tmp_path), service.secrets, ownership=service.ownership)
    try:
        awaiting_identity: Final = await service.environments.get(card.id)
        assert awaiting_identity.oauth_automation_operation is not None
        assert not native.gateway(awaiting_identity).routable
    finally:
        await native.close()
    await runner.run_once()
    failed: Final = (await service.view()).jobs[0]
    blocked: Final = await service.environments.get(card.id)
    assert failed.stage == "verification_failed" and failed.credentials_saved
    assert blocked.credential_email == "other@example.test"
    assert blocked.credential_fingerprints and blocked.auth_file_name
    assert blocked.oauth_automation_operation is None
    assert not blocked.enabled and blocked.status is EnvironmentStatus.DISABLED
    native_after: Final = CLIProxyAPIChannel(_settings(tmp_path), service.secrets, ownership=service.ownership)
    try:
        assert not native_after.gateway(blocked).routable
    finally:
        await native_after.close()
    await manager.get_environment(card.id)
    assert not (await service.environments.get(card.id)).enabled
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
async def test_saved_login_can_be_reauthorized_without_resending_credentials(memory_execution) -> None:
    from account_pool.domain import CredentialRefreshStatus, OAuthCallback
    from account_pool.oauth_automation.api import create_automation_router
    from account_pool.oauth_automation.models import AutomationSettings
    from fastapi import FastAPI

    service, card, manager, runner, _, commands, starts, _, submitted = memory_execution
    await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), (await service.view()).version)
    await runner.run_once()
    pending = await service.environments.get(card.id)
    assert pending.oauth_state is not None, (await service.view()).jobs[0].message
    assert isinstance(await manager.submit_oauth_callback(
        OAuthCallback(state=pending.oauth_state, code="first-fixture-code"), card.id,
    ), Success)
    await runner.run_once()
    first = (await service.view()).jobs[0]
    assert first.stage == "completed"

    app = FastAPI()
    app.include_router(create_automation_router(runner, lambda: None))
    path = f"/api/oauth-automation/environments/{card.id}/reauthorize"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://manager.test") as client:
        healthy = await client.post(path)
        assert healthy.status_code == 409
        ready = await service.environments.get(card.id)
        other = ready.model_copy(update={"id": uuid4()})
        await service.environments.save(other)
        empty = await client.post(f"/api/oauth-automation/environments/{other.id}/reauthorize")
        assert empty.status_code == 202 and empty.json() is None
        assert len((await service.view()).jobs) == 1
        await service.environments.save(ready.model_copy(update={
            "status": EnvironmentStatus.ERROR,
            "credential_email": "another@example.test",
            "credential_refresh": CredentialRefreshStatus(schedule_reason="reauthorization_required"),
        }))
        assert (await client.post(path)).status_code == 409
        await service.environments.save(ready.model_copy(update={
            "status": EnvironmentStatus.ERROR,
            "credential_refresh": CredentialRefreshStatus(schedule_reason="reauthorization_required"),
        }))
        response = await client.post(path)
        duplicate = await client.post(path)
        assert response.status_code == duplicate.status_code == 202
        assert response.json() == duplicate.json()
        assert response.json()["stage"] == "queued"
        assert response.json()["profile_id"] == str(submitted.value.profile_id)
        assert "test-only-password" not in response.text and "credential_ciphertext" not in response.text
        assert (await service.environments.get(card.id)).auth_file_name == ready.auth_file_name

    await runner.run_once()
    second = await service.environments.get(card.id)
    assert second.oauth_state != pending.oauth_state and second.status is EnvironmentStatus.AWAITING_AUTHORIZATION
    assert len(starts) == commands.count("start") == 2
    assert isinstance(await manager.submit_oauth_callback(
        OAuthCallback(state=second.oauth_state, code="second-fixture-code"), card.id,
    ), Success)
    await runner.run_once()
    renewed = (await service.view()).jobs[-1]
    assert renewed.stage == "completed" and renewed.credentials_saved
    assert renewed.chat_verified and renewed.responses_verified
    print("healthy=409 no_saved=202:null account_mismatch=409 expired=202 duplicate=same_job new_state=true")
    print("saved_email_password_2fa_reused=true card_id_unchanged=true stage=completed chat=true responses=true")


@pytest.mark.asyncio
async def test_history_deletion_does_not_forget_verified_login(memory_execution) -> None:
    from account_pool.domain import CredentialRefreshStatus, OAuthCallback
    from account_pool.oauth_automation.models import AutomationSettings

    registry, card, manager, runner, _, commands, _, _, submitted = memory_execution
    checked = await registry.self_check()
    assert isinstance(checked, Success)
    await registry.save_settings(AutomationSettings(enabled=True), checked.value.version)
    await runner.run_once()
    pending = await registry.environments.get(card.id)
    assert pending.oauth_state
    assert isinstance(await manager.submit_oauth_callback(
        OAuthCallback(state=pending.oauth_state, code="fixture-once"), card.id,
    ), Success)
    await runner.run_once()
    assert (await registry.view()).jobs[0].stage == "completed"
    assert isinstance(await registry.delete_job(submitted.value.id), Success)
    assert not (await registry.view()).jobs
    ready = await registry.environments.get(card.id)
    await registry.environments.save(ready.model_copy(update={
        "status": EnvironmentStatus.ERROR,
        "credential_refresh": CredentialRefreshStatus(schedule_reason="reauthorization_required"),
    }))
    reauthorized = await registry.reauthorize(card.id)
    assert isinstance(reauthorized, Success) and reauthorized.value is not None
    assert reauthorized.value.environment_id == card.id
    assert reauthorized.value.profile_id == submitted.value.profile_id
    assert commands.count("start") == 1
    await registry.cancel(reauthorized.value.id)
    erased = await registry.forget_login(reauthorized.value.profile_id)
    assert isinstance(erased, Success) and not erased.value.profiles[0].login_saved
    assert len(erased.value.jobs) == 1
    assert (await registry.environments.get(card.id)).auth_file_name == ready.auth_file_name
    assert (await registry.reauthorize(card.id)).value is None


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


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,cleanup,can_delete", [
    ("completed", False, True), ("cancelled", False, True), ("failed", False, True),
    ("queued", False, False), ("running", False, False), ("failed", True, False),
])
async def test_delete_automation_job_removes_only_finished_clean_record(deletion_registry, stage, cleanup, can_delete):
    from types import SimpleNamespace

    from account_pool.oauth_automation.api import create_automation_router
    from fastapi import FastAPI

    registry, card = deletion_registry
    profile = await registry.create_profile(ProfileInput(name="keep"))
    job = await registry.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(profile, Success) and isinstance(job, Success)
    async with registry.repository.transaction() as tx:
        (row,) = tx.snapshot.jobs
        await tx.save(tx.snapshot.model_copy(update={"jobs": (row.model_copy(update={
            "cleanup_pending": cleanup, "item": row.item.model_copy(update={"stage": stage}),
        }),)}))
    app = FastAPI()
    app.include_router(create_automation_router(SimpleNamespace(registry=registry), lambda: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        result = await client.delete(f"/api/oauth-automation/jobs/{job.value.id}")
        assert result.status_code == (200 if can_delete else 409)
        if can_delete:
            assert result.json()["jobs"] == []
            assert (await client.delete(f"/api/oauth-automation/jobs/{job.value.id}")).status_code == 404
    view = await registry.view()
    assert len(view.jobs) == (0 if can_delete else 1)
    assert view.profiles[0].bound_environment_id == card.id
    assert await registry.environments.get(card.id) == card


@pytest.mark.asyncio
@pytest.mark.parametrize("initial,expected", [
    ("Codex-套餐待识别-one@example.test", "Codex-plus-one@example.test"),
    ("我的备卡", "我的备卡"),
])
async def test_completed_browser_job_uses_shared_card_naming_without_overwriting_custom_names(
    deletion_registry, initial, expected,
):
    from types import SimpleNamespace

    from account_pool.oauth_automation.runner import AutomationRunner, operation

    registry, card = deletion_registry
    await registry.create_profile(ProfileInput(name="keep"))
    job = await registry.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(job, Success)
    async with registry.repository.transaction() as tx:
        (row,) = tx.snapshot.jobs
        finished = row.model_copy(update={
            "cleanup_pending": True, "item": row.item.model_copy(update={"stage": "completed"}),
        })
        await tx.save(tx.snapshot.model_copy(update={"jobs": (finished,)}))
    await registry.environments.save(card.model_copy(update={
        "name": initial, "status": EnvironmentStatus.READY, "credential_email": "one@example.test",
        "quota": card.quota.model_copy(update={"plan_type": "plus"}),
        "oauth_automation_operation": operation(finished),
    }))
    await AutomationRunner(registry, SimpleNamespace(), registry.worker).run_once()
    saved = await registry.environments.get(card.id)
    assert saved.name == expected
    assert saved.oauth_automation_operation is None
    assert saved.proxy_profile_id == card.proxy_profile_id


@pytest.mark.asyncio
async def test_fenced_browser_does_not_block_a_different_queued_card(deletion_registry) -> None:
    from types import SimpleNamespace

    from account_pool.oauth_automation.models import AutomationSettings
    from account_pool.oauth_automation.runner import AutomationRunner, operation
    from account_pool.shared.result import FailureCode

    registry, first = deletion_registry
    await registry.create_profile(ProfileInput(name="first"))
    await registry.create_profile(ProfileInput(name="second"))
    second = first.model_copy(update={"id": uuid4()})
    await registry.environments.save(second)
    one = await registry.submit(JobInput(id=uuid4(), environment_id=first.id, credentials=CREDENTIAL))
    two = await registry.submit(JobInput(
        id=uuid4(), environment_id=second.id, credentials=CREDENTIAL.replace("one@", "two@"),
    ))
    assert isinstance(one, Success) and isinstance(two, Success)
    checked = await registry.self_check()
    assert isinstance(checked, Success)
    await registry.save_settings(AutomationSettings(enabled=True), checked.value.version)
    async with registry.repository.transaction() as tx:
        old, new = tx.snapshot.jobs
        finished = old.model_copy(update={"cleanup_pending": True, "item": old.item.model_copy(update={
            "stage": "failed", "attempt": 1, "browser_session_id": uuid4(),
        })})
        await tx.save(tx.snapshot.model_copy(update={"jobs": (finished, new)}))

    class Repository(MemoryRepository):
        async def save_if_version(self, record, version):
            return None if record.id == first.id else await super().save_if_version(record, version)

    environments = Repository(first.model_copy(update={"oauth_automation_operation": operation(finished)}))
    await environments.save(second)
    registry = replace(registry, environments=environments)
    removed = []
    started = []

    async def remove_session(session_id):
        removed.append(session_id)

    async def nothing(*args):
        return None

    async def no_sessions(environment_id):
        return ()

    async def start(environment_id, **kwargs):
        started.append(environment_id)
        return Failure(FailureCode.UPSTREAM, "stop the fixture before supplying credentials")

    @asynccontextmanager
    async def proxy_lock(_):
        yield

    browser = SimpleNamespace(
        sessions=SimpleNamespace(
            get=nothing, transition=nothing, mark_cleaned=nothing, list_for_environment=no_sessions,
        ),
        runtime=SimpleNamespace(remove_oauth_browser=remove_session),
        service=SimpleNamespace(proxy_profile_operation=proxy_lock),
        start=start,
    )
    runner = AutomationRunner(registry, browser, registry.worker)
    await runner.run_once()
    assert removed == [finished.item.browser_session_id]
    assert started == [second.id]
    assert (await registry.view()).jobs[0].cleanup_pending
    assert (await environments.get(first.id)).oauth_automation_operation == operation(finished)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["browser", "cancel", "conflict"])
async def test_cleanup_failure_is_visible_redacted_and_retried_before_next_job(deletion_registry, caplog, failure):
    from types import SimpleNamespace

    from account_pool.oauth_automation.runner import AutomationRunner, operation
    from account_pool.shared.result import FailureCode

    registry, card = deletion_registry
    await registry.create_profile(ProfileInput(name="cleanup"))
    submitted = await registry.submit(JobInput(id=uuid4(), environment_id=card.id, credentials=CREDENTIAL))
    assert isinstance(submitted, Success)
    faults = [failure]
    removals = []

    class Environments(MemoryRepository):
        async def save_if_version(self, record, version):
            if "conflict" in faults:
                return None
            return await super().save_if_version(record, version)

    async def cancel_oauth_session(environment_id, *, operation_id):
        if "cancel" in faults:
            return Failure(FailureCode.UPSTREAM, "private-refresh-token-do-not-log")
        return Success(None)

    async def remove_oauth_browser(session_id):
        if "browser" in faults:
            raise RuntimeError("private-refresh-token-do-not-log")
        removals.append(session_id)

    async def get_session(session_id):
        return None

    async def update_session(*args):
        return None

    async with registry.repository.transaction() as tx:
        (row,) = tx.snapshot.jobs
        finished = row.model_copy(update={
            "cleanup_pending": True,
            "session_ciphertext": "encrypted-session",
            "item": row.item.model_copy(update={
                "stage": "failed", "message": "登录未完成", "browser_session_id": uuid4(),
            }),
        })
        queued = row.model_copy(update={"item": row.item.model_copy(update={"id": uuid4()})})
        await tx.save(tx.snapshot.model_copy(update={"jobs": (finished, queued)}))
    environments = Environments(card.model_copy(update={
        "oauth_automation_operation": operation(finished),
        "oauth_state": "fixture-oauth-state" if failure == "cancel" else None,
        "oauth_state_consumed_at": None,
    }))
    registry = replace(registry, environments=environments)
    browser = SimpleNamespace(
        sessions=SimpleNamespace(get=get_session, transition=update_session, mark_cleaned=update_session),
        runtime=SimpleNamespace(remove_oauth_browser=remove_oauth_browser),
        service=SimpleNamespace(cancel_oauth_session=cancel_oauth_session),
    )
    runner = AutomationRunner(registry, browser, registry.worker)
    await runner.run_once()
    blocked = (await registry.view()).jobs
    assert blocked[0].stage == "failed"
    assert "登录未完成" in blocked[0].message
    assert "清理" in blocked[0].message and "自动重试" in blocked[0].message
    assert blocked[1].stage == "queued"
    assert "OAuth automation cleanup pending" in caplog.text
    assert "private-refresh-token-do-not-log" not in caplog.text + blocked[0].message
    assert (await environments.get(card.id)).oauth_automation_operation == operation(finished)
    assert isinstance(await registry.delete_job(finished.item.id), Failure)
    assert isinstance(await runner.action(finished.item.id, "retry"), Failure)
    assert (await registry.repository.read()).jobs[0].session_ciphertext == "encrypted-session"
    assert blocked[0].cleanup_pending
    assert "encrypted-session" not in (await registry.view()).model_dump_json()
    before_retry = caplog.text
    await runner.run_once()
    assert (await registry.view()).jobs[0].message == blocked[0].message
    assert caplog.text == before_retry

    faults.clear()
    assert isinstance(await runner.action(finished.item.id, "cleanup"), Success)
    recovered = (await registry.view()).jobs
    assert recovered[0].stage == "failed" and recovered[0].message == "登录未完成"
    assert recovered[1].stage == "queued"
    assert (await environments.get(card.id)).oauth_automation_operation is None
    assert (await registry.repository.read()).jobs[0].session_ciphertext is None
    assert finished.item.browser_session_id in removals
    assert isinstance(await registry.delete_job(finished.item.id), Success)



@pytest.mark.asyncio
async def test_settings_snapshot_ignores_job_ticks_but_rejects_admin_overwrite(deletion_registry):
    from account_pool.oauth_automation.models import AutomationSettings
    service, _ = deletion_registry
    original = await service.view()
    await service.create_profile(ProfileInput(name="queue changes must not invalidate settings"))
    settings = AutomationSettings(total_timeout_seconds=500)
    saved = await service.save_settings(settings, original.version, expected_settings=original.settings)
    assert isinstance(saved, Success)
    conflict = await service.save_settings(
        AutomationSettings(total_timeout_seconds=550), saved.value.version, expected_settings=original.settings,
    )
    assert isinstance(conflict, Failure)
    assert (await service.view()).settings == settings
    assert isinstance(await service.save_settings(settings, original.version), Failure)


@pytest.mark.asyncio
async def test_disabled_automation_preserves_queue_until_explicit_enable(memory_execution):
    from account_pool.oauth_automation.models import AutomationSettings
    service, _, _, runner, _, commands, starts, _, submitted = memory_execution
    await runner.run_once()
    assert (await service.view()).jobs[0].stage == "queued" and not starts
    checked = await service.self_check()
    assert isinstance(await service.save_settings(AutomationSettings(enabled=True), checked.value.version), Success)
    await runner.run_once()
    assert (await service.view()).jobs[0].stage == "running" and commands.count("start") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "503", "401", "invalid", "oversized"])
@pytest.mark.parametrize("action", ["status", "start"])
async def test_only_readonly_worker_status_retries_transient_failures(memory_execution, outcome, action):
    import json
    _, _, _, runner, _, _, _, _, _ = memory_execution
    seen = []
    async def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            if outcome == "timeout":
                raise httpx.ReadTimeout("fixture-private-error", request=request)
            if outcome in {"503", "401"}:
                return httpx.Response(int(outcome), json={"private": "not-public"})
            return httpx.Response(200, content=b"x" * 4097 if outcome == "oversized" else b"not-json")
        return httpx.Response(200, json={"status": "running", "reason": None, "expires_at": "2099-01-01T00:00:00Z"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        worker = replace(runner.worker, http=http)
        if action == "status" and outcome in {"timeout", "503"}:
            assert (await worker.command(uuid4(), "private-token", action)).status == "running"
            assert len(seen) == 2 and all(item == {"action": "status"} for item in seen)
        else:
            with pytest.raises((httpx.HTTPError, ValueError)):
                await worker.command(uuid4(), "private-token", action, credentials=CREDENTIAL)
            assert len(seen) == 1


@pytest.mark.asyncio
async def test_status_retry_exhaustion_is_bounded_without_replaying_login(memory_execution):
    _, _, _, runner, _, _, _, _, _ = memory_execution
    seen = []
    async def unavailable(request):
        seen.append(request)
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await replace(runner.worker, http=http).command(uuid4(), "fixture-token", "status")
    assert len(seen) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["cancelled", "expired"])
async def test_worker_terminal_status_preserves_cancel_and_expiry(memory_execution, terminal):
    from account_pool.oauth_automation.models import AutomationSettings
    service, _, _, runner, _, commands, starts, _, _ = memory_execution
    checked = await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), checked.value.version)
    await runner.run_once()
    async def ended(request):
        return httpx.Response(200, json={"status": terminal, "reason": None, "expires_at": "2099-01-01T00:00:00Z"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(ended)) as http:
        await replace(runner, worker=replace(runner.worker, http=http)).run_once()
    job = (await service.view()).jobs[0]
    assert job.stage == terminal
    assert ("过期" if terminal == "expired" else "取消") in job.message
    assert commands.count("start") == len(starts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "responses"])
@pytest.mark.parametrize("result", ["complete", "truncated", "401", "timeout"])
async def test_model_budget_retry_requires_explicit_truncation_and_complete_text(memory_execution, mode, result):
    import json
    from account_pool.domain import GatewayEnvironment
    _, card, _, runner, _, _, _, _, _ = memory_execution
    endpoint = GatewayEnvironment(id=card.id, routable=True, concurrency_limit=1,
        enabled_models=("fixture-model",), api_base=f"http://cliproxy-{card.id.hex}:8317/v1", api_key="fixture-key")
    budgets = []
    async def handler(request):
        payload = json.loads(request.content)
        budgets.append(payload["max_completion_tokens" if mode == "chat" else "max_output_tokens"])
        if result == "timeout":
            raise httpx.ReadTimeout("private-network-error", request=request)
        if result == "401":
            return httpx.Response(401, json={"error": "private-token"})
        if len(budgets) == 1 or result == "truncated":
            return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}
                if mode == "chat" else {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Hello"}}]}
            if mode == "chat" else {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        accepted, detail = await replace(runner.worker, http=http)._infer(endpoint, "fixture-model", mode)
    assert accepted is (result == "complete")
    assert budgets == ([256] if result in {"401", "timeout"} else [256, 2048])
    assert "private" not in detail
    if result == "truncated":
        assert "预算不足" in detail


@pytest.mark.asyncio
async def test_verification_progress_returns_actionable_screen_error_without_waiting_or_login_replay(memory_execution):
    from account_pool.domain import OAuthCallback
    from account_pool.oauth_automation.models import AutomationSettings
    service, card, manager, runner, _, commands, starts, _, submitted = memory_execution
    checked = await service.self_check()
    await service.save_settings(AutomationSettings(enabled=True), checked.value.version)
    await runner.run_once()
    record = await service.environments.get(card.id)
    assert isinstance(await manager.submit_oauth_callback(OAuthCallback(state=record.oauth_state, code="fixture-code"), card.id), Success)
    commands.append("hold-chat")
    advancing = asyncio.create_task(runner.run_once())
    try:
        async with asyncio.timeout(2):
            while "chat" not in commands:
                await asyncio.sleep(0.01)
        for result in [await asyncio.wait_for(runner.screen(submitted.value.id), 0.5),
                       await asyncio.wait_for(runner.action(submitted.value.id, "takeover"), 0.5)]:
            assert isinstance(result, Failure) and "正在验收" in result.message
        assert len(starts) == commands.count("start") == 1
    finally:
        commands.remove("hold-chat")
        await asyncio.wait_for(advancing, 2)
    assert (await service.view()).jobs[0].stage == "completed"
    assert not (await service.environments.get(card.id)).oauth_verification_pending


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_manual_auth_replacement_recovers_pending_only_after_credential_validation(tmp_path, failed):
    from account_pool.channels.cliproxyapi.channel import CLIProxyAPIChannel
    from account_pool.domain import to_view
    from test_account_pool import _settings
    record = _record(status=EnvironmentStatus.READY).model_copy(update={"oauth_verification_pending": True})
    cli = FakeCLIProxy(read_error=RuntimeError("invalid credential") if failed else None)
    service = _service(record, cli, tmp_path)
    assert to_view(record).oauth_verification_pending
    native = CLIProxyAPIChannel(_settings(tmp_path), service._secrets)
    try:
        assert not native.gateway(record).routable
    finally:
        await native.close()
    result = await service.upload_auth_file(record.id, "replacement.json", b'{"refresh_token":"test-replacement"}',
        "application/json", replace=True)
    saved = await service._repository.get(record.id)
    assert saved is not None and saved.auth_file_name == "replacement.json"
    assert saved.oauth_verification_pending is failed
    native_after = CLIProxyAPIChannel(_settings(tmp_path), service._secrets)
    try:
        assert native_after.gateway(saved).routable is (not failed)
    finally:
        await native_after.close()
    assert isinstance(result, Failure if failed else Success)
