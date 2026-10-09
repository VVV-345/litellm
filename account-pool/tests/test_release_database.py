"""本文件验证数据库快照发布、显式恢复确认和跨两库失败补偿，不连接生产环境。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from account_pool.release_database import DockerReleaseDatabase
from account_pool.release_models import DatabaseDump, DatabaseSnapshot
from account_pool.release_runtime import DockerReleaseRuntime, ReleaseSettings
from account_pool.release_service import ReleaseService
from account_pool.release_store import ReleaseError, ReleaseStore
from test_releases import CURRENT, NEW, OLD, Clock, Runtime, action, queued


def test_missing_database_exec_permission_never_stops_business_containers(tmp_path):
    calls = []

    def command(*args, timeout=120):
        calls.append(args)
        if args[0] == "ps":
            return b"a" * 64
        if args[0] == "inspect":
            return json.dumps(
                [
                    {"Image": "sha256:" + "a" * 64, "Config": {"Env": ["DATABASE_URL=postgresql://test@db/test"]}},
                    {
                        "Image": "sha256:" + "b" * 64,
                        "Config": {"Env": ["ACCOUNT_POOL_DATABASE_URL=postgresql://test@account-pool-db/test"]},
                    },
                ]
            ).encode()
        if args[0] == "exec":
            raise ReleaseError("EXEC denied")
        return b""

    runtime = DockerReleaseRuntime(
        ReleaseSettings.model_validate({"token": "t" * 32, "root": tmp_path, "deployment": tmp_path.parent / "deploy"}),
        command,
    )
    with pytest.raises(ReleaseError, match="EXEC denied"):
        DockerReleaseDatabase(runtime).stop()
    assert not any(call[0] == "stop" for call in calls)


class Database:
    def __init__(self) -> None:
        self.data = b"current-data"
        self.events: list[str] = []
        self.fail_capture = False
        self.fail_restore: str | None = None
        self.fail_start = False

    def preflight(self) -> None:
        return None

    def stop(self) -> None:
        self.events.append("stop")

    def start(self) -> None:
        self.events.append("start")
        if self.fail_start:
            raise ReleaseError("start failed")

    def capture(self, directory: Path, version_id: str) -> DatabaseSnapshot:
        self.events.append("capture")
        if self.fail_capture:
            raise ReleaseError("capture failed")
        identifier = uuid4().hex
        files = []
        for service in ("db", "account-pool-db"):
            name = f"database-{identifier}-{service}.dump"
            (directory / name).write_bytes(self.data)
            files.append(
                DatabaseDump(
                    service=service,
                    database="test",
                    major=16,
                    filename=name,
                    sha256=hashlib.sha256(self.data).hexdigest(),
                    size=len(self.data),
                )
            )
        return DatabaseSnapshot(id=identifier, created_at=1000, version_id=version_id, files=tuple(files))

    def verify(self, directory: Path, snapshot: DatabaseSnapshot) -> None:
        for item in snapshot.files:
            if hashlib.sha256((directory / item.filename).read_bytes()).hexdigest() != item.sha256:
                raise ReleaseError("invalid snapshot")

    def restore(self, directory: Path, snapshot: DatabaseSnapshot) -> None:
        self.verify(directory, snapshot)
        self.events.append("restore:" + snapshot.id)
        self.data = (directory / snapshot.files[0].filename).read_bytes()
        if self.fail_restore == snapshot.id:
            raise ReleaseError("second database failed")


@pytest.fixture
def setup_data(tmp_path):
    runtime, clock, database = Runtime(), Clock(), Database()
    service = ReleaseService(ReleaseStore(tmp_path, clock), runtime, 0, database)
    return service, runtime, clock, database


def save_old(service, runtime, database):
    database.data = b"old-data"
    runtime.running = OLD
    saved = service.capture_database(service.backup(OLD, runtime.compose()))
    runtime.running = CURRENT
    database.data = b"new-data"
    return saved.database_snapshot


def restore_job(service, clock, snapshot):
    prepared = service.prepare(action(service, "restore_data", version_id=OLD.id, snapshot_id=snapshot.id), "admin")
    clock.now += prepared.delay_seconds
    return service.execute(prepared.token, "admin", f"RESTORE {snapshot.id}")


@pytest.mark.parametrize("operation", ("scan", "deploy"))
def test_preflight_failure_is_retryable_without_stopping_or_recovering_business(tmp_path, operation):
    class UnavailableDatabase(Database):
        def preflight(self):
            raise ReleaseError("EXEC denied")

    runtime, clock, database = Runtime(), Clock(), UnavailableDatabase()
    service = ReleaseService(ReleaseStore(tmp_path, clock), runtime, 0, database)
    request = action(service, operation, **({"tag": NEW.commit[:10]} if operation == "deploy" else {}))
    service.run(queued(service, clock, request))
    assert service.store.jobs()[0].status == "failed"
    assert service.store.jobs()[0].phase != "需要人工恢复"
    assert database.events == []
    assert not any(event.startswith("apply:") for event in runtime.events)
    assert service.prepare(action(service, "scan"), "admin").token


def test_scan_refreshes_data_even_when_image_archive_exists(setup_data):
    service, runtime, clock, database = setup_data
    service.run(queued(service, clock, action(service, "scan")))
    first = service.store.backup(CURRENT.id).database_snapshot
    database.data = b"next-data"
    service.run(queued(service, clock, action(service, "scan")))
    second = service.store.backup(CURRENT.id).database_snapshot
    assert first.id != second.id
    assert runtime.events.count("export:" + CURRENT.id) == 1
    assert not any((service.store.path(CURRENT.id) / item.filename).exists() for item in first.files)
    assert all((service.store.path(CURRENT.id) / item.filename).read_bytes() == b"next-data" for item in second.files)
    assert service.store.backup(OLD.id).database_snapshot is None
    assert database.events == ["stop", "capture", "start"] * 2


def test_audited_oauth_schema_upgrade_requires_a_fresh_verified_backup(setup_data):
    service, runtime, clock, database = setup_data
    previous = service.capture_database(service.backup(CURRENT, runtime.compose())).database_snapshot
    database.data = b"latest-data"
    runtime.changed_schema = NEW.id
    runtime.oauth_schema_upgrade = True
    service.run(queued(service, clock, action(service, "deploy", tag=NEW.commit)))
    saved = service.store.backup(CURRENT.id).database_snapshot
    assert saved.id != previous.id
    assert (service.store.path(CURRENT.id) / saved.files[0].filename).read_bytes() == b"latest-data"
    assert service.store.jobs()[0].status == "succeeded"
    assert service.store.jobs()[0].database_recovery is None
    assert runtime.running == NEW
    assert database.events[-2:] == ["stop", "capture"]


@pytest.mark.parametrize("backups", (False, True))
def test_unknown_schema_or_missing_backups_still_block_deployment(setup_data, backups):
    service, runtime, clock, database = setup_data
    selected = service if backups else ReleaseService(service.store, runtime, 0)
    runtime.changed_schema = NEW.id
    runtime.oauth_schema_upgrade = not backups
    selected.run(queued(selected, clock, action(selected, "deploy", tag=NEW.commit)))
    assert selected.store.jobs()[0].status == "failed"
    assert runtime.running == CURRENT
    assert not database.events
    assert not any(event.startswith("apply:") for event in runtime.events)


def test_failed_oauth_upgrade_backup_does_not_start_new_images(setup_data):
    service, runtime, clock, database = setup_data
    runtime.changed_schema = NEW.id
    runtime.oauth_schema_upgrade = True
    database.fail_capture = True
    service.run(queued(service, clock, action(service, "deploy", tag=NEW.commit)))
    assert service.store.jobs()[0].status == "failed"
    assert runtime.running == CURRENT
    assert database.events == ["stop", "capture", "stop", "start"]
    assert not any(event.startswith("apply:") for event in runtime.events)


def test_failed_oauth_upgrade_restores_images_without_rewinding_database(tmp_path):
    database = Database()

    class MigratingRuntime(Runtime):
        def apply(self, pair, configuration):
            if pair == NEW:
                database.data = b"new-table-and-current-data"
            super().apply(pair, configuration)

    runtime, clock = MigratingRuntime(), Clock()
    service = ReleaseService(ReleaseStore(tmp_path, clock), runtime, 0, database)
    runtime.changed_schema = NEW.id
    runtime.oauth_schema_upgrade = True
    runtime.fail_apply = NEW.id
    service.run(queued(service, clock, action(service, "deploy", tag=NEW.commit)))
    assert service.store.jobs()[0].status == "recovered"
    assert service.store.jobs()[0].database_recovery is None
    assert runtime.running == CURRENT
    assert database.data == b"new-table-and-current-data"
    assert not any(event.startswith("restore:") for event in database.events)


def test_failed_snapshot_preserves_previous_generation_and_restarts_apps(setup_data):
    service, _, clock, database = setup_data
    service.run(queued(service, clock, action(service, "scan")))
    original = service.store.backup(CURRENT.id)
    database.fail_capture = True
    service.run(queued(service, clock, action(service, "scan")))
    assert service.store.backup(CURRENT.id) == original
    assert service.store.jobs()[0].status == "failed"
    assert database.events[-1] == "start"
    assert not any(event.startswith("restore:") for event in database.events)


@pytest.mark.parametrize("ack", ("", "CONFIRM", "RESTORE wrong"))
def test_database_restore_requires_snapshot_specific_acknowledgement(setup_data, ack):
    service, runtime, clock, database = setup_data
    snapshot = save_old(service, runtime, database)
    prepared = service.prepare(action(service, "restore_data", version_id=OLD.id, snapshot_id=snapshot.id), "admin")
    assert prepared.database_snapshot == snapshot
    assert prepared.delay_seconds == 10
    clock.now += 10
    with pytest.raises(ReleaseError, match="RESTORE"):
        service.execute(prepared.token, "admin", ack)
    assert database.data == b"new-data"


def test_data_restore_accepts_schema_difference_but_keeps_preoperation_data(setup_data):
    service, runtime, clock, database = setup_data
    snapshot = save_old(service, runtime, database)
    runtime.changed_schema = OLD.id
    service.run(restore_job(service, clock, snapshot))
    assert runtime.running == OLD
    assert database.data == b"old-data"
    job = service.store.jobs()[0]
    assert job.status == "succeeded"
    assert job.database_recovery is not None
    assert (service.store.path(CURRENT.id) / job.database_recovery.files[0].filename).read_bytes() == b"new-data"
    assert database.events[-3:] == ["stop", "capture", "restore:" + snapshot.id]


@pytest.mark.parametrize("failure", ("restore", "health"))
def test_partial_restore_or_health_failure_recovers_both_data_and_images(setup_data, failure):
    service, runtime, clock, database = setup_data
    snapshot = save_old(service, runtime, database)
    if failure == "restore":
        database.fail_restore = snapshot.id
    else:
        runtime.fail_apply = OLD.id
    service.run(restore_job(service, clock, snapshot))
    assert service.store.jobs()[0].status == "recovered"
    assert runtime.running == CURRENT
    assert database.data == b"new-data"


def test_ordinary_rollback_does_not_restore_old_data(setup_data):
    service, runtime, clock, database = setup_data
    save_old(service, runtime, database)
    service.run(queued(service, clock, action(service, "apply", version_id=OLD.id)))
    assert runtime.running == OLD
    assert database.data == b"new-data"
    assert not any(event.startswith("restore:") for event in database.events)


def test_missing_or_imported_snapshot_cannot_be_restored(setup_data):
    service, runtime, _, database = setup_data
    service.backup(OLD, runtime.compose(), imported=True)
    with pytest.raises(ReleaseError, match="没有对应快照"):
        service.prepare(action(service, "restore_data", version_id=OLD.id, snapshot_id="a" * 32), "admin")
    with pytest.raises(ReleaseError, match="不能给历史镜像"):
        service.capture_database(service.store.backup(OLD.id))


def test_changed_configuration_or_snapshot_after_confirmation_blocks_restore(setup_data):
    service, runtime, clock, database = setup_data
    snapshot = save_old(service, runtime, database)
    job = restore_job(service, clock, snapshot)
    save_old(service, runtime, database)
    service.run(job)
    assert service.store.jobs()[0].status == "failed"
    assert runtime.running == CURRENT
    assert not any(event.startswith("restore:") for event in database.events)


def test_interrupted_database_replacement_restores_frozen_recovery_snapshot(setup_data):
    service, runtime, clock, database = setup_data
    snapshot = save_old(service, runtime, database)
    job = restore_job(service, clock, snapshot)
    recovery = service.capture_database(service.backup(CURRENT, runtime.compose())).database_snapshot
    service.store.save_job(
        job.model_copy(
            update={
                "status": "running",
                "phase": "恢复数据库",
                "recovery_id": CURRENT.id,
                "database_recovery": recovery,
            }
        )
    )
    database.data = b"partially-restored"
    service.recover_interrupted()
    assert database.data == b"new-data"
    assert runtime.running == CURRENT
    assert service.store.jobs()[0].status == "recovered"


def test_interrupted_backup_does_not_restore_database(setup_data):
    service, _, clock, database = setup_data
    job = queued(service, clock, action(service, "scan"))
    service.store.save_job(job.model_copy(update={"status": "running", "phase": "暂停业务并备份数据库"}))
    service.recover_interrupted()
    assert database.events == ["stop", "start"]


def test_interrupted_oauth_upgrade_restores_images_without_rewinding_database(setup_data):
    service, runtime, clock, database = setup_data
    service.capture_database(service.backup(CURRENT, runtime.compose()))
    job = queued(service, clock, action(service, "deploy", tag=NEW.commit))
    service.store.save_job(
        job.model_copy(update={"status": "running", "phase": "替换服务并检查健康", "recovery_id": CURRENT.id})
    )
    runtime.running = NEW
    database.data = b"new-table-and-current-data"
    service.recover_interrupted()
    assert runtime.running == CURRENT
    assert database.data == b"new-table-and-current-data"
    assert not any(event.startswith("restore:") for event in database.events)
    assert service.store.jobs()[0].database_recovery is None
    assert service.store.jobs()[0].status == "recovered"


def test_new_schema_deployment_still_requires_migration_review(setup_data):
    service, runtime, clock, database = setup_data
    runtime.changed_schema = NEW.id
    service.run(queued(service, clock, action(service, "deploy", tag=NEW.commit)))
    assert runtime.running == CURRENT
    assert service.store.jobs()[0].status == "failed"
    assert database.events == []


def test_pending_database_recovery_cannot_be_hidden_by_new_jobs(setup_data):
    service, runtime, clock, database = setup_data
    snapshot = save_old(service, runtime, database)
    job = restore_job(service, clock, snapshot)
    service.store.save_job(
        job.model_copy(update={"status": "failed", "phase": "需要人工恢复", "recovery_id": CURRENT.id})
    )
    for name in ("scan", "guide", "deploy"):
        with pytest.raises(ReleaseError, match="recover"):
            service.prepare(action(service, name), "admin")


def test_target_image_can_be_loaded_from_archive_for_data_restore(setup_data):
    service, runtime, clock, database = setup_data
    snapshot = save_old(service, runtime, database)
    runtime.missing_image = OLD.id
    service.run(restore_job(service, clock, snapshot))
    assert service.store.jobs()[0].status == "succeeded"
    assert runtime.running == OLD


def test_image_only_worker_keeps_null_snapshot_fields_out_of_legacy_payloads(tmp_path):
    from account_pool.release_app import release_application
    from fastapi.testclient import TestClient

    runtime = Runtime()
    service = ReleaseService(ReleaseStore(tmp_path), runtime, 0)
    service.backup(CURRENT, runtime.compose())
    client = TestClient(release_application(service, "x" * 32))
    response = client.get("/api/releases", headers={"Authorization": "Bearer " + "x" * 32})
    assert response.status_code == 200
    assert "database_snapshot" not in response.json()["versions"][0]["backup"]


def test_failed_recovery_snapshot_is_retained_during_a_refresh(setup_data):
    service, runtime, clock, database = setup_data
    saved = service.capture_database(service.backup(CURRENT, runtime.compose()))
    first = saved.database_snapshot
    job = queued(service, clock, action(service, "scan"))
    service.store.save_job(
        job.model_copy(
            update={"status": "failed", "phase": "需要人工恢复", "recovery_id": CURRENT.id, "database_recovery": first}
        )
    )
    database.data = b"latest"
    service.capture_database(saved)
    assert all((service.store.path(CURRENT.id) / item.filename).exists() for item in first.files)
