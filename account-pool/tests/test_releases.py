"""本文件验证确认协议、真实文件生命周期及切换失败恢复，不连接生产 Docker。"""

from __future__ import annotations

import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Final

import pytest
from account_pool.release_app import release_application, worker_lock
from account_pool.release_compatibility import RollbackEvidence, compare_evidence, configuration_checks
from account_pool.release_models import ReleaseAction, ReleaseJob, ReleasePair
from account_pool.release_runtime import ReleaseSettings, image_pair
from account_pool.release_service import ReleaseService
from account_pool.release_store import ReleaseError, ReleaseStore
from fastapi.testclient import TestClient
from pydantic import ValidationError


def pair(number: int) -> ReleasePair:
    return ReleasePair.model_validate(
        {
            "id": f"{number:024x}",
            "commit": f"{number:040x}",
            "images": tuple(
                {
                    "service": service,
                    "image_id": f"sha256:{number * 2 + index:064x}",
                    "repository": service,
                    "revision": f"{number:040x}",
                    "size": 50,
                }
                for index, service in enumerate(("litellm", "account-pool"))
            ),
        }
    )


OLD: Final = pair(1)
CURRENT: Final = pair(2)
NEW: Final = pair(3)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Runtime:
    def __init__(self) -> None:
        self.running = CURRENT
        self.events: tuple[str, ...] = ()
        self.fail_export = False
        self.fail_apply: str | None = None
        self.changed_schema: str | None = None
        self.configuration: str | None = None
        self.missing_feature: str | None = None
        self.missing_image: str | None = None

    def current(self) -> ReleasePair:
        return self.running

    def discover(self) -> tuple[ReleasePair, ...]:
        return (CURRENT, OLD)

    def compose(self) -> bytes:
        return json.dumps(
            {
                "services": {
                    "litellm": {
                        "command": ["--use_v2_migration_resolver"],
                        "entrypoint": ["docker/prod_entrypoint.sh"],
                    },
                    "account-pool": {},
                }
            }
        ).encode()

    def running_compose(self) -> bytes:
        return self.configuration.encode() if self.configuration else self.compose()

    def export(self, pair: ReleasePair, destination: Path) -> None:
        self.events += ("export:" + pair.id,)
        if self.fail_export:
            destination.write_bytes(b"partial")
            raise ReleaseError("导出失败")
        destination.write_bytes(gzip.compress(pair.model_dump_json().encode()))

    def fingerprint(self, pair: ReleasePair) -> str:
        return "changed" if self.changed_schema == pair.id else "identical-schema"

    def evidence(self, pair: ReleasePair) -> RollbackEvidence:
        if self.missing_image == pair.id:
            raise ReleaseError("镜像已清理")
        return RollbackEvidence(
            self.fingerprint(pair),
            "pool",
            "credentials",
            "logs",
            "startup",
            () if pair.id == self.missing_feature else ("完整日志查询",),
        )

    def load(self, pair: ReleasePair, archive: Path) -> None:
        assert ReleasePair.model_validate_json(gzip.decompress(archive.read_bytes())).id == pair.id
        self.events += ("load:" + pair.id,)
        self.missing_image = None

    def apply(self, pair: ReleasePair, configuration: bytes) -> None:
        assert configuration == self.running_compose()
        self.events += ("apply:" + pair.id,)
        self.running = pair
        if self.fail_apply == pair.id:
            raise ReleaseError("健康检查失败")

    def pull(self, tag: str) -> ReleasePair:
        self.events += ("pull:" + tag,)
        return NEW


@pytest.fixture
def setup(tmp_path: Path) -> tuple[ReleaseService, Runtime, Clock]:
    clock: Final = Clock()
    runtime: Final = Runtime()
    return ReleaseService(ReleaseStore(tmp_path, clock), runtime, 0), runtime, clock


def action(service: ReleaseService, name: str, **fields: str) -> ReleaseAction:
    return ReleaseAction.model_validate({"action": name, "revision": service.store.settings()[0], **fields})


def queued(service: ReleaseService, clock: Clock, request: ReleaseAction) -> ReleaseJob:
    prepared: Final = service.prepare(request, "admin")
    clock.now += prepared.delay_seconds
    return service.execute(prepared.token, "admin")


@pytest.mark.parametrize(("name", "delay"), (("apply", 5), ("delete", 10), ("note", 5), ("guide", 5)))
def test_confirmations_are_delayed_bound_and_single_use(
    setup: tuple[ReleaseService, Runtime, Clock], name: str, delay: int
) -> None:
    service, runtime, clock = setup
    service.backup(OLD, runtime.compose())
    request: Final = action(service, name, **({"version_id": OLD.id} if name != "guide" else {}))
    confirmation: Final = service.prepare(request, "admin")
    assert confirmation.delay_seconds == delay
    with pytest.raises(ReleaseError, match="倒计时"):
        service.execute(confirmation.token, "admin")
    clock.now += delay
    with pytest.raises(ReleaseError, match="失效"):
        service.execute(confirmation.token, "another-admin")
    job: Final = service.execute(confirmation.token, "admin")
    assert job.action == request
    with pytest.raises(ReleaseError, match="失效"):
        service.execute(confirmation.token, "admin")


@pytest.mark.parametrize("change", ("expiry", "revision", "current"))
def test_stale_confirmations_cannot_execute(setup: tuple[ReleaseService, Runtime, Clock], change: str) -> None:
    service, runtime, clock = setup
    confirmation: Final = service.prepare(action(service, "guide", text="draft"), "admin")
    clock.now += 301 if change == "expiry" else 5
    if change == "revision":
        service.store.edit(action(service, "guide", text="newer"))
    if change == "current":
        runtime.running = OLD
    with pytest.raises(ReleaseError, match="失效|变化"):
        service.execute(confirmation.token, "admin")


def test_concurrent_confirmations_allow_one_job(setup: tuple[ReleaseService, Runtime, Clock]) -> None:
    service, _, clock = setup
    tokens: Final = tuple(service.prepare(action(service, "guide"), "admin").token for _ in range(2))
    clock.now += 5

    def attempt(token: str) -> bool:
        try:
            service.execute(token, "admin")
            return True
        except ReleaseError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(attempt, tokens)) == 1


def test_apply_uses_archive_and_backs_up_current_before_replacing(setup: tuple[ReleaseService, Runtime, Clock]) -> None:
    service, runtime, clock = setup
    service.backup(OLD, runtime.compose())
    service.run(queued(service, clock, action(service, "apply", version_id=OLD.id)))
    assert runtime.running == OLD
    assert runtime.events == ("export:" + OLD.id, "load:" + OLD.id, "export:" + CURRENT.id, "apply:" + OLD.id)
    assert service.store.jobs()[0].status == "succeeded"
    saved: Final = service.store.backup(CURRENT.id)
    assert saved is not None
    assert service.verify(saved).joinpath("images.tar.gz").is_file()
    service.backup(CURRENT, runtime.compose())
    assert runtime.events.count("export:" + CURRENT.id) == 1


@pytest.mark.parametrize("failure", ("export", "space", "archive", "schema"))
def test_invalid_backups_and_incompatibility_never_replace_current(
    setup: tuple[ReleaseService, Runtime, Clock], failure: str
) -> None:
    service, runtime, clock = setup
    service.backup(OLD, runtime.compose())
    job: Final = queued(service, clock, action(service, "apply", version_id=OLD.id))
    if failure == "export":
        runtime.fail_export = True
    if failure == "archive":
        archive: Final = service.store.path(OLD.id) / "images.tar.gz"
        archive.write_bytes(b"x" * archive.stat().st_size)
    if failure == "schema":
        runtime.changed_schema = CURRENT.id
    runner: Final = ReleaseService(service.store, runtime, 10**30) if failure == "space" else service
    runner.run(job)
    assert runtime.running == CURRENT
    assert not any(event.startswith("apply:") for event in runtime.events)
    assert service.store.jobs()[0].status == "failed"
    assert not service.store.path(CURRENT.id).joinpath("images.pending.gz").exists()


def test_switch_health_failure_restores_previous_archive(setup: tuple[ReleaseService, Runtime, Clock]) -> None:
    service, runtime, clock = setup
    service.backup(OLD, runtime.compose())
    runtime.fail_apply = OLD.id
    service.run(queued(service, clock, action(service, "apply", version_id=OLD.id)))
    assert runtime.running == CURRENT
    assert runtime.events[-2:] == ("load:" + CURRENT.id, "apply:" + CURRENT.id)
    assert service.store.jobs()[0].status == "recovered"


@pytest.mark.parametrize("compatible", (True, False))
def test_new_deployment_backs_up_running_images_and_rejects_schema_changes(
    setup: tuple[ReleaseService, Runtime, Clock], compatible: bool
) -> None:
    service, runtime, clock = setup
    if not compatible:
        runtime.changed_schema = NEW.id
    service.run(queued(service, clock, action(service, "deploy", tag=NEW.commit)))
    assert service.store.backup(CURRENT.id) is not None
    assert runtime.events[:2] == ("pull:" + NEW.commit, "export:" + CURRENT.id)
    assert runtime.running == (NEW if compatible else CURRENT)
    assert service.store.jobs()[0].status == ("succeeded" if compatible else "failed")


def test_notes_and_guide_persist_without_reexporting_images(setup: tuple[ReleaseService, Runtime, Clock]) -> None:
    service, runtime, clock = setup
    service.run(queued(service, clock, action(service, "note", version_id=CURRENT.id, text="可回退基线")))
    service.run(queued(service, clock, action(service, "guide", text="自己的恢复步骤")))
    service.backup(CURRENT, runtime.compose())
    refreshed: Final = ReleaseService(ReleaseStore(service.store.root, clock), runtime, 0).view()
    assert refreshed.guide == "自己的恢复步骤"
    assert refreshed.versions[0].note == "可回退基线"
    service.backup(CURRENT, b'{"services":{"litellm":{"changed":true},"account-pool":{}}}')
    assert runtime.events.count("export:" + CURRENT.id) == 1
    assert service.store.path(CURRENT.id).joinpath("compose.json").read_bytes().find(b"changed") > 0


def test_rescan_refreshes_compatibility_fingerprint_without_reexporting(
    setup: tuple[ReleaseService, Runtime, Clock],
) -> None:
    service, runtime, _ = setup
    original: Final = service.backup(OLD, runtime.compose())
    runtime.changed_schema = OLD.id
    refreshed: Final = service.backup(OLD, b"unrelated running configuration", imported=True)
    assert refreshed.schema_fingerprint == "changed"
    assert refreshed.compose_sha256 == original.compose_sha256
    assert service.store.path(OLD.id).joinpath("compose.json").read_bytes() == runtime.compose()
    assert runtime.events.count("export:" + OLD.id) == 1


def test_restart_recovers_persisted_operation(setup: tuple[ReleaseService, Runtime, Clock]) -> None:
    service, runtime, clock = setup
    service.backup(CURRENT, runtime.compose())
    job: Final = queued(service, clock, action(service, "deploy", tag=NEW.commit))
    service.store.save_job(
        job.model_copy(update={"status": "running", "phase": "替换服务并检查健康", "recovery_id": CURRENT.id})
    )
    runtime.running = NEW
    restarted: Final = ReleaseService(ReleaseStore(service.store.root, clock), runtime, 0)
    restarted.recover_interrupted()
    assert runtime.running == CURRENT
    assert restarted.store.jobs()[0].status == "recovered"


def test_manual_recovery_cannot_reuse_an_old_failure_after_later_operations(
    setup: tuple[ReleaseService, Runtime, Clock],
) -> None:
    service, runtime, clock = setup
    service.backup(CURRENT, runtime.compose())
    failure: Final = queued(service, clock, action(service, "deploy", tag=NEW.commit))
    service.store.save_job(
        failure.model_copy(update={"status": "failed", "phase": "需要人工恢复", "recovery_id": CURRENT.id})
    )
    assert service.prepare(action(service, "recover"), "admin").delay_seconds == 5
    service.run(queued(service, clock, action(service, "guide", text="updated")))
    with pytest.raises(ReleaseError, match="没有需要人工恢复"):
        service.prepare(action(service, "recover"), "admin")


def test_delete_removes_files_and_protects_current(setup: tuple[ReleaseService, Runtime, Clock]) -> None:
    service, runtime, clock = setup
    service.backup(OLD, runtime.compose())
    service.backup(CURRENT, runtime.compose())
    with pytest.raises(ReleaseError, match="当前运行"):
        service.prepare(action(service, "delete", version_id=CURRENT.id), "admin")
    service.run(queued(service, clock, action(service, "delete", version_id=OLD.id)))
    assert not service.store.path(OLD.id).exists()
    assert service.store.backup(OLD.id) is None
    assert service.store.path(CURRENT.id).is_dir()
    assert runtime.running == CURRENT


def test_queue_rechecks_current_and_commands_use_selected_commit(setup: tuple[ReleaseService, Runtime, Clock]) -> None:
    service, runtime, clock = setup
    service.backup(OLD, runtime.compose())
    job: Final = queued(service, clock, action(service, "delete", version_id=OLD.id))
    runtime.running = NEW
    service.run(job)
    assert service.store.backup(OLD.id) is not None
    assert service.store.jobs()[0].status == "failed"
    commands: Final = service.commands(OLD.id)
    assert f"git switch -c codex/fix-{OLD.commit[:10]} {OLD.commit}" in commands.branch
    assert f"git revert --no-commit {OLD.commit}..HEAD" in commands.revert
    assert "--merges" in commands.revert and "git status --porcelain" in commands.branch


def test_worker_auth_and_lock(setup: tuple[ReleaseService, Runtime, Clock]) -> None:
    service, _, _ = setup
    with worker_lock(service.store.root), pytest.raises(OSError), worker_lock(service.store.root):
        pass
    with TestClient(release_application(service, "t" * 32)) as client:
        assert client.get("/api/releases").status_code == 401
        assert client.get("/health").status_code == 200
        response: Final = client.get("/api/releases", headers={"Authorization": "Bearer " + "t" * 32})
        assert response.status_code == 200
        assert "compose.json" not in response.text


def test_validation_rejects_paths_docker_socket_and_mixed_commits(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ReleaseStore(tmp_path).path("../other")
    with pytest.raises(ValidationError):
        ReleaseSettings.model_validate({"token": "t" * 32, "docker_host": "tcp://attacker:2375"})
    with pytest.raises(ReleaseError, match="不一致"):
        image_pair((OLD.images[0], CURRENT.images[1]))


def test_inspection_shows_evidence_impacts_and_only_verified_alternatives(
    setup: tuple[ReleaseService, Runtime, Clock],
) -> None:
    service, runtime, _ = setup
    service.backup(OLD, runtime.compose())
    service.backup(NEW, runtime.compose())
    runtime.changed_schema = OLD.id
    runtime.missing_feature = OLD.id
    confirmation: Final = service.prepare(action(service, "apply", version_id=OLD.id), "admin")
    assert confirmation.token == ""
    assert confirmation.rollback is not None
    assert confirmation.rollback.status == "unverified"
    assert confirmation.rollback.alternatives[0].version_id == NEW.id
    assert any("完整日志查询" in item for item in confirmation.rollback.impacts)
    assert not any(event.startswith(("load:", "apply:")) for event in runtime.events)
    assert service.store.jobs() == ()


def test_unchecked_ticket_and_post_confirmation_configuration_change_cannot_apply(
    setup: tuple[ReleaseService, Runtime, Clock],
) -> None:
    service, runtime, clock = setup
    service.backup(OLD, runtime.compose())
    unchecked: Final = service.store.prepare(
        action(service, "apply", version_id=OLD.id), "admin", CURRENT.id, CURRENT.commit
    )
    clock.now += 5
    with pytest.raises(ReleaseError, match="兼容性检查"):
        service.execute(unchecked.token, "admin")
    job: Final = queued(service, clock, action(service, "apply", version_id=OLD.id))
    runtime.configuration = '{"services":{"litellm":{},"account-pool":{"environment":{"SECRET":"do-not-display"}}}}'
    service.run(job)
    assert runtime.running == CURRENT
    assert not any(event.startswith("apply:") for event in runtime.events)
    assert "do-not-display" not in service.store.jobs()[0].model_dump_json()


@pytest.mark.parametrize("command", ([], ["--use_v2_migration_resolver", "--use_prisma_db_push"]))
def test_unsafe_startup_is_blocked_without_returning_configuration(command: list[str]) -> None:
    config: Final = json.dumps(
        {"services": {"litellm": {"command": command, "environment": {"SECRET": "do-not-display"}}, "account-pool": {}}}
    ).encode()
    checks: Final = configuration_checks(config, config)
    assert next(item for item in checks if item.key == "migration").status == "blocked"
    assert "do-not-display" not in str(checks)


def test_missing_evidence_is_never_reported_as_compatible() -> None:
    empty: Final = RollbackEvidence("", "", "", "", "", ())
    assert all(check.status == "unverified" for check in compare_evidence(empty, empty))


def test_old_backup_fingerprint_is_not_an_execution_gate(setup) -> None:
    service, runtime, clock = setup
    original = service.backup(OLD, runtime.compose())
    legacy = original.model_copy(update={"schema_fingerprint": "old-path-dependent-fingerprint"})
    service.store.save_backup(legacy)
    service.run(queued(service, clock, action(service, "apply", version_id=OLD.id)))
    assert runtime.running == OLD
    assert service.store.jobs()[0].status == "succeeded"
    assert service.store.backup(OLD.id) == legacy


def test_inspection_imports_missing_image_from_verified_backup_without_starting_it(setup) -> None:
    service, runtime, _ = setup
    service.backup(OLD, runtime.compose())
    runtime.missing_image = OLD.id
    confirmation = service.prepare(action(service, "apply", version_id=OLD.id), "admin")
    assert confirmation.rollback.status == "compatible"
    assert runtime.running == CURRENT
    assert runtime.events == ("export:" + OLD.id, "load:" + OLD.id)


@pytest.mark.parametrize("fail_start", [False, True])
def test_forced_rollback_requires_explicit_bound_confirmation_and_preserves_current_configuration(
    setup, fail_start
) -> None:
    service, runtime, clock = setup
    service.backup(OLD, runtime.compose())
    runtime.changed_schema = OLD.id
    runtime.configuration = json.dumps(
        {
            "services": {
                "litellm": {
                    "command": ["--use_v2_migration_resolver"],
                    "entrypoint": ["docker/prod_entrypoint.sh"],
                    "environment": {"DATABASE_URL": "current-private-value"},
                },
                "account-pool": {},
            }
        }
    )
    ordinary = service.prepare(action(service, "apply", version_id=OLD.id), "admin")
    assert ordinary.token == "" and ordinary.rollback.force_allowed
    with pytest.raises(ReleaseError, match="确认版本"):
        service.prepare(action(service, "apply", version_id=OLD.id, force=True), "admin")
    forced = action(service, "apply", version_id=OLD.id, force=True, force_acknowledgement=OLD.commit)
    confirmation = service.prepare(forced, "admin")
    assert confirmation.delay_seconds == 10 and confirmation.token
    assert "current-private-value" not in confirmation.model_dump_json()
    with pytest.raises(ReleaseError, match="倒计时"):
        service.execute(confirmation.token, "admin")
    clock.now += 10
    runtime.fail_apply = OLD.id if fail_start else None
    service.run(service.execute(confirmation.token, "admin"))
    assert runtime.running == (CURRENT if fail_start else OLD)
    assert service.store.jobs()[0].status == ("recovered" if fail_start else "succeeded")
    assert service.store.backup(CURRENT.id) is not None


@pytest.mark.parametrize("failure", ["archive", "startup", "configuration_after_confirm"])
def test_forced_rollback_never_bypasses_integrity_startup_or_stale_state(setup, failure) -> None:
    service, runtime, clock = setup
    service.backup(OLD, runtime.compose())
    runtime.changed_schema = OLD.id
    request = action(service, "apply", version_id=OLD.id, force=True, force_acknowledgement=OLD.commit)
    if failure == "archive":
        service.store.path(OLD.id).joinpath("images.tar.gz").write_bytes(b"broken")
    elif failure == "startup":
        runtime.configuration = (
            runtime.compose().decode().replace("--use_v2_migration_resolver", "--use_prisma_db_push")
        )
    if failure != "configuration_after_confirm":
        with pytest.raises(ReleaseError):
            service.prepare(request, "admin")
    else:
        job = queued(service, clock, request)
        runtime.configuration = (
            runtime.compose()
            .decode()
            .replace('"account-pool": {}', '"account-pool": {"environment": {"NEW": "value"}}')
        )
        service.run(job)
        assert service.store.jobs()[0].status == "failed"
    assert runtime.running == CURRENT
    assert not any(event.startswith("apply:") for event in runtime.events)
