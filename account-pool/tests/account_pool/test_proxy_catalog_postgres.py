"""在隔离 PostgreSQL schema 验证旧库升级、代理加密持久化、到期停用及重启读取。"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Final
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from account_pool.proxy_catalog import ISPProbeResult, ISPProxyInput
from account_pool.proxy_catalog_repository import PostgresProxyCatalogRepository, ProxyCatalogCipher
from account_pool.proxy_catalog_service import ProxyCatalogService
from account_pool.repository import database_connection
from account_pool.shared.secrets import EnvironmentSecretDeriver


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("PROXY_CATALOG_TEST_DATABASE_URL"), reason="isolated PostgreSQL URL required")
async def test_catalog_upgrade_preserves_old_data_and_persists_encrypted_profiles() -> None:
    base: Final = os.environ["PROXY_CATALOG_TEST_DATABASE_URL"]
    schema: Final = "isp_test_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(base, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dsn: Final = make_conninfo(base, options=f"-c search_path={schema}")
            async with database_connection(dsn) as connection:
                await connection.execute("CREATE TABLE account_pool_proxy_gateways (port integer PRIMARY KEY)")
                await connection.execute("INSERT INTO account_pool_proxy_gateways VALUES (7891), (7892)")
                await connection.execute("CREATE TABLE legacy_marker (value text NOT NULL)")
                await connection.execute("INSERT INTO legacy_marker VALUES ('preserved')")
            cipher: Final = ProxyCatalogCipher(EnvironmentSecretDeriver("test-seed-" * 4))
            repository: Final = PostgresProxyCatalogRepository(dsn, cipher)
            await repository.initialize()
            await repository.initialize()
            records: Final = await repository.save_isps((ISPProxyInput(
                name="ISP A", host="proxy.test", port=1234, username="alice", password="private-secret",
                expires_on=date(2026, 10, 15),
            ),))
            identity: Final = records[0].id
            probe: Final = ISPProbeResult(
                profile_id=identity,
                status="ok",
                checked_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
                exit_ip="8.8.8.8",
                delay_ms=82,
            )
            await repository.save_isp_probe(probe)
            await repository.bind_isp(7891, identity)
            await repository.bind_isp(7892, identity)
            assert not await repository.delete_isp(identity)
            assert await repository.deactivate_expired(date(2026, 10, 14)) == ()
            assert await repository.deactivate_expired(date(2026, 10, 15)) == (identity,)
            assert len(await repository.list_inactive_isp_bindings(date(2026, 10, 15))) == 2
            reopened: Final = PostgresProxyCatalogRepository(dsn, cipher)
            profile: Final = await reopened.get_isp(identity)
            assert profile is not None and not profile.enabled and profile.password == "private-secret"
            assert await reopened.list_isp_probes() == (probe,)
            updated: Final = await reopened.update_isp(profile.model_copy(update={"password": "new-secret"}))
            assert updated is not None and updated.password == "new-secret"
            first: Final = await repository.save_configuration("one", "proxies: []", "https://example.test/?secret=private")
            second: Final = await repository.save_configuration("two", "proxies: []", None)
            assert await repository.activate_configuration(first.id)
            assert await repository.activate_configuration(second.id)
            assert len(await repository.list_configuration_views()) == 2
            assert not await repository.delete_configuration(second.id)
            assert (await reopened.get_configuration(first.id)).content == "proxies: []"
            async with database_connection(dsn) as connection:
                cursor: Final = await connection.execute("SELECT value FROM legacy_marker")
                assert (await cursor.fetchone())["value"] == "preserved"
                raw: Final = await connection.execute("SELECT credentials_ciphertext FROM account_pool_isp_profiles")
                assert "secret" not in (await raw.fetchone())["credentials_ciphertext"]
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("PROXY_CATALOG_TEST_DATABASE_URL"), reason="isolated PostgreSQL URL required")
async def test_latest_probe_survives_reopen_and_older_writes_and_cascades_on_delete() -> None:
    base: Final = os.environ["PROXY_CATALOG_TEST_DATABASE_URL"]
    schema: Final = "isp_probe_test_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(base, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dsn: Final = make_conninfo(base, options=f"-c search_path={schema}")
            async with database_connection(dsn) as connection:
                await connection.execute("CREATE TABLE account_pool_proxy_gateways (port integer PRIMARY KEY)")
            cipher: Final = ProxyCatalogCipher(EnvironmentSecretDeriver("test-seed-" * 4))
            repository: Final = PostgresProxyCatalogRepository(dsn, cipher)
            await repository.initialize()
            profiles: Final = await repository.save_isps((ISPProxyInput(
                name="ISP", host="proxy.test", port=8080, username="alice", password="private-secret",
            ),))
            profile: Final = profiles[0]
            success: Final = ISPProbeResult(
                profile_id=profile.id, status="ok", checked_at=datetime(2026, 10, 16, tzinfo=timezone.utc),
                exit_ip="8.8.8.8", country="US", region="California", city="Mountain View", isp="operator", delay_ms=82,
            )
            assert await repository.save_isp_probe(success) == success
            reopened: Final = PostgresProxyCatalogRepository(dsn, cipher)
            view: Final = (await ProxyCatalogService(reopened).list_isps())[0]
            assert view.credentials_configured and view.last_probe == success
            assert "private-secret" not in view.model_dump_json() and "alice" not in view.model_dump_json()
            for hour, status in enumerate(("timeout", "error"), start=1):
                failure: Final = ISPProbeResult(
                    profile_id=profile.id, status=status, checked_at=datetime(2026, 10, 16, hour, tzinfo=timezone.utc),
                    error="timeout" if status == "timeout" else "probe_failed",
                )
                assert await reopened.save_isp_probe(failure) == failure
                assert await reopened.list_isp_probes() == (failure,)
                assert await repository.save_isp_probe(success) == failure
                assert await reopened.list_isp_probes() == (failure,)
            assert await reopened.delete_isp(profile.id)
            assert await reopened.list_isp_probes() == ()
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
