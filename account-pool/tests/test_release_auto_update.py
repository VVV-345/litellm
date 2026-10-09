"""验证自动更新授权和持久化，不访问 GHCR 或生产 Docker。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from account_pool.release_app import release_application
from account_pool.release_auto_update import AutoUpdater
from account_pool.release_models import AutoUpdateSettings, ReleaseCandidate, RemoteReleaseImage
from account_pool.release_service import ReleaseService
from account_pool.release_store import ReleaseDownloadError, ReleaseError, ReleaseStore
from fastapi.testclient import TestClient
from test_release_database import Database
from test_releases import CURRENT, NEW, OLD, Clock, Runtime, action, queued


def candidate(pair):
    return ReleaseCandidate(
        commit=pair.commit,
        images=tuple(
            RemoteReleaseImage(service=image.service, digest=f"sha256:{index + 8:064x}", image_id=image.image_id)
            for index, image in enumerate(pair.images)
        ),
    )


class Source:
    def __init__(self, value):
        self.value = value
        self.calls = 0
        self.fail = False
        self.callback = None

    def latest(self, current):
        self.calls += 1
        if self.callback:
            self.callback()
        if self.fail:
            raise ReleaseError("检查网络失败")
        return self.value


class AutoRuntime(Runtime):
    download_error = False

    def pull_candidate(self, selected):
        assert selected == candidate(NEW)
        self.events += ("pull-pinned",)
        if self.download_error:
            raise ReleaseDownloadError("镜像下载失败，旧服务保持不变")
        return NEW


def configured(tmp_path, selected=None):
    clock = Clock()
    runtime = AutoRuntime()
    service = ReleaseService(ReleaseStore(tmp_path, clock), runtime, 0, Database())
    source = Source(candidate(NEW) if selected is None else selected)
    updater = AutoUpdater(service, source)
    updater.configure(AutoUpdateSettings(enabled=True, revision=0, acknowledge_downtime=True), "admin")
    return updater, service, runtime, source, clock


def test_same_remote_image_skips_download_and_waits_next_interval(tmp_path):
    updater, service, runtime, source, clock = configured(tmp_path, candidate(CURRENT))
    updater.tick()
    assert source.calls == 1 and not runtime.events and not service.store.jobs()
    assert updater.view().status == "current"
    updater.tick()
    assert source.calls == 1
    clock.now += 300
    updater.tick()
    assert source.calls == 2


def test_new_image_is_pinned_and_queued_once_then_deployed_through_backup(tmp_path):
    updater, service, runtime, source, _ = configured(tmp_path)
    updater.tick()
    job = service.store.jobs()[0]
    assert job.action.candidate == candidate(NEW)
    updater.tick()
    assert len(service.store.jobs()) == 1 and source.calls == 1
    service.run(job)
    updater.tick()
    assert runtime.running == NEW
    assert runtime.events == ("pull-pinned", "export:" + CURRENT.id, "apply:" + NEW.id)
    assert service.database.events == ["stop", "capture"]
    assert updater.view().enabled and updater.view().status == "waiting"


def test_disable_during_registry_lookup_prevents_queueing(tmp_path):
    updater, service, runtime, source, _ = configured(tmp_path)
    source.callback = lambda: updater.configure(AutoUpdateSettings(enabled=False, revision=1), "admin")
    updater.tick()
    assert not service.store.jobs() and not runtime.events
    assert updater.view().status == "disabled"


def test_manual_and_automatic_queue_claims_are_mutually_exclusive(tmp_path):
    _, service, _, _, clock = configured(tmp_path)
    checked = service.store.claim_auto_check()
    confirmation = service.prepare(action(service, "deploy", tag=NEW.commit), "admin")
    clock.now += confirmation.delay_seconds
    barrier = Barrier(2)

    def enqueue(automatic):
        store = ReleaseStore(tmp_path, clock)
        barrier.wait(timeout=5)
        if automatic:
            return store.enqueue_auto_update(checked, candidate(NEW), CURRENT.id)
        try:
            return store.consume(confirmation.token, "admin", CURRENT.id)
        except ReleaseError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(enqueue, (True, False)))
    assert sum(job is not None for job in results) == 1
    assert len(service.store.jobs()) == 1


def test_disable_cancels_queued_auto_job_before_it_stops_service(tmp_path):
    updater, service, runtime, _, _ = configured(tmp_path)
    updater.tick()
    updater.configure(AutoUpdateSettings(enabled=False, revision=1), "admin")
    service.run(service.store.jobs()[0])
    assert not runtime.events and runtime.running == CURRENT
    assert service.store.jobs()[0].status == "interrupted"


@pytest.mark.parametrize("failure", ["schema", "health"])
def test_failed_deploy_pauses_across_worker_restart(tmp_path, failure):
    updater, service, runtime, source, clock = configured(tmp_path)
    if failure == "schema":
        runtime.changed_schema = NEW.id
    else:
        runtime.fail_apply = NEW.id
    updater.tick()
    service.run(service.store.jobs()[0])
    restarted = AutoUpdater(ReleaseService(ReleaseStore(tmp_path, clock), runtime, 0, service.database), source)
    restarted.tick()
    assert not restarted.view().enabled and restarted.view().status == "paused"
    assert runtime.running == CURRENT
    clock.now += 10000
    restarted.tick()
    assert source.calls == 1


def test_manual_rollback_pauses_auto_update_in_same_queue_transaction(tmp_path):
    updater, service, runtime, source, clock = configured(tmp_path)
    service.backup(OLD, runtime.compose())
    queued(service, clock, action(service, "apply", version_id=OLD.id))
    assert updater.view().status == "paused" and not updater.view().enabled
    updater.tick()
    assert source.calls == 0


@pytest.mark.parametrize("failure", ["schema", "health"])
def test_manual_deploy_failure_prevents_auto_retry_of_failed_version(tmp_path, failure):
    updater, service, runtime, source, clock = configured(tmp_path)
    if failure == "schema":
        runtime.changed_schema = NEW.id
    else:
        runtime.fail_apply = NEW.id
    job = queued(service, clock, action(service, "deploy", tag=NEW.commit))
    service.run(job)
    assert service.store.jobs()[0].status in ("failed", "recovered")
    assert runtime.running == CURRENT
    updater.tick()
    assert not updater.view().enabled and updater.view().status == "paused"
    assert source.calls == 0 and len(service.store.jobs()) == 1


def test_download_failure_waits_next_check_without_stopping_service(tmp_path):
    updater, service, runtime, source, clock = configured(tmp_path)
    runtime.download_error = True
    updater.tick()
    service.run(service.store.jobs()[0])
    updater.tick()
    assert service.store.jobs()[0].retryable
    assert updater.view().enabled and updater.view().status == "error"
    assert runtime.events == ("pull-pinned",) and not service.database.events
    assert runtime.running == CURRENT
    updater.tick()
    assert source.calls == 1
    clock.now += 300
    runtime.download_error = False
    updater.tick()
    service.run(service.store.jobs()[0])
    assert runtime.running == NEW


def test_disabled_check_only_detects_and_network_errors_retry_without_stopping(tmp_path):
    updater, service, runtime, source, clock = configured(tmp_path)
    updater.configure(AutoUpdateSettings(enabled=False, revision=1), "admin")
    updater.check()
    updater.tick()
    assert updater.view().status == "available" and not service.store.jobs()
    assert not runtime.events
    with pytest.raises(ReleaseError, match="一分钟"):
        updater.check()
    updater.configure(AutoUpdateSettings(enabled=True, revision=2, acknowledge_downtime=True), "admin")
    source.fail = True
    updater.tick()
    assert updater.view().status == "error" and updater.view().enabled
    assert not service.store.jobs() and not runtime.events
    clock.now += 300
    source.fail = False
    updater.tick()
    assert len(service.store.jobs()) == 1


def test_restart_releases_interrupted_manual_check(tmp_path):
    updater, service, _, _, clock = configured(tmp_path)
    updater.configure(AutoUpdateSettings(enabled=False, revision=1), "admin")
    updater.check()
    assert service.store.claim_auto_check().status == "checking"
    service.store.recover_auto_check()
    assert updater.view().status == "error"
    clock.now += 60
    updater.check()
    updater.tick()
    assert updater.view().status == "available"


def test_queued_job_rechecks_database_backup_requirement_after_restart(tmp_path):
    updater, service, runtime, _, _ = configured(tmp_path)
    updater.tick()
    restarted = ReleaseService(service.store, runtime, 0)
    restarted.run(service.store.jobs()[0])
    assert not runtime.events
    assert service.store.jobs()[0].status == "failed"


def test_auto_update_defaults_and_authorized_settings_persist(tmp_path):
    service = ReleaseService(ReleaseStore(tmp_path, Clock()), Runtime(), 0, Database())
    client = TestClient(release_application(service, "s" * 32))
    path = "/api/releases/auto-update"
    assert client.get(path).status_code == 401
    headers = {"Authorization": "Bearer " + "s" * 32}
    initial = client.get(path, headers=headers)
    assert initial.status_code == 200
    assert initial.json()["enabled"] is False
    assert initial.json()["interval_minutes"] == 5
    payload = {"enabled": True, "interval_minutes": 10, "revision": initial.json()["revision"]}
    assert client.post(path, headers=headers, json=payload).status_code == 409
    assert client.post(path, headers=headers, json={**payload, "interval_minutes": 0}).status_code == 422
    saved = client.post(path, headers=headers, json={**payload, "acknowledge_downtime": True})
    assert saved.status_code == 200
    assert saved.json()["enabled"] is True
    restarted = TestClient(
        release_application(ReleaseService(ReleaseStore(tmp_path), Runtime(), 0, Database()), "s" * 32)
    )
    assert restarted.get(path, headers=headers).json()["interval_minutes"] == 10
    assert client.post(path, headers=headers, json={**payload, "acknowledge_downtime": True}).status_code == 409


def test_enabling_auto_update_requires_database_backups(tmp_path):
    client = TestClient(release_application(ReleaseService(ReleaseStore(tmp_path), Runtime(), 0), "s" * 32))
    response = client.post(
        "/api/releases/auto-update",
        headers={"Authorization": "Bearer " + "s" * 32},
        json={"enabled": True, "interval_minutes": 5, "revision": 0, "acknowledge_downtime": True},
    )
    assert response.status_code == 409
    assert "数据库备份" in response.json()["detail"]
