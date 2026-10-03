"""本文件仅在显式启用时创建本地隔离 PostgreSQL 容器，验证真实导出及恢复并清理测试容器。"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
from account_pool.release_database import DockerReleaseDatabase
from account_pool.release_runtime import DockerReleaseRuntime, ReleaseSettings
from account_pool.release_store import ReleaseError
from pydantic import SecretStr

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_RELEASE_DATABASE_INTEGRATION") != "1", reason="explicit Docker opt-in required"
)


def docker(*args: str, timeout: int = 120) -> bytes:
    result = subprocess.run(("docker", *args), capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise ReleaseError(result.stderr.decode(errors="replace"))
    return result.stdout


def transfer(args: tuple[str, ...], path: Path, *, exporting: bool) -> None:
    with path.open("wb" if exporting else "rb") as stream:
        result = subprocess.run(
            ("docker", *args),
            stdout=stream if exporting else subprocess.DEVNULL,
            stdin=subprocess.DEVNULL if exporting else stream,
            stderr=subprocess.PIPE,
            timeout=90,
            check=False,
        )
    if result.returncode:
        raise ReleaseError(result.stderr.decode(errors="replace"))


@pytest.fixture
def databases(tmp_path):
    project = "release-test-" + uuid4().hex[:12]
    names = tuple(project + "-" + service for service in ("db", "account-pool-db", "litellm", "account-pool"))
    try:
        for service in ("db", "account-pool-db"):
            docker(
                "run",
                "-d",
                "--name",
                project + "-" + service,
                "--network",
                "none",
                "--label",
                "com.docker.compose.project=" + project,
                "--label",
                "com.docker.compose.service=" + service,
                "--tmpfs",
                "/var/lib/postgresql/data",
                "-e",
                "POSTGRES_PASSWORD=isolated-test-only",
                "-e",
                "POSTGRES_USER=release_test",
                "-e",
                "POSTGRES_DB=release_test",
                "postgres:16",
            )
            for attempt in range(50):
                try:
                    docker("exec", project + "-" + service, "pg_isready", "-U", "release_test", "-d", "release_test")
                    break
                except ReleaseError:
                    time.sleep(0.2)
            else:
                pytest.fail("isolated PostgreSQL did not become ready")
        for service, host, variable in (
            ("litellm", "db", "DATABASE_URL"),
            ("account-pool", "account-pool-db", "ACCOUNT_POOL_DATABASE_URL"),
        ):
            docker(
                "run",
                "-d",
                "--name",
                project + "-" + service,
                "--network",
                "none",
                "--label",
                "com.docker.compose.project=" + project,
                "--label",
                "com.docker.compose.service=" + service,
                "-e",
                f"{variable}=postgresql://release_test:isolated-test-only@{host}:5432/release_test",
                "alpine:3.20",
                "sh",
                "-c",
                "trap 'exit 0' TERM; while :; do sleep 1 & wait $!; done",
            )
        runtime = DockerReleaseRuntime(
            ReleaseSettings(
                root=tmp_path / "backups",
                deployment=tmp_path / "deployment",
                project=project,
                token=SecretStr("x" * 32),
                reserve_bytes=0,
            ),
            command=docker,
        )
        yield DockerReleaseDatabase(runtime, transfer), project
    finally:
        for name in names:
            subprocess.run(
                ("docker", "rm", "-f", "-v", name), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
            )


def sql(project: str, service: str, query: str) -> bytes:
    return docker(
        "exec",
        project + "-" + service,
        "psql",
        "-X",
        "-U",
        "release_test",
        "-d",
        "release_test",
        "-At",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        query,
    )


def test_postgres_pair_roundtrip_preserves_values_and_removes_new_schema(databases, tmp_path):
    database, project = databases
    for service in ("db", "account-pool-db"):
        sql(project, service, "CREATE TABLE marker (value text); INSERT INTO marker VALUES ('baseline');")
    database.stop()
    baseline = database.capture(tmp_path, "a" * 24)
    print(
        "BASELINE",
        [sql(project, service, "SELECT value FROM marker").decode().strip() for service in ("db", "account-pool-db")],
    )
    for service in ("db", "account-pool-db"):
        sql(project, service, "UPDATE marker SET value='modified'; CREATE TABLE new_schema_only (id int);")
    modified = database.capture(tmp_path, "a" * 24)
    print(
        "MODIFIED",
        [sql(project, service, "SELECT value FROM marker").decode().strip() for service in ("db", "account-pool-db")],
    )
    database.restore(tmp_path, baseline)
    print(
        "ROLLBACK",
        [sql(project, service, "SELECT value FROM marker").decode().strip() for service in ("db", "account-pool-db")],
    )
    for service in ("db", "account-pool-db"):
        assert sql(project, service, "SELECT value FROM marker").strip() == b"baseline"
        assert sql(project, service, "SELECT to_regclass('public.new_schema_only')").strip() == b""
    database.restore(tmp_path, modified)
    print(
        "REAPPLIED",
        [sql(project, service, "SELECT value FROM marker").decode().strip() for service in ("db", "account-pool-db")],
    )
    for service in ("db", "account-pool-db"):
        assert sql(project, service, "SELECT value FROM marker").strip() == b"modified"
    database.start()


def test_corrupt_second_dump_never_changes_first_database(databases, tmp_path):
    database, project = databases
    for service in ("db", "account-pool-db"):
        sql(project, service, "CREATE TABLE marker (value text); INSERT INTO marker VALUES ('baseline');")
    database.stop()
    snapshot = database.capture(tmp_path, "b" * 24)
    sql(project, "db", "UPDATE marker SET value='current'")
    (tmp_path / snapshot.files[1].filename).write_bytes(b"invalid")
    with pytest.raises(ReleaseError, match="校验失败"):
        database.restore(tmp_path, snapshot)
    assert sql(project, "db", "SELECT value FROM marker").strip() == b"current"
    invalid = snapshot.model_copy(
        update={
            "files": (
                snapshot.files[0],
                snapshot.files[1].model_copy(update={"sha256": hashlib.sha256(b"invalid").hexdigest(), "size": 7}),
            )
        }
    )
    with pytest.raises(ReleaseError):
        database.restore(tmp_path, invalid)
    assert sql(project, "db", "SELECT value FROM marker").strip() == b"current"
