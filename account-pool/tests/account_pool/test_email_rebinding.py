"""验证邮箱换绑的确认、防重复分配和故障恢复，不操作真实账号。"""

import asyncio
import importlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_account_pool import EmptyProfiles, FakeCLIProxy, MemoryRepository, _record, _settings


def test_email_rebinding_contract_requires_explicit_confirmation() -> None:
    module = importlib.import_module("account_pool.email_rebinding.models")
    assert module.ConfirmBinding.model_fields["confirmed"].is_required()
    assert "password" not in module.MailboxView.model_fields
    assert "refresh_token" not in module.MailboxView.model_fields
    for value in (False, 1, "true", None):
        with pytest.raises(ValidationError):
            module.ConfirmBinding(version=0, confirmed=value)


class MemoryBindingStore:
    def __init__(self):
        from account_pool.email_rebinding.models import BindingState

        self.state = BindingState()
        self.lock = asyncio.Lock()
        self.requests = False

    async def read(self):
        return self.state

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            owner = self

            @dataclass
            class Transaction:
                snapshot: object

                async def save(self, state):
                    owner.state = state.model_copy(update={"version": self.snapshot.version + 1})

            yield Transaction(self.state)

    @asynccontextmanager
    async def worker_lock(self):
        yield True

    async def has_requests(self, environment_id):
        return self.requests


@pytest.fixture
def bindings():
    from account_pool.credential_ownership import CredentialOwnership
    from account_pool.domain import EnvironmentStatus
    from account_pool.email_rebinding.service import BindingService
    from account_pool.oauth_automation.models import AutomationSettings, AutomationState, ProfileView, StoredProfile
    from account_pool.oauth_automation.service import AutomationRegistry
    from account_pool.shared.secrets import EnvironmentSecretDeriver, StateCipher

    secrets = EnvironmentSecretDeriver("s" * 32)
    card = _record(status=EnvironmentStatus.READY).model_copy(
        update={
            "credential_email": "old@example.test",
            "credential_account_id": "same-upstream-account",
            "auth_file_name": "codex.json",
            "proxy_profile_id": "proxy-one",
        }
    )
    store = MemoryBindingStore()
    profiles = MemoryBindingStore()

    registry = AutomationRegistry(
        profiles,
        MemoryRepository(card),
        SimpleNamespace(image="fixture-image"),
        secrets,
        CredentialOwnership(),
        EmptyProfiles(),
    )
    profile = StoredProfile(
        item=ProfileView(id=uuid4(), name="same-profile", digest="a" * 64, bound_environment_id=card.id),
        data={"locale": "ja-JP"},
        account_digest=registry.identity(card.credential_email),
        login_ciphertext=StateCipher(secrets).seal(card.id, "old@example.test----site-password----JBSWY3DPEHPK3PXP"),
    )
    profiles.state = AutomationState(
        settings=AutomationSettings(enabled=True), profiles=(profile,), checked_image="fixture-image"
    )
    return BindingService(store, registry), card, profiles


@pytest.mark.parametrize(
    "text",
    [
        "new@outlook.com----mail-password----11111111-2222-3333-4444-555555555555----mail-refresh-token",
        "email,password,client_id,refresh_token\nnew@outlook.com,mail-password,client-id,mail-refresh-token",
        "邮箱,密码,client_id,refresh_token\nnew@outlook.com,mail-password,client-id,mail-refresh-token",
    ],
)
def test_csv_and_four_part_txt_parse_without_printing_secrets(text):
    from account_pool.email_rebinding.imports import parse_mailboxes

    rows = parse_mailboxes(text)
    assert rows[0].secret.email == "new@outlook.com"
    assert rows[0].secret.method == "microsoft"
    assert "mail-password" not in repr(rows)
    assert "mail-refresh-token" not in repr(rows)


def test_other_mailboxes_import_without_assuming_universal_oauth():
    from account_pool.email_rebinding.imports import parse_mailboxes

    assert parse_mailboxes("new@gmail.com----app-password")[0].secret.method == "imap"
    assert parse_mailboxes("new@custom.example----password----client----refresh")[0].secret.method == "manual"
    with pytest.raises(ValueError, match="重复"):
        parse_mailboxes("new@gmail.com----p\nnew@gmail.com----p")


@pytest.mark.asyncio
async def test_upload_is_encrypted_idempotent_and_does_not_start_binding(bindings):
    from account_pool.email_rebinding.models import PoolUpload
    from account_pool.shared.result import Success

    service, card, _ = bindings
    request = PoolUpload(content="new@outlook.com----mail-password----client----mail-refresh-token")
    first = await service.upload(request)
    second = await service.upload(request)
    assert isinstance(first, Success) and first.value.added == 1
    assert isinstance(second, Success) and second.value.existing == 1
    assert (await service.view()).jobs == ()
    assert await service.automation.environments.get(card.id) == card
    serialized = (await service.repository.read()).model_dump_json()
    assert "mail-password" not in serialized and "mail-refresh-token" not in serialized
    assert "mail-password" not in (await service.view()).model_dump_json()
    assert "mail-password" not in await service.export()


@pytest.mark.asyncio
async def test_explicit_confirmation_and_cancel_preserve_original_card(bindings, tmp_path):
    from account_pool.email_rebinding.models import ConfirmBinding, PoolUpload, ProposeBinding
    from account_pool.shared.result import Success

    service, card, _ = bindings
    await service.upload(PoolUpload(content="new@gmail.com----app-password"))
    proposal = await service.propose(ProposeBinding(id=uuid4(), environment_id=card.id))
    assert isinstance(proposal, Success)
    assert proposal.value.stage == "awaiting_confirmation"
    assert await service.automation.environments.get(card.id) == card
    confirmed = await service.confirm(proposal.value.id, ConfirmBinding(version=proposal.value.version, confirmed=True))
    repeated = await service.confirm(proposal.value.id, ConfirmBinding(version=proposal.value.version, confirmed=True))
    assert isinstance(confirmed, Success) and isinstance(repeated, Success)
    assert confirmed.value == repeated.value and confirmed.value.stage == "draining"
    held = await service.automation.environments.get(card.id)
    assert held.id == card.id and held.email_rebinding_job_id == proposal.value.id
    assert held.oauth_automation_operation.startswith("email-rebind:")
    from account_pool.channels.cliproxyapi.channel import CLIProxyAPIChannel

    channel = CLIProxyAPIChannel(_settings(tmp_path), service.automation.secrets, client=FakeCLIProxy())
    assert channel.gateway(card).routable
    assert not channel.gateway(held).routable
    assert held.credential_account_id == card.credential_account_id
    cancelled = await service.cancel(proposal.value.id)
    assert isinstance(cancelled, Success)
    restored = await service.automation.environments.get(card.id)
    assert restored.id == card.id and restored.auth_file_name == card.auth_file_name
    assert restored.oauth_automation_operation is None and restored.email_rebinding_job_id is None
    assert channel.gateway(restored).routable
    assert (await service.view()).mailboxes[0].status == "available"


@pytest.mark.asyncio
async def test_reserved_and_bound_addresses_never_reallocated_after_reupload(bindings):
    from account_pool.email_rebinding.models import PoolUpload, ProposeBinding
    from account_pool.shared.result import Success

    service, card, _ = bindings
    request = PoolUpload(content="email,password,binding_status\nused@gmail.com,app,bound\nnew@gmail.com,app,available")
    await service.upload(request)
    proposal = await service.propose(ProposeBinding(id=uuid4(), environment_id=card.id))
    assert isinstance(proposal, Success) and proposal.value.target_email == "new@gmail.com"
    await service.upload(PoolUpload(content="used@gmail.com----changed\nnew@gmail.com----changed"))
    assert [m.status for m in (await service.view()).mailboxes] == ["bound", "reserved"]
    duplicate = await service.propose(ProposeBinding(id=uuid4(), environment_id=card.id))
    assert isinstance(duplicate, Success) and duplicate.value.id == proposal.value.id


@pytest.mark.asyncio
async def test_email_changed_stays_bound_even_when_reauthorization_fails(bindings):
    from account_pool.email_rebinding.models import ConfirmBinding, PoolUpload, ProposeBinding
    from account_pool.shared.result import Failure

    service, card, _ = bindings
    await service.upload(PoolUpload(content="new@gmail.com----app-password"))
    proposal = (await service.propose(ProposeBinding(id=uuid4(), environment_id=card.id))).value
    await service.confirm(proposal.id, ConfirmBinding(version=proposal.version, confirmed=True))
    row = (await service.repository.read()).jobs[0]
    row = await service.save(
        row.model_copy(update={"item": row.item.model_copy(update={"irreversible_started": True})})
    )
    bound = await service.mark_bound(row)
    await service.save(
        bound.model_copy(update={"item": bound.item.model_copy(update={"stage": "verification_failed"})})
    )
    assert isinstance(await service.cancel(proposal.id), Failure)
    await service.upload(PoolUpload(content="new@gmail.com----other-password"))
    view = await service.view()
    assert view.mailboxes[0].status == "bound" and view.jobs[0].email_changed
    assert view.jobs[0].stage == "verification_failed"
    assert (await service.automation.environments.get(card.id)).id == card.id


async def confirmed_row(service, card):
    from account_pool.email_rebinding.models import ConfirmBinding, PoolUpload, ProposeBinding

    await service.upload(PoolUpload(content="new@outlook.com----mail-password----client----old-mail-rt"))
    proposed = (await service.propose(ProposeBinding(id=uuid4(), environment_id=card.id))).value
    await service.confirm(proposed.id, ConfirmBinding(version=proposed.version, confirmed=True))
    return (await service.repository.read()).jobs[-1]


class BindingBrowser:
    def __init__(self):
        self.starts = 0
        self.cleaned = []
        self.sessions = self

    async def start_rebinding(self, environment_id, operation_id, automation, reserved):
        from account_pool.shared.result import Success

        self.starts += 1
        grant = SimpleNamespace(session=SimpleNamespace(id=uuid4()), ticket="fixture-ticket")
        await reserved(grant)
        return Success(grant)

    async def fail(self, session_id):
        self.cleaned.append(session_id)

    async def get(self, session_id):
        return SimpleNamespace(cleanup_pending=False)


class BindingWorker:
    def __init__(self, service, image_ok=True):
        self.service = service
        self.image_ok = image_ok
        self.commands = []
        self.step = "login"
        self.subject = "web-user-1"

    async def confirm_binding_script(self, *args):
        return self.image_ok

    async def binding_command(self, session_id, token, action, payload):
        from account_pool.domain import utc_now
        from account_pool.email_rebinding.models import RebindingWorkerView

        row = (await self.service.repository.read()).jobs[-1]
        if action == "start":
            assert row.item.irreversible_started
            assert payload["credentials"].startswith("old@example.test----site-password")
            assert "mail-password" not in payload["credentials"]
        if action == "commit":
            assert self.service.cipher.open(row.item.id, row.subject_ciphertext) == self.subject
        self.commands.append((action, payload))
        return RebindingWorkerView(
            status="callback_received" if self.step == "changed" else "running",
            expires_at=utc_now(),
            step=self.step,
            subject=self.subject,
            source_email="old@example.test",
            target_email="new@outlook.com",
        )


def binding_runner(service, tmp_path, worker=None):
    from account_pool.email_rebinding.runner import EmailBindingRunner
    from account_pool.oauth_automation.login_scripts import LoginScriptStore

    browser = BindingBrowser()
    active = worker or BindingWorker(service)
    oauth = SimpleNamespace(browser=browser, worker=active, record_diagnostic=None)
    reader = SimpleNamespace()
    return (
        EmailBindingRunner(service, oauth, reader, LoginScriptStore(tmp_path / "script.json", "key")),
        browser,
        active,
    )


@pytest.mark.asyncio
async def test_drain_and_protocol_attestation_precede_sending_credentials(bindings, tmp_path):
    service, card, _ = bindings
    row = await confirmed_row(service, card)
    worker = BindingWorker(service, image_ok=False)
    runner, browser, _ = binding_runner(service, tmp_path, worker)
    service.repository.requests = True
    await runner.run_once()
    assert browser.starts == 0
    service.repository.requests = False
    await runner.run_once()
    failed = (await service.view()).jobs[-1]
    assert failed.stage == "failed" and not failed.irreversible_started
    assert worker.commands == [] and len(browser.cleaned) == 1
    assert (await service.view()).mailboxes[0].status == "reserved"
    await runner.action(row.item.id, "cancel")
    assert (await service.view()).mailboxes[0].status == "available"


@pytest.mark.asyncio
async def test_checkpoint_before_external_commit_and_same_card_oauth_gates_completion(bindings, tmp_path):
    from account_pool.domain import EnvironmentStatus

    service, card, profiles = bindings
    await confirmed_row(service, card)
    runner, browser, worker = binding_runner(service, tmp_path)
    await runner.run_once()
    worker.step = "awaiting_commit"
    await runner.run_once()
    assert [a for a, _ in worker.commands] == ["start", "status", "commit"]
    worker.step = "changed"
    await runner.run_once()
    view = await service.view()
    assert view.mailboxes[0].status == "bound" and view.jobs[-1].email_changed
    assert view.jobs[-1].stage == "reauthorizing" and browser.starts == 1
    linked = profiles.state.jobs[-1]
    assert linked.item.environment_id == card.id
    credentials = service.cipher.open(card.id, linked.credential_ciphertext)
    assert credentials == "new@outlook.com----site-password----JBSWY3DPEHPK3PXP"
    profile = profiles.state.profiles[0]
    assert profile.item.id == linked.item.profile_id and profile.data == {"locale": "ja-JP"}
    current = await service.automation.environments.get(card.id)
    assert current.credential_account_id == card.credential_account_id
    assert current.oauth_verification_pending and current.status is EnvironmentStatus.ERROR
    profiles.state = profiles.state.model_copy(
        update={
            "jobs": (
                linked.model_copy(
                    update={
                        "item": linked.item.model_copy(
                            update={
                                "stage": "completed",
                                "credentials_saved": True,
                                "chat_verified": True,
                                "responses_verified": False,
                            }
                        )
                    }
                ),
            )
        }
    )
    await runner.run_once()
    assert (await service.view()).jobs[-1].stage != "completed"
    verified = profiles.state.jobs[-1]
    profiles.state = profiles.state.model_copy(
        update={
            "jobs": (
                verified.model_copy(update={"item": verified.item.model_copy(update={"responses_verified": True})}),
            )
        }
    )
    await runner.run_once()
    assert (await service.view()).jobs[-1].stage == "completed"
    assert (await service.automation.environments.get(card.id)).email_rebinding_job_id is None
    assert len(service.automation.environments.records) == 1
    assert browser.starts == 1 and sum(a == "start" for a, _ in worker.commands) == 1


@pytest.mark.asyncio
async def test_changed_status_without_durable_original_identity_is_never_accepted(bindings, tmp_path):
    service, card, _ = bindings
    await confirmed_row(service, card)
    runner, _, worker = binding_runner(service, tmp_path)
    await runner.run_once()
    worker.step = "changed"
    await runner.run_once()
    assert (await service.view()).jobs[-1].stage == "uncertain"
    assert not (await service.view()).jobs[-1].email_changed
    assert (await service.view()).mailboxes[0].status == "reserved"
    await runner.run_once()
    assert sum(a == "start" for a, _ in worker.commands) == 1


@pytest.mark.asyncio
async def test_reconcile_after_lost_worker_is_read_only_and_does_not_release_mailbox(bindings, tmp_path):
    from account_pool.shared.result import Success

    service, card, _ = bindings
    await confirmed_row(service, card)
    runner, browser, worker = binding_runner(service, tmp_path)
    await runner.run_once()
    worker.step = "awaiting_commit"
    await runner.run_once()
    row = (await service.repository.read()).jobs[-1]
    await service.save(row.model_copy(update={"item": row.item.model_copy(update={"stage": "uncertain"})}))
    result = await runner.action(row.item.id, "reconcile")
    assert isinstance(result, Success)
    await runner.run_once()
    start = [payload for action, payload in worker.commands if action == "start"][-1]
    assert start["target"]["verify_only"] is True
    assert start["target"]["subject"] == "web-user-1"
    assert (await service.view()).mailboxes[0].status == "reserved" and browser.starts == 2


@pytest.mark.asyncio
async def test_reauth_failure_retry_never_resubmits_email_change(bindings, tmp_path):
    from account_pool.shared.result import Success

    service, card, profiles = bindings
    await confirmed_row(service, card)
    runner, browser, worker = binding_runner(service, tmp_path)
    await runner.run_once()
    worker.step = "awaiting_commit"
    await runner.run_once()
    worker.step = "changed"
    await runner.run_once()
    linked = profiles.state.jobs[-1]
    profiles.state = profiles.state.model_copy(
        update={"jobs": (linked.model_copy(update={"item": linked.item.model_copy(update={"stage": "failed"})}),)}
    )
    await runner.run_once()
    assert (await service.view()).jobs[-1].stage == "verification_failed"
    retries = []

    async def retry(job_id, action):
        retries.append((job_id, action))
        return Success(linked.item)

    runner.oauth.action = retry
    row = (await service.view()).jobs[-1]
    assert isinstance(await runner.action(row.id, "retry"), Success)
    assert retries == [(linked.item.id, "retry")]
    assert browser.starts == 1 and (await service.view()).mailboxes[0].status == "bound"


@pytest.mark.asyncio
async def test_restart_before_credentials_safely_releases_unstarted_browser_only(bindings, tmp_path):
    service, card, _ = bindings
    row = await confirmed_row(service, card)
    await service.save(
        row.model_copy(
            update={"item": row.item.model_copy(update={"stage": "starting", "browser_session_id": uuid4()})}
        )
    )
    runner, browser, worker = binding_runner(service, tmp_path)
    await runner.run_once()
    assert len(browser.cleaned) == 1 and worker.commands == []
    assert (await service.view()).jobs[-1].stage == "draining"
    await runner.run_once()
    assert browser.starts == 1


@pytest.mark.asyncio
async def test_invalid_import_does_not_leak_secret_in_validation_error(bindings):
    from account_pool.email_rebinding.models import PoolUpload
    from account_pool.shared.result import Failure

    service, _, _ = bindings
    result = await service.upload(PoolUpload(content="new@gmail.com----" + "private-mail-password" * 300))
    assert isinstance(result, Failure)
    assert "private-mail-password" not in result.message
    assert (await service.view()).mailboxes == ()
