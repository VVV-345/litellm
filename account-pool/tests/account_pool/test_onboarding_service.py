"""验证上号隔离、去重、重试、密码保护及等待授权的真实状态。"""

from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from account_pool.domain import (
    CreateEnvironmentRequest,
    EnvironmentStatus,
    ModelCooldown,
    ModelQuotaSnapshot,
    QuotaSnapshot,
    QuotaWindow,
    SupplierKind,
    utc_now,
)
from account_pool.onboarding_models import OnboardingAction, OnboardingEntry, OnboardingImport, OnboardingTarget
from account_pool.onboarding_service import OnboardingService, target_view, usable_models
from account_pool.result import Failure, Success
from account_pool.secrets import EnvironmentSecretDeriver
from account_pool.service import EnvironmentService
from test_account_pool import (
    EmptyProfiles,
    FakeCLIProxy,
    FakeRuntime,
    MemoryRepository,
    _fake_channels,
    _record,
    _settings,
)


class MemoryOnboarding:
    def __init__(self):
        self.rows = {}
        self.target_rows = {}

    @asynccontextmanager
    async def worker_lock(self):
        yield True

    async def list(self):
        return tuple(self.rows.values())

    async def get(self, item_id):
        return self.rows.get(item_id)

    async def insert(self, row):
        if row.item.id in self.rows or any(
            set(row.fingerprints) & set(other.fingerprints) for other in self.rows.values()
        ):
            return False
        self.rows[row.item.id] = row
        return True

    async def save(self, row):
        self.rows[row.item.id] = row

    async def targets(self):
        return tuple(self.target_rows.values())

    async def save_target(self, target):
        self.target_rows[target.supplier] = target


def setup_service(tmp_path: Path, cli: FakeCLIProxy | None = None):
    environments = MemoryRepository(_record(status=EnvironmentStatus.READY))
    runtime = FakeRuntime()
    upstream = cli or FakeCLIProxy()
    secrets = EnvironmentSecretDeriver("s" * 32)
    service = EnvironmentService(
        _settings(tmp_path),
        environments,
        runtime,
        upstream,
        EmptyProfiles(),
        secrets,
        channels=_fake_channels(runtime, upstream),
        direct_credential_validation_timeout_seconds=0.02,
        direct_credential_validation_interval_seconds=0,
    )
    tasks = MemoryOnboarding()
    return OnboardingService(tasks, environments, service, secrets), tasks, environments, runtime, upstream, service


def file_request():
    return OnboardingImport(
        job_id=uuid4(),
        source="auth_file",
        supplier="openai_codex",
        entries=(
            OnboardingEntry(
                label="account.json",
                content='{"type":"codex","email":"test@example.com","refresh_token":"private-refresh"}',
            ),
        ),
    )


def oauth_request(prepare=True):
    return OnboardingImport(
        job_id=uuid4(),
        source="oauth",
        supplier="openai_codex",
        mailbox="gmail",
        prepare_mailbox=prepare,
        entries=(
            OnboardingEntry(
                label="test@example.com", mailbox_password="mail-secret", supplier_password="supplier-secret"
            ),
        ),
    )


def test_model_target_excludes_exhausted_quota_and_active_model_cooldowns():
    exhausted = QuotaSnapshot(
        windows=(QuotaWindow(name="usage", used_percent=100, remaining_percent=0, window_minutes=60),)
    )
    record = _record(status=EnvironmentStatus.READY).model_copy(
        update={
            "enabled_models": ("available", "exhausted", "cooling"),
            "model_quotas": (ModelQuotaSnapshot(model="exhausted", quota=exhausted),),
            "model_cooldowns": (ModelCooldown(model="cooling", retry_at=utc_now() + timedelta(minutes=1)),),
        }
    )
    assert usable_models(record) == ("available",)
    assert usable_models(record.model_copy(update={"quota": exhausted})) == ()


async def test_file_import_creates_one_card_without_oauth_and_survives_resubmission(tmp_path):
    service, tasks, environments, runtime, cli, _ = setup_service(tmp_path)
    request = file_request()
    first = await service.submit(request)
    repeated = await service.submit(request)
    assert first.items == repeated.items
    assert repeated.preview[0].status == "duplicate"
    assert "private-refresh" not in tasks.rows[first.items[0].id].model_dump_json()
    await service.run_once()
    item = (await service.list())[0]
    assert item.state == "ready"
    assert item.source == "auth_file"
    assert item.mailbox is None
    assert item.card_name == "Codex-套餐待识别-test@example.com"
    assert len(runtime.provisioned) == 1
    assert len(cli.upload_calls) == 1
    assert (await environments.get(item.card_id)).oauth_state is None
    assert not cli.submit_calls and not cli.authorization_status_calls
    assert isinstance(await service.reveal(item.id), Failure)
    assert target_view(OnboardingTarget(supplier="openai_codex", count=1), (item,)).ready == 0


async def test_failed_file_validation_reuses_original_card_on_retry(tmp_path):
    cli = FakeCLIProxy(data_plane_healthy=False)
    service, _, _, runtime, _, _ = setup_service(tmp_path, cli)
    first = await service.submit(file_request())
    await service.run_once()
    assert (await service.list())[0].state == "failed"
    cli.data_plane_healthy = True
    await service.action(first.items[0].id, OnboardingAction(action="retry"))
    await service.run_once()
    item = (await service.list())[0]
    assert item.state == "ready"
    assert item.card_id == first.items[0].card_id
    assert {card.id for card in runtime.provisioned} == {item.card_id}


async def test_preview_rejects_provider_mismatch_and_deduplicates_rotated_credentials(tmp_path):
    service, _, _, _, _, _ = setup_service(tmp_path)
    original = file_request()
    request = original.model_copy(
        update={
            "entries": (
                original.entries[0],
                original.entries[0].model_copy(
                    update={"content": '{"type":"codex","email":"test@example.com","refresh_token":"rotated"}'}
                ),
                original.entries[0].model_copy(update={"content": '{"type":"claude","refresh_token":"other"}'}),
                original.entries[0].model_copy(update={"content": "[]"}),
            )
        }
    )
    assert [item.status for item in await service.preview(request)] == ["valid", "duplicate", "invalid", "invalid"]


async def test_mailbox_secrets_are_separate_and_generated_password_requires_confirmation(tmp_path):
    service, tasks, _, runtime, _, _ = setup_service(tmp_path)
    item = (await service.submit(oauth_request())).items[0]
    assert item.state == "awaiting_mailbox"
    assert "mail-secret" not in tasks.rows[item.id].model_dump_json()
    assert "supplier-secret" not in item.model_dump_json()
    assert isinstance(await service.action(item.id, OnboardingAction(action="start")), Failure)
    await service.action(item.id, OnboardingAction(action="generate_password"))
    secrets = await service.reveal(item.id)
    assert isinstance(secrets, Success)
    assert secrets.value.mailbox_password == "mail-secret"
    assert secrets.value.supplier_password == "supplier-secret"
    assert secrets.value.proposed_password
    assert (await service.list())[0].state == "awaiting_mailbox"
    await service.action(item.id, OnboardingAction(action="mailbox_ready"))
    confirmed = await service.reveal(item.id)
    assert confirmed.value.mailbox_password == secrets.value.proposed_password
    assert confirmed.value.supplier_password == "supplier-secret"
    assert (await service.list())[0].state == "standby"
    assert not runtime.provisioned


async def test_target_counts_pending_and_restart_does_not_create_another_card(tmp_path):
    service, tasks, environments, runtime, _, environment_service = setup_service(tmp_path)
    request = oauth_request(prepare=False)
    second = request.entries[0].model_copy(update={"label": "other@example.com"})
    await service.submit(request.model_copy(update={"entries": (*request.entries, second)}))
    await tasks.save_target(OnboardingTarget(supplier="openai_codex", count=1, enabled=True))
    await service.run_once()
    items = await service.list()
    assert [item.state for item in items] == ["awaiting_authorization", "standby"]
    assert len(runtime.provisioned) == 1
    assert isinstance(await service.authorization(items[0].id), Success)
    restarted = OnboardingService(tasks, environments, environment_service, EnvironmentSecretDeriver("s" * 32))
    await restarted.run_once()
    assert len(runtime.provisioned) == 1
    target = (await restarted.targets())[0]
    assert target.ready == 0 and target.in_progress == 1 and target.shortage == 0


async def test_manual_disable_is_respected(tmp_path):
    service, tasks, environments, _, _, _ = setup_service(tmp_path)
    item = (await service.submit(oauth_request(prepare=False))).items[0]
    await service.action(item.id, OnboardingAction(action="start"))
    await service.run_once()
    record = await environments.get(item.card_id)
    await environments.save(record.model_copy(update={"enabled": False}))
    row = tasks.rows[item.id]
    tasks.rows[item.id] = row.model_copy(
        update={"item": row.item.model_copy(update={"updated_at": utc_now() - timedelta(minutes=1)})}
    )
    await service.run_once()
    assert (await service.list())[0].state == "disabled"
    assert (await environments.get(item.card_id)).enabled is False


async def test_expired_oauth_stops_for_manual_retry(tmp_path):
    service, tasks, environments, runtime, _, _ = setup_service(tmp_path)
    item = (await service.submit(oauth_request(prepare=False))).items[0]
    await service.action(item.id, OnboardingAction(action="start"))
    await service.run_once()
    record = await environments.get(item.card_id)
    await environments.save(record.model_copy(update={"oauth_expires_at": utc_now() - timedelta(minutes=1)}))
    row = tasks.rows[item.id]
    tasks.rows[item.id] = row.model_copy(
        update={"item": row.item.model_copy(update={"updated_at": utc_now() - timedelta(minutes=1)})}
    )
    await service.run_once()
    assert (await service.list())[0].state == "failed"
    assert isinstance(await service.authorization(item.id), Failure)
    assert len(runtime.provisioned) == 1


async def test_running_file_job_recovers_after_restart(tmp_path):
    service, tasks, environments, runtime, _, environment_service = setup_service(tmp_path)
    item = (await service.submit(file_request())).items[0]
    row = tasks.rows[item.id]
    tasks.rows[item.id] = row.model_copy(
        update={"item": row.item.model_copy(update={"state": "running", "attempts": 1})}
    )
    restarted = OnboardingService(tasks, environments, environment_service, EnvironmentSecretDeriver("s" * 32))
    await restarted.run_once()
    assert (await restarted.list())[0].state == "ready"
    assert len(runtime.provisioned) == 1


async def test_oauth_identity_mismatch_disables_wrong_account(tmp_path):
    service, tasks, environments, _, _, _ = setup_service(tmp_path)
    item = (await service.submit(oauth_request(prepare=False))).items[0]
    await service.action(item.id, OnboardingAction(action="start"))
    await service.run_once()
    record = await environments.get(item.card_id)
    ready = record.model_copy(
        update={
            "status": EnvironmentStatus.READY,
            "credential_email": "wrong@example.com",
            "auth_file_name": "codex.json",
            "oauth_state": None,
            "oauth_expires_at": None,
            "available_models": ("example-model",),
            "enabled_models": ("example-model",),
        }
    )
    await environments.save(ready)
    await service._complete(tasks.rows[item.id], ready)
    assert (await service.list())[0].state == "failed"
    assert (await environments.get(item.card_id)).enabled is False


async def test_file_creation_keeps_observed_cooldown_instead_of_forcing_ready(tmp_path):
    _, _, environments, _, _, service = setup_service(
        tmp_path, FakeCLIProxy(observed_status=EnvironmentStatus.COOLING_DOWN)
    )
    item_id = uuid4()
    result = await service.create_auth_file_environment(
        CreateEnvironmentRequest(name="import", supplier=SupplierKind.OPENAI_CODEX),
        item_id,
        "account.json",
        file_request().entries[0].content.encode(),
    )
    assert isinstance(result, Success)
    assert result.value.status == EnvironmentStatus.COOLING_DOWN
    assert (await environments.get(item_id)).oauth_state is None
