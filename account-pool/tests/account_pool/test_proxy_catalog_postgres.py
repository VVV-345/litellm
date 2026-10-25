"""在隔离 PostgreSQL schema 验证旧库升级、代理加密持久化、到期停用及重启读取。"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Final, cast
from uuid import uuid4

import psycopg
import pytest
import yaml
from account_pool.clash import ClashController, ClashError
from account_pool.config import Settings
from account_pool.isp_subscription import parse_isp_subscription
from account_pool.management_repository import initialize_management_schema
from account_pool.proxy_catalog import ISPProbeResult, ISPProxyInput
from account_pool.proxy_catalog_repository import PostgresProxyCatalogRepository, ProxyCatalogCipher
from account_pool.proxy_catalog_service import ProxyCatalogService
from account_pool.proxy_gateways import ProxyGatewayService
from account_pool.repository import PostgresEnvironmentRepository, PostgresProxyProfileRepository, database_connection
from account_pool.shared.secrets import EnvironmentSecretDeriver
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_proxy_gateways import FakeController


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
@pytest.mark.parametrize("failure,expected_status,expected_detail", (
    ("name_conflict", 409, "ISP subscription node name already exists"),
    ("other_unique", 503, "ISP catalog storage unavailable; retry"),
    ("database_error", 503, "ISP catalog storage unavailable; retry"),
))
@pytest.mark.skipif(not os.environ.get("PROXY_CATALOG_TEST_DATABASE_URL"), reason="isolated PostgreSQL URL required")
async def test_subscription_update_conflict_is_distinct_from_storage_failure_and_rolls_back(
    failure: str, expected_status: int, expected_detail: str,
) -> None:
    import httpx
    from account_pool.api import create_router
    from fastapi import FastAPI

    base: Final = os.environ["PROXY_CATALOG_TEST_DATABASE_URL"]
    schema: Final = "isp_subscription_conflict_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(base, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dsn: Final = make_conninfo(base, options=f"-c search_path={schema}")
            async with database_connection(dsn) as connection:
                await connection.execute("CREATE TABLE account_pool_proxy_gateways (port integer PRIMARY KEY)")
                await connection.execute("INSERT INTO account_pool_proxy_gateways VALUES (7891)")
            cipher: Final = ProxyCatalogCipher(EnvironmentSecretDeriver("test-seed-" * 4))
            repository: Final = PostgresProxyCatalogRepository(dsn, cipher)
            await repository.initialize()
            await repository.save_isp_subscription(
                "https://isp.example/fixture.yaml?token=private-token", parse_isp_subscription(
                    "proxies:\n  - name: first\n    type: socks5\n    server: first.example\n    port: 1080\n"
                    "    username: private-user\n    password: private-secret\n"
                    "  - name: second\n    type: http\n    server: second.example\n    port: 8080\n"
                ),
            )
            first, second = await repository.list_isps()
            await repository.bind_isp(7891, first.id)
            async with database_connection(dsn) as connection:
                if failure == "other_unique":
                    await connection.execute(
                        "UPDATE account_pool_isp_subscription_nodes SET enabled = false WHERE id = %s", (second.id,),
                    )
                    await connection.execute("ALTER TABLE account_pool_isp_subscription_nodes ADD UNIQUE (enabled)")
                elif failure == "database_error":
                    await connection.execute("ALTER TABLE account_pool_isp_subscription_nodes ADD CHECK (enabled)")
                before_cursor: Final = await connection.execute(
                    "SELECT * FROM account_pool_isp_subscription_nodes ORDER BY id"
                )
                before: Final = await before_cursor.fetchall()
                subscription_cursor: Final = await connection.execute("SELECT * FROM account_pool_isp_subscription")
                subscription_before: Final = await subscription_cursor.fetchall()
            app: Final = FastAPI()
            app.include_router(create_router(object(), "manager-test", proxy_catalog=ProxyCatalogService(repository)))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response: Final = await client.put(
                    f"/api/proxy-isps/{first.id}", headers={"Authorization": "Bearer manager-test"},
                    json={"name": second.name if failure == "name_conflict" else "renamed", "enabled": False},
                )
            assert response.status_code == expected_status
            assert response.json() == {"detail": expected_detail}
            assert "private-" not in response.text and "ciphertext" not in response.text
            reopened: Final = PostgresProxyCatalogRepository(dsn, cipher)
            assert await reopened.get_isp(first.id) == first
            assert await reopened.list_isp_bindings() == ((7891, first.id),)
            async with database_connection(dsn) as connection:
                after_cursor: Final = await connection.execute(
                    "SELECT * FROM account_pool_isp_subscription_nodes ORDER BY id"
                )
                assert await after_cursor.fetchall() == before
                subscription_after: Final = await connection.execute("SELECT * FROM account_pool_isp_subscription")
                assert await subscription_after.fetchall() == subscription_before
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ("manual", "subscription", "inactive-subscription"))
@pytest.mark.skipif(not os.environ.get("PROXY_CATALOG_TEST_DATABASE_URL"), reason="isolated PostgreSQL URL required")
async def test_gateway_deletion_failure_restores_port_before_foreign_key_bindings(
    tmp_path: Path, source: str,
) -> None:
    base: Final = os.environ["PROXY_CATALOG_TEST_DATABASE_URL"]
    schema: Final = "isp_gateway_restore_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(base, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dsn: Final = make_conninfo(base, options=f"-c search_path={schema}")
            await PostgresEnvironmentRepository(dsn).initialize()
            await initialize_management_schema(dsn)
            repository: Final = PostgresProxyCatalogRepository(
                dsn, ProxyCatalogCipher(EnvironmentSecretDeriver("test-seed-" * 4)),
            )
            await repository.initialize()
            async with database_connection(dsn) as connection:
                await connection.execute("INSERT INTO account_pool_proxy_gateways (port) VALUES (7891)")
                await connection.execute("INSERT INTO account_pool_proxy_gateway_registry (singleton) VALUES (true)")
            if source != "manual":
                await repository.save_isp_subscription(
                    "https://isp.example/fixture.yaml",
                    parse_isp_subscription(
                        "proxies:\n  - name: fixture\n    type: socks5\n    server: isp.example\n    port: 1080\n"
                    ),
                )
            else:
                await repository.save_isps((ISPProxyInput(name="fixture", host="isp.example", port=1080),))
            profile: Final = (await repository.list_isps())[0]
            await repository.bind_isp(7891, profile.id)
            await repository.bind_front_node(7891, "airport-a")
            if source == "inactive-subscription":
                await repository.save_isp_subscription(
                    "https://isp.example/fixture.yaml",
                    parse_isp_subscription(
                        "proxies:\n  - name: replacement\n    type: socks5\n    server: isp.example\n    port: 1080\n"
                    ),
                )
                inactive: Final = await repository.get_isp(profile.id)
                assert inactive is not None and not inactive.enabled
            config_path: Final = tmp_path / "config.yaml"
            original: Final = yaml.safe_dump({
                "listeners": [{"name": "gateway-7891", "port": 7891, "proxy": "clash-gateway-7891"}],
                "proxy-groups": [
                    {"name": "clash-gateway-7891", "type": "select", "proxies": ["airport-a", "REJECT"]},
                    {"name": "account-pool-front-7891", "type": "select", "proxies": ["airport-a", "REJECT"]},
                ],
                "proxies": [{"name": "airport-a", "type": "socks5", "server": "airport.example", "port": 443}],
            }, sort_keys=False)
            config_path.write_text(original, encoding="utf-8")

            class FailingDeletionProfiles(PostgresProxyProfileRepository):
                async def delete_gateway(self, profile_id: str) -> bool:
                    async with database_connection(dsn) as connection:
                        cursor: Final = await connection.execute("SELECT port FROM account_pool_proxy_gateways")
                        assert await cursor.fetchall() == []
                    assert await repository.list_isp_bindings() == ()
                    assert await repository.list_front_bindings() == ()
                    raise RuntimeError("fixture profile deletion failure")

            profiles: Final = FailingDeletionProfiles(dsn)
            await profiles.upsert_gateways((("clash-gateway-7891", "fixture gateway", "http://127.0.0.1:7891"),))
            controller: Final = FakeController()
            service: Final = ProxyGatewayService(
                Settings(
                    database_url=dsn, manager_token="t" * 32, secret_seed="s" * 32,
                    ssh_host="fixture.example", ssh_user="fixture", clash_gateway_ports=(7891,),
                    clash_config_path=str(config_path),
                ),
                profiles, cast(ClashController, controller), catalog=repository,
            )

            if source == "inactive-subscription":
                with pytest.raises(ClashError, match="ISP profile is unavailable or expired"):
                    await service.bind_isp_gateway(7891, profile.id)

            with pytest.raises(ClashError, match="proxy gateway deletion failed") as failure:
                await service.remove_gateway(7891, frozenset())

            assert isinstance(failure.value.__cause__, RuntimeError)
            assert str(failure.value.__cause__) == "fixture profile deletion failure"
            async with database_connection(dsn) as connection:
                cursor: Final = await connection.execute("SELECT port FROM account_pool_proxy_gateways")
                assert await cursor.fetchall() == [{"port": 7891}]
            assert await repository.list_isp_bindings() == ((7891, profile.id),)
            assert await repository.list_front_bindings() == ((7891, "airport-a"),)
            assert config_path.read_text(encoding="utf-8") == original
            assert len(controller.reloaded) == 2
            assert await profiles.get_url("clash-gateway-7891") == "http://127.0.0.1:7891"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("renamed,swap_names", ((False, False), (True, False), (True, True)))
@pytest.mark.skipif(not os.environ.get("PROXY_CATALOG_TEST_DATABASE_URL"), reason="isolated PostgreSQL URL required")
async def test_isp_subscription_refresh_preserves_node_ids_bindings_and_encryption(
    renamed: bool, swap_names: bool,
) -> None:
    base: Final = os.environ["PROXY_CATALOG_TEST_DATABASE_URL"]
    schema: Final = "isp_subscription_test_" + uuid4().hex
    first_url: Final = "https://isp.example/first.yaml?token=subscription-secret"
    first_content: Final = (
        "proxies:\n"
        "  - name: residential-a\n"
        "    type: socks5\n"
        "    server: a.example\n"
        "    port: 1080\n"
        "    username: user-a\n"
        "    password: node-secret-a\n"
        "  - name: residential-b\n"
        "    type: vless\n"
        "    server: b.example\n"
        "    port: 443\n"
        "    uuid: 00000000-0000-0000-0000-000000000002\n"
    )
    changed_content: Final = (
        "proxies:\n"
        "  - name: residential-a\n"
        "    type: socks5\n"
        "    server: a.example\n"
        "    port: 1080\n"
        "    username: user-a\n"
        "    password: node-secret-updated\n"
    )
    restored_content: Final = first_content.replace("node-secret-a", "node-secret-restored")
    async with await psycopg.AsyncConnection.connect(base, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dsn: Final = make_conninfo(base, options=f"-c search_path={schema}")
            async with database_connection(dsn) as connection:
                await connection.execute("CREATE TABLE account_pool_proxy_gateways (port integer PRIMARY KEY)")
                await connection.execute("INSERT INTO account_pool_proxy_gateways VALUES (7891), (7892), (7893)")
            cipher: Final = ProxyCatalogCipher(EnvironmentSecretDeriver("test-seed-" * 4))
            repository: Final = PostgresProxyCatalogRepository(dsn, cipher)
            await repository.initialize()
            manual: Final = (await repository.save_isps((ISPProxyInput(
                name="manual ISP", host="manual.example", port=8080,
            ),)))[0]
            first: Final = await repository.save_isp_subscription(
                first_url, parse_isp_subscription(first_content)
            )
            assert first.configured and first.node_count == 2 and first.enabled_node_count == 2
            original_nodes: Final = tuple(
                profile for profile in await repository.list_isps() if profile.name.startswith("residential-")
            )
            first_node: Final = next(profile for profile in original_nodes if profile.name == "residential-a")
            missing_node: Final = next(profile for profile in original_nodes if profile.name == "residential-b")
            await repository.bind_isp(7891, manual.id)
            await repository.bind_isp(7892, first_node.id)
            await repository.bind_isp(7893, missing_node.id)
            await repository.bind_front_node(7892, "airport-a")
            first_name: Final = missing_node.name if swap_names else "local alias"
            missing_name: Final = first_node.name if swap_names else "missing alias"
            if renamed:
                await repository.update_isp(first_node.model_copy(update={"name": "temporary alias"}))
                await repository.update_isp(missing_node.model_copy(update={"name": missing_name}))
                await repository.update_isp(first_node.model_copy(update={"name": first_name}))

            reopened: Final = PostgresProxyCatalogRepository(dsn, cipher)
            refreshed: Final = await reopened.save_isp_subscription(
                "https://isp.example/updated.yaml?token=subscription-secret",
                parse_isp_subscription(changed_content),
            )
            refreshed_node: Final = await repository.get_isp(first_node.id)
            assert refreshed.configured and refreshed.node_count == 1 and refreshed.enabled_node_count == 1
            assert refreshed_node is not None and refreshed_node.id == first_node.id
            assert refreshed_node.enabled
            assert refreshed_node.name == (first_name if renamed else first_node.name)
            assert len(await repository.list_isps()) == 3
            assert refreshed_node.config["password"] == "node-secret-updated"
            assert await repository.list_isp_bindings() == (
                (7891, manual.id),
                (7892, first_node.id),
                (7893, missing_node.id),
            )
            assert await repository.list_inactive_isp_bindings(date.today()) == ((7893, missing_node.id),)
            assert await repository.list_front_bindings() == ((7892, "airport-a"),)

            await repository.save_isp_subscription(first_url, parse_isp_subscription(restored_content))
            restored_node: Final = await repository.get_isp(missing_node.id)
            assert restored_node is not None and restored_node.id == missing_node.id and restored_node.enabled
            assert restored_node.name == (missing_name if renamed else missing_node.name)
            restored_first: Final = await repository.get_isp(first_node.id)
            assert restored_first is not None and restored_first.config["password"] == "node-secret-restored"
            assert restored_first.enabled
            assert restored_first.name == (first_name if renamed else first_node.name)
            assert len(await repository.list_isps()) == 3
            assert await repository.get_isp_subscription_url() == first_url

            async with database_connection(dsn) as connection:
                url_cursor: Final = await connection.execute(
                    "SELECT url_ciphertext FROM account_pool_isp_subscription WHERE singleton = true"
                )
                node_cursor: Final = await connection.execute(
                    "SELECT config_ciphertext FROM account_pool_isp_subscription_nodes WHERE id = %s",
                    (first_node.id,),
                )
                assert "subscription-secret" not in str((await url_cursor.fetchone())["url_ciphertext"])
                assert "node-secret-restored" not in str((await node_cursor.fetchone())["config_ciphertext"])
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("PROXY_CATALOG_TEST_DATABASE_URL"), reason="isolated PostgreSQL URL required")
async def test_subscription_edit_probe_persistence_and_guarded_delete() -> None:
    from account_pool.proxy_catalog import ISPSubscriptionNodeProfile

    base: Final = os.environ["PROXY_CATALOG_TEST_DATABASE_URL"]
    schema: Final = "isp_subscription_actions_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(base, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dsn: Final = make_conninfo(base, options=f"-c search_path={schema}")
            async with database_connection(dsn) as connection:
                await connection.execute("CREATE TABLE account_pool_proxy_gateways (port integer PRIMARY KEY)")
                await connection.execute("INSERT INTO account_pool_proxy_gateways VALUES (7891)")
            cipher: Final = ProxyCatalogCipher(EnvironmentSecretDeriver("test-seed-" * 4))
            repository: Final = PostgresProxyCatalogRepository(dsn, cipher)
            await repository.initialize()
            await repository.initialize()
            await repository.save_isp_subscription("https://isp.example/fixture.yaml", parse_isp_subscription(
                "proxies:\n  - name: fixture\n    type: socks5\n    server: isp.example\n    port: 1080\n"
                "    username: private-user\n    password: private-secret\n"
            ))
            profile: Final = (await repository.list_isps())[0]
            assert isinstance(profile, ISPSubscriptionNodeProfile)
            async with database_connection(dsn) as connection:
                raw: Final = await connection.execute("SELECT config_ciphertext FROM account_pool_isp_subscription_nodes")
                encrypted: Final = (await raw.fetchone())["config_ciphertext"]
            updated: Final = await repository.update_isp(profile.model_copy(update={"name": "renamed", "enabled": False}))
            assert updated is not None and updated.config == profile.config
            probe: Final = ISPProbeResult(profile_id=profile.id, status="ok", checked_at=datetime.now(timezone.utc),
                                          exit_ip="8.8.8.8", delay_ms=23)
            assert await repository.save_isp_probe(probe) == probe
            reopened: Final = PostgresProxyCatalogRepository(dsn, cipher)
            view: Final = (await ProxyCatalogService(reopened).list_isps())[0]
            assert view.name == "renamed" and not view.enabled and view.last_probe == probe
            assert "private-secret" not in view.model_dump_json()
            async with database_connection(dsn) as connection:
                raw_after: Final = await connection.execute("SELECT config_ciphertext FROM account_pool_isp_subscription_nodes")
                assert (await raw_after.fetchone())["config_ciphertext"] == encrypted
            await repository.bind_isp(7891, profile.id)
            assert not await repository.delete_isp(profile.id)
            await repository.unbind_isp(7891)
            assert await repository.delete_isp(profile.id)
            assert await reopened.list_isp_probes() == ()
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ("duplicate_source", "display_name"))
@pytest.mark.parametrize("method", ("PUT", "POST"))
@pytest.mark.skipif(not os.environ.get("PROXY_CATALOG_TEST_DATABASE_URL"), reason="isolated PostgreSQL URL required")
async def test_subscription_refresh_rejects_ambiguous_identity_without_changing_bindings(
    conflict: str, method: str,
) -> None:
    import httpx
    from account_pool.api import create_router
    from fastapi import FastAPI

    base: Final = os.environ["PROXY_CATALOG_TEST_DATABASE_URL"]
    schema: Final = "isp_refresh_conflict_" + uuid4().hex
    content: Final = (
        "proxies:\n  - name: first\n    type: socks5\n    server: first.example\n    port: 1080\n"
    )
    async with await psycopg.AsyncConnection.connect(base, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dsn: Final = make_conninfo(base, options=f"-c search_path={schema}")
            async with database_connection(dsn) as connection:
                await connection.execute("CREATE TABLE account_pool_proxy_gateways (port integer PRIMARY KEY)")
                await connection.execute("INSERT INTO account_pool_proxy_gateways VALUES (7891)")
            cipher: Final = ProxyCatalogCipher(EnvironmentSecretDeriver("test-seed-" * 4))
            repository: Final = PostgresProxyCatalogRepository(dsn, cipher)
            await repository.initialize()
            await repository.save_isp_subscription("https://isp.example/old.yaml", parse_isp_subscription(content))
            first: Final = (await repository.list_isps())[0]
            await repository.bind_isp(7891, first.id)
            await repository.update_isp(first.model_copy(update={"name": "second"}))
            if conflict == "duplicate_source":
                duplicate_id: Final = uuid4()
                async with database_connection(dsn) as connection:
                    await connection.execute(
                        "INSERT INTO account_pool_isp_subscription_nodes "
                        "(id, name, proxy_type, config_ciphertext, enabled) VALUES (%s, %s, %s, %s, true)",
                        (duplicate_id, "first", "socks5", cipher.seal(
                            duplicate_id,
                            '{"name":"first","type":"socks5","server":"first.example","port":1080}',
                        )),
                    )
            before: Final = await repository.list_isps()
            before_subscription: Final = await repository.get_isp_subscription_view()
            refreshed_content: Final = content if conflict == "duplicate_source" else content + (
                "  - name: second\n    type: http\n    server: second.example\n    port: 8080\n"
            )
            async def fetch(url: str) -> str:
                return refreshed_content

            app: Final = FastAPI()
            app.include_router(create_router(
                object(), "manager-test", proxy_catalog=ProxyCatalogService(repository, fetch_subscription=fetch),
            ))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response: Final = await client.request(
                    method,
                    "/api/proxy-isps/subscription" + ("/refresh" if method == "POST" else ""),
                    headers={"Authorization": "Bearer manager-test"},
                    json={"url": "https://isp.example/new.yaml?token=private-token"} if method == "PUT" else None,
                )
            assert response.status_code == 409
            assert ("identity" if conflict == "duplicate_source" else "display name") in response.json()["detail"]
            assert "private-token" not in response.text and "ciphertext" not in response.text
            reopened: Final = PostgresProxyCatalogRepository(dsn, cipher)
            assert await reopened.list_isps() == before
            assert await reopened.get_isp_subscription_view() == before_subscription
            assert await reopened.get_isp_subscription_url() == "https://isp.example/old.yaml"
            assert await reopened.list_isp_bindings() == ((7891, first.id),)
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
