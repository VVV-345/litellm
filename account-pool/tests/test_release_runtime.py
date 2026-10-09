"""本文件验证 Docker 命令边界及 Compose 插值，应用归档不能触发拉取或构建。"""

from __future__ import annotations

import io
import json
import tarfile
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest
import yaml
from account_pool.release_compatibility import digest_sources
from account_pool.release_models import ReleasePair
from account_pool.release_runtime import (
    ContainerInspection,
    DockerReleaseRuntime,
    ReleaseSettings,
    image_pair,
    snapshot_container,
)


def test_release_worker_uses_separate_exec_proxy_without_widening_manager_access() -> None:
    compose = yaml.safe_load((Path(__file__).parents[2] / "deploy/docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    proxy = services["release-docker-socket-proxy"]
    worker = services["release-worker"]
    assert proxy["environment"]["EXEC"] == "1"
    assert services["docker-socket-proxy"]["environment"]["EXEC"] == "0"
    assert proxy["networks"] == ["release-socket"]
    assert compose["networks"]["release-socket"]["internal"] is True
    assert not proxy.get("ports")
    assert proxy["image"] == services["docker-socket-proxy"]["image"]
    assert worker["environment"]["ACCOUNT_POOL_RELEASE_DOCKER_HOST"] == "tcp://release-docker-socket-proxy:2375"
    assert "release-socket" in worker["networks"] and "account-pool-socket" not in worker["networks"]
    assert "release-socket" not in services["account-pool"]["networks"]
    assert "release-socket" not in services["litellm"]["networks"]


def test_release_settings_accepts_private_release_proxy_but_manager_does_not(tmp_path: Path) -> None:
    from account_pool.config import Settings

    endpoint = "tcp://release-docker-socket-proxy:2375"
    settings = ReleaseSettings.model_validate(
        {"token": "t" * 32, "root": tmp_path, "deployment": tmp_path.parent / "deploy", "docker_host": endpoint}
    )
    assert settings.docker_host == endpoint
    with pytest.raises(ValueError):
        Settings.validate_docker_host(endpoint)


def schema_contract(source: str, name: str = "account_pool/settings.py") -> bytes:
    data: Final = source.encode()
    output: Final = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member: Final = tarfile.TarInfo(name)
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    with tarfile.open(fileobj=io.BytesIO(output.getvalue())) as archive:
        return DockerReleaseRuntime._schema_member(archive, archive.getmembers()[0])


@pytest.mark.parametrize(
    "changed",
    (
        'SQL = "CREATE TABLE account_pool_settings (state text);"\n'
        "class Settings(BaseModel):\n    full_log_skip_failed: bool = False\n",
        'SQL = "CREATE TABLE account_pool_settings (state text);"\n'
        'class Settings(BaseModel):\n    state: Literal["pending", "standard"] = "pending"\n',
    ),
)
def test_persisted_json_contract_changes_block_image_only_rollback(changed: str) -> None:
    old: Final = (
        'SQL = "CREATE TABLE account_pool_settings (state text);"\n'
        'class Settings(BaseModel):\n    state: Literal["pending"] = "pending"\n'
    )
    assert schema_contract(old) != schema_contract(changed)


def test_non_persistent_api_contract_changes_do_not_block_image_only_rollback() -> None:
    old: Final = (
        'Target = Literal["cliproxyapi"]\nclass UpstreamSyncView(BaseModel):\n    target: Target = "cliproxyapi"\n'
    )
    changed: Final = (
        'Target = Literal["cliproxyapi", "litellm"]\n'
        "class UpstreamSyncView(BaseModel):\n"
        '    target: Target = "cliproxyapi"\n'
        '    latest_tag: str = ""\n'
    )
    assert schema_contract(old, "account_pool/upstream_sync.py") == schema_contract(
        changed, "account_pool/upstream_sync.py"
    )


def test_contract_fingerprint_ignores_method_bodies_and_formatting() -> None:
    old: Final = (
        'SQL = "CREATE TABLE account_pool_settings (enabled boolean);"\n'
        "class Settings(BaseModel):\n    enabled: bool = True\n    def ready(self): return True\n"
    )
    changed: Final = (
        'SQL = "CREATE TABLE account_pool_settings (enabled boolean);"\n'
        "class Settings(BaseModel):\n    enabled: bool=True\n    def ready(self): return False\n"
    )
    assert schema_contract(old) == schema_contract(changed)


def test_contract_fingerprint_ignores_file_moves_but_preserves_fields_and_aliases() -> None:
    source = (
        'SQL = "CREATE TABLE account_pool_settings (mode text);"\n'
        'class Settings(BaseModel):\n    mode: Literal["a"] = "a"\nKind = Literal["a"]\n'
    )
    assert schema_contract(source) == schema_contract(source, "account_pool/providers/contracts.py")
    assert schema_contract(source) != schema_contract(source.replace('Literal["a"]', 'Literal["b"]'))


def test_credential_contract_follows_moved_encryption_definitions_and_detects_changes() -> None:
    from account_pool.release_compatibility import credential_contract

    encryption = b'class SecretPurpose(Enum):\n    STATE = "state"\nclass EnvironmentSecretDeriver:\n    def derive(self): return "v1"\nclass StateCipher:\n    def seal(self, text): return text\n'
    stores = {"repository.py": b"storage-v1", "management_repository.py": b"keys-v1"}
    proxy = {"virtual_key_secret.py": b"key-v1", "encrypt_decrypt_utils.py": b"cipher-v1"}
    before = credential_contract({**stores, "secrets.py": encryption}, proxy)
    after = credential_contract(
        {**stores, "shared/secrets.py": encryption, "secrets.py": b"from shared.secrets import StateCipher"}, proxy
    )
    assert before and before == after
    assert before != credential_contract({**stores, "secrets.py": encryption.replace(b"v1", b"v2")}, proxy)
    assert credential_contract(stores, proxy) == ""


class Commands:
    def __init__(self) -> None:
        self.calls: tuple[tuple[str, ...], ...] = ()

    def __call__(self, *args: str, timeout: int = 120) -> bytes:
        self.calls += (args,)
        if args[0] == "ps":
            return b"a" * 12 if args[-1].endswith("=litellm") else b"b" * 12
        if args[0] == "inspect":
            return json.dumps([{"Image": "sha256:" + char * 64, "Config": {}} for char in ("a", "b")]).encode()
        if args[:2] == ("image", "inspect"):
            return json.dumps(
                [{"Id": args[2], "Size": 100, "Config": {"Labels": {"org.opencontainers.image.revision": "c" * 40}}}]
            ).encode()
        return b""


def test_apply_never_pulls_builds_or_replaces_worker_and_preserves_dollar_values(tmp_path: Path) -> None:
    command: Final = Commands()
    runtime: Final = DockerReleaseRuntime(
        ReleaseSettings.model_validate({"token": "t" * 32, "root": tmp_path, "deployment": tmp_path.parent / "deploy"}),
        command,
    )
    target: Final = runtime.current()
    runtime.apply(target, b'{"services":{"litellm":{"environment":{"PASSWORD":"one$two"}},"account-pool":{}}}')
    compose: Final = next(call for call in command.calls if call[0] == "compose")
    assert "--no-build" in compose and "--no-deps" in compose
    assert compose[compose.index("--pull") + 1] == "never"
    assert compose[-2:] == ("litellm", "account-pool")
    assert not any(call[:2] in (("image", "pull"), ("image", "build")) for call in command.calls)
    assert "one$$two" in (tmp_path / "switch-compose.json").read_text()
    assert "one$two" in (tmp_path / "active-compose.json").read_text()
    assert ReleasePair.model_validate_json((tmp_path / "active-pair.json").read_bytes()) == target


def test_pair_identity_uses_images_and_not_discovery_order(tmp_path: Path) -> None:
    runtime: Final = DockerReleaseRuntime(
        ReleaseSettings.model_validate({"token": "t" * 32, "root": tmp_path, "deployment": tmp_path.parent / "deploy"}),
        Commands(),
    )
    current: Final = runtime.current()
    assert image_pair((current.images[1], current.images[0])).id == current.id


def test_pinned_candidate_pull_checks_image_identity_before_deploy(tmp_path: Path) -> None:
    from account_pool.release_models import ReleaseCandidate, RemoteReleaseImage
    from account_pool.release_store import ReleaseError

    calls = []

    def command(*args, timeout=120):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps(
                [
                    {
                        "Id": "sha256:" + "f" * 64,
                        "Size": 100,
                        "Config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}},
                    }
                ]
            ).encode()
        return b""

    runtime = DockerReleaseRuntime(
        ReleaseSettings.model_validate({"token": "t" * 32, "root": tmp_path, "deployment": tmp_path.parent / "deploy"}),
        command,
    )
    selected = ReleaseCandidate(
        commit="a" * 40,
        images=tuple(
            RemoteReleaseImage(
                service=service,
                digest="sha256:" + str(index + 1) * 64,
                image_id="sha256:" + str(index + 3) * 64,
            )
            for index, service in enumerate(("litellm", "account-pool"))
        ),
    )
    with pytest.raises(ReleaseError, match="镜像身份"):
        runtime.pull_candidate(selected)
    assert calls[0] == ("image", "pull", "ghcr.io/vvv-345/litellm@sha256:" + "1" * 64)
    assert not any(call[0] == "compose" for call in calls)


def test_apply_checks_application_readiness_not_only_container_liveness(tmp_path: Path) -> None:
    command = Commands()
    runtime = DockerReleaseRuntime(
        ReleaseSettings.model_validate({"token": "t" * 32, "root": tmp_path, "deployment": tmp_path.parent / "deploy"}),
        command,
    )
    runtime.apply(runtime.current(), b'{"services":{"litellm":{},"account-pool":{}}}')
    scripts = [call[-1] for call in command.calls if call[0] == "exec"]
    assert any("/health/readiness" in script for script in scripts)
    assert any("/api/environments" in script for script in scripts)


def test_first_backup_captures_actual_mounts_ports_and_environment() -> None:
    container: Final = ContainerInspection.model_validate(
        {
            "Image": "sha256:" + "a" * 64,
            "Config": {"Env": ["KEY=literal$value"], "Cmd": ["start"], "User": "65532:65532"},
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": "existing_data",
                    "Source": "/var/lib/docker/volumes/existing_data",
                    "Destination": "/data",
                    "RW": True,
                },
                {"Type": "bind", "Source": "/opt/original", "Destination": "/config", "RW": False},
            ],
            "HostConfig": {
                "ReadonlyRootfs": True,
                "PortBindings": {"4000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "4001"}]},
            },
        }
    )
    config: Final = snapshot_container(
        {"volumes": ["new_empty_data:/data"], "environment": {"KEY": "changed"}}, container
    )
    assert config["environment"] == {"KEY": "literal$value"}
    assert config["read_only"] is True
    assert config["volumes"] == [
        {"type": "volume", "source": "existing_data", "target": "/data", "read_only": False},
        {"type": "bind", "source": "/opt/original", "target": "/config", "read_only": True},
    ]
    assert config["ports"] == [{"target": 4000, "protocol": "tcp", "host_ip": "127.0.0.1", "published": "4001"}]


class InspectionCommands:
    def __init__(self) -> None:
        self.calls: tuple[tuple[str, ...], ...] = ()

    def __call__(self, *args: str, timeout: int = 120) -> bytes:
        self.calls += (args,)
        if args[0] != "cp":
            return b""
        source: Final = args[1].partition(":")[2]
        files: Final = {
            "/app/account_pool": {
                "account_pool/repository.py": b'SQL = "CREATE TABLE old_table (id uuid)"\n',
                "account_pool/extra.sql": b"SELECT 1;\n",
                "account_pool/release_runtime.py": b"worker-source",
            },
            "/app/schema.prisma": {"schema.prisma": b"model A {\r\n id String @id\r\n}\r\n"},
            "/app/.venv/pyvenv.cfg": {"pyvenv.cfg": b"version_info = 3.13.15\n"},
            "/app/.venv/lib/python3.13/site-packages/litellm/proxy/management_endpoints": {
                "account_pool_full_logs.py": b"class FullLogStore: pass",
                "account_pool_full_log_api.py": b"def create_full_log_router(): pass",
            },
            "/app/.venv/lib/python3.13/site-packages/litellm_proxy_extras/migrations": {
                "migrations/001/migration.sql": b"CREATE TABLE example(id INTEGER);",
            },
        }.get(source, {Path(source).name: b"source"})
        output: Final = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for name, body in files.items():
                member: Final = tarfile.TarInfo(name)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
        return output.getvalue()


def test_static_inspection_reads_installed_sources_without_starting_target(tmp_path: Path) -> None:
    command: Final = InspectionCommands()
    runtime: Final = DockerReleaseRuntime(
        ReleaseSettings.model_validate({"token": "t" * 32, "root": tmp_path, "deployment": tmp_path.parent / "deploy"}),
        command,
    )
    image_runtime: Final = DockerReleaseRuntime(runtime.settings, Commands())
    image: Final = next(item for item in image_runtime.current().images if item.service == "litellm")
    sources: Final = runtime._inspection_sources(image)
    assert b"create_full_log_router" in sources["account_pool_full_log_api.py"]
    assert sources["migration-history"]
    assert "encrypt_decrypt_utils.py" in sources
    assert command.calls[0][:6] == ("create", "--name", command.calls[0][2], "--network", "none", "--entrypoint")
    assert command.calls[-1] == ("rm", "-v", command.calls[0][2])
    assert not any(call[0] in ("start", "run", "exec") for call in command.calls)
    assert digest_sources({"migration-history": b""}, ("migration-history",)) == ""


OAUTH_BROWSER_DDL: Final = (
    "CREATE TABLE IF NOT EXISTS account_pool_oauth_browser_sessions ( id uuid PRIMARY KEY, "
    "environment_id uuid NOT NULL REFERENCES account_pool_environments(id) ON DELETE CASCADE, "
    "status text NOT NULL, expires_at timestamptz NOT NULL, payload jsonb NOT NULL )",
    "CREATE INDEX IF NOT EXISTS account_pool_oauth_browser_sessions_environment_idx "
    "ON account_pool_oauth_browser_sessions (environment_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS account_pool_oauth_browser_sessions_active_environment_idx "
    "ON account_pool_oauth_browser_sessions (environment_id) WHERE status IN ('starting', 'active')",
    "CREATE UNIQUE INDEX IF NOT EXISTS account_pool_oauth_browser_sessions_active_callback_environment_idx "
    "ON account_pool_oauth_browser_sessions (environment_id) WHERE status IN ('starting', 'active', 'callback_pending')",
)
OLD_POOL_SOURCE: Final = (
    'SQL = "CREATE TABLE account_pool_settings (mode text);"\n'
    'class Settings(BaseModel):\n    mode: Literal["a"] = "a"\nKind = Literal["a"]\n'
)


def test_oauth_browser_schema_upgrade_accepts_only_the_audited_forward_delta() -> None:
    from account_pool.release_compatibility import OAuthBrowserSchemaEvidence, oauth_browser_schema_upgrade

    before: Final = OAuthBrowserSchemaEvidence(
        prisma=b"model A {\n id String @id\n}\n",
        pool=schema_contract(OLD_POOL_SOURCE),
        sources="27e3e2ca5cdee2bbecba5c0f5accf77dafb9daf100e811244fd834610732fba5",
        startup="unchanged-startup-and-migrations",
    )
    after: Final = replace(
        before,
        pool=schema_contract(
            OLD_POOL_SOURCE + "\n".join(f"SQL_{index} = {ddl!r}" for index, ddl in enumerate(OAUTH_BROWSER_DDL))
        ),
        sources="aeb2c6886210594cb6652dc6541127d3468092989786aaf89518b4588a3bc953",
    )
    assert oauth_browser_schema_upgrade(before, after)
    assert not oauth_browser_schema_upgrade(after, before)
    assert not oauth_browser_schema_upgrade(before, before)
    assert not oauth_browser_schema_upgrade(after, after)
    assert not oauth_browser_schema_upgrade(replace(before, pool=b""), after)
    assert not oauth_browser_schema_upgrade(replace(before, startup=""), replace(after, startup=""))
    assert oauth_browser_schema_upgrade(before, replace(after, prisma=after.prisma.replace(b"\n", b"\r\n")))


@pytest.mark.parametrize(
    "changed_source, additions",
    (
        *(
            (OLD_POOL_SOURCE, tuple(ddl for index, ddl in enumerate(OAUTH_BROWSER_DDL) if index != missing))
            for missing in range(4)
        ),
        (OLD_POOL_SOURCE.replace("mode text", "mode integer"), OAUTH_BROWSER_DDL),
        (OLD_POOL_SOURCE.replace('Literal["a"]', 'Literal["b"]'), OAUTH_BROWSER_DDL),
        (OLD_POOL_SOURCE + 'EXTRA = "CREATE TABLE unrelated (id uuid)"\n', OAUTH_BROWSER_DDL),
        (OLD_POOL_SOURCE + 'EXTRA = "ALTER TABLE account_pool_settings ADD COLUMN extra text"\n', OAUTH_BROWSER_DDL),
        (OLD_POOL_SOURCE, (*OAUTH_BROWSER_DDL, OAUTH_BROWSER_DDL[0])),
        (OLD_POOL_SOURCE, (OAUTH_BROWSER_DDL[0].replace("status text", "status integer"), *OAUTH_BROWSER_DDL[1:])),
        ("", OAUTH_BROWSER_DDL),
    ),
    ids=(
        "missing-table",
        "missing-index-1",
        "missing-index-2",
        "missing-index-3",
        "old-column",
        "persisted-model",
        "extra-table",
        "extra-alter",
        "duplicate",
        "new-column",
        "removed-old-contract",
    ),
)
def test_oauth_browser_schema_upgrade_rejects_incomplete_or_additional_contract_changes(
    changed_source: str, additions: tuple[str, ...]
) -> None:
    from account_pool.release_compatibility import OAuthBrowserSchemaEvidence, oauth_browser_schema_upgrade

    before: Final = OAuthBrowserSchemaEvidence(
        prisma=b"schema",
        pool=schema_contract(OLD_POOL_SOURCE),
        sources="27e3e2ca5cdee2bbecba5c0f5accf77dafb9daf100e811244fd834610732fba5",
        startup="startup",
    )
    after: Final = replace(
        before,
        pool=schema_contract(
            changed_source + "\n".join(f"SQL_{index} = {ddl!r}" for index, ddl in enumerate(additions))
        ),
        sources="aeb2c6886210594cb6652dc6541127d3468092989786aaf89518b4588a3bc953",
    )
    assert not oauth_browser_schema_upgrade(before, after)


@pytest.mark.parametrize("changed", ("prisma", "startup", "sources", "old_sources"))
def test_oauth_browser_schema_upgrade_rejects_unaudited_sources_and_prisma_field_moves(changed: str) -> None:
    from account_pool.release_compatibility import OAuthBrowserSchemaEvidence, oauth_browser_schema_upgrade

    before: Final = OAuthBrowserSchemaEvidence(
        prisma=b"model A {\n id String @id\n value String\n}\nmodel B {\n id String @id\n}\n",
        pool=schema_contract(OLD_POOL_SOURCE),
        sources="unknown"
        if changed == "old_sources"
        else "27e3e2ca5cdee2bbecba5c0f5accf77dafb9daf100e811244fd834610732fba5",
        startup="startup",
    )
    after: Final = replace(
        before,
        prisma=b"model A {\n id String @id\n}\nmodel B {\n id String @id\n value String\n}\n"
        if changed == "prisma"
        else before.prisma,
        pool=schema_contract(
            OLD_POOL_SOURCE + "\n".join(f"SQL_{index} = {ddl!r}" for index, ddl in enumerate(OAUTH_BROWSER_DDL))
        ),
        sources="unknown"
        if changed == "sources"
        else "aeb2c6886210594cb6652dc6541127d3468092989786aaf89518b4588a3bc953",
        startup="changed-startup-or-migration" if changed == "startup" else before.startup,
    )
    assert not oauth_browser_schema_upgrade(before, after)


@pytest.mark.parametrize(
    "change",
    (
        b'\nSQL = "DROP TABLE account_pool_settings"',
        b'\nSQL = "TRUNCATE account_pool_settings"',
        b'\nSQL = "DR" + "OP TABLE account_pool_settings"',
        b"\nclass PersistedModel(BaseModel):\n    new_field: str",
    ),
)
def test_audited_manager_source_digest_detects_code_outside_schema_extraction(change: bytes) -> None:
    from account_pool.release_compatibility import manager_source_digest

    before: Final = (
        ("account_pool/repository.py", OLD_POOL_SOURCE.encode()),
        ("account_pool/app.py", b"initialize()\n"),
    )
    assert manager_source_digest(before) != manager_source_digest(((before[0][0], before[0][1] + change), before[1]))
    assert manager_source_digest(before) != manager_source_digest(
        (before[0], ("account_pool/app.py", b"other_init()\n"))
    )
    assert manager_source_digest(before) != manager_source_digest(
        (before[0], ("account_pool/moved/app.py", before[1][1]))
    )
    assert manager_source_digest(before) != manager_source_digest((*before, ("account_pool/new.sql", b"DROP TABLE x")))


def test_audited_manager_source_digest_normalizes_line_endings_and_ignores_only_worker_and_bytecode() -> None:
    from account_pool.release_compatibility import manager_source_digest

    before: Final = (("account_pool/app.py", b"initialize()\n"), ("account_pool/repository.py", b"schema\n"))
    assert manager_source_digest(before) == manager_source_digest(
        (
            ("account_pool/repository.py", b"schema\r\n"),
            ("account_pool/app.py", b"initialize()\r\n"),
            ("account_pool/release_runtime.py", b"worker-change"),
            ("account_pool/__pycache__/app.cpython-313.pyc", b"compiled"),
        )
    )
    assert manager_source_digest(before) != manager_source_digest(
        (*before, ("account_pool/application/release_new.py", b"new"))
    )


def test_oauth_browser_upgrade_runtime_reads_raw_prisma_and_complete_manager_sources(tmp_path: Path) -> None:
    from account_pool.release_compatibility import manager_source_digest

    command: Final = InspectionCommands()
    runtime: Final = DockerReleaseRuntime(
        ReleaseSettings.model_validate({"token": "t" * 32, "root": tmp_path, "deployment": tmp_path.parent / "deploy"}),
        command,
    )
    pair: Final = DockerReleaseRuntime(runtime.settings, Commands()).current()
    pool: Final = next(image for image in pair.images if image.service == "account-pool")
    sources: Final = runtime._upgrade_sources(pool)
    assert sources == (
        ("account_pool/repository.py", b'SQL = "CREATE TABLE old_table (id uuid)"\n'),
        ("account_pool/extra.sql", b"SELECT 1;\n"),
        ("account_pool/release_runtime.py", b"worker-source"),
    )
    evidence: Final = runtime._oauth_browser_schema_evidence(pair)
    assert evidence.prisma == b"model A {\r\n id String @id\r\n}\r\n"
    assert evidence.pool == b"CREATE TABLE old_table (id uuid)"
    assert evidence.sources == manager_source_digest(sources)
    assert evidence.startup
    assert not runtime.is_oauth_browser_schema_upgrade(pair, pair)
    assert not any(call[0] in ("start", "run", "exec") for call in command.calls)
    assert all("--network" in call and "none" in call for call in command.calls if call[0] == "create")
