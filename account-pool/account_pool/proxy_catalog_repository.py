"""本模块加密持久化 ISP 凭据、Clash 配置档案及端口绑定。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Final, Protocol
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from pydantic import JsonValue, TypeAdapter

from account_pool.isp_subscription import MihomoISPNode
from account_pool.proxy_catalog import (
    ISPProbeResult,
    ISPProxyCatalogProfile,
    ISPProxyInput,
    ISPProxyProfile,
    ISPSubscriptionNodeProfile,
    ISPSubscriptionView,
    ProxyConfigurationProfile,
)
from account_pool.repository import database_connection
from account_pool.shared.secrets import EnvironmentSecretDeriver, StateCipher

_CREDENTIALS: Final = TypeAdapter(tuple[str, str])
_NODE_CONFIG: Final = TypeAdapter(dict[str, JsonValue])
_SUBSCRIPTION_ID: Final = UUID("00000000-0000-0000-0000-000000000001")
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
    CREATE TABLE IF NOT EXISTS account_pool_isp_probe_results (
        profile_id uuid PRIMARY KEY REFERENCES account_pool_isp_profiles(id) ON DELETE CASCADE,
        status text NOT NULL CHECK (status IN ('ok', 'timeout', 'error')),
        checked_at timestamptz NOT NULL,
        exit_ip inet,
        country text,
        region text,
        city text,
        isp text,
        delay_ms integer CHECK (delay_ms >= 0),
        error text
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_pool_proxy_gateway_isp_bindings (
        port integer PRIMARY KEY REFERENCES account_pool_proxy_gateways(port) ON DELETE CASCADE,
        isp_profile_id uuid NOT NULL REFERENCES account_pool_isp_profiles(id) ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS account_pool_isp_binding_profile_idx ON account_pool_proxy_gateway_isp_bindings (isp_profile_id)",
    """
    CREATE TABLE IF NOT EXISTS account_pool_isp_subscription (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        url_ciphertext text NOT NULL,
        refreshed_at timestamptz NOT NULL,
        node_count integer NOT NULL CHECK (node_count >= 1)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_pool_isp_subscription_nodes (
        id uuid PRIMARY KEY,
        name text NOT NULL UNIQUE,
        proxy_type text NOT NULL,
        config_ciphertext text NOT NULL,
        enabled boolean NOT NULL DEFAULT true
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_pool_isp_subscription_probe_results (
        LIKE account_pool_isp_probe_results INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
        PRIMARY KEY (profile_id),
        FOREIGN KEY (profile_id) REFERENCES account_pool_isp_subscription_nodes(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_pool_proxy_gateway_isp_subscription_bindings (
        port integer PRIMARY KEY REFERENCES account_pool_proxy_gateways(port) ON DELETE CASCADE,
        isp_profile_id uuid NOT NULL REFERENCES account_pool_isp_subscription_nodes(id) ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS account_pool_isp_subscription_binding_profile_idx "
    "ON account_pool_proxy_gateway_isp_subscription_bindings (isp_profile_id)",
    """
    CREATE TABLE IF NOT EXISTS account_pool_proxy_gateway_front_bindings (
        port integer PRIMARY KEY REFERENCES account_pool_proxy_gateways(port) ON DELETE CASCADE,
        node_name text NOT NULL
    )
    """,
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

    async def list_isps(self) -> tuple[ISPProxyCatalogProfile, ...]: ...

    async def list_isp_probes(self) -> tuple[ISPProbeResult, ...]: ...

    async def get_isp(self, profile_id: UUID) -> ISPProxyCatalogProfile | None: ...

    async def save_isps(self, profiles: tuple[ISPProxyInput, ...]) -> tuple[ISPProxyProfile, ...]: ...

    async def update_isp(self, profile: ISPProxyCatalogProfile) -> ISPProxyCatalogProfile | None: ...

    async def save_isp_probe(self, result: ISPProbeResult) -> ISPProbeResult: ...

    async def delete_isp(self, profile_id: UUID) -> bool: ...

    async def get_isp_subscription_view(self) -> ISPSubscriptionView: ...

    async def get_isp_subscription_url(self) -> str | None: ...

    async def save_isp_subscription(
        self, url: str, nodes: tuple[MihomoISPNode, ...]
    ) -> ISPSubscriptionView: ...

    async def deactivate_expired(self, today: date) -> tuple[UUID, ...]: ...

    async def list_isp_bindings(self) -> tuple[tuple[int, UUID], ...]: ...

    async def list_inactive_isp_bindings(self, today: date) -> tuple[tuple[int, UUID], ...]: ...

    async def bind_isp(self, port: int, profile_id: UUID) -> None: ...

    async def unbind_isp(self, port: int) -> None: ...

    async def list_front_bindings(self) -> tuple[tuple[int, str], ...]: ...

    async def bind_front_node(self, port: int, node_name: str | None) -> None: ...

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

    async def list_isps(self) -> tuple[ISPProxyCatalogProfile, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT * FROM account_pool_isp_profiles ORDER BY name, id")
            manual_rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
            subscription_cursor: Final = await connection.execute(
                "SELECT id, name, proxy_type, config_ciphertext, enabled "
                "FROM account_pool_isp_subscription_nodes ORDER BY name, id"
            )
            subscription_rows: Final[Sequence[Mapping[str, object]]] = await subscription_cursor.fetchall()
        profiles: Final = tuple(self._isp_from_row(row) for row in manual_rows) + tuple(
            self._subscription_node_from_row(row) for row in subscription_rows
        )
        return tuple(sorted(profiles, key=lambda profile: (profile.name, profile.id.int)))

    async def list_isp_probes(self) -> tuple[ISPProbeResult, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT profile_id, status, checked_at, exit_ip, country, region, city, isp, delay_ms, error "
                "FROM account_pool_isp_probe_results UNION ALL "
                "SELECT profile_id, status, checked_at, exit_ip, country, region, city, isp, delay_ms, error "
                "FROM account_pool_isp_subscription_probe_results ORDER BY checked_at DESC, profile_id"
            )
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(self._probe_from_row(row) for row in rows)

    async def get_isp(self, profile_id: UUID) -> ISPProxyCatalogProfile | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT * FROM account_pool_isp_profiles WHERE id = %s", (profile_id,)
            )
            row: Final = await cursor.fetchone()
            if row is not None:
                return self._isp_from_row(row)
            subscription_cursor: Final = await connection.execute(
                "SELECT id, name, proxy_type, config_ciphertext, enabled "
                "FROM account_pool_isp_subscription_nodes WHERE id = %s",
                (profile_id,),
            )
            subscription_row: Final = await subscription_cursor.fetchone()
        return None if subscription_row is None else self._subscription_node_from_row(subscription_row)

    async def get_isp_subscription_view(self) -> ISPSubscriptionView:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT refreshed_at, node_count FROM account_pool_isp_subscription WHERE singleton = true"
            )
            row: Final = await cursor.fetchone()
            node_cursor: Final = await connection.execute(
                "SELECT count(*) AS total, count(*) FILTER (WHERE enabled) AS enabled "
                "FROM account_pool_isp_subscription_nodes"
            )
            node_row: Final = await node_cursor.fetchone()
        if row is None:
            return ISPSubscriptionView(
                configured=False,
                refreshed_at=None,
                node_count=0,
                enabled_node_count=0,
            )
        return ISPSubscriptionView(
            configured=True,
            refreshed_at=row["refreshed_at"] if isinstance(row["refreshed_at"], datetime) else None,
            node_count=int(row["node_count"]),
            enabled_node_count=0 if node_row is None else int(node_row["enabled"]),
        )

    async def get_isp_subscription_url(self) -> str | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT url_ciphertext FROM account_pool_isp_subscription WHERE singleton = true"
            )
            row: Final = await cursor.fetchone()
        return None if row is None else self._cipher.open(_SUBSCRIPTION_ID, str(row["url_ciphertext"]))

    async def save_isp_subscription(
        self, url: str, nodes: tuple[MihomoISPNode, ...]
    ) -> ISPSubscriptionView:
        refreshed_at: Final = datetime.now(timezone.utc)
        async with database_connection(self._database_url) as connection:
            existing_cursor: Final = await connection.execute(
                "SELECT id, name FROM account_pool_isp_subscription_nodes"
            )
            existing_rows: Final[Sequence[Mapping[str, object]]] = await existing_cursor.fetchall()
            node_identities: Final = tuple(
                _subscription_node_identity(node, existing_rows) for node in nodes
            )
            await connection.execute(
                """
                INSERT INTO account_pool_isp_subscription (singleton, url_ciphertext, refreshed_at, node_count)
                VALUES (true, %s, %s, %s)
                ON CONFLICT (singleton) DO UPDATE SET
                    url_ciphertext = EXCLUDED.url_ciphertext,
                    refreshed_at = EXCLUDED.refreshed_at,
                    node_count = EXCLUDED.node_count
                """,
                (self._cipher.seal(_SUBSCRIPTION_ID, url), refreshed_at, len(nodes)),
            )
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO account_pool_isp_subscription_nodes
                        (id, name, proxy_type, config_ciphertext, enabled)
                    VALUES (%s, %s, %s, %s, true)
                    ON CONFLICT (id) DO UPDATE SET
                        proxy_type = EXCLUDED.proxy_type,
                        config_ciphertext = EXCLUDED.config_ciphertext,
                        enabled = true
                    """,
                    tuple(
                        (
                            identity,
                            node.name,
                            node.proxy_type,
                            self._cipher.seal(
                                identity,
                                json.dumps(node.config, ensure_ascii=False, separators=(",", ":")),
                            ),
                        )
                        for node, identity in node_identities
                    ),
                )
            await connection.execute(
                "UPDATE account_pool_isp_subscription_nodes SET enabled = false "
                "WHERE enabled AND NOT (name = ANY(%s))",
                (list(node.name for node in nodes),),
            )
            node_cursor: Final = await connection.execute(
                "SELECT count(*) AS total, count(*) FILTER (WHERE enabled) AS enabled "
                "FROM account_pool_isp_subscription_nodes"
            )
            node_row: Final = await node_cursor.fetchone()
        return ISPSubscriptionView(
            configured=True,
            refreshed_at=refreshed_at,
            node_count=len(nodes),
            enabled_node_count=0 if node_row is None else int(node_row["enabled"]),
        )

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

    async def update_isp(self, profile: ISPProxyCatalogProfile) -> ISPProxyCatalogProfile | None:
        async with database_connection(self._database_url) as connection:
            if isinstance(profile, ISPSubscriptionNodeProfile):
                try:
                    subscription_cursor: Final = await connection.execute(
                        "UPDATE account_pool_isp_subscription_nodes SET name = %s, enabled = %s WHERE id = %s RETURNING *",
                        (profile.name, profile.enabled, profile.id),
                    )
                except UniqueViolation as error:
                    if (
                        error.diag.table_name == "account_pool_isp_subscription_nodes"
                        and error.diag.constraint_name == "account_pool_isp_subscription_nodes_name_key"
                    ):
                        raise ValueError("ISP subscription node name already exists") from None
                    raise
                subscription_row: Final = await subscription_cursor.fetchone()
                return None if subscription_row is None else self._subscription_node_from_row(subscription_row)
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

    async def save_isp_probe(self, result: ISPProbeResult) -> ISPProbeResult:
        profile: Final = await self.get_isp(result.profile_id)
        table: Final = (
            "account_pool_isp_subscription_probe_results" if isinstance(profile, ISPSubscriptionNodeProfile)
            else "account_pool_isp_probe_results"
        )
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                f"""
                INSERT INTO {table}
                    (profile_id, status, checked_at, exit_ip, country, region, city, isp, delay_ms, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (profile_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    checked_at = EXCLUDED.checked_at,
                    exit_ip = EXCLUDED.exit_ip,
                    country = EXCLUDED.country,
                    region = EXCLUDED.region,
                    city = EXCLUDED.city,
                    isp = EXCLUDED.isp,
                    delay_ms = EXCLUDED.delay_ms,
                    error = EXCLUDED.error
                WHERE {table}.checked_at <= EXCLUDED.checked_at
                RETURNING *
                """,
                self._probe_parameters(result),
            )
            written: Final = await cursor.fetchone()
            if written is not None:
                return self._probe_from_row(written)
            latest: Final = await connection.execute(
                f"SELECT * FROM {table} WHERE profile_id = %s", (result.profile_id,)
            )
            row: Final = await latest.fetchone()
        if row is None:
            raise ValueError("ISP probe result was not found")
        return self._probe_from_row(row)

    async def delete_isp(self, profile_id: UUID) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "DELETE FROM account_pool_isp_profiles WHERE id = %s AND NOT EXISTS "
                "(SELECT 1 FROM account_pool_proxy_gateway_isp_bindings WHERE isp_profile_id = %s) "
                "AND NOT EXISTS (SELECT 1 FROM account_pool_proxy_gateway_isp_subscription_bindings "
                "WHERE isp_profile_id = %s)",
                (profile_id, profile_id, profile_id),
            )
            if cursor.rowcount == 1:
                return True
            subscription_cursor: Final = await connection.execute(
                "DELETE FROM account_pool_isp_subscription_nodes WHERE id = %s AND NOT EXISTS "
                "(SELECT 1 FROM account_pool_proxy_gateway_isp_subscription_bindings WHERE isp_profile_id = %s)",
                (profile_id, profile_id),
            )
        return subscription_cursor.rowcount == 1

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
            manual_cursor: Final = await connection.execute(
                "SELECT port, isp_profile_id FROM account_pool_proxy_gateway_isp_bindings ORDER BY port"
            )
            manual_rows: Final[Sequence[Mapping[str, object]]] = await manual_cursor.fetchall()
            subscription_cursor: Final = await connection.execute(
                "SELECT port, isp_profile_id FROM account_pool_proxy_gateway_isp_subscription_bindings ORDER BY port"
            )
            subscription_rows: Final[Sequence[Mapping[str, object]]] = await subscription_cursor.fetchall()
        return tuple(sorted(
            (
                (int(row["port"]), row["isp_profile_id"])
                for row in (*manual_rows, *subscription_rows)
                if isinstance(row.get("isp_profile_id"), UUID)
            ),
            key=lambda binding: binding[0],
        ))

    async def list_inactive_isp_bindings(self, today: date) -> tuple[tuple[int, UUID], ...]:
        async with database_connection(self._database_url) as connection:
            manual_cursor: Final = await connection.execute(
                """
                SELECT binding.port, profile.id AS isp_profile_id
                FROM account_pool_proxy_gateway_isp_bindings AS binding
                JOIN account_pool_isp_profiles AS profile ON profile.id = binding.isp_profile_id
                WHERE NOT profile.enabled OR profile.expires_on <= %s
                ORDER BY binding.port
                """,
                (today,),
            )
            manual_rows: Final[Sequence[Mapping[str, object]]] = await manual_cursor.fetchall()
            subscription_cursor: Final = await connection.execute(
                """
                SELECT binding.port, profile.id AS isp_profile_id
                FROM account_pool_proxy_gateway_isp_subscription_bindings AS binding
                JOIN account_pool_isp_subscription_nodes AS profile ON profile.id = binding.isp_profile_id
                WHERE NOT profile.enabled
                ORDER BY binding.port
                """
            )
            subscription_rows: Final[Sequence[Mapping[str, object]]] = await subscription_cursor.fetchall()
        return tuple(
            (int(row["port"]), row["isp_profile_id"])
            for row in (*manual_rows, *subscription_rows)
            if isinstance(row.get("isp_profile_id"), UUID)
        )

    async def bind_isp(self, port: int, profile_id: UUID) -> None:
        async with database_connection(self._database_url) as connection:
            manual_profile_cursor: Final = await connection.execute(
                "SELECT id FROM account_pool_isp_profiles WHERE id = %s", (profile_id,)
            )
            manual_profile: Final = await manual_profile_cursor.fetchone()
            if manual_profile is not None:
                await connection.execute(
                    "DELETE FROM account_pool_proxy_gateway_isp_subscription_bindings WHERE port = %s", (port,)
                )
                await connection.execute(
                    """
                    INSERT INTO account_pool_proxy_gateway_isp_bindings (port, isp_profile_id)
                    VALUES (%s, %s)
                    ON CONFLICT (port) DO UPDATE SET isp_profile_id = EXCLUDED.isp_profile_id
                    """,
                    (port, profile_id),
                )
                return
            subscription_profile_cursor: Final = await connection.execute(
                "SELECT id FROM account_pool_isp_subscription_nodes WHERE id = %s", (profile_id,)
            )
            if await subscription_profile_cursor.fetchone() is None:
                raise ValueError("ISP profile was not found or is unavailable")
            await connection.execute(
                """
                INSERT INTO account_pool_proxy_gateway_isp_subscription_bindings (port, isp_profile_id)
                VALUES (%s, %s)
                ON CONFLICT (port) DO UPDATE SET isp_profile_id = EXCLUDED.isp_profile_id
                """,
                (port, profile_id),
            )
            await connection.execute("DELETE FROM account_pool_proxy_gateway_isp_bindings WHERE port = %s", (port,))

    async def unbind_isp(self, port: int) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute("DELETE FROM account_pool_proxy_gateway_isp_bindings WHERE port = %s", (port,))
            await connection.execute(
                "DELETE FROM account_pool_proxy_gateway_isp_subscription_bindings WHERE port = %s", (port,)
            )

    async def list_front_bindings(self) -> tuple[tuple[int, str], ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT port, node_name FROM account_pool_proxy_gateway_front_bindings ORDER BY port"
            )
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(
            (int(row["port"]), str(row["node_name"]))
            for row in rows
            if isinstance(row.get("port"), int) and isinstance(row.get("node_name"), str)
        )

    async def bind_front_node(self, port: int, node_name: str | None) -> None:
        async with database_connection(self._database_url) as connection:
            if node_name is None:
                await connection.execute("DELETE FROM account_pool_proxy_gateway_front_bindings WHERE port = %s", (port,))
                return
            await connection.execute(
                """
                INSERT INTO account_pool_proxy_gateway_front_bindings (port, node_name)
                VALUES (%s, %s)
                ON CONFLICT (port) DO UPDATE SET node_name = EXCLUDED.node_name
                """,
                (port, node_name),
            )

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

    def _subscription_node_from_row(self, row: Mapping[str, object]) -> ISPSubscriptionNodeProfile:
        identity: Final = row["id"]
        if not isinstance(identity, UUID):
            raise ValueError("stored subscription node id is invalid")
        config: Final = _NODE_CONFIG.validate_json(self._cipher.open(identity, str(row["config_ciphertext"])))
        return ISPSubscriptionNodeProfile(
            id=identity,
            name=str(row["name"]),
            proxy_type=str(row["proxy_type"]),
            config=config,
            enabled=bool(row["enabled"]),
        )

    def _probe_from_row(self, row: Mapping[str, object]) -> ISPProbeResult:
        return ISPProbeResult.model_validate({
            **row,
            "exit_ip": None if row.get("exit_ip") is None else str(row["exit_ip"]),
        })

    def _probe_parameters(self, result: ISPProbeResult) -> tuple[object, ...]:
        return (
            result.profile_id,
            result.status,
            result.checked_at,
            result.exit_ip,
            result.country,
            result.region,
            result.city,
            result.isp,
            result.delay_ms,
            result.error,
        )


def _subscription_node_identity(
    node: MihomoISPNode,
    existing_rows: Sequence[Mapping[str, object]],
) -> tuple[MihomoISPNode, UUID]:
    existing_id: Final = next(
        (
            row["id"]
            for row in existing_rows
            if row.get("name") == node.name and isinstance(row.get("id"), UUID)
        ),
        None,
    )
    return node, existing_id if isinstance(existing_id, UUID) else uuid4()
