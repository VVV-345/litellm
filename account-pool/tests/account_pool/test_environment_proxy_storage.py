"""验证环境快照中的 ISP 凭据加密存储，并保留旧代理记录的读取方式。"""

import os
from typing import Final
from uuid import uuid4

import psycopg
import pytest
from account_pool.domain import (
    EnvironmentStatus,
    OpenAICompatibleConfiguration,
    OpenAICompatibleCredential,
    ProxyMode,
    configuration_from_record,
)
from account_pool.repository import PostgresEnvironmentRepository
from account_pool.secrets import EnvironmentSecretDeriver, StateCipher
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_account_pool import _record


def test_proxy_credentials_round_trip_without_plaintext_in_persisted_json() -> None:
    cipher: Final = StateCipher(EnvironmentSecretDeriver("s" * 32))
    repository: Final = PostgresEnvironmentRepository("unused", proxy_cipher=cipher)
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(update={
        "proxy_mode": ProxyMode.PROFILE, "proxy_profile_id": "isp-direct-d33cb1de-9247-432a-a827-7503ab1c9f95",
    })
    configured: Final = record.model_copy(update={
        "desired_configuration": configuration_from_record(record, "socks5://alice:private-password@isp.example:1080"),
    })
    payload: Final = repository._encode(configured)
    assert "private-password" not in str(payload)
    assert "alice" not in str(payload)
    assert repository._decode(payload) == configured
    with pytest.raises(ValueError):
        PostgresEnvironmentRepository("unused", proxy_cipher=StateCipher(EnvironmentSecretDeriver("t" * 32)))._decode(payload)
    with pytest.raises(ValueError):
        PostgresEnvironmentRepository("unused")._encode(configured)


def test_legacy_proxy_records_remain_readable_without_encryption_changes() -> None:
    repository: Final = PostgresEnvironmentRepository("unused")
    record: Final = _record(status=EnvironmentStatus.READY)
    assert repository._encode(record) == record.model_dump(mode="json")
    assert repository._decode(record.model_dump(mode="json")) == record


def test_per_key_isp_proxy_credentials_are_encrypted_with_the_account_snapshot() -> None:
    cipher: Final = StateCipher(EnvironmentSecretDeriver("s" * 32))
    repository: Final = PostgresEnvironmentRepository("unused", proxy_cipher=cipher)
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(update={
        "openai_compatible": OpenAICompatibleConfiguration(
            base_url="https://provider.example/v1", test_model="test-model",
            credentials=(OpenAICompatibleCredential(
                api_key_ciphertext="existing-ciphertext",
                proxy_profile_id="isp-direct-d33cb1de-9247-432a-a827-7503ab1c9f95",
                proxy_url="socks5://alice:private-password@isp.example:1080",
            ),),
        ),
    })
    payload: Final = repository._encode(record)
    assert "private-password" not in str(payload) and "alice" not in str(payload)
    assert repository._decode(payload) == record


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("PROXY_CATALOG_TEST_DATABASE_URL"), reason="isolated PostgreSQL URL required")
async def test_postgres_restarts_read_encrypted_proxy_snapshots_and_versioned_updates() -> None:
    base: Final = os.environ["PROXY_CATALOG_TEST_DATABASE_URL"]
    schema: Final = "proxy_storage_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(base, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dsn: Final = make_conninfo(base, options=f"-c search_path={schema}")
            cipher: Final = StateCipher(EnvironmentSecretDeriver("s" * 32))
            repository: Final = PostgresEnvironmentRepository(dsn, proxy_cipher=cipher)
            await repository.initialize()
            record: Final = _record(status=EnvironmentStatus.READY).model_copy(update={
                "proxy_mode": ProxyMode.PROFILE,
                "proxy_profile_id": "isp-direct-d33cb1de-9247-432a-a827-7503ab1c9f95",
                "operation_id": "direct-isp-storage",
            })
            configured: Final = record.model_copy(update={
                "desired_configuration": configuration_from_record(record, "http://alice:private-password@isp.example:1080"),
            })
            await repository.save(configured)
            reopened: Final = PostgresEnvironmentRepository(dsn, proxy_cipher=cipher)
            assert await reopened.get(record.id) == configured
            assert await reopened.find_by_operation_id("direct-isp-storage") == configured
            assert await reopened.list() == (configured,)
            updated: Final = configured.model_copy(update={"version": record.version + 1})
            assert await reopened.save_if_version(updated, record.version) == updated
            assert await reopened.get(record.id) == updated
            cursor: Final = await admin.execute(sql.SQL("SELECT payload FROM {}.account_pool_environments").format(sql.Identifier(schema)))
            payload: Final = str(await cursor.fetchone())
            assert "private-password" not in payload and "alice" not in payload
            assert "isp-encrypted:v1:" in payload
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
