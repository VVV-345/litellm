"""验证代理目录只以幂等 DDL 扩展号池数据库，保留旧版本数据和表结构。"""

from __future__ import annotations

from typing import Final

from account_pool.proxy_catalog_repository import _SCHEMA


def test_proxy_catalog_schema_is_additive_and_idempotent() -> None:
    statements: Final = tuple(" ".join(statement.split()).upper() for statement in _SCHEMA)

    assert all(
        statement.startswith("CREATE TABLE IF NOT EXISTS ")
        or statement.startswith("CREATE INDEX IF NOT EXISTS ")
        for statement in statements
    )
    assert any("ACCOUNT_POOL_ISP_PROFILES" in statement for statement in statements)
    assert any("ACCOUNT_POOL_PROXY_GATEWAY_ISP_BINDINGS" in statement for statement in statements)
    assert any("ACCOUNT_POOL_ISP_PROBE_RESULTS" in statement for statement in statements)
    assert any("ACCOUNT_POOL_PROXY_CONFIGURATION_PROFILES" in statement for statement in statements)
    assert any("ACCOUNT_POOL_PROXY_CONFIGURATION_STATE" in statement for statement in statements)
    assert any("ACCOUNT_POOL_ISP_SUBSCRIPTION" in statement for statement in statements)
    assert any("ACCOUNT_POOL_ISP_SUBSCRIPTION_NODES" in statement for statement in statements)
    assert any("ACCOUNT_POOL_PROXY_GATEWAY_ISP_SUBSCRIPTION_BINDINGS" in statement for statement in statements)
    assert any("ACCOUNT_POOL_PROXY_GATEWAY_FRONT_BINDINGS" in statement for statement in statements)
    assert any("REFERENCES ACCOUNT_POOL_PROXY_GATEWAYS(PORT)" in statement for statement in statements)
    assert not any(
        statement.startswith(("ALTER ", "DROP ", "INSERT ", "UPDATE ", "DELETE ", "MERGE "))
        for statement in statements
    )
