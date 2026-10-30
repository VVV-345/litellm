"""单卡运行时维护入口，锁定停用卡并检查在途租约，不批量更新或改写认证卷。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping
from typing import Final
from uuid import UUID

import httpx
import psycopg

from account_pool.channels.cliproxyapi.client import HttpCLIProxyClient
from account_pool.compose_runtime import ComposeRuntime
from account_pool.config import Settings
from account_pool.domain import ChannelKind, EnvironmentRecord, EnvironmentStatus
from account_pool.repository import database_connection
from account_pool.shared.secrets import EnvironmentSecretDeriver

_CAPABILITY_PATH: Final = "/v0/management/codex/managed-identity"


async def update_runtime(
    connection: psycopg.AsyncConnection[Mapping[str, object]],
    runtime: ComposeRuntime,
    client: httpx.AsyncClient,
    environment_id: UUID,
) -> bool:
    # 与 CredentialOwnership 使用同一锁键，再锁环境行，串行化配置和网关租约领取。
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(environment_id),))
    cursor: Final = await connection.execute(
        "SELECT payload FROM account_pool_environments WHERE id = %s FOR UPDATE", (environment_id,)
    )
    row: Final = await cursor.fetchone()
    if row is None:
        raise ValueError("card not found")
    record: Final = EnvironmentRecord.model_validate(row["payload"])
    if (
        record.channel is not ChannelKind.CLIPROXYAPI
        or record.enabled
        or record.status is not EnvironmentStatus.DISABLED
        or record.configuration_pending
        or record.desired_configuration_version != record.observed_configuration_version
        or record.auth_file_name is not None and not record.auth_file_disabled
    ):
        raise ValueError("maintenance requires a disabled CLIProxyAPI card with its configuration fully applied")
    leases: Final = await connection.execute(
        "SELECT 1 FROM account_pool_leases WHERE account_id = %s AND expires_at > now() LIMIT 1", (environment_id,)
    )
    if await leases.fetchone() is not None:
        raise ValueError("card still has active requests; wait for leases to drain")
    secrets: Final = EnvironmentSecretDeriver(runtime.settings.secret_seed)

    async def verify(current: EnvironmentRecord) -> None:
        async with asyncio.timeout(60):
            while True:
                try:
                    response: Final = await client.get(f"http://cliproxy-{current.id.hex}:8317{_CAPABILITY_PATH}")
                    break
                except httpx.TransportError:
                    await asyncio.sleep(0.2)
            if response.status_code != 401:
                raise ValueError("managed-identity must reject unauthenticated requests with 401")
            await HttpCLIProxyClient(secrets, client=client).require_managed_identity(current)

    return await runtime.align_runtime_image(record, verify=verify)


async def _run(environment_id: UUID, image: str) -> None:
    settings: Final = Settings(cli_proxy_image=image)  # pyright: ignore[reportCallIssue]  # Remaining settings use the Manager environment.
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver(settings.secret_seed))
    async with database_connection(settings.database_url) as connection, httpx.AsyncClient(
        timeout=2, trust_env=False
    ) as client:
        changed: Final = await update_runtime(connection, runtime, client, environment_id)
    sys.stdout.write(f"card={environment_id} image={'updated and verified' if changed else 'unchanged'} enabled=false\n")


def main() -> None:
    parser: Final = argparse.ArgumentParser(description="Update one disabled CLIProxyAPI card to a verified image digest")
    parser.add_argument("environment_id", type=UUID)
    parser.add_argument("--image", required=True, help="Published ghcr.io/vvv-345/litellm-cliproxyapi@sha256:... reference")
    arguments: Final = parser.parse_args()
    try:
        asyncio.run(_run(arguments.environment_id, arguments.image))
    except Exception as error:
        # 不回显数据库地址、管理密钥或外部 Docker 原始错误。
        raise SystemExit(
            f"runtime maintenance failed ({type(error).__name__}); no enable action was performed; "
            "inspect card and runtime state before retry"
        ) from None


if __name__ == "__main__":
    main()
