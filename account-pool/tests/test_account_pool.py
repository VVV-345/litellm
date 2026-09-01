"""本文件覆盖号池 Manager 的额度解析、环境状态和 Compose 隔离约束。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Final
from uuid import uuid4

import httpx
import pytest
import yaml
from account_pool.cliproxy import HttpCLIProxyClient, _QuotaObservation, parse_quota
from account_pool.compose import render_compose
from account_pool.config import Settings
from account_pool.domain import (
    CreateEnvironmentRequest,
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
    def __init__(self, record: EnvironmentRecord, *, validate_state_before_consume: bool = True) -> None:
        self.records = {record.id: record}
        self.validate_state_before_consume = validate_state_before_consume

    async def initialize(self) -> None:
        return None

    async def list(self) -> tuple[EnvironmentRecord, ...]:
        return tuple(self.records.values())

    async def get(self, environment_id):
        return self.records.get(environment_id)

    async def find_by_oauth_state(self, state: str):
        return next((record for record in self.records.values() if record.oauth_state == state), None)

    async def find_by_operation_id(self, operation_id: str):
        return next((record for record in self.records.values() if record.operation_id == operation_id), None)

    async def consume_oauth_state(self, state: str, consumed_at):
        record = await self.find_by_oauth_state(state)
        if record is None or record.oauth_state_consumed_at is not None:
            return None
        if self.validate_state_before_consume and record.oauth_expires_at is not None and record.oauth_expires_at <= consumed_at:
            return None
        consumed = record.model_copy(
            update={
                "version": record.version + 1,
                "oauth_state_consumed_at": consumed_at,
            }
        )
        return await self.save_if_version(consumed, record.version)

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


class AuthorizationConflictRepository(MemoryRepository):
    def __init__(
        self,
        record: EnvironmentRecord,
        *,
        reject_ready_save: bool,
        reject_configuration_claim: bool,
    ) -> None:
        super().__init__(record)
        self.reject_ready_save = reject_ready_save
        self.reject_configuration_claim = reject_configuration_claim
        self.ready_save_attempts = 0
        self.configuration_claim_attempts = 0

    async def save_if_version(
        self,
        record: EnvironmentRecord,
        expected_version: int,
    ) -> EnvironmentRecord | None:
        if record.status is EnvironmentStatus.READY and not record.configuration_pending:
            self.ready_save_attempts += 1
            if self.reject_ready_save:
                return None
        if record.configuration_pending:
            self.configuration_claim_attempts += 1
            if self.reject_configuration_claim:
                return None
        return await super().save_if_version(record, expected_version)


class MissingConsumedSignatureRepository(MemoryRepository):
    async def consume_oauth_state(self, state: str, consumed_at):
        consumed = await super().consume_oauth_state(state, consumed_at)
        return None if consumed is None else consumed.model_copy(update={"oauth_state_signature": None})


class AuthorizationRecoveryRepository(MemoryRepository):
    def __init__(self, record: EnvironmentRecord) -> None:
        super().__init__(record)
        self.conflicted = False

    async def save_if_version(
        self,
        record: EnvironmentRecord,
        expected_version: int,
    ) -> EnvironmentRecord | None:
        is_initial_configuration_claim = record.status is EnvironmentStatus.READY and record.configuration_pending
        if is_initial_configuration_claim and not self.conflicted:
            self.conflicted = True
            current = await self.get(record.id)
            assert current is not None
            concurrent = current.model_copy(update={"version": current.version + 1, "updated_at": utc_now()})
            await self.save(concurrent)
            return None
        return await super().save_if_version(record, expected_version)


class FinalConfigurationSaveConflictRepository(MemoryRepository):
    def __init__(self, record: EnvironmentRecord) -> None:
        super().__init__(record)
        self.conflicted = False

    async def save_if_version(
        self,
        record: EnvironmentRecord,
        expected_version: int,
    ) -> EnvironmentRecord | None:
        is_final_configuration_save = not record.configuration_pending and record.status is EnvironmentStatus.READY
        if is_final_configuration_save and not self.conflicted:
            self.conflicted = True
            current = await self.get(record.id)
            assert current is not None
            concurrent = current.model_copy(update={"version": current.version + 1, "updated_at": utc_now()})
            await self.save(concurrent)
            return None
        return await super().save_if_version(record, expected_version)


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


class FakeCLIProxy:
    def __init__(
        self,
        authorization_status: str = "wait",
        data_plane_healthy: bool = True,
        observed_status: EnvironmentStatus = EnvironmentStatus.READY,
    ) -> None:
        self.authorization_status_value = authorization_status
        self.data_plane_healthy = data_plane_healthy
        self.observed_status = observed_status
        self.read_calls = 0
        self.status_calls: list[bool] = []
        self.proxy_calls: list[str] = []
        self.model_calls: list[tuple[str, ...]] = []
        self.concurrency_calls: list[int] = []
        self.health_calls = 0
        self.submit_calls: list[OAuthCallback] = []
        self.fail_proxy_once = False

    async def close(self) -> None:
        return None

    async def start_openai_authorization(self, record: EnvironmentRecord) -> tuple[str, str]:
        return "https://example.com/oauth", "state-for-test-1234"

    async def authorization_status(self, record: EnvironmentRecord, state: str) -> str:
        return self.authorization_status_value

    async def submit_callback(self, record: EnvironmentRecord, callback: OAuthCallback) -> None:
        self.submit_calls.append(callback)

    async def read_account(self, record: EnvironmentRecord) -> EnvironmentRecord:
        self.read_calls += 1
        return record.model_copy(
            update={
                "auth_file_name": record.auth_file_name or "codex.json",
                "available_models": ("gpt-5",),
                "enabled_models": ("gpt-5",),
                "status": self.observed_status,
            }
        )

    async def data_plane_health_check(self, record: EnvironmentRecord) -> bool:
        self.health_calls += 1
        return self.data_plane_healthy

    async def set_credential_enabled(self, record: EnvironmentRecord, enabled: bool) -> None:
        self.status_calls.append(enabled)

    async def set_proxy_url(self, record: EnvironmentRecord, proxy_url: str) -> None:
        if self.fail_proxy_once:
            self.fail_proxy_once = False
            raise RuntimeError("proxy update failed")
        self.proxy_calls.append(proxy_url)

    async def set_enabled_models(self, record: EnvironmentRecord, enabled_models) -> None:
        self.model_calls.append(tuple(enabled_models))

    async def set_concurrency_limit(self, record: EnvironmentRecord, concurrency_limit: int) -> None:
        self.concurrency_calls.append(concurrency_limit)


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
async def test_consuming_oauth_state_advances_version_against_stale_snapshot() -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    repository: Final = MemoryRepository(record)
    consumed_at: Final = utc_now()

    consumed: Final = await repository.consume_oauth_state(record.oauth_state, consumed_at)
    assert consumed is not None
    stale: Final = record.model_copy(update={"oauth_state_consumed_at": None})

    stale_saved: Final = await repository.save_if_version(stale, record.version)
    current: Final = await repository.get(record.id)

    assert consumed.version == record.version + 1
    assert stale_saved is None
    assert current is not None
    assert current.oauth_state_consumed_at == consumed_at


@pytest.mark.asyncio
async def test_late_oauth_callback_after_polling_completion_cannot_change_ready_state(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    state: Final = bootstrap._callback_state(record)
    awaiting: Final = record.model_copy(
        update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]}
    )
    repository: Final = MemoryRepository(awaiting)
    cli: Final = FakeCLIProxy(authorization_status="ok")
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    polled: Final = await service._refresh_authorization(awaiting)
    late_callback: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=state))
    durable: Final = await repository.get(record.id)

    assert polled.status is EnvironmentStatus.READY
    assert isinstance(late_callback, Failure)
    assert late_callback.code is FailureCode.CONFLICT
    assert durable is not None
    assert durable.status is EnvironmentStatus.READY
    assert cli.submit_calls == []


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
async def test_oauth_callback_consumes_signed_state_once_and_validates_immediately(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    repository: Final = MemoryRepository(record)
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    state: Final = service._callback_state(record)
    signed_record: Final = record.model_copy(
        update={
            "oauth_state": state,
            "oauth_state_signature": state.rpartition(".")[2],
        }
    )
    await repository.save(signed_record)

    first: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=state))
    second: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=state))

    assert not isinstance(first, Failure)
    assert first.value.status == EnvironmentStatus.READY
    assert isinstance(second, Failure)
    assert second.code == FailureCode.CONFLICT
    assert cli.read_calls == 1
    assert (await repository.get(record.id)).oauth_state_consumed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reject_ready_save", "reject_configuration_claim", "expected_ready_attempts", "expected_claim_attempts"),
    ((False, True, 0, 1),),
)
async def test_oauth_callback_fails_when_ready_or_configuration_claim_conflicts(
    tmp_path: Path,
    reject_ready_save: bool,
    reject_configuration_claim: bool,
    expected_ready_attempts: int,
    expected_claim_attempts: int,
) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    state: Final = bootstrap._callback_state(record)
    signed: Final = record.model_copy(
        update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]}
    )
    repository: Final = AuthorizationConflictRepository(
        signed,
        reject_ready_save=reject_ready_save,
        reject_configuration_claim=reject_configuration_claim,
    )
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=state))

    assert isinstance(result, Failure)
    assert result.code == FailureCode.CONFLICT
    assert cli.submit_calls != []
    assert cli.proxy_calls == []
    assert repository.ready_save_attempts == expected_ready_attempts
    assert repository.configuration_claim_attempts == expected_claim_attempts


@pytest.mark.asyncio
async def test_oauth_callback_preserves_configuration_failure(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    state: Final = bootstrap._callback_state(record)
    signed: Final = record.model_copy(
        update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]}
    )
    repository: Final = MemoryRepository(signed)
    cli: Final = FakeCLIProxy()
    cli.fail_proxy_once = True
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=state))

    assert isinstance(result, Failure)
    assert result.code == FailureCode.UPSTREAM
    persisted: Final = await repository.get(record.id)
    assert persisted is not None
    assert persisted.status is EnvironmentStatus.ERROR
    assert persisted.configuration_pending is True
    assert cli.proxy_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_configuration_claim", (False, True))
async def test_refresh_authorization_handles_reconciliation_conflicts(
    tmp_path: Path,
    reject_configuration_claim: bool,
) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    state: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )._callback_state(record)
    awaiting: Final = record.model_copy(
        update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]}
    )
    repository: Final = AuthorizationConflictRepository(
        awaiting,
        reject_ready_save=not reject_configuration_claim,
        reject_configuration_claim=reject_configuration_claim,
    )
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(authorization_status="ok"),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    refreshed: Final = await service._refresh_authorization(awaiting)

    assert refreshed.status is (
        EnvironmentStatus.VALIDATING if reject_configuration_claim else EnvironmentStatus.READY
    )
    persisted: Final = await repository.get(record.id)
    assert persisted is not None
    assert persisted.status is (
        EnvironmentStatus.VALIDATING if reject_configuration_claim else EnvironmentStatus.READY
    )
    assert persisted.configuration_pending is not reject_configuration_claim


@pytest.mark.asyncio
@pytest.mark.parametrize("observed_status", (EnvironmentStatus.DISABLED, EnvironmentStatus.COOLING_DOWN))
async def test_oauth_callback_persists_legal_non_ready_authorization_state(
    tmp_path: Path,
    observed_status: EnvironmentStatus,
) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None).model_copy(
        update={
            "enabled": observed_status is not EnvironmentStatus.DISABLED,
            "manual_cooldown": observed_status is EnvironmentStatus.COOLING_DOWN,
        }
    )
    bootstrap: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    state: Final = bootstrap._callback_state(record)
    awaiting: Final = record.model_copy(
        update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]}
    )
    repository: Final = MemoryRepository(awaiting)
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(observed_status=observed_status),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=state))

    assert not isinstance(result, Failure)
    assert result.value.status is observed_status
    persisted: Final = await repository.get(record.id)
    assert persisted is not None
    assert persisted.status is observed_status
    assert persisted.configuration_pending is False


@pytest.mark.asyncio
async def test_refresh_authorization_recovers_configuration_claim_conflict_before_routing(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    state: Final = bootstrap._callback_state(record)
    awaiting: Final = record.model_copy(
        update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]}
    )
    repository: Final = AuthorizationRecoveryRepository(awaiting)
    cli: Final = FakeCLIProxy(authorization_status="ok")
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    refreshed: Final = await service._refresh_authorization(awaiting)
    conflicted: Final = await repository.get(record.id)
    assert conflicted is not None
    before_recovery: Final = service._gateway_environment(conflicted)
    recovered: Final = await service.get_environment(record.id)
    second_gateway: Final = await service.list_gateway_environments()

    assert refreshed.status is EnvironmentStatus.VALIDATING
    assert before_recovery.routable is False
    assert not isinstance(recovered, Failure)
    assert recovered.value.status is EnvironmentStatus.READY
    assert recovered.value.configuration_pending is False
    assert recovered.value.observed_configuration_version == recovered.value.desired_configuration_version
    assert second_gateway[0].routable is True
    assert cli.proxy_calls == [""]


@pytest.mark.asyncio
async def test_refresh_authorization_reloads_durable_record_after_final_configuration_save_conflict(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    state: Final = bootstrap._callback_state(record)
    awaiting: Final = record.model_copy(
        update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]}
    )
    repository: Final = FinalConfigurationSaveConflictRepository(awaiting)
    cli: Final = FakeCLIProxy(authorization_status="ok")
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    refreshed: Final = await service._refresh_authorization(awaiting)
    durable: Final = await repository.get(record.id)
    gateway: Final = await service.list_gateway_environments()

    assert durable is not None
    assert refreshed == durable
    assert durable.configuration_pending is True
    assert service._gateway_environment(durable).routable is False
    assert gateway[0].routable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observed_status", "enabled", "manual_cooldown"),
    (
        (EnvironmentStatus.DISABLED, False, False),
        (EnvironmentStatus.COOLING_DOWN, True, True),
        (EnvironmentStatus.COOLING_DOWN, True, False),
    ),
)
async def test_non_ready_reauthorization_clears_stale_configuration_work(
    tmp_path: Path,
    observed_status: EnvironmentStatus,
    enabled: bool,
    manual_cooldown: bool,
) -> None:
    record: Final = _record(status=EnvironmentStatus.ERROR).model_copy(
        update={
            "enabled": enabled,
            "manual_cooldown": manual_cooldown,
            "configuration_pending": True,
            "desired_configuration_version": 2,
            "observed_configuration_version": 1,
        }
    )
    bootstrap: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    authorization: Final = await bootstrap.authorize_environment(record.id)
    assert not isinstance(authorization, Failure)
    state: Final = (await bootstrap._repository.get(record.id)).oauth_state
    assert state is not None
    cli: Final = FakeCLIProxy(observed_status=observed_status)
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=bootstrap._repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=state))
    fetched: Final = await service.get_environment(record.id)
    gateway: Final = await service.list_gateway_environments()
    persisted: Final = await bootstrap._repository.get(record.id)

    assert not isinstance(result, Failure)
    assert not isinstance(fetched, Failure)
    assert persisted is not None
    assert persisted.status is observed_status
    assert persisted.configuration_pending is False
    assert persisted.desired_configuration_version == persisted.observed_configuration_version
    assert cli.proxy_calls == []
    assert cli.model_calls == []
    assert cli.status_calls == []
    assert cli.concurrency_calls == []
    assert gateway[0].routable is False


@pytest.mark.asyncio
async def test_oauth_callback_rejects_missing_signature_after_atomic_consumption(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    state: Final = bootstrap._callback_state(record)
    signed: Final = record.model_copy(
        update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]}
    )
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MissingConsumedSignatureRepository(signed),
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=state))

    assert isinstance(result, Failure)
    assert result.code == FailureCode.CONFLICT
    assert cli.submit_calls == []


@pytest.mark.asyncio
async def test_oauth_callback_rejects_expired_mismatched_and_unknown_states_without_forwarding(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    repository: Final = MemoryRepository(record)
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    state: Final = service._callback_state(record)
    signed_record: Final = record.model_copy(
        update={
            "oauth_state": state,
            "oauth_state_signature": state.rpartition(".")[2],
            "oauth_expires_at": utc_now() - timedelta(minutes=1),
        }
    )
    await repository.save(signed_record)

    expired: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=state))
    mismatched: Final = await service.submit_oauth_callback(
        OAuthCallback(code="code", state=state),
        uuid4(),
    )
    unknown: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state="unknown-state-1234"))

    assert isinstance(expired, Failure)
    assert expired.code == FailureCode.CONFLICT
    assert isinstance(mismatched, Failure)
    assert mismatched.code == FailureCode.CONFLICT
    assert isinstance(unknown, Failure)
    assert unknown.code == FailureCode.NOT_FOUND
    assert cli.read_calls == 0
    assert cli.submit_calls == []


@pytest.mark.asyncio
async def test_oauth_callback_rejects_signature_mismatch_without_forwarding(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    repository: Final = MemoryRepository(record)
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    tampered_state: Final = "tampered-state-1234.invalid-signature"
    await repository.save(
        record.model_copy(
            update={
                "oauth_state": tampered_state,
                "oauth_state_signature": "invalid-signature",
            }
        )
    )

    result: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=tampered_state))

    assert isinstance(result, Failure)
    assert result.code == FailureCode.CONFLICT
    assert cli.read_calls == 0
    assert cli.submit_calls == []


@pytest.mark.asyncio
async def test_oauth_callback_does_not_forward_signature_mismatch_during_atomic_consume(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    repository: Final = MemoryRepository(record, validate_state_before_consume=False)
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    tampered_state: Final = "tampered-state-1234.invalid-signature"
    await repository.save(
        record.model_copy(
            update={
                "oauth_state": tampered_state,
                "oauth_state_signature": "invalid-signature",
            }
        )
    )

    result: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=tampered_state))

    assert isinstance(result, Failure)
    assert result.code == FailureCode.CONFLICT
    assert cli.submit_calls == []


@pytest.mark.asyncio
async def test_oauth_callback_immediately_reconciles_ready_environment(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    repository: Final = MemoryRepository(record)
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    created: Final = await service.create_environment(CreateEnvironmentRequest(name="Test environment"))

    assert not isinstance(created, Failure)
    state: Final = (await repository.list())[-1].oauth_state
    assert state is not None

    result: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=state))

    assert not isinstance(result, Failure)
    assert result.value.status == EnvironmentStatus.READY
    assert cli.proxy_calls == [""]
    assert cli.model_calls == [("gpt-5",)]
    assert cli.status_calls == [True]
    assert cli.concurrency_calls == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (EnvironmentStatus.ERROR, EnvironmentStatus.AWAITING_AUTHORIZATION))
async def test_authorize_environment_refreshes_recoverable_environment_without_provisioning(
    tmp_path: Path,
    status: EnvironmentStatus,
) -> None:
    record: Final = _record(status=status, auth_file_name=None)
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

    result: Final = await service.authorize_environment(record.id)

    assert not isinstance(result, Failure)
    assert result.value.environment.status == EnvironmentStatus.AWAITING_AUTHORIZATION
    assert str(result.value.authorization_url).startswith("https://example.com/oauth?state=")
    assert "state-for-test-1234" not in str(result.value.authorization_url)
    assert result.value.environment.last_error is None
    assert runtime.provisioned == []


@pytest.mark.asyncio
async def test_update_operation_id_is_idempotent_and_pending_configuration_retries(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    repository: Final = MemoryRepository(record)
    cli: Final = FakeCLIProxy()
    cli.fail_proxy_once = True
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
        operation_id="update-1",
        name="Updated",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )

    failed: Final = await service.update_environment(record.id, request)
    pending: Final = await repository.get(record.id)
    retried: Final = await service.reconcile_pending_configurations()
    repeated: Final = await service.update_environment(record.id, request)

    assert isinstance(failed, Failure)
    assert failed.code == FailureCode.UPSTREAM
    assert pending is not None
    assert pending.configuration_pending is True
    assert pending.status == EnvironmentStatus.ERROR
    assert len(retried) == 1
    assert retried[0].configuration_pending is False
    assert retried[0].observed_configuration_version == retried[0].desired_configuration_version
    assert not isinstance(repeated, Failure)
    assert repeated.value.version == retried[0].version
    assert cli.concurrency_calls == [3]


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
