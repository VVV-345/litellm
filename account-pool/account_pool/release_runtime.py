"""本模块通过受限 Docker TCP 代理导出和恢复配套镜像，应用备份时禁止拉取或构建。"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from functools import lru_cache
from pathlib import Path
from threading import Timer
from typing import Final, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, JsonValue, SecretStr, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from account_pool.config import Settings
from account_pool.release_compatibility import (
    FEATURES,
    STARTUP_SOURCES,
    OAuthBrowserSchemaEvidence,
    RollbackEvidence,
    credential_contract,
    additive_manager_schema_upgrade,
    digest_sources,
    forward_compatible_manager_upgrade,
    manager_source_digest,
    oauth_browser_schema_upgrade,
    schema_member_contract,
)
from account_pool.release_models import ReleaseCandidate, ReleaseImage, ReleasePair
from account_pool.release_store import ReleaseDownloadError, ReleaseError, write_private

REPOSITORIES: Final = {"litellm": "ghcr.io/vvv-345/litellm", "account-pool": "ghcr.io/vvv-345/account-pool-manager"}


class ReleaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACCOUNT_POOL_RELEASE_", frozen=True)
    root: Path = Path("/var/lib/litellm-releases")
    deployment: Path = Path("/opt/litellm")
    project: str = Field(default="litellm", pattern=r"^[a-z0-9][a-z0-9_-]*$")
    token: SecretStr = Field(min_length=32)
    docker_host: str = "tcp://docker-socket-proxy:2375"
    reserve_bytes: int = Field(default=1024 * 1024 * 1024, ge=0)
    initialize_backups: bool = True
    database_backups: bool = False
    skip_compatibility_checks: bool = False

    @field_validator("root", "deployment")
    @classmethod
    def absolute_directory(cls, value: Path) -> Path:
        if not value.is_absolute() or value.parent == value:
            raise ValueError("must be a dedicated absolute directory")
        return value

    @model_validator(mode="after")
    def separate_directories(self) -> ReleaseSettings:
        if self.root.resolve().is_relative_to(self.deployment.resolve()) or self.deployment.resolve().is_relative_to(
            self.root.resolve()
        ):
            raise ValueError("backup and deployment directories must not overlap")
        return self

    @field_validator("docker_host")
    @classmethod
    def docker_endpoint(cls, value: str) -> str:
        if value.strip() == "tcp://release-docker-socket-proxy:2375":
            return value.strip()
        return Settings.validate_docker_host(value)


class ImageInspection(BaseModel):
    id: str = Field(alias="Id")
    size: int = Field(default=0, alias="Size")
    digests: tuple[str, ...] | None = Field(default=None, alias="RepoDigests")
    tags: tuple[str, ...] | None = Field(default=None, alias="RepoTags")
    config: dict[str, JsonValue] = Field(alias="Config")


class ContainerInspection(BaseModel):
    name: str = Field(default="", alias="Name")
    image: str = Field(alias="Image")
    config: dict[str, JsonValue] = Field(alias="Config")
    mounts: tuple[MountInspection, ...] = Field(default=(), alias="Mounts")
    host: dict[str, JsonValue] = Field(default_factory=dict, alias="HostConfig")


class MountInspection(BaseModel):
    type: str = Field(alias="Type")
    source: str = Field(default="", alias="Source")
    destination: str = Field(alias="Destination")
    name: str = Field(default="", alias="Name")
    writable: bool = Field(alias="RW")


class PortBinding(BaseModel):
    host_ip: str = Field(alias="HostIp")
    host_port: str = Field(alias="HostPort")


def snapshot_container(service: dict[str, JsonValue], container: ContainerInspection) -> dict[str, JsonValue]:
    if any(mount.type not in ("bind", "volume", "tmpfs") for mount in container.mounts):
        raise ReleaseError("存在无法归档的容器挂载类型")
    ports: Final = TypeAdapter(dict[str, tuple[PortBinding, ...] | None]).validate_python(
        container.host.get("PortBindings") or {}
    )
    return {
        **service,
        "image": container.image,
        "environment": {
            key: value
            for raw in TypeAdapter(tuple[str, ...]).validate_python(container.config.get("Env") or ())
            for key, _, value in (raw.partition("="),)
        },
        "command": container.config.get("Cmd"),
        "entrypoint": container.config.get("Entrypoint"),
        "user": container.config.get("User", ""),
        "working_dir": container.config.get("WorkingDir", ""),
        "read_only": container.host.get("ReadonlyRootfs", False),
        "volumes": [
            {
                "type": mount.type,
                "source": mount.name if mount.type == "volume" else mount.source,
                "target": mount.destination,
                "read_only": not mount.writable,
            }
            for mount in container.mounts
            if mount.type != "tmpfs"
        ],
        "ports": [
            {
                "target": int(port.partition("/")[0]),
                "protocol": port.partition("/")[2],
                "host_ip": binding.host_ip,
                "published": binding.host_port,
            }
            for port, bindings in ports.items()
            for binding in bindings or ()
        ],
    }


class DockerCommand(Protocol):
    def __call__(self, *args: str, timeout: int = 120) -> bytes: ...


def image_pair(images: tuple[ReleaseImage, ReleaseImage]) -> ReleasePair:
    if {image.service for image in images} != set(REPOSITORIES) or images[0].revision != images[1].revision:
        raise ReleaseError("两个服务的 Git commit 不一致，无法作为配套版本")
    ordered: Final = sorted(images, key=lambda image: image.service)
    identifier: Final = hashlib.sha256("|".join(image.image_id for image in ordered).encode()).hexdigest()[:24]
    return ReleasePair(id=identifier, commit=images[0].revision, images=images)


class DockerReleaseRuntime:
    def __init__(self, settings: ReleaseSettings, command: DockerCommand | None = None) -> None:
        self.settings: Final = settings
        self.command: Final = command

    def run(self, *args: str, timeout: int = 120) -> bytes:
        if self.command is not None:
            return self.command(*args, timeout=timeout)
        try:
            result: Final = subprocess.run(
                ("docker", *args),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "DOCKER_HOST": self.settings.docker_host},
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReleaseError("Docker 操作超时或不可用，请检查部署管理服务") from error
        if result.returncode:
            raise ReleaseError("Docker 操作失败，请检查镜像、磁盘和服务状态")
        return result.stdout

    def inspect_image(self, reference: str, service: str) -> ReleaseImage:
        image: Final = TypeAdapter(tuple[ImageInspection, ...]).validate_json(self.run("image", "inspect", reference))[
            0
        ]
        labels: Final = TypeAdapter(dict[str, str]).validate_python(image.config.get("Labels") or {})
        revision: Final = labels.get("org.opencontainers.image.revision", "")
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ReleaseError("镜像缺少完整 Git commit 标签，不能识别源码版本")
        if service not in REPOSITORIES:
            raise ReleaseError("不支持的项目服务")
        return ReleaseImage.model_validate(
            {
                "service": service,
                "image_id": image.id,
                "revision": revision,
                "size": image.size,
                "repository": REPOSITORIES[service],
                "digests": image.digests or (),
            }
        )

    def containers(self) -> tuple[ContainerInspection, ContainerInspection]:
        ids: Final = tuple(
            self.run(
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={self.settings.project}",
                "--filter",
                f"label=com.docker.compose.service={service}",
            )
            .decode()
            .strip()
            for service in REPOSITORIES
        )
        if any(not re.fullmatch(r"[a-f0-9]{12,64}", identifier) for identifier in ids):
            raise ReleaseError("未找到唯一的 LiteLLM 和 Manager 容器，请检查项目名配置")
        return TypeAdapter(tuple[ContainerInspection, ContainerInspection]).validate_json(self.run("inspect", *ids))

    def current(self) -> ReleasePair:
        first, second = self.containers()
        return image_pair(
            (self.inspect_image(first.image, "litellm"), self.inspect_image(second.image, "account-pool"))
        )

    def discover(self) -> tuple[ReleasePair, ...]:
        references: Final = tuple(
            (service, line)
            for service, repository in REPOSITORIES.items()
            for source in (repository, "litellm-backup/" + service)
            for line in self.run("image", "ls", source, "--quiet", "--no-trunc").decode().splitlines()
            if re.fullmatch(r"sha256:[a-f0-9]{64}", line)
        )
        images: Final = tuple(
            self.inspect_image(reference, service) for service, reference in dict.fromkeys(references)
        )
        commits: Final = tuple(dict.fromkeys(image.revision for image in images))
        pairs: Final = tuple(
            image_pair((first, second))
            for commit in commits
            for first in images
            if first.revision == commit and first.service == "litellm"
            for second in images
            if second.revision == commit and second.service == "account-pool"
        )
        return tuple({pair.id: pair for pair in pairs}.values())

    def image_users(self, pair: ReleasePair) -> tuple[str, ...]:
        identifiers: Final = self.run("ps", "-aq").decode().split()
        if not identifiers:
            return ()
        containers: Final = TypeAdapter(tuple[ContainerInspection, ...]).validate_json(
            self.run("inspect", *identifiers)
        )
        return tuple(
            container.name or container.image
            for container in containers
            if container.image in {image.image_id for image in pair.images}
        )

    def _removal_references(self, image: ReleaseImage) -> tuple[str, ...]:
        inspected: Final = TypeAdapter(tuple[ImageInspection, ...]).validate_json(
            self.run("image", "inspect", image.image_id)
        )[0]
        actual: Final = self.inspect_image(image.image_id, image.service)
        if actual.image_id != image.image_id or actual.revision != image.revision:
            raise ReleaseError("镜像身份已变化，请重新扫描")
        references: Final = tuple(dict.fromkeys((*(inspected.tags or ()), *(inspected.digests or ()))))
        if any(
            not reference.startswith(
                (
                    REPOSITORIES[image.service] + ":",
                    REPOSITORIES[image.service] + "@sha256:",
                    "litellm-backup/" + image.service + ":",
                )
            )
            for reference in references
        ):
            raise ReleaseError("镜像还被其他仓库标签引用，不能删除")
        return inspected.tags or inspected.digests or (image.image_id,)

    def remove_images(self, pair: ReleasePair) -> None:
        if self.image_users(pair):
            raise ReleaseError("镜像被容器引用，不能删除")
        references: Final = tuple(self._removal_references(image) for image in pair.images)
        for image, expected in zip(pair.images, references, strict=True):
            if self.image_users(pair) or self._removal_references(image) != expected:
                raise ReleaseError("镜像引用已变化，请重新扫描")
            self.run("image", "rm", *expected)
            if image.image_id in self.run("image", "ls", "--quiet", "--no-trunc").decode().split():
                if self.image_users(pair):
                    raise ReleaseError("镜像被容器引用，不能删除")
                self._removal_references(image)
                self.run("image", "rm", image.image_id)
            if image.image_id in self.run("image", "ls", "--quiet", "--no-trunc").decode().split():
                raise ReleaseError("镜像仍有引用，未完全删除，请重新扫描")

    def compose(self) -> bytes:
        raw: Final = self.run(
            "compose",
            "--project-directory",
            str(self.settings.deployment),
            "--project-name",
            self.settings.project,
            "-f",
            str(self.settings.deployment / "docker-compose.yml"),
            "config",
            "--format",
            "json",
        )
        config: Final = TypeAdapter(dict[str, JsonValue]).validate_json(raw)
        services: Final = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(config.get("services"))
        if any(service not in services for service in REPOSITORIES):
            raise ReleaseError("部署配置缺少配套服务")
        # 只替换两个业务服务；卷和网络沿用同一 Compose 项目，不执行 down 或删除卷。
        reduced: Final = {
            **config,
            "services": {
                service: {key: value for key, value in services[service].items() if key != "depends_on"}
                for service in REPOSITORIES
            },
        }
        return json.dumps(reduced, ensure_ascii=False).encode()

    def running_compose(self) -> bytes:
        current: Final = self.current()
        active: Final = self.settings.root / "active-compose.json"
        active_pair: Final = self.settings.root / "active-pair.json"
        captured: Final = (
            active.exists()
            and active_pair.exists()
            and ReleasePair.model_validate_json(active_pair.read_bytes()).id == current.id
        )
        template: Final = TypeAdapter(dict[str, JsonValue]).validate_json(
            active.read_bytes() if captured else self.compose()
        )
        services: Final = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(template["services"])
        running: Final = self.containers()
        # 首次接管时按容器实际环境变量和启动参数留存，避免 .env 已切到新版本而备份错配。
        snapshots: Final = {
            service: snapshot_container(services[service], container)
            for service, container in zip(REPOSITORIES, running, strict=True)
        }
        volumes: Final = {
            mount.name: {"external": True, "name": mount.name}
            for container in running
            for mount in container.mounts
            if mount.type == "volume"
        }
        return json.dumps({**template, "services": snapshots, "volumes": volumes}, ensure_ascii=False).encode()

    def export(self, pair: ReleasePair, destination: Path) -> None:
        process: Final = subprocess.Popen(
            ("docker", "image", "save", *(image.image_id for image in pair.images)),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "DOCKER_HOST": self.settings.docker_host},
        )
        deadline: Final = Timer(3600, process.kill)
        deadline.start()
        try:
            if process.stdout is None:
                raise ReleaseError("镜像导出管道不可用")
            with destination.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=3) as output:
                destination.chmod(0o600)
                shutil.copyfileobj(process.stdout, output, length=1024 * 1024)
                output.close()
                raw.flush()
                os.fsync(raw.fileno())
            if process.wait(timeout=3600):
                raise ReleaseError("镜像备份失败，未切换版本")
        finally:
            deadline.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout:
                process.stdout.close()

    @lru_cache(maxsize=24)
    def fingerprint(self, pair: ReleasePair) -> str:
        return hashlib.sha256(
            b"\n".join(self._schema(image) for image in sorted(pair.images, key=lambda item: item.service))
        ).hexdigest()

    def is_oauth_browser_schema_upgrade(self, current: ReleasePair, target: ReleasePair) -> bool:
        return oauth_browser_schema_upgrade(
            self._oauth_browser_schema_evidence(current), self._oauth_browser_schema_evidence(target)
        )

    def is_additive_manager_schema_upgrade(self, current: ReleasePair, target: ReleasePair) -> bool:
        return additive_manager_schema_upgrade(self._upgrade_sources(current), self._upgrade_sources(target))

    def is_forward_compatible_upgrade(self, current: ReleasePair, target: ReleasePair) -> bool:
        current_pool: Final = next(image for image in current.images if image.service == "account-pool")
        target_pool: Final = next(image for image in target.images if image.service == "account-pool")
        current_proxy: Final = next(image for image in current.images if image.service == "litellm")
        target_proxy: Final = next(image for image in target.images if image.service == "litellm")
        current_manager_sources: Final = self._upgrade_sources(current_pool)
        target_manager_sources: Final = self._upgrade_sources(target_pool)
        current_prisma: Final = tuple(
            (name, source) for name, source in self._upgrade_sources(current_proxy) if name == "schema.prisma"
        )
        target_prisma: Final = tuple(
            (name, source) for name, source in self._upgrade_sources(target_proxy) if name == "schema.prisma"
        )
        return forward_compatible_manager_upgrade(
            (*current_manager_sources, *current_prisma),
            (*target_manager_sources, *target_prisma),
        )

    @lru_cache(maxsize=24)
    def _oauth_browser_schema_evidence(self, pair: ReleasePair) -> OAuthBrowserSchemaEvidence:
        proxy: Final = next(image for image in pair.images if image.service == "litellm")
        pool: Final = next(image for image in pair.images if image.service == "account-pool")
        prisma: Final = self._upgrade_sources(proxy)
        return OAuthBrowserSchemaEvidence(
            prisma=prisma[0][1] if len(prisma) == 1 and prisma[0][0] == "schema.prisma" else b"",
            pool=self._schema(pool),
            sources=manager_source_digest(self._upgrade_sources(pool)),
            startup=digest_sources(self._inspection_sources(proxy), STARTUP_SOURCES),
        )

    @lru_cache(maxsize=24)
    def evidence(self, pair: ReleasePair) -> RollbackEvidence:
        proxy: Final = next(image for image in pair.images if image.service == "litellm")
        pool: Final = next(image for image in pair.images if image.service == "account-pool")
        proxy_sources: Final = self._inspection_sources(proxy)
        pool_sources: Final = self._inspection_sources(pool)
        return RollbackEvidence(
            database=hashlib.sha256(self._schema(proxy)).hexdigest(),
            pool=hashlib.sha256(self._schema(pool)).hexdigest(),
            credentials=credential_contract(pool_sources, proxy_sources),
            logs=digest_sources(proxy_sources, ("account_pool_full_logs.py",)),
            startup=digest_sources(proxy_sources, STARTUP_SOURCES),
            features=tuple(
                title
                for filename, (title, marker) in FEATURES.items()
                if filename in proxy_sources and marker.encode() in proxy_sources[filename]
            ),
        )

    def _inspection_sources(self, image: ReleaseImage) -> dict[str, bytes]:
        name: Final = "litellm-release-inspect-" + uuid4().hex
        self.run("create", "--name", name, "--network", "none", "--entrypoint", "/bin/true", image.image_id)
        try:
            if image.service == "account-pool":
                return {
                    **self._read_sources(name, "/app/account_pool"),
                    "credential-client": self._read_sources(
                        name, "/app/account_pool/channels/cliproxyapi/client.py"
                    ).get("client.py", b""),
                }
            venv: Final = self._read_sources(name, "/app/.venv/pyvenv.cfg")
            version: Final = re.search(rb"(?m)^version(?:_info)?\s*=\s*(3\.\d+)", venv.get("pyvenv.cfg", b""))
            if version is None:
                return {}
            packages: Final = "/app/.venv/lib/python" + version[1].decode("ascii") + "/site-packages/"
            return {
                **self._read_sources(name, packages + "litellm/proxy/management_endpoints"),
                **self._read_sources(name, packages + "litellm/proxy/management_helpers/virtual_key_secret.py"),
                **self._read_sources(name, packages + "litellm/proxy/common_utils/encrypt_decrypt_utils.py"),
                **self._read_sources(name, packages + "litellm/proxy/proxy_cli.py"),
                **self._read_sources(name, packages + "litellm_proxy_extras/utils.py"),
                **self._read_sources(name, "/app/docker/prod_entrypoint.sh"),
                "migration-history": self._migration_history(name, packages + "litellm_proxy_extras/migrations"),
            }
        finally:
            self.run("rm", "-v", name)

    def _migration_history(self, container: str, path: str) -> bytes:
        try:
            content: Final = self.run("cp", f"{container}:{path}", "-", timeout=60)
        except ReleaseError:
            return b""
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            return b"\n".join(
                sorted(
                    member.name.encode() + b":" + hashlib.sha256(stream.read()).hexdigest().encode()
                    for member in archive.getmembers()
                    if member.isfile() and member.name.endswith(".sql") and member.size < 8 * 1024 * 1024
                    for stream in (archive.extractfile(member),)
                    if stream is not None
                )
            )

    def _read_sources(self, container: str, path: str) -> dict[str, bytes]:
        try:
            content: Final = self.run("cp", f"{container}:{path}", "-", timeout=60)
        except ReleaseError:
            return {}
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            names: Final = tuple(member.name for member in archive.getmembers() if member.isfile())
            return {
                (
                    Path(member.name).name
                    if sum(Path(name).name == Path(member.name).name for name in names) == 1
                    else member.name
                ): stream.read()
                for member in archive.getmembers()
                if member.isfile() and member.size <= 8 * 1024 * 1024 and member.name.endswith((".py", ".cfg", ".sh"))
                for stream in (archive.extractfile(member),)
                if stream is not None
            }

    def _upgrade_sources(self, image: ReleaseImage) -> tuple[tuple[str, bytes], ...]:
        name: Final = "litellm-release-inspect-" + uuid4().hex
        self.run("create", "--name", name, "--network", "none", "--entrypoint", "/bin/true", image.image_id)
        try:
            source: Final = "/app/schema.prisma" if image.service == "litellm" else "/app/account_pool"
            content: Final = self.run("cp", f"{name}:{source}", "-", timeout=120)
            with tarfile.open(fileobj=io.BytesIO(content)) as archive:
                members: Final = archive.getmembers()
                if any(not (member.isfile() or member.isdir()) or member.size > 8 * 1024 * 1024 for member in members):
                    raise ReleaseError("无法完整核对受控升级源码")
                return tuple(
                    (member.name, stream.read())
                    for member in members
                    if member.isfile()
                    for stream in (archive.extractfile(member),)
                    if stream is not None
                )
        finally:
            self.run("rm", "-v", name)

    def _schema(self, image: ReleaseImage) -> bytes:
        name: Final = "litellm-release-inspect-" + uuid4().hex
        self.run("create", "--name", name, "--network", "none", "--entrypoint", "/bin/true", image.image_id)
        try:
            source: Final = "/app/schema.prisma" if image.service == "litellm" else "/app/account_pool"
            content: Final = self.run("cp", f"{name}:{source}", "-", timeout=120)
            with tarfile.open(fileobj=io.BytesIO(content)) as archive:
                entries: Final = tuple(
                    member
                    for member in archive.getmembers()
                    if member.isfile() and member.name.endswith((".prisma", ".py"))
                )
                bodies: Final = tuple(self._schema_member(archive, member) for member in entries)
            if not any(bodies):
                raise ReleaseError("无法核对镜像数据库结构")
            return b"\n".join(sorted(part for body in bodies for part in body.splitlines() if part))
        finally:
            self.run("rm", "-v", name)

    @staticmethod
    def _schema_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
        if member.size > 8 * 1024 * 1024:
            raise ReleaseError("镜像结构文件过大")
        stream: Final = archive.extractfile(member)
        if stream is None:
            return b""
        raw: Final = stream.read()
        return schema_member_contract(member.name, raw)

    def load(self, pair: ReleasePair, archive: Path) -> None:
        self.run("image", "load", "--input", str(archive), timeout=3600)
        for image in pair.images:
            if self.inspect_image(image.image_id, image.service).revision != pair.commit:
                raise ReleaseError("导入镜像与备份记录不一致")

    def apply(self, pair: ReleasePair, configuration: bytes) -> None:
        config: Final = TypeAdapter(dict[str, JsonValue]).validate_json(configuration)
        services: Final = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(config["services"])
        for image in pair.images:
            self.run("image", "tag", image.image_id, f"litellm-backup/{image.service}:{pair.id}")
        resolved: Final = {
            **config,
            "services": {
                image.service: {
                    **services[image.service],
                    "image": f"litellm-backup/{image.service}:{pair.id}",
                    "pull_policy": "never",
                }
                for image in pair.images
            },
        }
        path: Final = self.settings.root / "switch-compose.json"
        # Compose 会再次插值已解析的配置，执行文件需转义字面量美元符号。
        write_private(path, json.dumps(resolved, ensure_ascii=False).replace("$", "$$").encode())
        self.run(
            "compose",
            "--project-directory",
            str(self.settings.deployment),
            "--project-name",
            self.settings.project,
            "-f",
            str(path),
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "--pull",
            "never",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "180",
            *REPOSITORIES,
            timeout=240,
        )
        if self.current().id != pair.id:
            raise ReleaseError("服务运行的镜像与目标版本不一致")
        self.check_health(pair)
        write_private(self.settings.root / "active-compose.json", json.dumps(resolved, ensure_ascii=False).encode())
        write_private(self.settings.root / "active-pair.json", pair.model_dump_json().encode())

    def pull(self, tag: str) -> ReleasePair:
        if not re.fullmatch(r"[a-f0-9]{10,40}", tag):
            raise ReleaseError("新版本必须使用 Git commit 标签")
        for repository in REPOSITORIES.values():
            self.run("image", "pull", f"{repository}:{tag}", timeout=1800)
        pair: Final = image_pair(
            (
                self.inspect_image(f"{REPOSITORIES['litellm']}:{tag}", "litellm"),
                self.inspect_image(f"{REPOSITORIES['account-pool']}:{tag}", "account-pool"),
            )
        )
        if not pair.commit.startswith(tag):
            raise ReleaseError("拉取镜像的 commit 与标签不一致")
        return pair

    def pull_candidate(self, candidate: ReleaseCandidate) -> ReleasePair:
        if {image.service for image in candidate.images} != set(REPOSITORIES):
            raise ReleaseError("远程镜像必须包含两个不同的配套服务")
        images: Final = tuple(self._pull_pinned(candidate, index) for index in range(2))
        return image_pair((images[0], images[1]))

    def _pull_pinned(self, candidate: ReleaseCandidate, index: int) -> ReleaseImage:
        selected: Final = candidate.images[index]
        reference: Final = f"{REPOSITORIES[selected.service]}@{selected.digest}"
        try:
            self.run("image", "pull", reference, timeout=1800)
        except ReleaseError as error:
            raise ReleaseDownloadError("镜像下载失败，旧服务保持不变") from error
        actual: Final = self.inspect_image(reference, selected.service)
        if actual.image_id not in (selected.image_id, selected.digest) or actual.revision != candidate.commit:
            raise ReleaseError("拉取后的镜像身份与检查结果不一致，未替换服务")
        return actual

    def check_health(self, pair: ReleasePair) -> None:
        for service in REPOSITORIES:
            self._check_service_health(service)
        if self.current().id != pair.id:
            raise ReleaseError("健康检查期间运行镜像发生变化")

    def _check_service_health(self, service: str) -> None:
        identifier: Final = (
            self.run(
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={self.settings.project}",
                "--filter",
                f"label=com.docker.compose.service={service}",
            )
            .decode()
            .strip()
        )
        if not re.fullmatch(r"[a-f0-9]{12,64}", identifier):
            raise ReleaseError("无法找到唯一的业务容器进行就绪检查")
        # 使用容器自身的内部凭据，只有退出状态返回发布器；不打印账号列表或令牌。
        request: Final = (
            "urllib.request.Request('http://127.0.0.1:4000/health/readiness')"
            if service == "litellm"
            else "urllib.request.Request('http://127.0.0.1:8091/api/environments', "
            "headers={'Authorization':'Bearer '+os.environ['ACCOUNT_POOL_MANAGER_TOKEN']})"
        )
        validation: Final = (
            "assert result.get('db') == 'connected' and result.get('status') == 'healthy'"
            if service == "litellm"
            else "assert isinstance(result, list)"
        )
        script: Final = (
            "import json,os,time,urllib.request\n"
            "deadline=time.monotonic()+60\n"
            "while True:\n"
            " try:\n"
            f"  with urllib.request.urlopen({request},timeout=5) as response: result=json.load(response)\n"
            f"  {validation}\n"
            "  break\n"
            " except Exception:\n"
            "  if time.monotonic()>=deadline: raise SystemExit(1)\n"
            "  time.sleep(2)\n"
        )
        self.run("exec", identifier, "python", "-c", script, timeout=75)
