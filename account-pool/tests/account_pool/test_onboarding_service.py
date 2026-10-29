"""验证上号隔离、去重、重试、密码保护及等待授权的真实状态。"""

import json
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
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
    FakeCLIProxy,
    FakeRuntime,
    MemoryRepository,
    StaticProfiles,
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
        StaticProfiles("http://onboarding-proxy.example:8080"),
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
        proxy_profile_id="onboarding-proxy",
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


def sub2api_fixture():
    return {
        "name": "fixture@example.test",
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": "fixture-access",
            "refresh_token": "fixture-refresh",
            "chatgpt_account_id": "fixture-account",
            "email": "fixture@example.test",
            "plan_type": "plus",
        },
        "extra": {"last_refresh": "2026-10-28T04:20:05.764Z"},
        "concurrency": 10,
        "priority": 1,
    }


@pytest.mark.parametrize("wrapped", [False, True])
async def test_sub2api_batch_normalizes_tokens_before_ownership_and_upload(tmp_path, wrapped):
    service, _, _, _, cli, _ = setup_service(tmp_path)
    account = sub2api_fixture()
    content = json.dumps({"accounts": [account], "proxies": []} if wrapped else account)
    request = file_request().model_copy(update={"entries": (OnboardingEntry(label="sub2.json", content=content),)})
    result = await service.submit(request)
    assert result.preview[0].status == "valid"
    await service.run_once()
    assert (await service.list())[0].state == "ready"
    uploaded = json.loads(cli.upload_calls[0][2])
    assert uploaded["type"] == "codex"
    assert uploaded["refresh_token"] == "fixture-refresh"
    assert uploaded["account_id"] == "fixture-account"
    assert uploaded["plan_type"] == "plus"
    assert uploaded["last_refresh"] == "2026-10-28T04:20:05.764Z"
    assert "credentials" not in uploaded and "concurrency" not in uploaded
    cpa = request.model_copy(
        update={"job_id": uuid4(), "entries": (OnboardingEntry(label="same.json", content=json.dumps(uploaded)),)}
    )
    assert (await service.preview(cpa))[0].status == "duplicate"


async def test_sub2api_single_upload_uses_the_same_normalization(tmp_path):
    _, _, environments, _, cli, environment_service = setup_service(tmp_path)
    record = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
    await environments.save(record)
    result = await environment_service.upload_auth_file(
        record.id, "sub2.json", json.dumps(sub2api_fixture()).encode(), "application/json"
    )
    assert isinstance(result, Success)
    assert json.loads(cli.upload_calls[0][2])["account_id"] == "fixture-account"


async def test_two_factor_text_creates_encrypted_oauth_tasks_without_fake_auth_files(tmp_path):
    service, tasks, _, _, cli, _ = setup_service(tmp_path)
    content = "first@example.test----fixture-password----JBSWY3DPEHPK3PXP\r\nsecond@example.test----other-password----JBSWY3DPEHPK3PXP"
    request = file_request().model_copy(
        update={
            "proxy_profile_id": "onboarding-proxy",
            "entries": (OnboardingEntry(label="accounts.txt", content=content),),
        }
    )
    result = await service.submit(request)
    assert len(result.items) == 2
    assert all(item.source == "oauth" and item.state == "queued" for item in result.items)
    assert "fixture-password" not in result.model_dump_json()
    assert "JBSWY3DPEHPK3PXP" not in tasks.rows[result.items[0].id].model_dump_json()
    revealed = await service.reveal(result.items[0].id)
    assert revealed.value.mailbox_password == ""
    assert revealed.value.supplier_password == "fixture-password"
    assert revealed.value.totp_secret == "JBSWY3DPEHPK3PXP"
    await service.run_once()
    assert (await service.list())[0].state == "awaiting_authorization"
    assert not cli.upload_calls


@pytest.mark.parametrize(
    "content",
    [
        "fixture@example.test----password----bad!secret",
        "fixture@example.test----password----JBSWY3DPEHPK3PXP----extra",
    ],
)
async def test_invalid_two_factor_text_never_echoes_secret_or_creates_tasks(tmp_path, content):
    service, _, _, runtime, _, _ = setup_service(tmp_path)
    request = file_request().model_copy(
        update={
            "proxy_profile_id": "onboarding-proxy",
            "entries": (OnboardingEntry(label="accounts.txt", content=content),),
        }
    )
    result = await service.submit(request)
    assert result.preview[0].status == "invalid"
    assert not result.items and not runtime.provisioned
    assert "password" not in result.model_dump_json() and "bad!secret" not in result.model_dump_json()


async def test_two_factor_import_requires_proxy_and_rejects_wrong_provider(tmp_path):
    service, _, _, _, _, _ = setup_service(tmp_path)
    request = file_request().model_copy(
        update={
            "entries": (
                OnboardingEntry(label="accounts.txt", content="fixture@example.test----password----JBSWY3DPEHPK3PXP"),
            )
        }
    )
    assert (await service.preview(request))[0].status == "invalid"
    wrong = request.model_copy(update={"proxy_profile_id": "onboarding-proxy", "supplier": "anthropic_claude"})
    assert (await service.preview(wrong))[0].status == "invalid"


async def test_sub2api_multi_account_package_expands_and_enforces_batch_limit(tmp_path):
    service, _, _, _, _, _ = setup_service(tmp_path)
    accounts = [{**sub2api_fixture(), "credentials": {"refresh_token": f"fixture-{index}"}} for index in range(2)]
    request = file_request().model_copy(
        update={
            "entries": (
                OnboardingEntry(label="export.json", content=json.dumps({"accounts": accounts, "proxies": []})),
            )
        }
    )
    result = await service.submit(request)
    assert len(result.items) == 2 and result.items[0].card_id != result.items[1].card_id
    oversized = request.model_copy(
        update={
            "entries": tuple(
                OnboardingEntry(label=f"batch-{index}.json", content=json.dumps({"accounts": accounts}))
                for index in range(51)
            )
        }
    )
    assert (await service.preview(oversized))[0].status == "invalid"


async def test_two_factor_pending_card_does_not_use_credentials_before_authorization_completes(tmp_path):
    from account_pool.domain import ProxyMode

    service, _, environments, _, cli, _ = setup_service(tmp_path)
    record = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None).model_copy(
        update={"proxy_mode": ProxyMode.PROFILE, "proxy_profile_id": "onboarding-proxy"}
    )
    await environments.save(record)
    result = await service.import_login_file(record.id, "fixture@example.test----password----JBSWY3DPEHPK3PXP")
    assert isinstance(result, Success)
    assert result.value.status is EnvironmentStatus.AWAITING_AUTHORIZATION
    assert not cli.authorization_status_calls


async def test_two_factor_empty_card_guard_preserves_a_concurrently_uploaded_credential(tmp_path):
    _, _, environments, _, cli, environment_service = setup_service(tmp_path)
    record = (await environments.list())[0]
    result = await environment_service.authorize_environment(record.id, require_empty=True)
    assert isinstance(result, Failure)
    assert (await environments.get(record.id)).auth_file_name == record.auth_file_name
    assert not cli.events


def test_sub2api_normalization_preserves_real_tokens_and_derives_access_expiration():
    import base64

    from account_pool.credential_import import normalize_auth_file

    claims = base64.urlsafe_b64encode(b'{"exp":1794018581}').decode().rstrip("=")
    account = sub2api_fixture()
    account["credentials"].update({"access_token": f"header.{claims}.signature", "id_token": "real-fixture-id"})
    normalized = json.loads(normalize_auth_file(json.dumps(account).encode(), "openai_codex"))
    assert normalized["expired"] == "2026-11-07T02:29:41+00:00"
    assert normalized["id_token"] == "real-fixture-id"
    assert normalized["access_token"] == f"header.{claims}.signature"


@pytest.mark.parametrize(
    "changes", [{"platform": "anthropic"}, {"type": "api_key"}, {"credentials": {"access_token": "access-only"}}]
)
async def test_unsupported_sub2api_does_not_replace_existing_file(tmp_path, changes):
    _, _, environments, _, cli, environment_service = setup_service(tmp_path)
    record = (await environments.list())[0]
    result = await environment_service.upload_auth_file(
        record.id, "sub2.json", json.dumps({**sub2api_fixture(), **changes}).encode(), "application/json", replace=True
    )
    assert isinstance(result, Failure)
    assert (await environments.get(record.id)).auth_file_name == record.auth_file_name
    assert not cli.upload_calls


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


async def test_oauth_onboarding_uses_selected_proxy_for_card(tmp_path):
    service, _, environments, _, cli, _ = setup_service(tmp_path)
    request = OnboardingImport.model_validate(
        {**oauth_request(prepare=False).model_dump(), "proxy_profile_id": "onboarding-proxy"}
    )
    item = (await service.submit(request)).items[0]
    await service.action(item.id, OnboardingAction(action="start"))

    await service.run_once()

    record = await environments.get(item.card_id)
    assert record is not None
    assert record.proxy_profile_id == "onboarding-proxy"
    assert record.desired_configuration is not None
    assert record.desired_configuration.proxy_url == "http://onboarding-proxy.example:8080"
    assert cli.events[:2] == ["proxy:http://onboarding-proxy.example:8080", "oauth"]


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


async def test_file_import_selected_proxy_overrides_embedded_proxy_without_changing_credentials(tmp_path):
    service, _, environments, _, cli, _ = setup_service(tmp_path)
    original = file_request()
    request = original.model_copy(
        update={
            "proxy_profile_id": "onboarding-proxy",
            "entries": (
                original.entries[0].model_copy(
                    update={
                        "content": json.dumps(
                            {
                                "type": "codex",
                                "email": "test@example.com",
                                "refresh_token": "private-refresh",
                                "proxy_url": "http://embedded-user:embedded-secret@embedded.example:8080",
                                "priority": 17,
                            }
                        )
                    }
                ),
            ),
        }
    )
    item = (await service.submit(request)).items[0]

    await service.run_once()

    record = await environments.get(item.card_id)
    assert record.proxy_profile_id == "onboarding-proxy"
    assert record.desired_configuration.proxy_url == "http://onboarding-proxy.example:8080"
    assert json.loads(cli.upload_calls[0][2]) == {
        "type": "codex",
        "email": "test@example.com",
        "refresh_token": "private-refresh",
        "proxy_url": "http://onboarding-proxy.example:8080",
        "priority": 17,
    }
    assert "embedded-secret" not in (await service.list())[0].model_dump_json()
    assert not cli.submit_calls and not cli.authorization_status_calls


async def test_completion_time_survives_later_observations(tmp_path):
    service, tasks, environments, _, _, _ = setup_service(tmp_path)
    item = (await service.submit(file_request())).items[0]
    await service.run_once()
    row = tasks.rows[item.id]
    assert row.item.completed_at is not None
    completed = utc_now() - timedelta(hours=1)
    tasks.rows[item.id] = row.model_copy(
        update={"item": row.item.model_copy(update={"completed_at": completed, "updated_at": completed})}
    )

    await service._complete(tasks.rows[item.id], await environments.get(item.card_id))

    assert tasks.rows[item.id].item.completed_at == completed
    assert tasks.rows[item.id].item.updated_at > completed


async def test_cleanup_hides_completed_history_and_preserves_cards_and_fingerprints(tmp_path):
    from account_pool.onboarding_models import OnboardingCleanup

    service, tasks, environments, runtime, cli, environment_service = setup_service(tmp_path)
    request = file_request()
    item = (await service.submit(request)).items[0]
    await service.run_once()
    card_before = await environments.get(item.card_id)
    fingerprints = tasks.rows[item.id].fingerprints

    result = await service.cleanup(OnboardingCleanup(source="auth_file"))

    assert isinstance(result, Success) and result.value.cleared == 1
    assert await service.list() == ()
    assert (await environments.get(item.card_id)) == card_before
    assert tasks.rows[item.id].fingerprints == fingerprints
    assert tasks.rows[item.id].ciphertext == ""
    assert (await service.preview(request))[0].status == "duplicate"
    assert (await service.submit(request)).items == ()
    assert isinstance(await service.action(item.id, OnboardingAction(action="retry")), Failure)
    restarted = OnboardingService(tasks, environments, environment_service, EnvironmentSecretDeriver("s" * 32))
    assert await restarted.list() == ()
    assert (await restarted.preview(request))[0].status == "duplicate"
    again = await restarted.cleanup(OnboardingCleanup())
    assert isinstance(again, Success) and again.value.cleared == 0
    assert len(runtime.provisioned) == len(cli.upload_calls) == 1


@pytest.mark.parametrize("state", ("ready", "cooling_down", "disabled"))
async def test_cleanup_uses_completion_cutoff_and_source_not_last_poll_time(tmp_path, state):
    from account_pool.onboarding_models import OnboardingCleanup

    service, tasks, _, _, _, _ = setup_service(tmp_path)
    item = (await service.submit(file_request())).items[0]
    await service.run_once()
    cutoff = utc_now() - timedelta(hours=1)
    row = tasks.rows[item.id]
    tasks.rows[item.id] = row.model_copy(
        update={
            "item": row.item.model_copy(
                update={"state": state, "completed_at": cutoff - timedelta(seconds=1), "updated_at": utc_now()}
            )
        }
    )
    wrong_source = await service.cleanup(OnboardingCleanup(source="oauth", completed_before=cutoff))
    assert isinstance(wrong_source, Success) and wrong_source.value.cleared == 0
    too_early = await service.cleanup(OnboardingCleanup(completed_before=cutoff - timedelta(seconds=1)))
    assert isinstance(too_early, Success) and too_early.value.cleared == 0
    result = await service.cleanup(OnboardingCleanup(source="auth_file", completed_before=cutoff))
    assert isinstance(result, Success) and result.value.cleared == 1


@pytest.mark.parametrize(
    "state", ("awaiting_mailbox", "standby", "queued", "running", "awaiting_authorization", "failed")
)
async def test_cleanup_preserves_unfinished_or_failed_tasks(tmp_path, state):
    from account_pool.onboarding_models import OnboardingCleanup

    service, tasks, _, _, _, _ = setup_service(tmp_path)
    item = (await service.submit(oauth_request())).items[0]
    row = tasks.rows[item.id]
    tasks.rows[item.id] = row.model_copy(update={"item": row.item.model_copy(update={"state": state})})
    result = await service.cleanup(OnboardingCleanup())
    assert isinstance(result, Success) and result.value.cleared == 0
    assert len(await service.list()) == 1
    assert tasks.rows[item.id].ciphertext == row.ciphertext


async def test_cleanup_preserves_explicitly_paused_completed_task(tmp_path):
    from account_pool.onboarding_models import OnboardingCleanup

    service, _, _, _, _, _ = setup_service(tmp_path)
    item = (await service.submit(file_request())).items[0]
    await service.run_once()
    await service.action(item.id, OnboardingAction(action="pause"))

    result = await service.cleanup(OnboardingCleanup())

    assert isinstance(result, Success) and result.value.cleared == 0
    assert (await service.list())[0].state == "disabled"


async def test_cleanup_keeps_oauth_target_accounting_after_restart(tmp_path):
    from account_pool.onboarding_models import OnboardingCleanup

    service, tasks, environments, runtime, _, environment_service = setup_service(tmp_path)
    item = (await service.submit(oauth_request(prepare=False))).items[0]
    await service.action(item.id, OnboardingAction(action="start"))
    await service.run_once()
    record = (await environments.get(item.card_id)).model_copy(
        update={
            "status": EnvironmentStatus.READY,
            "auth_file_name": "account.json",
            "oauth_state": None,
            "oauth_expires_at": None,
            "available_models": ("example-model",),
            "enabled_models": ("example-model",),
        }
    )
    await environments.save(record)
    await service._complete(tasks.rows[item.id], record)
    await tasks.save_target(OnboardingTarget(supplier="openai_codex", enabled=True, count=1))
    ciphertext = tasks.rows[item.id].ciphertext
    result = await service.cleanup(OnboardingCleanup(source="oauth"))
    assert isinstance(result, Success) and result.value.cleared == 1
    assert tasks.rows[item.id].ciphertext == ciphertext

    restarted = OnboardingService(tasks, environments, environment_service, EnvironmentSecretDeriver("s" * 32))

    assert await restarted.list() == ()
    target = (await restarted.targets())[0]
    assert target.ready == 1 and target.shortage == 0
    assert isinstance(await restarted.reveal(item.id), Failure)
    await restarted.submit(
        oauth_request(prepare=False).model_copy(
            update={"entries": (oauth_request().entries[0].model_copy(update={"label": "standby@example.com"}),)}
        )
    )
    await restarted.run_once()
    assert len(runtime.provisioned) == 1
    assert (await restarted.list())[0].state == "standby"


async def test_cleanup_metadata_round_trip_and_legacy_pause_compatibility(tmp_path):
    from account_pool.onboarding_models import OnboardingCleanup
    from account_pool.onboarding_repository import PostgresOnboardingRepository, persisted_item

    service, tasks, _, _, _, _ = setup_service(tmp_path)
    item = (await service.submit(file_request())).items[0]
    await service.run_once()
    await service.cleanup(OnboardingCleanup())
    row = tasks.rows[item.id]
    stored = {
        "payload": persisted_item(row).model_dump(mode="json"),
        "ciphertext": row.ciphertext,
        "fingerprints": list(row.fingerprints),
    }

    decoded = PostgresOnboardingRepository._decode(stored)

    assert decoded.history_cleared and decoded.ciphertext == ""
    assert decoded.fingerprints == row.fingerprints
    assert decoded.item.completed_at == row.item.completed_at
    assert "history_cleared" not in decoded.item.model_dump()
    legacy = PostgresOnboardingRepository._decode(
        {**stored, "payload": {**row.item.model_dump(mode="json"), "state": "disabled"}}
    )
    assert legacy.paused
    assert not legacy.history_cleared
