"""本文件覆盖号池 Manager 的额度解析、环境状态和 Compose 隔离约束。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Final
from uuid import uuid4

import httpx
import pytest
import yaml
from account_pool.api import create_router
from account_pool.cliproxy import HttpCLIProxyClient, _QuotaObservation, parse_quota
from account_pool.compose import render_compose
from account_pool.config import Settings
from account_pool.domain import (
    EnvironmentRecord,
    EnvironmentStatus,
    OAuthCallback,
    Provider,
    ProxyMode,
    QuotaSnapshot,
    UpdateEnvironmentRequest,
    utc_now,
)
from account_pool.secrets import EnvironmentSecretDeriver
from account_pool.service import EnvironmentService, Failure, FailureCode, _safe_error


class MemoryRepository:
    def __init__(self, record: EnvironmentRecord) -> None:
        self.records = {record.id: record}

    async def initialize(self) -> None:
        return None

    async def list(self) -> tuple[EnvironmentRecord, ...]:
        return tuple(self.records.values())

    async def get(self, environment_id):
        return self.records.get(environment_id)

    async def find_by_oauth_state(self, state: str):
        return next((record for record in self.records.values() if record.oauth_state == state), None)

    async def save(self, record: EnvironmentRecord) -> EnvironmentRecord:
        self.records[record.id] = record
        return record

    async def save_if_version(
        self,
        record: EnvironmentRecord,
        expected_version: int,
    ) -> EnvironmentRecord | None:
        current = self.records.get(record.id)
        if current is None or current.version != expected_version:
            return None
        self.records[record.id] = record
        return record

    async def delete(self, environment_id) -> None:
        self.records.pop(environment_id, None)


class RejectingVersionRepository(MemoryRepository):
    async def save_if_version(
        self,
        record: EnvironmentRecord,
        expected_version: int,
    ) -> EnvironmentRecord | None:
        return None


class FakeRuntime:
    def __init__(self) -> None:
        self.provisioned: list[EnvironmentRecord] = []
        self.removed: list[EnvironmentRecord] = []

    def environment_dir(self, environment_id):
        return Path("/tmp") / environment_id.hex

    async def provision(self, record: EnvironmentRecord) -> None:
        self.provisioned.append(record)

    async def ensure_control_plane_connections(self, environment_id) -> None:
        return None

    async def set_running(self, record: EnvironmentRecord, running: bool) -> None:
        return None

    async def remove(self, record: EnvironmentRecord) -> None:
        self.removed.append(record)



class FailingOnceRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def remove(self, record: EnvironmentRecord) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("docker unavailable")
        await super().remove(record)


class FailingOnceRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def remove(self, record: EnvironmentRecord) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("docker unavailable")
        await super().remove(record)


class FakeCLIProxy:
    def __init__(self, authorization_status: str = "wait", data_plane_healthy: bool = True) -> None:
        self.authorization_status_value = authorization_status
        self.data_plane_healthy = data_plane_healthy
        self.read_calls = 0
        self.status_calls: list[bool] = []
        self.proxy_calls: list[str] = []
        self.model_calls: list[tuple[str, ...]] = []
        self.health_calls = 0

    async def close(self) -> None:
        return None

    async def start_openai_authorization(self, record: EnvironmentRecord) -> tuple[str, str]:
        return "https://example.com/oauth", "state-for-test-1234"

    async def authorization_status(self, record: EnvironmentRecord, state: str) -> str:
        return self.authorization_status_value

    async def submit_callback(self, record: EnvironmentRecord, callback: OAuthCallback) -> None:
        return None

    async def read_account(self, record: EnvironmentRecord) -> EnvironmentRecord:
        self.read_calls += 1
        return record.model_copy(
            update={
                "auth_file_name": record.auth_file_name or "codex.json",
                "available_models": ("gpt-5",),
                "enabled_models": ("gpt-5",),
                "status": EnvironmentStatus.READY,
            }
        )

    async def data_plane_health_check(self, record: EnvironmentRecord) -> bool:
        self.health_calls += 1
        return self.data_plane_healthy

    async def set_credential_enabled(self, record: EnvironmentRecord, enabled: bool) -> None:
        self.status_calls.append(enabled)

    async def set_proxy_url(self, record: EnvironmentRecord, proxy_url: str) -> None:
        self.proxy_calls.append(proxy_url)

    async def set_enabled_models(self, record: EnvironmentRecord, enabled_models) -> None:
        self.model_calls.append(tuple(enabled_models))


class EmptyProfiles:
    async def list(self):
        return ()

    async def get_url(self, profile_id: str):
        return None


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="postgresql://unused",
        data_root=tmp_path,
        manager_token="m" * 32,
        secret_seed="s" * 32,
        ssh_host="example.com",
        ssh_user="operator",
    )


def _record(
    *,
    status: EnvironmentStatus,
    auth_file_name: str | None = "codex.json",
    cooldown_until=None,
) -> EnvironmentRecord:
    now: Final = utc_now()
    return EnvironmentRecord(
        id=uuid4(),
        name="Test environment",
        provider=Provider.OPENAI,
        status=status,
        enabled=True,
        manual_cooldown=False,
        concurrency_limit=2,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=("gpt-5",),
        enabled_models=("gpt-5",),
        auth_file_name=auth_file_name,
        auth_index=None,
        quota=QuotaSnapshot(),
        model_quotas=(),
        cooldown_until=cooldown_until,
        oauth_state="state-for-test-1234" if status == EnvironmentStatus.AWAITING_AUTHORIZATION else None,
        oauth_expires_at=now + timedelta(minutes=5) if status == EnvironmentStatus.AWAITING_AUTHORIZATION else None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


def _service(record: EnvironmentRecord, cli: FakeCLIProxy, tmp_path: Path) -> EnvironmentService:
    return EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )


def test_parse_quota_supports_multiple_windows_and_ignores_invalid_values() -> None:
    observation: Final = _QuotaObservation(
        observed_at=utc_now(),
        signals={
            "x-codex-plan-type": "pro",
            "x-codex-five-hour-used-percent": "25",
            "x-codex-five-hour-window-minutes": "300",
            "x-codex-weekly-used-percent": "bad",
            "x-codex-weekly-window-minutes": "10080",
        },
    )

    snapshot: Final = parse_quota(observation)

    assert snapshot.plan_type == "pro"
    assert len(snapshot.windows) == 1
    assert snapshot.windows[0].name == "Five Hour"
    assert snapshot.windows[0].remaining_percent == 75


def test_oauth_callback_accepts_error_description_without_error_code() -> None:
    callback: Final = OAuthCallback(state="state-for-test-1234", error_description="access denied")

    assert callback.error is None
    assert callback.error_description == "access denied"


def test_render_compose_has_no_host_ports_and_uses_an_environment_network(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.PROVISIONING, auth_file_name=None)
    settings: Final = _settings(tmp_path)

    rendered: Final = yaml.safe_load(render_compose(record, settings))
    service: Final = rendered["services"]["cli-proxy-api"]

    assert "ports" not in service
    assert rendered["networks"]["environment"]["name"] == f"account-pool-{record.id.hex}"
    assert service["networks"]["environment"]["aliases"] == [f"cliproxy-{record.id.hex}"]


def test_safe_error_redacts_urls_and_credentials() -> None:
    error: Final = RuntimeError(
        "request failed for https://user:password@example.com/oauth?code=oauth-code&state=oauth-state "
        "Bearer access-token"
    )

    safe: Final = _safe_error(error)

    assert "password" not in safe
    assert "oauth-code" not in safe
    assert "oauth-state" not in safe
    assert "access-token" not in safe
    assert "example.com/oauth" in safe


@pytest.mark.asyncio
async def test_unknown_authorization_status_does_not_complete_oauth(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    cli: Final = FakeCLIProxy(authorization_status="unexpected")
    service: Final = _service(record, cli, tmp_path)

    result: Final = await service._refresh_authorization(record)

    assert result.status == EnvironmentStatus.AWAITING_AUTHORIZATION
    assert cli.read_calls == 0


@pytest.mark.asyncio
async def test_read_account_preserves_empty_model_selection_and_clears_stale_error() -> None:
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(
        update={
            "available_models": ("gpt-5",),
            "enabled_models": (),
            "last_error": "stale error",
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={"files": [{"name": "codex.json", "provider": "codex"}]},
                request=request,
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(
                200,
                json={"models": [{"id": "gpt-5"}, {"id": "gpt-4.1"}]},
                request=request,
            )
        return httpx.Response(404, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    try:
        observed: Final = await proxy.read_account(record)
    finally:
        await client.aclose()

    assert observed.available_models == ("gpt-5", "gpt-4.1")
    assert observed.enabled_models == ()
    assert observed.last_error is None


@pytest.mark.asyncio
async def test_configuration_update_requires_completed_authorization(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    cli: Final = FakeCLIProxy()
    service: Final = _service(record, cli, tmp_path)
    request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name="Updated",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=(),
    )

    result: Final = await service.update_environment(record.id, request)

    assert isinstance(result, Failure)
    assert result.code == FailureCode.CONFLICT
    assert cli.status_calls == []


@pytest.mark.asyncio
async def test_elapsed_cooldown_requires_data_plane_health_before_refresh(tmp_path: Path) -> None:
    record: Final = _record(
        status=EnvironmentStatus.COOLING_DOWN,
        cooldown_until=utc_now() - timedelta(minutes=1),
    )
    cli: Final = FakeCLIProxy(data_plane_healthy=False)
    service: Final = _service(record, cli, tmp_path)

    result: Final = await service._refresh_if_needed(record)

    assert result.status == EnvironmentStatus.COOLING_DOWN
    assert cli.read_calls == 0


@pytest.mark.asyncio
async def test_configuration_update_cannot_bypass_active_cooldown(tmp_path: Path) -> None:
    record: Final = _record(
        status=EnvironmentStatus.COOLING_DOWN,
        cooldown_until=utc_now() + timedelta(minutes=5),
    )
    cli: Final = FakeCLIProxy()
    service: Final = _service(record, cli, tmp_path)
    request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name="Updated",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )

    result: Final = await service.update_environment(record.id, request)

    assert not isinstance(result, Failure)
    assert result.value.status == EnvironmentStatus.COOLING_DOWN
    assert result.value.cooldown_until is not None
    assert cli.status_calls == [False]


@pytest.mark.asyncio
async def test_configuration_update_recovers_after_healthy_cooldown(tmp_path: Path) -> None:
    record: Final = _record(
        status=EnvironmentStatus.COOLING_DOWN,
        cooldown_until=utc_now() - timedelta(minutes=1),
    )
    cli: Final = FakeCLIProxy(data_plane_healthy=True)
    service: Final = _service(record, cli, tmp_path)
    request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name="Recovered",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )

    result: Final = await service.update_environment(record.id, request)

    assert not isinstance(result, Failure)
    assert result.value.status == EnvironmentStatus.READY
    assert result.value.cooldown_until is None
    assert cli.status_calls == [True]
    assert cli.health_calls == 1


@pytest.mark.asyncio
async def test_configuration_update_keeps_cooldown_when_recovery_health_check_fails(tmp_path: Path) -> None:
    cooldown_until: Final = utc_now() - timedelta(minutes=1)
    record: Final = _record(status=EnvironmentStatus.COOLING_DOWN, cooldown_until=cooldown_until)
    cli: Final = FakeCLIProxy(data_plane_healthy=False)
    service: Final = _service(record, cli, tmp_path)
    request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name="Still cooling",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )

    result: Final = await service.update_environment(record.id, request)

    assert not isinstance(result, Failure)
    assert result.value.status == EnvironmentStatus.COOLING_DOWN
    assert result.value.cooldown_until == cooldown_until
    assert cli.status_calls == [False]
    assert cli.health_calls == 1


@pytest.mark.asyncio
async def test_disabling_and_reenabling_does_not_bypass_active_cooldown(tmp_path: Path) -> None:
    cooldown_until: Final = utc_now() + timedelta(minutes=5)
    record: Final = _record(status=EnvironmentStatus.COOLING_DOWN, cooldown_until=cooldown_until)
    cli: Final = FakeCLIProxy()
    service: Final = _service(record, cli, tmp_path)
    disabled_request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name="Disabled",
        concurrency_limit=3,
        enabled=False,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )
    disabled_result: Final = await service.update_environment(record.id, disabled_request)
    assert not isinstance(disabled_result, Failure)
    enabled_request: Final = disabled_request.model_copy(
        update={"version": disabled_result.value.version, "name": "Re-enabled", "enabled": True}
    )

    enabled_result: Final = await service.update_environment(record.id, enabled_request)

    assert not isinstance(enabled_result, Failure)
    assert disabled_result.value.status == EnvironmentStatus.DISABLED
    assert disabled_result.value.cooldown_until == cooldown_until
    assert enabled_result.value.status == EnvironmentStatus.COOLING_DOWN
    assert enabled_result.value.cooldown_until == cooldown_until
    assert cli.status_calls == [False, False]
    assert cli.health_calls == 0


@pytest.mark.asyncio
async def test_manual_cooldown_blocks_automatic_recovery_until_disabled(tmp_path: Path) -> None:
    cooldown_until: Final = utc_now() - timedelta(minutes=1)
    record: Final = _record(status=EnvironmentStatus.COOLING_DOWN, cooldown_until=cooldown_until)
    cli: Final = FakeCLIProxy(data_plane_healthy=True)
    service: Final = _service(record, cli, tmp_path)
    manual_request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name="Manual pause",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=True,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )
    manual_result: Final = await service.update_environment(record.id, manual_request)
    assert not isinstance(manual_result, Failure)
    resume_request: Final = manual_request.model_copy(
        update={"version": manual_result.value.version, "manual_cooldown": False}
    )
    resumed_result: Final = await service.update_environment(record.id, resume_request)

    assert not isinstance(resumed_result, Failure)
    assert manual_result.value.status == EnvironmentStatus.COOLING_DOWN
    assert manual_result.value.cooldown_until == cooldown_until
    assert resumed_result.value.status == EnvironmentStatus.READY
    assert resumed_result.value.cooldown_until is None
    assert cli.status_calls == [False, True]
    assert cli.health_calls == 1


@pytest.mark.asyncio
async def test_releasing_manual_cooldown_health_checks_a_ready_environment(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    cli: Final = FakeCLIProxy(data_plane_healthy=True)
    service: Final = _service(record, cli, tmp_path)
    manual_request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name="Manual pause",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=True,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )
    manual_result: Final = await service.update_environment(record.id, manual_request)
    assert not isinstance(manual_result, Failure)
    resume_request: Final = manual_request.model_copy(
        update={"version": manual_result.value.version, "manual_cooldown": False}
    )
    resumed_result: Final = await service.update_environment(record.id, resume_request)

    assert not isinstance(resumed_result, Failure)
    assert resumed_result.value.status == EnvironmentStatus.READY
    assert cli.status_calls == [False, True]
    assert cli.health_calls == 1


@pytest.mark.asyncio
async def test_repository_rejects_second_save_from_same_version() -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    repository: Final = MemoryRepository(record)
    first: Final = record.model_copy(update={"version": 1, "name": "First update"})
    second: Final = record.model_copy(update={"version": 1, "name": "Second update"})

    first_saved: Final = await repository.save_if_version(first, expected_version=0)
    second_saved: Final = await repository.save_if_version(second, expected_version=0)

    assert first_saved == first
    assert second_saved is None
    assert await repository.get(record.id) == first


@pytest.mark.asyncio
async def test_configuration_update_conflict_has_no_cli_proxy_side_effects(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    repository: Final = RejectingVersionRepository(record)
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name="Updated",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )
    stale: Final = record.model_copy(update={"name": "Concurrent update"})
    await repository.save(stale)

    result: Final = await service.update_environment(record.id, request)

    assert isinstance(result, Failure)
    assert result.code == FailureCode.CONFLICT
    assert (await repository.get(record.id)).name == "Concurrent update"
    assert cli.status_calls == []
    assert cli.proxy_calls == []
    assert cli.model_calls == []


@pytest.mark.asyncio
async def test_delete_environment_is_idempotent_and_removes_resources(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    repository: Final = MemoryRepository(record)
    runtime: Final = FakeRuntime()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=runtime,
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    first_result: Final = await service.delete_environment(record.id)
    second_result: Final = await service.delete_environment(record.id)

    assert not isinstance(first_result, Failure)
    assert not isinstance(second_result, Failure)
    assert await repository.get(record.id) is None
    assert len(runtime.removed) == 1
    assert runtime.removed[0].id == record.id
    assert runtime.removed[0].status == EnvironmentStatus.DELETING
    assert runtime.removed[0].enabled is False


@pytest.mark.asyncio
async def test_delete_environment_can_retry_after_runtime_failure(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    repository: Final = MemoryRepository(record)
    runtime: Final = FailingOnceRuntime()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=runtime,
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    failed: Final = await service.delete_environment(record.id)

    assert isinstance(failed, Failure)
    assert failed.code == FailureCode.UPSTREAM
    failed_record: Final = await repository.get(record.id)
    assert failed_record is not None
    assert failed_record.status == EnvironmentStatus.DELETING

    retried: Final = await service.delete_environment(record.id)

    assert not isinstance(retried, Failure)
    assert await repository.get(record.id) is None
    assert runtime.attempts == 2
    assert len(runtime.removed) == 1


@pytest.mark.asyncio
async def test_releasing_manual_cooldown_keeps_environment_cooling_when_unhealthy(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    cli: Final = FakeCLIProxy(data_plane_healthy=False)
    service: Final = _service(record, cli, tmp_path)
    manual_request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name="Manual pause",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=True,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )
    manual_result: Final = await service.update_environment(record.id, manual_request)
    assert not isinstance(manual_result, Failure)
    resume_request: Final = manual_request.model_copy(
        update={"version": manual_result.value.version, "manual_cooldown": False}
    )
    resumed_result: Final = await service.update_environment(record.id, resume_request)

    assert not isinstance(resumed_result, Failure)
    assert resumed_result.value.status == EnvironmentStatus.COOLING_DOWN
    assert cli.status_calls == [False, False]
    assert cli.health_calls == 1
