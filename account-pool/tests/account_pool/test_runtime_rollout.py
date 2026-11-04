"""验证显式逐卡发布的排空、状态保留、故障阻断及同版本续跑，不操作真实凭据。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import EnvironmentStatus
from test_cliproxy_runtime_image import _NEW_IMAGE, _record, _settings


class Environments:
    def __init__(self, records):
        self.records = {record.id: record for record in records}

    async def list(self):
        return tuple(self.records.values())

    async def get(self, identifier):
        return self.records.get(identifier)


class Checkpoints:
    def __init__(self):
        self.items = {}
        self.leased = False
        self.phases = []

    async def begin(self, identifier, release, image):
        from account_pool.runtime_rollout_repository import RuntimeCheckpoint

        previous = self.items.get(identifier)
        if previous is not None:
            if (previous.release_id, previous.target_image) == (release, image):
                return previous
            if previous.phase != "verified":
                raise ValueError("unfinished rollout belongs to another release")
        return await self.save(RuntimeCheckpoint(identifier, release, image))

    async def save(self, checkpoint):
        self.items[checkpoint.environment_id] = checkpoint
        self.phases.append(checkpoint.phase)
        return checkpoint

    async def get(self, identifier):
        return self.items.get(identifier)

    async def has_leases(self, identifier):
        return self.leased


class Runtime:
    def __init__(self, tmp_path):
        self.settings = _settings(tmp_path)
        self.events = []
        self.backups = {}
        self.fail = False
        self.current = False

    async def retain_current_runtime_image(self, record):
        return self.current

    async def backup_runtime(self, record, directory, capture):
        assert not store_for(self).leased
        if directory not in self.backups:
            self.events.append(("backup", record.id))
            self.backups[directory] = await capture(record)
        return self.backups[directory]

    async def rollout_runtime_image(self, record, directory, *, verify):
        assert not store_for(self).leased
        self.events.append(("switch", record.id))
        if self.fail:
            self.events.append(("rollback_image_only", record.id))
            raise RuntimeError("synthetic secret must not be printed")
        await verify(record)
        self.current = True
        return True


def store_for(runtime):
    return runtime.checkpoints


async def verified(record, expected):
    assert expected in (None, "fixture-state")
    return "fixture-state"


async def test_rollout_drains_before_docker_and_keeps_original_card_state(tmp_path: Path):
    from account_pool.runtime_rollout import RuntimeRollout

    record = _record().model_copy(update={"enabled": True, "status": EnvironmentStatus.ERROR, "last_error": "original"})
    environments = Environments((record,))
    checkpoints = Checkpoints()
    checkpoints.leased = True
    runtime = Runtime(tmp_path)
    runtime.checkpoints = checkpoints
    service = RuntimeRollout(environments, checkpoints, runtime, CredentialOwnership(), verified)
    task = asyncio.create_task(service.card(record.id, "a" * 40, _NEW_IMAGE))
    for _ in range(20):
        if record.id in checkpoints.items:
            break
        await asyncio.sleep(0)
    assert checkpoints.items[record.id].phase == "draining"
    assert runtime.events == []
    assert not task.done()
    checkpoints.leased = False
    result = await asyncio.wait_for(task, 2)
    assert result.phase == "verified"
    assert result.image_verified is True
    assert result.credential_state == "preserved"
    assert result.model_inference == "not_run"
    assert environments.records[record.id] == record
    assert runtime.events == [("backup", record.id), ("switch", record.id)]
    assert (await service.card(record.id, "a" * 40, _NEW_IMAGE)).unchanged
    assert len(runtime.events) == 2


async def test_unchanged_image_skips_gate_leases_backup_and_switch(tmp_path: Path):
    from account_pool.runtime_rollout import RuntimeRollout

    record = _record().model_copy(update={"enabled": False, "status": EnvironmentStatus.COOLING_DOWN})
    checkpoints = Checkpoints()
    checkpoints.leased = True
    runtime = Runtime(tmp_path)
    runtime.current = True
    runtime.checkpoints = checkpoints
    environments = Environments((record,))
    service = RuntimeRollout(environments, checkpoints, runtime, CredentialOwnership(), verified)
    result = await asyncio.wait_for(service.card(record.id, "b" * 40, _NEW_IMAGE), 1)
    assert result.unchanged and result.image_verified and result.phase == "verified"
    assert checkpoints.items == {} and checkpoints.phases == []
    assert runtime.events == []
    assert environments.records[record.id] == record


async def test_matching_image_does_not_bypass_failed_checkpoint(tmp_path: Path):
    from account_pool.runtime_rollout import RuntimeRollout
    from account_pool.runtime_rollout_repository import RuntimeCheckpoint

    record = _record()
    checkpoints = Checkpoints()
    checkpoints.items[record.id] = RuntimeCheckpoint(record.id, "a" * 40, _NEW_IMAGE, phase="failed")
    runtime = Runtime(tmp_path)
    runtime.current = True
    runtime.checkpoints = checkpoints
    service = RuntimeRollout(Environments((record,)), checkpoints, runtime, CredentialOwnership(), verified)
    with pytest.raises(ValueError, match="another release"):
        await service.card(record.id, "b" * 40, _NEW_IMAGE)
    result = await service.card(record.id, "a" * 40, _NEW_IMAGE)
    assert not result.unchanged
    assert runtime.events == [("backup", record.id), ("switch", record.id)]


async def test_persisted_maintenance_gate_rejects_an_otherwise_current_gateway_snapshot():
    from account_pool.gateway_repository import lock_version

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        async def execute(self, query, params):
            if "account_pool_runtime_rollouts" in query:
                return Cursor({"environment_id": params[0]})
            if "account_pool_environments" in query:
                return Cursor({"version": 0})
            return Cursor(None)

    assert await lock_version(Connection(), _record().id, 0, 0) is False


async def test_failed_rollout_retains_gate_and_reuses_backup_on_restart(tmp_path: Path):
    from account_pool.runtime_rollout import RuntimeRollout, RuntimeRolloutError

    record = _record()
    checkpoints = Checkpoints()
    runtime = Runtime(tmp_path)
    runtime.checkpoints = checkpoints
    runtime.fail = True
    environments = Environments((record,))
    service = RuntimeRollout(environments, checkpoints, runtime, CredentialOwnership(), verified)
    with pytest.raises(RuntimeRolloutError, match="phase=switching") as error:
        await service.card(record.id, "a" * 40, _NEW_IMAGE)
    assert "synthetic secret" not in str(error.value)
    assert checkpoints.items[record.id].phase == "failed"
    assert checkpoints.items[record.id].failed_phase == "switching"
    assert environments.records[record.id] == record
    with pytest.raises(ValueError, match="another release"):
        await service.card(record.id, "b" * 40, _NEW_IMAGE)
    runtime.fail = False
    resumed = RuntimeRollout(environments, checkpoints, runtime, CredentialOwnership(), verified)
    assert (await resumed.card(record.id, "a" * 40, _NEW_IMAGE)).phase == "verified"
    assert runtime.events.count(("backup", record.id)) == 1
    assert runtime.events.count(("switch", record.id)) == 2


async def test_drain_timeout_preserves_gate_without_touching_the_runtime(tmp_path: Path):
    from account_pool.runtime_rollout import RuntimeRollout, RuntimeRolloutError

    record = _record()
    checkpoints = Checkpoints()
    checkpoints.leased = True
    runtime = Runtime(tmp_path)
    runtime.checkpoints = checkpoints
    service = RuntimeRollout(Environments((record,)), checkpoints, runtime, CredentialOwnership(), verified,
                             drain_timeout_seconds=0.01)
    with pytest.raises(RuntimeRolloutError, match="phase=draining"):
        await service.card(record.id, "a" * 40, _NEW_IMAGE)
    assert checkpoints.items[record.id].phase == "failed"
    assert runtime.events == []


async def test_runtime_verification_is_read_only_and_rejects_extra_credentials(tmp_path: Path):
    import httpx
    from account_pool.channels.cliproxyapi.client import HttpCLIProxyClient
    from account_pool.shared.secrets import EnvironmentSecretDeriver

    record = _record()
    requests = []
    extra = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        path = request.url.path
        if path.endswith("managed-identity"):
            return (httpx.Response(200, json={"supported": True, "version": 1})
                    if request.headers.get("X-Management-Key") else httpx.Response(401))
        if path.endswith("access-token-fallback"):
            return httpx.Response(200, json={"supported": True, "version": 1, "token_refresh": False})
        if path.endswith("config.yaml"):
            return httpx.Response(200, text="proxy-url: ''\nrequest-retry: 0\n")
        if path.endswith("download"):
            return httpx.Response(200, json={"refresh_token": "fixture-refresh", "account_id": "fixture-account"})
        return httpx.Response(200, json={"files": [
            {"name": record.auth_file_name, "type": "codex", "disabled": True, "attributes": {"proxy_url": ""}}, *extra,
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=http)
        captured = await client.runtime_state_digest(record, require_unique=False)
        assert await client.runtime_state_digest(record, require_unique=True) == captured
        assert not any("managed-identity" in str(request.url) for request in requests)
        extra.append({"name": "second.json", "type": "codex"})
        with pytest.raises(ValueError, match="unique credential"):
            await client.runtime_state_digest(record, require_unique=True)


@pytest.mark.parametrize("fault", ["none", "extra", "config", "credential", "unauthenticated", "capability"])
async def test_post_switch_verifier_requires_capabilities_and_unchanged_readback(fault):
    import httpx
    from account_pool.runtime_rollout import RuntimeVerifier
    from account_pool.shared.secrets import EnvironmentSecretDeriver

    record = _record()
    active = []
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        problem = active[0] if active else "none"
        path = request.url.path
        if path.endswith(("managed-identity", "access-token-fallback")):
            if not request.headers.get("X-Management-Key"):
                return httpx.Response(200 if problem == "unauthenticated" else 401)
            return httpx.Response(200, json={"supported": True, "version": 2 if problem == "capability" else 1,
                                            "token_refresh": False})
        if path.endswith("config.yaml"):
            return httpx.Response(200, text="request-retry: " + ("3" if problem == "config" else "0"))
        if path.endswith("download"):
            return httpx.Response(200, json={"refresh_token": "changed" if problem == "credential" else "fixture-refresh",
                                            "account_id": "fixture-account"})
        return httpx.Response(200, json={"files": [
            {"name": record.auth_file_name, "type": "codex", "disabled": True},
            *([{"name": "other.json", "type": "codex"}] if problem == "extra" else []),
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        verifier = RuntimeVerifier(http, EnvironmentSecretDeriver("s" * 32))
        expected = await verifier(record, None)
        active.append(fault)
        if fault == "none":
            assert await verifier(record, expected) == expected
        else:
            with pytest.raises(ValueError):
                await verifier(record, expected)
    assert all(request.method == "GET" for request in requests)


def test_rollout_cli_failure_does_not_expose_underlying_credentials(monkeypatch):
    import account_pool.runtime_rollout as rollout

    async def fail(*args):
        raise RuntimeError("fixture secret must remain private")

    monkeypatch.setattr(rollout, "_run", fail)
    monkeypatch.setattr("sys.argv", ["runtime_rollout", "--release", "a" * 40, "--image", _NEW_IMAGE,
                                     "--card", str(_record().id)])
    with pytest.raises(SystemExit) as error:
        rollout.main()
    assert "RuntimeError" in str(error.value)
    assert "fixture secret" not in str(error.value)


async def test_runtime_inventory_distinguishes_current_missing_and_failed_without_mutating(tmp_path):
    from account_pool.runtime_rollout import runtime_inventory
    from account_pool.runtime_rollout_repository import RuntimeCheckpoint
    record = _record()
    checkpoints = Checkpoints()
    class InventoryRuntime(Runtime):
        async def runtime_image_identity(self, identifier):
            return "sha256:" + "a" * 64, "sha256:" + "a" * 64
    runtime = InventoryRuntime(tmp_path)
    environments = Environments((record,))
    report = await runtime_inventory(environments, checkpoints, runtime)
    assert report.default_image == _NEW_IMAGE
    assert report.cards[0].status == "current"
    checkpoints.items[record.id] = RuntimeCheckpoint(record.id, "a" * 40, _NEW_IMAGE, phase="failed")
    report = await runtime_inventory(environments, checkpoints, runtime)
    assert report.cards[0].status == "maintenance" and report.cards[0].phase == "failed"
    assert checkpoints.phases == [] and runtime.events == []
