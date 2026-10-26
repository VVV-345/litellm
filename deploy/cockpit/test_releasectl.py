import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture
def ctl():
    path = HERE / "releasectl.py"
    assert path.exists(), "isolated release controller has not been implemented"
    spec = importlib.util.spec_from_file_location("cockpit_releasectl", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def manifest(version="a" * 10):
    return {
        "format": 1,
        "channel": "cockpit-test",
        "version": version,
        "litellm_revision": version + "0" * 30,
        "cockpit_revision": "e" * 40,
        "compatibility": "c" * 64,
        "runtime": "d" * 64,
        "litellm": "ghcr.io/vvv-345/litellm-cockpit@sha256:" + "1" * 64,
        "cockpit": "ghcr.io/vvv-345/litellm-cockpit@sha256:" + "2" * 64,
    }


class Docker:
    def __init__(self, ctl, release, fail=""):
        self.ctl = ctl
        self.release = release
        self.fail = fail
        self.commands = []

    def __call__(self, command, *, stdin=None, stdout=None):
        self.commands.append(command)
        if self.fail and self.fail in " ".join(command):
            raise RuntimeError("injected Docker failure")
        if stdout:
            stdout.write_bytes(b"-- private test snapshot\n")
        if command[:3] == ("docker", "image", "inspect"):
            if command[-1] == "{{.Id}}":
                return "sha256:fixture-image"
            component = "litellm" if command[3] == self.release.litellm else "cockpit"
            return json.dumps(self.ctl.image_labels(self.release, component))
        if command[:3] == ("docker", "inspect", "--format"):
            return "sha256:fixture-image"
        if "ps" in command:
            return "fixture-container"
        return ""


def prepare(tmp_path, ctl):
    ctl.initialize(tmp_path)
    shutil.copyfile(HERE / "compose.yaml", tmp_path / "compose.yaml")
    return tmp_path


def test_manifest_is_a_pair_of_digests_not_mutable_tags(ctl):
    release = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    assert release.version == "a" * 10
    assert release.litellm != release.cockpit


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", 2),
        ("channel", "production"),
        ("version", "../bad"),
        ("litellm", "ghcr.io/vvv-345/litellm:latest"),
        ("cockpit", "ghcr.io/vvv-345/litellm-cockpit:cockpit-aaaaaaaaaa"),
        ("cockpit_revision", "main"),
        ("compatibility", ""),
        ("runtime", ""),
        ("litellm_revision", "b" * 40),
    ],
)
def test_reject_wrong_package_channel_identity_and_mutable_refs(ctl, field, value):
    with pytest.raises(ValueError):
        ctl.parse_release(json.dumps({**manifest(), field: value}).encode(), "a" * 10)


def test_initialization_creates_persistent_independent_secrets_and_empty_manifest(ctl, tmp_path):
    ctl.initialize(tmp_path)
    before = (tmp_path / ".env").read_bytes()
    secrets = [line.split("=", 1)[1] for line in before.decode().splitlines() if "=" in line]
    assert len(secrets) == len(set(secrets))
    assert len(secrets) == 6
    assert json.loads((tmp_path / "cockpit-data/manifest.json").read_text())["apiKeys"] == []
    with pytest.raises(ValueError):
        ctl.initialize(tmp_path)
    assert (tmp_path / ".env").read_bytes() == before


def test_cli_refuses_original_production_directory(ctl):
    with pytest.raises(ValueError, match="isolated runtime"):
        ctl.check_root(Path("/opt/zlia-pool/litellm-migration/runtime"))


def test_deploy_requires_init_before_pulling_or_creating_journal(ctl, tmp_path):
    release = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    docker = Docker(ctl, release)
    with pytest.raises(ValueError, match="initialize"):
        ctl.deploy(tmp_path, release, docker, lambda: None)
    assert docker.commands == []
    assert not (tmp_path / "pending.json").exists()


def test_first_deploy_records_success_only_after_health_probe(ctl, tmp_path):
    root = prepare(tmp_path, ctl)
    release = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    docker = Docker(ctl, release)
    probes = []
    ctl.deploy(root, release, docker, lambda: probes.append("healthy"))
    assert probes == ["healthy"]
    assert json.loads((root / "current.json").read_text())["version"] == release.version
    assert not (root / "pending.json").exists()
    assert not any("8092" in part for cmd in docker.commands for part in cmd)
    assert any("--wait" in cmd for cmd in docker.commands)
    assert all("litellm-migration" not in " ".join(cmd) for cmd in docker.commands)


def test_different_compatibility_stops_before_any_docker_action(ctl, tmp_path):
    root = prepare(tmp_path, ctl)
    (root / "current.json").write_text(json.dumps(manifest()))
    release = ctl.parse_release(json.dumps({**manifest("b" * 10), "compatibility": "f" * 64}).encode(), "b" * 10)
    docker = Docker(ctl, release)
    with pytest.raises(ValueError, match="compatibility"):
        ctl.deploy(root, release, docker, lambda: None)
    assert docker.commands == []


def test_reviewed_runtime_update_does_not_change_data_compatibility(ctl, tmp_path):
    root = prepare(tmp_path, ctl)
    old = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    ctl.deploy(root, old, Docker(ctl, old), lambda: None)
    new = ctl.parse_release(json.dumps({**manifest("b" * 10), "runtime": "f" * 64}).encode(), "b" * 10)
    ctl.deploy(root, new, Docker(ctl, new), lambda: None)
    assert json.loads((root / "current.json").read_text())["version"] == new.version


def test_wrong_image_identity_stops_before_mutating_runtime(ctl, tmp_path):
    root = prepare(tmp_path, ctl)
    release = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    commands = []

    def wrong_image(command, **kwargs):
        commands.append(command)
        return "{}"

    with pytest.raises(ValueError, match="image labels"):
        ctl.deploy(root, release, wrong_image, lambda: None)
    assert not (root / "pending.json").exists()
    assert not any("stop" in cmd for cmd in commands)


def test_failed_update_retains_snapshot_and_recover_restores_data_and_pair(ctl, tmp_path):
    root = prepare(tmp_path, ctl)
    old = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    ctl.deploy(root, old, Docker(ctl, old), lambda: None)
    old_manifest = (root / "cockpit-data/manifest.json").read_bytes()
    new = ctl.parse_release(json.dumps(manifest("b" * 10)).encode(), "b" * 10)
    docker = Docker(ctl, new)

    def unhealthy():
        (root / "cockpit-data/manifest.json").write_text('{"changed":true}')
        raise RuntimeError("injected health failure")

    with pytest.raises(RuntimeError, match="injected health"):
        ctl.deploy(root, new, docker, unhealthy)
    assert (root / "pending.json").exists()
    assert json.loads((root / "current.json").read_text())["version"] == old.version
    assert any("pg_dump" in cmd for cmd in docker.commands)
    assert docker.commands[-1][-3:] == ("stop", "litellm", "cockpit")
    recovery = Docker(ctl, old)
    ctl.recover(root, recovery, lambda: None)
    assert not (root / "pending.json").exists()
    assert (root / "cockpit-data/manifest.json").read_bytes() == old_manifest
    assert old.litellm in (root / "images.env").read_text()
    assert any("psql" in cmd for cmd in recovery.commands)
    assert any("dropdb" in cmd for cmd in recovery.commands)


def test_first_deploy_failure_is_recoverable_without_deleting_volume(ctl, tmp_path):
    root = prepare(tmp_path, ctl)
    release = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    with pytest.raises(RuntimeError):
        ctl.deploy(root, release, Docker(ctl, release, "--wait"), lambda: None)
    docker = Docker(ctl, release)
    ctl.recover(root, docker, lambda: None)
    assert not (root / "pending.json").exists()
    assert json.loads((root / "current.json").read_text())["version"] == release.version
    assert not any("--volumes" in cmd or "-v" in cmd for cmd in docker.commands)


def test_pending_operation_prevents_new_deploy(ctl, tmp_path):
    root = prepare(tmp_path, ctl)
    (root / "pending.json").write_text("{}")
    release = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    docker = Docker(ctl, release)
    with pytest.raises(ValueError, match="recover"):
        ctl.deploy(root, release, docker, lambda: None)
    assert docker.commands == []


def test_recovery_rejects_corrupt_backup_before_stopping_services(ctl, tmp_path):
    root = prepare(tmp_path, ctl)
    old = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    ctl.deploy(root, old, Docker(ctl, old), lambda: None)
    new = ctl.parse_release(json.dumps(manifest("b" * 10)).encode(), "b" * 10)
    with pytest.raises(RuntimeError):
        ctl.deploy(root, new, Docker(ctl, new, "--wait"), lambda: None)
    backup = next((root / "backups").glob("*/database.sql"))
    backup.write_bytes(b"corrupt")
    docker = Docker(ctl, old)
    with pytest.raises(ValueError, match="checksum"):
        ctl.recover(root, docker, lambda: None)
    assert not any("stop" in cmd or "dropdb" in cmd for cmd in docker.commands)
    assert (root / "pending.json").exists()


def test_partial_backup_recovery_does_not_restore_incomplete_sql(ctl, tmp_path):
    root = prepare(tmp_path, ctl)
    old = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    ctl.deploy(root, old, Docker(ctl, old), lambda: None)
    new = ctl.parse_release(json.dumps(manifest("b" * 10)).encode(), "b" * 10)
    with pytest.raises(RuntimeError):
        ctl.deploy(root, new, Docker(ctl, new, "pg_dump"), lambda: None)
    assert json.loads((root / "pending.json").read_text())["stage"] == "prepared"
    docker = Docker(ctl, old)
    ctl.recover(root, docker, lambda: None)
    assert not any("dropdb" in cmd for cmd in docker.commands)
    assert not (root / "pending.json").exists()


def test_running_wrong_image_never_records_success(ctl, tmp_path):
    root = prepare(tmp_path, ctl)
    release = ctl.parse_release(json.dumps(manifest()).encode(), "a" * 10)
    docker = Docker(ctl, release)

    def wrong_running_image(command, **kwargs):
        if command[:3] == ("docker", "inspect", "--format"):
            return "sha256:wrong-image"
        return docker(command, **kwargs)

    with pytest.raises(ValueError, match="running image"):
        ctl.deploy(root, release, wrong_running_image, lambda: None)
    assert not (root / "current.json").exists()
    assert (root / "pending.json").exists()


def test_compatibility_hash_covers_actual_migration_directory(ctl, tmp_path):
    sys.modules["releasectl"] = ctl
    spec = importlib.util.spec_from_file_location("release_bundle", HERE / "release_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    migration = tmp_path / "litellm-proxy-extras/litellm_proxy_extras/migrations/test/migration.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text("CREATE TABLE one (id integer);")
    before = module.compatibility_hash(tmp_path, "e" * 40)
    migration.write_text("CREATE TABLE two (id integer);")
    assert module.compatibility_hash(tmp_path, "e" * 40) != before


def test_compose_has_only_isolated_services():
    path = HERE / "compose.yaml"
    assert path.exists(), "isolated Compose has not been implemented"
    import yaml

    doc = yaml.safe_load(path.read_text())
    assert set(doc["services"]) == {"db", "litellm", "cockpit"}
    assert doc["name"] == "litellm-cockpit-test"
    assert doc["services"]["litellm"]["ports"] == ["127.0.0.1:18400:4000"]
    assert doc["services"]["litellm"]["environment"]["ACCOUNT_POOL_BACKEND"] == "cockpit"
    assert not doc["services"]["db"].get("ports")
    assert not doc["services"]["cockpit"].get("ports")
    assert "container_name" not in path.read_text()
    assert "docker.sock" not in path.read_text()
    assert "external:" not in path.read_text()


def test_workflow_separates_publication_and_uses_native_architecture_gates():
    path = HERE.parents[1] / ".github/workflows/publish-cockpit-images.yml"
    assert path.exists(), "isolated publishing workflow has not been implemented"
    import yaml

    doc = yaml.safe_load(path.read_text())
    assert doc["jobs"]["publish"]["needs"] == ["prepare", "build", "smoke"]
    assert "ubuntu-24.04-arm" in path.read_text()
    assert "ghcr.io/vvv-345/litellm-cockpit" in path.read_text()
    assert "codex/cockpit-reuse-plan" in path.read_text()
    assert "delete-package" not in path.read_text()
    assert ":latest" not in path.read_text()


def test_workflow_overrides_cockpit_dockerfile_amd64_default():
    import yaml

    doc = yaml.safe_load((HERE.parents[1] / ".github/workflows/publish-cockpit-images.yml").read_text())
    build = next(step for step in doc["jobs"]["build"]["steps"] if step.get("id") == "build")
    assert build["with"].get("build-args", "").splitlines() == ["TARGETOS=linux", "TARGETARCH=${{ matrix.arch }}"]


def test_failed_smoke_captures_container_logs_before_cleanup():
    import yaml

    doc = yaml.safe_load((HERE.parents[1] / ".github/workflows/publish-cockpit-images.yml").read_text())
    smoke = next(step["run"] for step in doc["jobs"]["smoke"]["steps"] if "run" in step)
    assert "dc logs --no-color --tail 100" in smoke
    assert smoke.index("dc logs --no-color --tail 100") < smoke.index("dc down --volumes")
