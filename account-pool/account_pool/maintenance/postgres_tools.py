"""通过无 shell 子进程安全调用 pg_dump、pg_restore 校验和恢复。"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, Protocol, cast
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

ChunkConsumer = Callable[[bytes], None]
_PASSTHROUGH_ENV: Final = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "LANG",
        "LC_ALL",
        "PGSSLMODE",
        "PGSSLROOTCERT",
        "PGSSLCERT",
        "PGSSLKEY",
    }
)


class PostgresTools(Protocol):
    def dump(self, database_url: str, consume: ChunkConsumer) -> bool: ...

    def validate(self, archive_path: Path) -> bool: ...

    def restore(self, database_url: str, archive_path: Path) -> bool: ...


@dataclass(frozen=True, slots=True)
class _DatabaseConnection:
    public_url: str
    environment: Mapping[str, str]


class SubprocessPostgresTools:
    def __init__(self, pg_dump: str = "pg_dump", pg_restore: str = "pg_restore") -> None:
        self._pg_dump: Final = pg_dump
        self._pg_restore: Final = pg_restore

    def dump(self, database_url: str, consume: ChunkConsumer) -> bool:
        connection: Final = _database_connection(database_url)
        process: Final[subprocess.Popen[bytes]] = subprocess.Popen(
            (
                self._pg_dump,
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--dbname",
                connection.public_url,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=connection.environment,
        )
        if process.stdout is None:
            process.kill()
            return False
        stdout: Final = cast(BinaryIO, process.stdout)
        try:
            while chunk := stdout.read(1024 * 1024):
                consume(chunk)
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            stdout.close()
        return process.wait() == 0

    def validate(self, archive_path: Path) -> bool:
        return _run(
            (self._pg_restore, "--list", str(archive_path)),
            environment=_tool_environment(),
        )

    def restore(self, database_url: str, archive_path: Path) -> bool:
        connection: Final = _database_connection(database_url)
        return _run(
            (
                self._pg_restore,
                "--exit-on-error",
                "--single-transaction",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-acl",
                "--dbname",
                connection.public_url,
                str(archive_path),
            ),
            environment=connection.environment,
        )


def _run(command: tuple[str, ...], *, environment: Mapping[str, str]) -> bool:
    try:
        completed: Final = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            check=False,
        )
        return completed.returncode == 0
    except OSError:
        return False


def _database_connection(database_url: str) -> _DatabaseConnection:
    parsed: Final = urlsplit(database_url)
    if parsed.scheme not in ("postgres", "postgresql") or parsed.hostname is None:
        raise ValueError("DATABASE_URL must be a PostgreSQL URL with a host")
    query_keys: Final = frozenset(key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True))
    if query_keys.intersection({"password", "passfile", "sslpassword"}):
        raise ValueError("DATABASE_URL query cannot contain credential parameters")
    username: Final = "" if parsed.username is None else quote(unquote(parsed.username), safe="")
    host: Final = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port: Final = "" if parsed.port is None else f":{parsed.port}"
    userinfo: Final = "" if not username else f"{username}@"
    public_url: Final = urlunsplit((parsed.scheme, f"{userinfo}{host}{port}", parsed.path, parsed.query, ""))
    password: Final = None if parsed.password is None else unquote(parsed.password)
    environment: Final = {
        **_tool_environment(),
        **({} if password is None else {"PGPASSWORD": password}),
    }
    return _DatabaseConnection(public_url=public_url, environment=environment)


def _tool_environment() -> Mapping[str, str]:
    return {key: value for key in _PASSTHROUGH_ENV if (value := os.environ.get(key)) is not None}
