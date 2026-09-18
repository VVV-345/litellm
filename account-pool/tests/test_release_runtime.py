"""本文件验证 Docker 命令边界及 Compose 插值，应用归档不能触发拉取或构建。"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Final

import pytest
from account_pool.release_compatibility import digest_sources
from account_pool.release_models import ReleasePair
from account_pool.release_runtime import (
    ContainerInspection,
    DockerReleaseRuntime,
    ReleaseSettings,
    image_pair,
    snapshot_container,
)


def schema_contract(source: str) -> bytes:
    data: Final = source.encode()
    output: Final = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member: Final = tarfile.TarInfo("account_pool/settings.py")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    with tarfile.open(fileobj=io.BytesIO(output.getvalue())) as archive:
        return DockerReleaseRuntime._schema_member(archive, archive.getmembers()[0])


@pytest.mark.parametrize(
    "changed",
    (
        "class Settings(BaseModel):\n    full_log_skip_failed: bool = False\n",
        'class Settings(BaseModel):\n    state: Literal["pending", "standard"] = "pending"\n',
    ),
)
def test_persisted_json_contract_changes_block_image_only_rollback(changed: str) -> None:
    old: Final = 'class Settings(BaseModel):\n    state: Literal["pending"] = "pending"\n'
    assert schema_contract(old) != schema_contract(changed)


def test_contract_fingerprint_ignores_method_bodies_and_formatting() -> None:
    old: Final = "class Settings(BaseModel):\n    enabled: bool = True\n    def ready(self): return True\n"
    changed: Final = "class Settings(BaseModel):\n    enabled: bool=True\n    def ready(self): return False\n"
    assert schema_contract(old) == schema_contract(changed)


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
