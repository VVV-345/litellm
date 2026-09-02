"""本文件覆盖号池 Manager 的额度解析、环境状态和 Compose 隔离约束。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Final
from uuid import uuid4

import httpx
import pytest
import yaml
from account_pool.app import _reconcile_pending_configurations_until_cancelled
from account_pool.cliproxy import HttpCLIProxyClient, _QuotaObservation, parse_quota
from account_pool.compose import ComposeRuntime, _communicate_with_timeout, render_compose
from account_pool.config import Settings, validate_proxy_profile_url
from account_pool.domain import (
    CreateEnvironmentRequest,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    OAuthCallback,
    Provider,
    ProxyMode,
    ProxyProfile,
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
        if (
            self.validate_state_before_consume
            and record.oauth_expires_at is not None
            and record.oauth_expires_at <= consumed_at
        ):
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


class FailFinalConfigurationSaveOnceRepository(MemoryRepository):
    def __init__(self, record: EnvironmentRecord) -> None:
        super().__init__(record)
        self.failed = False

    async def save_if_version(
        self,
        record: EnvironmentRecord,
        expected_version: int,
    ) -> EnvironmentRecord | None:
        if not self.failed and not record.configuration_pending:
            self.failed = True
            return None
        return await super().save_if_version(record, expected_version)


class AuthorizationFailureRaceRepository(MemoryRepository):
    def __init__(self, record: EnvironmentRecord, replacement: EnvironmentRecord) -> None:
        super().__init__(record)
        self.replacement = replacement
        self.raced = False

    async def save_if_version(
        self,
        record: EnvironmentRecord,
        expected_version: int,
    ) -> EnvironmentRecord | None:
        if record.status is EnvironmentStatus.ERROR and not self.raced:
            self.raced = True
            await self.save(self.replacement)
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


class FinalAuthorizationStatusRaceRepository(MemoryRepository):
    def __init__(self, record: EnvironmentRecord, raced_status: EnvironmentStatus) -> None:
        super().__init__(record)
        self.raced_status = raced_status
        self.raced = False

    async def get(self, environment_id):
        current = await super().get(environment_id)
        if (
            current is not None
            and not self.raced
            and current.status is EnvironmentStatus.READY
            and not current.configuration_pending
        ):
            self.raced = True
            raced = current.model_copy(
                update={
                    "version": current.version + 1,
                    "status": self.raced_status,
                    "desired_state": self.raced_status,
                    "updated_at": utc_now(),
                }
            )
            await self.save(raced)
            return raced
        return current


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
        self.configuration_completed = asyncio.Event()
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
        self.configuration_completed.set()

    async def apply_configuration(self, record: EnvironmentRecord, configuration: EnvironmentConfiguration) -> None:
        await self.set_proxy_url(record, configuration.proxy_url)
        await self.set_enabled_models(record, configuration.enabled_models)
        await self.set_credential_enabled(record, configuration.credential_enabled)
        await self.set_concurrency_limit(record, configuration.concurrency_limit)


class InvalidAuthorizationURLCLI(FakeCLIProxy):
    def __init__(self, authorization_url: str) -> None:
        super().__init__()
        self.authorization_url = authorization_url

    async def start_openai_authorization(self, record: EnvironmentRecord) -> tuple[str, str]:
        return self.authorization_url, "state-for-test-1234"


class BlockingCallbackCLI(FakeCLIProxy):
    def __init__(self) -> None:
        super().__init__()
        self.callback_started = asyncio.Event()
        self.release_callback = asyncio.Event()

    async def submit_callback(self, record: EnvironmentRecord, callback: OAuthCallback) -> None:
        self.submit_calls.append(callback)
        self.callback_started.set()
        await self.release_callback.wait()


class EmptyProfiles:
    async def list(self):
        return ()

    async def get_url(self, profile_id: str):
        return None


class StaticProfiles:
    def __init__(self, proxy_url: str) -> None:
        self.proxy_url: Final = proxy_url

    async def list(self):
        return ()

    async def get_url(self, profile_id: str):
        return self.proxy_url


class CompletedDockerProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self._returncode: Final = returncode
        self._stdout: Final = stdout
        self._stderr: Final = stderr
        self.killed = False

    @property
    def returncode(self) -> int:
        return self._returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


class RecordingDockerRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    async def __call__(self, arguments: tuple[str, ...], environment: dict[str, str]) -> CompletedDockerProcess:
        self.calls.append((arguments, environment))
        return CompletedDockerProcess()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="postgresql://unused",
        data_root=tmp_path,
        manager_token="m" * 32,
        secret_seed="s" * 32,
        ssh_host="example.com",
        ssh_user="operator",
    )


def _settings_with(tmp_path: Path, **updates: object) -> Settings:
    return Settings.model_validate({**_settings(tmp_path).model_dump(), **updates})


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


def test_proxy_profile_keeps_non_sensitive_protocol_metadata() -> None:
    profile: Final = ProxyProfile(id="office", name="办公代理", protocol="https")

    assert profile.protocol == "https"


@pytest.mark.asyncio
async def test_read_account_persists_automatic_cooldown_marker() -> None:
    record: Final = _record(status=EnvironmentStatus.READY)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={"files": [{"name": "codex.json", "provider": "codex", "unavailable": True}]},
                request=request,
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(200, json={"models": [{"id": "gpt-5"}]}, request=request)
        return httpx.Response(404, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    try:
        observed: Final = await proxy.read_account(record)
    finally:
        await client.aclose()

    assert observed.status is EnvironmentStatus.COOLING_DOWN
    assert observed.automatic_cooldown is True


def test_oauth_callback_accepts_error_description_without_error_code() -> None:
    callback: Final = OAuthCallback(state="state-for-test-1234", error_description="access denied")

    assert callback.error is None
    assert callback.error_description == "access denied"


def test_render_compose_uses_uuid_identity_and_retains_upstream_egress(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.PROVISIONING, auth_file_name=None)
    renamed: Final = record.model_copy(update={"name": "A display name that must not identify Docker resources"})
    settings: Final = _settings(tmp_path)

    rendered: Final = yaml.safe_load(render_compose(record, settings))
    rerendered: Final = yaml.safe_load(render_compose(renamed, settings))
    service: Final = rendered["services"]["cli-proxy-api"]

    assert "ports" not in service
    assert rendered == rerendered
    assert rendered["name"] == f"account-pool-{record.id.hex}"
    assert rendered["networks"]["environment"] == {
        "name": f"account-pool-{record.id.hex}",
        "driver": "bridge",
        "internal": False,
    }
    assert service["networks"] == {"environment": {"aliases": [f"cliproxy-{record.id.hex}"]}}


def test_render_compose_hardens_cli_proxy_container(tmp_path: Path) -> None:
    rendered: Final = yaml.safe_load(
        render_compose(_record(status=EnvironmentStatus.PROVISIONING), _settings(tmp_path))
    )
    service: Final = rendered["services"]["cli-proxy-api"]

    assert service["user"] == "65532:65532"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["mem_limit"] == "512m"
    assert service["cpus"] == "1.0"
    assert service["pids_limit"] == 256
    assert service["logging"] == {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}


@pytest.mark.parametrize(
    "docker_host",
    (
        "unix:///var/run/docker.sock",
        "npipe:////./pipe/docker_engine",
        "tcp://user:password@docker-socket-proxy:2375",
        "tcp://docker-socket-proxy:2375/v1.47",
        "tcp://docker-socket-proxy:2375?debug=true",
        "tcp://docker-socket-proxy:2375#fragment",
        "tcp://docker-socket-proxy:not-a-port",
        "http://docker-socket-proxy:2375",
        "https://docker-socket-proxy:2375",
        "tcp://127.0.0.1:2375",
        "tcp://[::ffff:127.0.0.1]:2375",
        "tcp://[0:0:0:0:0:0:0:1]:2375",
        "tcp://localhost.:2375",
        "tcp://unexpected-proxy:2375",
        "tcp://docker-socket-proxy.:2375",
    ),
)
def test_settings_rejects_unsafe_or_unexpected_docker_hosts(tmp_path: Path, docker_host: str) -> None:
    with pytest.raises(ValueError, match="docker_host"):
        _settings_with(tmp_path, docker_host=docker_host)


@pytest.mark.parametrize("ssh_host", ("ssh://host", "host/path", "host?query", "host#fragment", "user@host"))
def test_settings_rejects_invalid_ssh_hosts(tmp_path: Path, ssh_host: str) -> None:
    with pytest.raises(ValueError, match="ssh_host"):
        Settings(
            database_url="postgresql://unused",
            data_root=tmp_path,
            manager_token="m" * 32,
            secret_seed="s" * 32,
            ssh_host=ssh_host,
            ssh_user="operator",
        )


@pytest.mark.parametrize("bind_host", ("0.0.0.0", "192.168.1.10", "example.com"))
def test_settings_rejects_non_loopback_callback_bind_hosts(tmp_path: Path, bind_host: str) -> None:
    with pytest.raises(ValueError, match="callback_bind_host"):
        _settings_with(tmp_path, callback_bind_host=bind_host)


@pytest.mark.parametrize(
    "url",
    (
        "socks5://proxy.example",
        "file:///tmp/proxy",
        "http://user:secret@proxy.example",
        "http://proxy.example:70000",
        "https://proxy.example:bad",
        "https://proxy.example/path",
        "https://proxy.example?token=secret",
        "https://proxy.example#fragment",
    ),
)
def test_proxy_profile_url_rejects_unsafe_protocols_and_credentials(url: str) -> None:
    with pytest.raises(ValueError, match="proxy"):
        validate_proxy_profile_url(url)


def test_manager_compose_uses_socket_proxy_and_hardens_manager() -> None:
    compose_path: Final = Path(__file__).parents[1] / "docker-compose.manager.yml"
    manager_compose: Final = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    proxy: Final = manager_compose["services"]["docker-socket-proxy"]
    manager: Final = manager_compose["services"]["account-pool"]

    dockerfile: Final = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "mkdir -p /var/lib/litellm-account-pool/environments" in dockerfile
    assert "chown -R 65532:65532 /var/lib/litellm-account-pool" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert proxy["image"] == "tecnativa/docker-socket-proxy:0.3.0"
    assert proxy["environment"] == {
        "CONTAINERS": "1",
        "IMAGES": "1",
        "NETWORKS": "1",
        "INFO": "1",
        "POST": "1",
        "AUTH": "0",
        "BUILD": "0",
        "EXEC": "0",
        "PLUGINS": "0",
        "SECRETS": "0",
        "SERVICES": "0",
        "SESSION": "0",
        "SWARM": "0",
        "SYSTEM": "0",
        "TASKS": "0",
    }
    assert proxy["networks"] == ["account-pool-socket"]
    assert manager["networks"] == {"account-pool-socket": None, "litellm-control": {"aliases": ["account-pool"]}}
    assert manager["depends_on"]["docker-cli-check"]["condition"] == "service_completed_successfully"
    assert manager["user"] == "65532:65532"
    assert manager["read_only"] is True
    assert manager["cap_drop"] == ["ALL"]
    assert manager["security_opt"] == ["no-new-privileges:true"]
    assert manager["mem_limit"] == "512m"
    assert manager["cpus"] == "1.0"
    assert manager["pids_limit"] == 256
    assert manager["logging"] == {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}


@pytest.mark.asyncio
async def test_compose_runtime_uses_exact_docker_commands_and_allowlisted_environment(tmp_path: Path) -> None:
    settings: Final = _settings(tmp_path)
    runner: Final = RecordingDockerRunner()
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver("s" * 32), runner=runner)
    record: Final = _record(status=EnvironmentStatus.READY)
    directory: Final = runtime.environment_dir(record.id)
    directory.mkdir()
    (directory / "compose.yaml").write_text("name: test\n", encoding="utf-8")

    await runtime._compose(record.id, "up", "-d", "--pull", "always", "--remove-orphans")
    await runtime._connect_control_plane(record.id, settings.manager_container)
    await runtime._disconnect_control_plane(record.id, settings.gateway_container)

    network: Final = f"account-pool-{record.id.hex}"
    assert tuple(arguments for arguments, _ in runner.calls) == (
        (
            "docker",
            "compose",
            "--project-name",
            network,
            "--file",
            str(directory / "compose.yaml"),
            "up",
            "-d",
            "--pull",
            "always",
            "--remove-orphans",
        ),
        ("docker", "network", "connect", network, settings.manager_container),
        ("docker", "network", "disconnect", network, settings.gateway_container),
    )
    assert tuple(environment for _, environment in runner.calls) == (
        {"DOCKER_HOST": "tcp://docker-socket-proxy:2375", "HOME": "/tmp"},
        {"DOCKER_HOST": "tcp://docker-socket-proxy:2375", "HOME": "/tmp"},
        {"DOCKER_HOST": "tcp://docker-socket-proxy:2375", "HOME": "/tmp"},
    )


@pytest.mark.asyncio
async def test_docker_command_timeout_kills_and_reaps_process() -> None:
    process: Final = await asyncio.create_subprocess_exec(
        "python",
        "-c",
        "import time; time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    with pytest.raises(RuntimeError, match="timed out"):
        await _communicate_with_timeout(process, 0.01)

    assert process.returncode is not None


@pytest.mark.asyncio
async def test_unsafe_proxy_profile_url_is_rejected_before_cli_proxy_update(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=MemoryRepository(record),
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=StaticProfiles("http://user:secret@proxy.example"),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name=record.name,
        concurrency_limit=record.concurrency_limit,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.PROFILE,
        proxy_profile_id="proxy-profile",
        enabled_models=record.enabled_models,
    )

    result: Final = await service.update_environment(record.id, request)

    assert isinstance(result, Failure)
    assert result.code is FailureCode.INVALID
    assert "secret" not in result.message
    assert cli.proxy_calls == []


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
    state: Final = service._callback_state(record)
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]})

    result: Final = await service._refresh_authorization(signed)

    assert result.status == EnvironmentStatus.AWAITING_AUTHORIZATION
    assert cli.read_calls == 0


@pytest.mark.asyncio
async def test_refresh_authorization_resumes_after_state_consumed_before_validation(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    repository: Final = MemoryRepository(record)
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(authorization_status="ok"),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    state: Final = service._callback_state(record)
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]})
    await repository.save(signed)
    consumed: Final = await repository.consume_oauth_state(state, utc_now())

    assert consumed is not None
    resumed: Final = await service._refresh_authorization(consumed)
    durable: Final = await repository.get(record.id)

    assert resumed.status is EnvironmentStatus.READY
    assert durable is not None
    assert durable.status is EnvironmentStatus.READY


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", (False, True))
async def test_refresh_authorization_does_not_overwrite_newer_state(
    tmp_path: Path,
    expired: bool,
) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = _service(record, FakeCLIProxy(), tmp_path)
    state: Final = bootstrap._callback_state(record)
    signed: Final = record.model_copy(
        update={
            "oauth_state": state,
            "oauth_state_signature": state.rpartition(".")[2],
            "oauth_expires_at": utc_now() - timedelta(minutes=1) if expired else record.oauth_expires_at,
        }
    )
    replacement: Final = signed.model_copy(
        update={
            "version": signed.version + 1,
            "status": EnvironmentStatus.READY,
            "desired_state": EnvironmentStatus.READY,
            "auth_file_name": "codex.json",
            "oauth_state": None,
            "oauth_expires_at": None,
            "oauth_state_consumed_at": None,
            "oauth_state_signature": None,
            "oauth_provider_state": None,
            "oauth_authorization_url": None,
        }
    )
    repository: Final = AuthorizationFailureRaceRepository(signed, replacement)
    cli: Final = FakeCLIProxy(
        authorization_status="error:https://user:password@example.com/oauth?code=secret-code&state=secret-state"
    )
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service._refresh_authorization(signed)
    durable: Final = await repository.get(record.id)

    assert result == replacement
    assert durable == replacement


@pytest.mark.asyncio
async def test_refresh_authorization_redacts_upstream_error(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = _service(record, FakeCLIProxy(), tmp_path)
    state: Final = bootstrap._callback_state(record)
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]})
    repository: Final = MemoryRepository(signed)
    cli: Final = FakeCLIProxy(
        authorization_status="error:https://user:password@example.com/oauth?code=secret-code&state=secret-state Bearer secret-token"
    )
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service._refresh_authorization(signed)
    durable: Final = await repository.get(record.id)

    assert result.status is EnvironmentStatus.ERROR
    assert durable is not None
    assert durable.last_error is not None
    assert "password" not in durable.last_error
    assert "secret-code" not in durable.last_error
    assert "secret-state" not in durable.last_error
    assert "secret-token" not in durable.last_error


@pytest.mark.asyncio
async def test_refresh_authorization_rejects_invalid_persisted_state_signature(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = _service(record, FakeCLIProxy(), tmp_path)
    state: Final = bootstrap._callback_state(record)
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": "invalid-signature"})
    repository: Final = MemoryRepository(signed)
    cli: Final = FakeCLIProxy(authorization_status="ok")
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    refreshed: Final = await service._refresh_authorization(signed)
    durable: Final = await repository.get(record.id)

    assert refreshed.status is EnvironmentStatus.ERROR
    assert durable is not None
    assert durable.status is EnvironmentStatus.ERROR
    assert durable.oauth_state is None
    assert cli.read_calls == 0


@pytest.mark.asyncio
async def test_refresh_authorization_rejects_missing_signature_after_atomic_consumption(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = _service(record, FakeCLIProxy(), tmp_path)
    state: Final = bootstrap._callback_state(record)
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]})
    repository: Final = MissingConsumedSignatureRepository(signed)
    cli: Final = FakeCLIProxy(authorization_status="ok")
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    refreshed: Final = await service._refresh_authorization(signed)
    durable: Final = await repository.get(record.id)

    assert refreshed.status is EnvironmentStatus.ERROR
    assert durable is not None
    assert durable.status is EnvironmentStatus.ERROR
    assert durable.oauth_state is None
    assert cli.read_calls == 0


@pytest.mark.asyncio
async def test_oauth_callback_error_marks_authorization_failed_without_forwarding(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = _service(record, FakeCLIProxy(), tmp_path)
    state: Final = bootstrap._callback_state(record)
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]})
    repository: Final = MemoryRepository(signed)
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service.submit_oauth_callback(
        OAuthCallback(
            state=state,
            error="access_denied",
            error_description="https://user:password@example.com/oauth?error=secret-error",
        )
    )
    durable: Final = await repository.get(record.id)

    assert isinstance(result, Failure)
    assert result.code is FailureCode.CONFLICT
    assert cli.submit_calls == []
    assert cli.read_calls == 0
    assert durable is not None
    assert durable.status is EnvironmentStatus.ERROR
    assert durable.oauth_state is None
    assert durable.oauth_expires_at is None
    assert durable.last_error is not None
    assert "secret-error" not in durable.last_error
    assert "password" not in durable.last_error


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


async def test_elapsed_automatic_cooldown_requires_health_before_quota_refresh(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.COOLING_DOWN).model_copy(update={"automatic_cooldown": True})
    cli: Final = FakeCLIProxy(data_plane_healthy=False)
    service: Final = _service(record, cli, tmp_path)

    result: Final = await service._refresh_if_needed(record)

    assert result.status is EnvironmentStatus.COOLING_DOWN
    assert cli.health_calls == 1
    assert cli.read_calls == 0

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
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]})
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
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]})
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

    assert refreshed.status is (EnvironmentStatus.VALIDATING if reject_configuration_claim else EnvironmentStatus.READY)
    persisted: Final = await repository.get(record.id)
    assert persisted is not None
    assert persisted.status is (EnvironmentStatus.VALIDATING if reject_configuration_claim else EnvironmentStatus.READY)
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
async def test_refresh_authorization_reloads_durable_record_after_final_configuration_save_conflict(
    tmp_path: Path,
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
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]})
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
    expired_record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    expired_repository: Final = MemoryRepository(expired_record)
    expired_cli: Final = FakeCLIProxy()
    expired_service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=expired_repository,
        runtime=FakeRuntime(),
        cli_proxy=expired_cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    expired_state: Final = expired_service._callback_state(expired_record)
    signed_expired_record: Final = expired_record.model_copy(
        update={
            "oauth_state": expired_state,
            "oauth_state_signature": expired_state.rpartition(".")[2],
            "oauth_expires_at": utc_now() - timedelta(minutes=1),
        }
    )
    await expired_repository.save(signed_expired_record)

    mismatched_record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    mismatched_repository: Final = MemoryRepository(mismatched_record)
    mismatched_cli: Final = FakeCLIProxy()
    mismatched_service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=mismatched_repository,
        runtime=FakeRuntime(),
        cli_proxy=mismatched_cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )
    mismatched_state: Final = mismatched_service._callback_state(mismatched_record)
    signed_mismatched_record: Final = mismatched_record.model_copy(
        update={
            "oauth_state": mismatched_state,
            "oauth_state_signature": mismatched_state.rpartition(".")[2],
        }
    )
    await mismatched_repository.save(signed_mismatched_record)

    expired: Final = await expired_service.submit_oauth_callback(OAuthCallback(code="code", state=expired_state))
    mismatched: Final = await mismatched_service.submit_oauth_callback(
        OAuthCallback(code="code", state=mismatched_state),
        uuid4(),
    )
    unknown: Final = await mismatched_service.submit_oauth_callback(
        OAuthCallback(code="code", state="unknown-state-1234")
    )

    assert isinstance(expired, Failure)
    assert expired.code == FailureCode.CONFLICT
    assert isinstance(mismatched, Failure)
    assert mismatched.code == FailureCode.CONFLICT
    assert isinstance(unknown, Failure)
    assert unknown.code == FailureCode.NOT_FOUND
    assert expired_cli.read_calls == 0
    assert expired_cli.submit_calls == []
    assert mismatched_cli.read_calls == 0
    assert mismatched_cli.submit_calls == []


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
async def test_reauthorization_waits_for_inflight_oauth_callback(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = _service(record, FakeCLIProxy(), tmp_path)
    state: Final = bootstrap._callback_state(record)
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]})
    repository: Final = MemoryRepository(signed)
    cli: Final = BlockingCallbackCLI()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    callback_task: Final = asyncio.create_task(service.submit_oauth_callback(OAuthCallback(code="code", state=state)))
    await cli.callback_started.wait()
    reauthorize_task: Final = asyncio.create_task(service.authorize_environment(record.id))
    await asyncio.sleep(0)

    assert not reauthorize_task.done()

    cli.release_callback.set()
    callback_result: Final
    reauthorize_result: Final
    callback_result, reauthorize_result = await asyncio.gather(callback_task, reauthorize_task)

    assert not isinstance(callback_result, Failure)
    assert not isinstance(reauthorize_result, Failure)
    assert reauthorize_result.value.environment.status is EnvironmentStatus.AWAITING_AUTHORIZATION


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raced_status",
    (
        EnvironmentStatus.ERROR,
        EnvironmentStatus.DELETING,
        EnvironmentStatus.AWAITING_AUTHORIZATION,
        EnvironmentStatus.VALIDATING,
    ),
)
async def test_oauth_callback_rejects_non_completion_status_after_final_reload(
    tmp_path: Path,
    raced_status: EnvironmentStatus,
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
    signed: Final = record.model_copy(update={"oauth_state": state, "oauth_state_signature": state.rpartition(".")[2]})
    repository: Final = FinalAuthorizationStatusRaceRepository(signed, raced_status)
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
    durable: Final = await repository.get(record.id)

    assert isinstance(result, Failure)
    assert result.code is FailureCode.CONFLICT
    assert durable is not None
    assert durable.status is raced_status
    assert cli.submit_calls != []


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization_url", ("not-a-url", "/relative", "example.com/oauth"))
async def test_create_rejects_invalid_upstream_authorization_url_without_pending_record(
    tmp_path: Path,
    authorization_url: str,
) -> None:
    cli: Final = InvalidAuthorizationURLCLI(authorization_url)
    repository: Final = MemoryRepository(_record(status=EnvironmentStatus.READY))
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service.create_environment(CreateEnvironmentRequest(name="New environment"))

    assert isinstance(result, Failure)
    assert result.code is FailureCode.UPSTREAM
    assert all(item.status is not EnvironmentStatus.AWAITING_AUTHORIZATION for item in await repository.list())


@pytest.mark.asyncio
async def test_reauthorize_rejects_invalid_upstream_authorization_url_without_pending_record(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.ERROR)
    repository: Final = MemoryRepository(record)
    cli: Final = InvalidAuthorizationURLCLI("/relative")
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service.authorize_environment(record.id)
    durable: Final = await repository.get(record.id)

    assert isinstance(result, Failure)
    assert result.code is FailureCode.UPSTREAM
    assert durable is not None
    assert durable.status is EnvironmentStatus.ERROR
    assert durable.oauth_authorization_url is None
    assert durable.oauth_state is None


@pytest.mark.asyncio
async def test_reauthorize_failure_invalidates_previous_oauth_state(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    bootstrap: Final = _service(record, FakeCLIProxy(), tmp_path)
    old_state: Final = bootstrap._callback_state(record)
    pending: Final = record.model_copy(
        update={
            "oauth_state": old_state,
            "oauth_state_signature": old_state.rpartition(".")[2],
            "oauth_provider_state": "provider-state",
            "oauth_authorization_url": "https://example.com/oauth",
        }
    )
    repository: Final = MemoryRepository(pending)
    cli: Final = InvalidAuthorizationURLCLI("/relative")
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    result: Final = await service.authorize_environment(record.id)
    durable: Final = await repository.get(record.id)
    stale_callback: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=old_state))

    assert isinstance(result, Failure)
    assert result.code is FailureCode.UPSTREAM
    assert durable is not None
    assert durable.status is EnvironmentStatus.ERROR
    assert durable.version == pending.version + 1
    assert durable.oauth_state is None
    assert durable.oauth_expires_at is None
    assert durable.oauth_state_signature is None
    assert durable.oauth_provider_state is None
    assert durable.oauth_authorization_url is None
    assert isinstance(stale_callback, Failure)
    assert stale_callback.code is FailureCode.NOT_FOUND
    assert cli.submit_calls == []


@pytest.mark.asyncio
async def test_oauth_callback_rejects_state_not_in_authorization_status(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(
        update={
            "oauth_state": "state-for-test-1234",
            "oauth_state_signature": "invalid-signature",
            "oauth_expires_at": utc_now() + timedelta(minutes=5),
        }
    )
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

    result: Final = await service.submit_oauth_callback(OAuthCallback(code="code", state=record.oauth_state))

    assert isinstance(result, Failure)
    assert result.code is FailureCode.CONFLICT
    assert cli.submit_calls == []


class OrderedFailingCLI(FakeCLIProxy):
    def __init__(self, failing_step: str | None = None) -> None:
        super().__init__()
        self.failing_step = failing_step
        self.events: list[str] = []

    async def set_proxy_url(self, record: EnvironmentRecord, proxy_url: str) -> None:
        self.events.append("proxy")
        if self.failing_step == "proxy":
            self.failing_step = None
            raise RuntimeError("proxy_url=https://user:password@example.com/ token=secret-token")
        await super().set_proxy_url(record, proxy_url)

    async def set_enabled_models(self, record: EnvironmentRecord, enabled_models) -> None:
        self.events.append("models")
        if self.failing_step == "models":
            self.failing_step = None
            raise RuntimeError("model configuration failed")
        await super().set_enabled_models(record, enabled_models)

    async def set_credential_enabled(self, record: EnvironmentRecord, enabled: bool) -> None:
        self.events.append("credential")
        if self.failing_step == "credential":
            self.failing_step = None
            raise RuntimeError("credential configuration failed")
        await super().set_credential_enabled(record, enabled)

    async def set_concurrency_limit(self, record: EnvironmentRecord, concurrency_limit: int) -> None:
        self.events.append("concurrency")
        if self.failing_step == "concurrency":
            self.failing_step = None
            raise RuntimeError("concurrency configuration failed")
        await super().set_concurrency_limit(record, concurrency_limit)


@pytest.mark.asyncio
async def test_reconciliation_retries_entire_configuration_in_fixed_order_after_middle_failure(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    repository: Final = MemoryRepository(record)
    cli: Final = OrderedFailingCLI(failing_step="models")
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
        operation_id="configuration-order-1",
        name="Updated",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )

    failed: Final = await service.update_environment(record.id, request)
    pending: Final = await repository.get(record.id)
    reconciled: Final = await service.reconcile_pending_configurations()

    assert isinstance(failed, Failure)
    assert pending is not None
    assert pending.status is EnvironmentStatus.ERROR
    assert pending.configuration_pending is True
    assert pending.configuration_last_error is not None
    assert "password" not in pending.configuration_last_error
    assert "secret-token" not in pending.configuration_last_error
    assert cli.events == ["proxy", "models", "proxy", "models", "credential", "concurrency"]
    assert reconciled[0].status is EnvironmentStatus.READY
    assert reconciled[0].configuration_pending is False


@pytest.mark.asyncio
async def test_configuration_snapshot_preserves_automatic_cooldown_credential_disablement(tmp_path: Path) -> None:
    record: Final = _record(
        status=EnvironmentStatus.COOLING_DOWN,
        cooldown_until=utc_now() + timedelta(minutes=5),
    )
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
    request: Final = UpdateEnvironmentRequest(
        version=record.version,
        name="Cooling",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )

    updated: Final = await service.update_environment(record.id, request)
    durable: Final = await repository.get(record.id)

    assert not isinstance(updated, Failure)
    assert durable is not None
    assert durable.desired_configuration is not None
    assert durable.desired_configuration.credential_enabled is False
    assert cli.status_calls == [False]


@pytest.mark.asyncio
async def test_restarted_service_recovers_pending_configuration_from_repository(tmp_path: Path) -> None:
    configuration: Final = EnvironmentConfiguration(
        name="Recovered",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
        credential_enabled=True,
    )
    record: Final = _record(status=EnvironmentStatus.ERROR).model_copy(
        update={
            "configuration_pending": True,
            "desired_state": EnvironmentStatus.READY,
            "desired_configuration_version": 2,
            "observed_configuration_version": 1,
            "desired_configuration": configuration,
        }
    )
    repository: Final = MemoryRepository(record)
    cli: Final = FakeCLIProxy()
    restarted: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=cli,
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    reconciled: Final = await restarted.reconcile_pending_configurations()
    durable: Final = await repository.get(record.id)

    assert reconciled[0].status is EnvironmentStatus.READY
    assert durable is not None
    assert durable.configuration_pending is False
    assert durable.observed_configuration_version == durable.desired_configuration_version


@pytest.mark.asyncio
async def test_reconciliation_skips_deleted_or_completed_record_after_waiting_for_lock(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(
        update={"configuration_pending": True, "desired_configuration_version": 1}
    )
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
    lock: Final = await service._lock_for(record.id)

    async with lock:
        reconciled_task: Final = asyncio.create_task(service._reconcile_configuration(record))
        await asyncio.sleep(0)
        await repository.delete(record.id)

    result: Final = await reconciled_task

    assert isinstance(result, Failure)
    assert result.code is FailureCode.NOT_FOUND
    assert cli.proxy_calls == []


@pytest.mark.asyncio
async def test_reconciliation_skips_completed_record_after_waiting_for_lock(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(
        update={"configuration_pending": True, "desired_configuration_version": 1}
    )
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
    lock: Final = await service._lock_for(record.id)

    async with lock:
        reconciled_task: Final = asyncio.create_task(service._reconcile_configuration(record))
        await asyncio.sleep(0)
        await repository.save(
            record.model_copy(update={"configuration_pending": False, "observed_configuration_version": 1})
        )

    result: Final = await reconciled_task

    assert not isinstance(result, Failure)
    assert cli.proxy_calls == []


@pytest.mark.asyncio
async def test_reconciliation_recovers_after_final_database_save_conflict(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.ERROR).model_copy(
        update={
            "configuration_pending": True,
            "desired_configuration_version": 1,
        }
    )
    repository: Final = FailFinalConfigurationSaveOnceRepository(record)
    service: Final = EnvironmentService(
        settings=_settings(tmp_path),
        repository=repository,
        runtime=FakeRuntime(),
        cli_proxy=FakeCLIProxy(),
        proxy_profiles=EmptyProfiles(),
        secrets=EnvironmentSecretDeriver("s" * 32),
    )

    first: Final = await service.reconcile_pending_configurations()
    durable_after_first: Final = await repository.get(record.id)
    second: Final = await service.reconcile_pending_configurations()

    assert first == ()
    assert durable_after_first is not None
    assert durable_after_first.configuration_pending is True
    assert second[0].configuration_pending is False


@pytest.mark.asyncio
async def test_background_reconciliation_retries_after_startup_failure(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(
        update={
            "configuration_pending": True,
            "desired_state": EnvironmentStatus.READY,
            "desired_configuration_version": 1,
        }
    )
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
    stopped: Final = asyncio.Event()
    task: Final = asyncio.create_task(
        _reconcile_pending_configurations_until_cancelled(service, stopped, retry_seconds=0.1)
    )
    await cli.configuration_completed.wait()
    stopped.set()
    await task
    durable: Final = await repository.get(record.id)

    assert durable is not None
    assert durable.configuration_pending is False


@pytest.mark.asyncio
async def test_repeated_operation_id_does_not_create_a_second_configuration_version(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
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
    request: Final = UpdateEnvironmentRequest(
        version=record.version,
        operation_id="same-operation",
        name="Updated",
        concurrency_limit=3,
        enabled=True,
        manual_cooldown=False,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        enabled_models=("gpt-5",),
    )

    first: Final = await service.update_environment(record.id, request)
    second: Final = await service.update_environment(record.id, request)

    assert not isinstance(first, Failure)
    assert not isinstance(second, Failure)
    assert first.value.desired_configuration_version == 1
    assert second.value.desired_configuration_version == 1
