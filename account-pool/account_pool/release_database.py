"""本模块通过项目内 PostgreSQL 容器备份与恢复两库，不管理外部数据库或认证文件。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from pydantic import BaseModel, Field, TypeAdapter

from account_pool.release_models import DatabaseDump, DatabaseSnapshot
from account_pool.release_runtime import DockerReleaseRuntime
from account_pool.release_store import ReleaseError, file_hash

DATABASES: Final = {"db": ("litellm", "DATABASE_URL"), "account-pool-db": ("account-pool", "ACCOUNT_POOL_DATABASE_URL")}


class DatabaseTarget(BaseModel):
    service: str
    container: str
    database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    major: int


class DatabaseTransfer(Protocol):
    def __call__(self, args: tuple[str, ...], path: Path, *, exporting: bool) -> None: ...


class ReleaseDatabase(Protocol):
    def preflight(self) -> None: ...
    def stop(self) -> None: ...
    def start(self) -> None: ...
    def capture(self, directory: Path, version_id: str) -> DatabaseSnapshot: ...
    def verify(self, directory: Path, snapshot: DatabaseSnapshot) -> None: ...
    def restore(self, directory: Path, snapshot: DatabaseSnapshot) -> None: ...


class DockerReleaseDatabase:
    def __init__(self, runtime: DockerReleaseRuntime, transfer: DatabaseTransfer | None = None) -> None:
        self.runtime: Final = runtime
        self.transfer: Final = transfer or self._transfer

    def container(self, service: str) -> str:
        identifier: Final = (
            self.runtime.run(
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={self.runtime.settings.project}",
                "--filter",
                f"label=com.docker.compose.service={service}",
            )
            .decode()
            .strip()
        )
        if not re.fullmatch(r"[a-f0-9]{12,64}", identifier):
            raise ReleaseError("数据库备份需要项目内唯一的业务及 PostgreSQL 容器")
        return identifier

    def preflight(self) -> None:
        # 停业务前确认两库身份和 EXEC 权限，避免权限不足导致无谓停机。
        self.targets()

    def stop(self) -> None:
        self.preflight()
        self.runtime.run(
            "stop", "--time", "60", *(self.container(service) for service, _ in DATABASES.values()), timeout=150
        )
        for service in DATABASES:
            self.runtime.run(
                "exec",
                self.container(service),
                "sh",
                "-ec",
                'psql -X -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "$1"',
                "sh",
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = 'litellm-release-worker' AND pid <> pg_backend_pid()",
            )

    def start(self) -> None:
        self.runtime.run("start", *(self.container(service) for service, _ in DATABASES.values()))

    def targets(self) -> tuple[DatabaseTarget, ...]:
        containers: Final = self.runtime.containers()
        return tuple(
            self._target(
                service,
                {
                    key: value
                    for raw in TypeAdapter(tuple[str, ...]).validate_python(container.config.get("Env") or ())
                    for key, _, value in (raw.partition("="),)
                },
                variable,
            )
            for (service, (_, variable)), container in zip(DATABASES.items(), containers, strict=True)
        )

    def _target(self, service: str, environment: dict[str, str], variable: str) -> DatabaseTarget:
        url: Final = urlsplit(environment.get(variable, ""))
        container: Final = self.container(service)
        raw: Final = (
            self.runtime.run(
                "exec",
                container,
                "sh",
                "-ec",
                'printf "%s\\n%s\\n" "$POSTGRES_DB" "$POSTGRES_USER"; psql -X -U "$POSTGRES_USER" -d postgres -Atqc "SHOW server_version_num"',
            )
            .decode()
            .splitlines()
        )
        if (
            len(raw) != 3
            or not raw[2].isdigit()
            or url.scheme not in ("postgres", "postgresql")
            or url.hostname != service
            or url.port not in (None, 5432)
            or unquote(url.path.removeprefix("/")) != raw[0]
            or unquote(url.username or "") != raw[1]
        ):
            raise ReleaseError("数据库连接与项目内 PostgreSQL 不匹配，外部或自定义数据库需单独恢复方案")
        if raw[0] in ("postgres", "template0", "template1"):
            raise ReleaseError("不能备份或覆盖 PostgreSQL 系统数据库")
        return DatabaseTarget(service=service, container=container, database=raw[0], major=int(raw[2]) // 10000)

    def _transfer(self, args: tuple[str, ...], path: Path, *, exporting: bool) -> None:
        try:
            with path.open("wb" if exporting else "rb") as stream:
                if exporting:
                    path.chmod(0o600)
                result: Final = subprocess.run(
                    ("docker", *args),
                    stdin=subprocess.DEVNULL if exporting else stream,
                    stdout=stream if exporting else subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env={**os.environ, "DOCKER_HOST": self.runtime.settings.docker_host},
                    timeout=3600,
                    check=False,
                )
                if exporting:
                    stream.flush()
                    os.fsync(stream.fileno())
            if result.returncode:
                raise ReleaseError("PostgreSQL 导出或恢复失败；保留原快照，检查数据库权限和磁盘")
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReleaseError("PostgreSQL 备份或恢复超时/不可用；请检查部署后台") from error

    def capture(self, directory: Path, version_id: str) -> DatabaseSnapshot:
        targets: Final = self.targets()
        identifier: Final = uuid4().hex
        required: Final = sum(
            int(
                self.runtime.run(
                    "exec",
                    target.container,
                    "sh",
                    "-ec",
                    'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT pg_database_size(current_database())"',
                )
            )
            for target in targets
        )
        if shutil.disk_usage(directory).free < required + self.runtime.settings.reserve_bytes:
            raise ReleaseError("数据库快照空间不足，保留旧快照")
        created: Final = time.time()
        files: Final = tuple(self._dump(target, directory, identifier) for target in targets)
        snapshot: Final = DatabaseSnapshot(id=identifier, version_id=version_id, created_at=created, files=files)
        self.verify(directory, snapshot)
        for target, item in zip(targets, files, strict=True):
            self._validate_restore(target, directory / item.filename)
        return snapshot

    def _dump(self, target: DatabaseTarget, directory: Path, identifier: str) -> DatabaseDump:
        name: Final = f"database-{identifier}-{target.service}.dump"
        destination: Final = directory / name
        self.transfer(
            (
                "exec",
                target.container,
                "sh",
                "-ec",
                'export PGAPPNAME=litellm-release-worker; exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --create',
            ),
            destination,
            exporting=True,
        )
        return DatabaseDump(
            service=target.service,
            database=target.database,
            major=target.major,
            filename=name,
            sha256=file_hash(destination),
            size=destination.stat().st_size,
        )

    def verify(self, directory: Path, snapshot: DatabaseSnapshot) -> None:
        if {item.service for item in snapshot.files} != set(DATABASES):
            raise ReleaseError("快照必须包含两份不同的数据库")
        targets: Final = {item.service: item for item in self.targets()}
        for item in snapshot.files:
            path: Final = directory / item.filename
            target: Final = targets[item.service]
            if item.filename != f"database-{snapshot.id}-{item.service}.dump":
                raise ReleaseError("快照文件与记录不一致")
            if target.database != item.database or target.major != item.major:
                raise ReleaseError("数据库名称或 PostgreSQL 主版本已变化，禁止直接恢复")
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != item.size
                or file_hash(path) != item.sha256
            ):
                raise ReleaseError("数据库快照缺失或校验失败，未恢复数据库")

    def _validate_restore(self, target: DatabaseTarget, path: Path) -> None:
        temporary: Final = "release_check_" + uuid4().hex
        self.runtime.run(
            "exec",
            target.container,
            "sh",
            "-ec",
            'exec createdb -U "$POSTGRES_USER" -T template0 "$1"',
            "sh",
            temporary,
        )
        try:
            self.transfer(
                (
                    "exec",
                    "-i",
                    target.container,
                    "sh",
                    "-ec",
                    'export PGAPPNAME=litellm-release-worker; exec pg_restore -U "$POSTGRES_USER" -d "$1" --exit-on-error --single-transaction',
                    "sh",
                    temporary,
                ),
                path,
                exporting=False,
            )
        finally:
            self.runtime.run(
                "exec",
                target.container,
                "sh",
                "-ec",
                'exec dropdb -U "$POSTGRES_USER" --if-exists "$1"',
                "sh",
                temporary,
            )

    def restore(self, directory: Path, snapshot: DatabaseSnapshot) -> None:
        self.verify(directory, snapshot)
        targets: Final = {item.service: item for item in self.targets()}
        # 先在临时库恢复两份归档；任一失败都不得触碰正式库。正式恢复前业务必须停写。
        for item in snapshot.files:
            self._validate_restore(targets[item.service], directory / item.filename)
        for item in snapshot.files:
            self.transfer(
                (
                    "exec",
                    "-i",
                    targets[item.service].container,
                    "sh",
                    "-ec",
                    'export PGAPPNAME=litellm-release-worker; exec pg_restore -U "$POSTGRES_USER" -d postgres --clean --if-exists --create --exit-on-error',
                ),
                directory / item.filename,
                exporting=False,
            )
