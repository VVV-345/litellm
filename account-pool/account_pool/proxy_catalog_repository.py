"""本模块加密持久化 ISP 凭据、Clash 配置档案及端口绑定。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Final, Protocol
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from account_pool.proxy_catalog import ISPProxyInput, ISPProxyProfile, ProxyConfigurationProfile
from account_pool.repository import database_connection
from account_pool.shared.secrets import EnvironmentSecretDeriver, StateCipher

_CREDENTIALS: Final = TypeAdapter(tuple[str, str])
_SCHEMA: Final = (
    """
    CREATE TABLE IF NOT EXISTS account_pool_isp_profiles (
        id uuid PRIMARY KEY,
        name text NOT NULL,
        protocol text NOT NULL CHECK (protocol IN ('http', 'https', 'socks5')),
        host text NOT NULL,
        port integer NOT NULL CHECK (port BETWEEN 1 AND 65535),
        credentials_ciphertext text NOT NULL,
        expires_on date,
        enabled boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS account_pool_isp_expiry_idx ON account_pool_isp_profiles (expires_on)",
    """
    CREATE TABLE IF NOT EXISTS account_pool_proxy_gateway_isp_bindings (
        port integer PRIMARY KEY REFERENCES account_pool_proxy_gateways(port) ON DELETE CASCADE,
        isp_profile_id uuid NOT NULL REFERENCES account_pool_isp_profiles(id) ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS account_pool_isp_binding_profile_idx ON account_pool_proxy_gateway_isp_bindings (isp_profile_id)",
    """
    CREATE TABLE IF NOT EXISTS account_pool_proxy_configuration_profiles (
        id uuid PRIMARY KEY,
        name text NOT NULL,
        content_ciphertext text NOT NULL,
        source_url_ciphertext text,
        created_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_pool_proxy_configuration_state (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        active_profile_id uuid NOT NULL REFERENCES account_pool_proxy_configuration_profiles(id) ON DELETE RESTRICT
    )
    """,
)


class ProxyCatalogRepository(Protocol):
    async def initialize(self) -> None: ...

    async def list_isps(self) -> tuple[ISPProxyProfile, ...]: ...

    async def get_isp(self, profile_id: UUID) -> ISPProxyProfile | None: ...

    async def save_isps(self, profiles: tuple[ISPProxyInput, ...]) -> tuple[ISPProxyProfile, ...]: ...

    async def update_isp(self, profile: ISPProxyProfile) -> ISPProxyProfile | None: ...

    async def delete_isp(self, profile_id: UUID) -> bool: ...

    async def deactivate_expired(self, today: date) -> tuple[UUID, ...]: ...

    async def list_isp_bindings(self) -> tuple[tuple[int, UUID], ...]: ...

    async def list_inactive_isp_bindings(self, today: date) -> tuple[tuple[int, UUID], ...]: ...

    async def bind_isp(self, port: int, profile_id: UUID) -> None: ...

    async def unbind_isp(self, port: int) -> None: ...

    async def list_configuration_views(self) -> tuple[tuple[UUID, str, datetime, bool], ...]: ...

    async def get_configuration(self, profile_id: UUID) -> ProxyConfigurationProfile | None: ...

    async def save_configuration(
        self, name: str, content: str, source_url: str | None
    ) -> ProxyConfigurationProfile: ...

    async def ensure_active_configuration(self, name: str, content: str) -> UUID: ...

    async def activate_configuration(self, profile_id: UUID) -> bool: ...

    async def delete_configuration(self, profile_id: UUID) -> bool: ...


class ProxyCatalogCipher:
    def __init__(self, secrets: EnvironmentSecretDeriver) -> None:
        self._cipher: Final = StateCipher(secrets)

    def seal(self, identity: UUID, value: str) -> str:
        return self._cipher.seal(identity, value)

    def open(self, identity: UUID, ciphertext: str) -> str:
        return self._cipher.open(identity, ciphertext)


class PostgresProxyCatalogRepository:
    def __init__(self, database_url: str, cipher: ProxyCatalogCipher) -> None:
        self._database_url: Final = database_url
        self._cipher: Final = cipher

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
            for statement in _SCHEMA:
                await connection.execute(statement)

    async def list_isps(self) -> tuple[ISPProxyProfile, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT * FROM account_pool_isp_profiles ORDER BY name, id")
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(self._isp_from_row(row) for row in rows)

    async def get_isp(self, profile_id: UUID) -> ISPProxyProfile | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT * FROM account_pool_isp_profiles WHERE id = %s", (profile_id,)
            )
            row: Final = await cursor.fetchone()
        return None if row is None else self._isp_from_row(row)

    async def save_isps(self, profiles: tuple[ISPProxyInput, ...]) -> tuple[ISPProxyProfile, ...]:
        records: Final = tuple(
            ISPProxyProfile(
                id=uuid4(),
                name=profile.name,
                protocol=profile.protocol,
                host=profile.host,
                port=profile.port,
                username=profile.username,
                password=profile.password,
                expires_on=profile.expires_on,
            )
            for profile in profiles
        )
        async with database_connection(self._database_url) as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO account_pool_isp_profiles
                        (id, name, protocol, host, port, credentials_ciphertext, expires_on, enabled)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    tuple(self._isp_parameters(profile) for profile in records),
                )
        return records

    async def update_isp(self, profile: ISPProxyProfile) -> ISPProxyProfile | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                UPDATE account_pool_isp_profiles
                SET name = %s, protocol = %s, host = %s, port = %s, credentials_ciphertext = %s,
                    expires_on = %s, enabled = %s
                WHERE id = %s
                RETURNING *
                """,
                (*self._isp_parameters(profile)[1:], profile.id),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else self._isp_from_row(row)

    async def delete_isp(self, profile_id: UUID) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "DELETE FROM account_pool_isp_profiles WHERE id = %s AND NOT EXISTS "
                "(SELECT 1 FROM account_pool_proxy_gateway_isp_bindings WHERE isp_profile_id = %s)",
                (profile_id, profile_id),
            )
        return cursor.rowcount == 1

    async def deactivate_expired(self, today: date) -> tuple[UUID, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "UPDATE account_pool_isp_profiles SET enabled = false "
                "WHERE enabled AND expires_on IS NOT NULL AND expires_on <= %s RETURNING id",
                (today,),
            )
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(row["id"] for row in rows if isinstance(row.get("id"), UUID))

    async def list_isp_bindings(self) -> tuple[tuple[int, UUID], ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT port, isp_profile_id FROM account_pool_proxy_gateway_isp_bindings ORDER BY port"
            )
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(
            (int(row["port"]), row["isp_profile_id"])
            for row in rows
            if isinstance(row.get("isp_profile_id"), UUID)
        )

    async def list_inactive_isp_bindings(self, today: date) -> tuple[tuple[int, UUID], ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                SELECT binding.port, profile.id AS isp_profile_id
                FROM account_pool_proxy_gateway_isp_bindings AS binding
                JOIN account_pool_isp_profiles AS profile ON profile.id = binding.isp_profile_id
                WHERE NOT profile.enabled OR profile.expires_on <= %s
                ORDER BY binding.port
                """,
                (today,),
            )
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(
            (int(row["port"]), row["isp_profile_id"])
            for row in rows
            if isinstance(row.get("isp_profile_id"), UUID)
        )

    async def bind_isp(self, port: int, profile_id: UUID) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                """
                INSERT INTO account_pool_proxy_gateway_isp_bindings (port, isp_profile_id)
                VALUES (%s, %s)
                ON CONFLICT (port) DO UPDATE SET isp_profile_id = EXCLUDED.isp_profile_id
                """,
                (port, profile_id),
            )

    async def unbind_isp(self, port: int) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute("DELETE FROM account_pool_proxy_gateway_isp_bindings WHERE port = %s", (port,))

    async def list_configuration_views(self) -> tuple[tuple[UUID, str, datetime, bool], ...]:
        async with database_connection(self._database_url) as connection:
            active_cursor: Final = await connection.execute(
                "SELECT active_profile_id FROM account_pool_proxy_configuration_state WHERE singleton = true"
            )
            active_row: Final = await active_cursor.fetchone()
            active_id: Final = None if active_row is None else active_row["active_profile_id"]
            cursor: Final = await connection.execute(
                "SELECT id, name, created_at FROM account_pool_proxy_configuration_profiles ORDER BY created_at DESC, id"
            )
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(
            (row["id"], str(row["name"]), row["created_at"], row["id"] == active_id)
            for row in rows
            if isinstance(row.get("id"), UUID) and isinstance(row.get("created_at"), datetime)
        )

    async def get_configuration(self, profile_id: UUID) -> ProxyConfigurationProfile | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT * FROM account_pool_proxy_configuration_profiles WHERE id = %s", (profile_id,)
            )
            row: Final = await cursor.fetchone()
        if row is None:
            return None
        return ProxyConfigurationProfile(
            id=profile_id,
            name=str(row["name"]),
            content=self._cipher.open(profile_id, str(row["content_ciphertext"])),
            source_url=None
            if row["source_url_ciphertext"] is None
            else self._cipher.open(profile_id, str(row["source_url_ciphertext"])),
            created_at=row["created_at"],
        )

    async def save_configuration(
        self, name: str, content: str, source_url: str | None
    ) -> ProxyConfigurationProfile:
        identity: Final = uuid4()
        created_at: Final = datetime.now(timezone.utc)
        profile: Final = ProxyConfigurationProfile(
            id=identity, name=name, content=content, source_url=source_url, created_at=created_at
        )
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                """
                INSERT INTO account_pool_proxy_configuration_profiles
                    (id, name, content_ciphertext, source_url_ciphertext, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    identity,
                    name,
                    self._cipher.seal(identity, content),
                    None if source_url is None else self._cipher.seal(identity, source_url),
                    created_at,
                ),
            )
        return profile

    async def ensure_active_configuration(self, name: str, content: str) -> UUID:
        async with database_connection(self._database_url) as connection:
            active_cursor: Final = await connection.execute(
                "SELECT active_profile_id FROM account_pool_proxy_configuration_state WHERE singleton = true"
            )
            active_row: Final = await active_cursor.fetchone()
            if active_row is not None and isinstance(active_row["active_profile_id"], UUID):
                return active_row["active_profile_id"]
            identity: Final = uuid4()
            created_at: Final = datetime.now(timezone.utc)
            await connection.execute(
                """
                INSERT INTO account_pool_proxy_configuration_profiles
                    (id, name, content_ciphertext, source_url_ciphertext, created_at)
                VALUES (%s, %s, %s, NULL, %s)
                """,
                (identity, name, self._cipher.seal(identity, content), created_at),
            )
            await connection.execute(
                "INSERT INTO account_pool_proxy_configuration_state (singleton, active_profile_id) "
                "VALUES (true, %s) ON CONFLICT (singleton) DO NOTHING",
                (identity,),
            )
            cursor: Final = await connection.execute(
                "SELECT active_profile_id FROM account_pool_proxy_configuration_state WHERE singleton = true"
            )
            row: Final = await cursor.fetchone()
        if row is None or not isinstance(row["active_profile_id"], UUID):
            raise ValueError("active proxy configuration could not be initialized")
        return row["active_profile_id"]

    async def activate_configuration(self, profile_id: UUID) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                INSERT INTO account_pool_proxy_configuration_state (singleton, active_profile_id)
                SELECT true, id FROM account_pool_proxy_configuration_profiles WHERE id = %s
                ON CONFLICT (singleton) DO UPDATE SET active_profile_id = EXCLUDED.active_profile_id
                RETURNING active_profile_id
                """,
                (profile_id,),
            )
            row: Final = await cursor.fetchone()
        return row is not None and row["active_profile_id"] == profile_id

    async def delete_configuration(self, profile_id: UUID) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "DELETE FROM account_pool_proxy_configuration_profiles WHERE id = %s AND NOT EXISTS "
                "(SELECT 1 FROM account_pool_proxy_configuration_state WHERE active_profile_id = %s)",
                (profile_id, profile_id),
            )
        return cursor.rowcount == 1

    def _isp_parameters(self, profile: ISPProxyProfile) -> tuple[object, ...]:
        credentials: Final = json.dumps((profile.username, profile.password), separators=(",", ":"))
        return (
            profile.id,
            profile.name,
            profile.protocol,
            profile.host,
            profile.port,
            self._cipher.seal(profile.id, credentials),
            profile.expires_on,
            profile.enabled,
        )

    def _isp_from_row(self, row: Mapping[str, object]) -> ISPProxyProfile:
        identity: Final = row["id"]
        if not isinstance(identity, UUID):
            raise ValueError("stored proxy profile id is invalid")
        ciphertext: Final = row["credentials_ciphertext"]
        credentials: Final = _CREDENTIALS.validate_json(self._cipher.open(identity, str(ciphertext)))
        expiry: Final = row["expires_on"]
        return ISPProxyProfile(
            id=identity,
            name=str(row["name"]),
            protocol=str(row["protocol"]),
            host=str(row["host"]),
            port=int(row["port"]),
            username=credentials[0],
            password=credentials[1],
            expires_on=expiry if isinstance(expiry, date) else None,
            enabled=bool(row["enabled"]),
        )
