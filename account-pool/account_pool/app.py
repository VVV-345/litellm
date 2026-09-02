"""本模块组装号池服务依赖，并提供生产 ASGI 应用入口。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Final

import uvicorn
from fastapi import FastAPI

from account_pool.api import create_router
from account_pool.cliproxy import HttpCLIProxyClient
from account_pool.compose import ComposeRuntime
from account_pool.config import Settings
from account_pool.domain import EnvironmentRecord
from account_pool.repository import PostgresEnvironmentRepository, PostgresProxyProfileRepository
from account_pool.secrets import EnvironmentSecretDeriver
from account_pool.service import EnvironmentService

_LOGGER: Final = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved: Final = settings or Settings()  # pyright: ignore[reportCallIssue]  # values come from environment
    environments: Final = PostgresEnvironmentRepository(resolved.database_url)
    profiles: Final = PostgresProxyProfileRepository(resolved.database_url)
    secrets: Final = EnvironmentSecretDeriver(resolved.secret_seed)
    cli_proxy: Final = HttpCLIProxyClient(secrets)
    runtime: Final = ComposeRuntime(resolved, secrets)
    service: Final = EnvironmentService(
        settings=resolved,
        repository=environments,
        runtime=runtime,
        cli_proxy=cli_proxy,
        proxy_profiles=profiles,
        secrets=secrets,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        resolved.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        await environments.initialize()
        records: Final = await environments.list()
        await _restore_control_plane_connections(runtime, records)
        # 启动后持续重试，Docker 或 CLIProxyAPI 短暂不可用时由后续轮次补偿。
        retry_stopped: Final = asyncio.Event()
        retry_task: Final = asyncio.create_task(_reconcile_pending_configurations_until_cancelled(service, retry_stopped))
        try:
            yield
        finally:
            retry_stopped.set()
            retry_task.cancel()
            try:
                await retry_task
            except asyncio.CancelledError:
                pass
            await cli_proxy.close()

    app: Final = FastAPI(title="LiteLLM Account Pool Manager", version="0.1.0", lifespan=lifespan)
    app.include_router(create_router(service, resolved.manager_token))
    return app


def main() -> None:
    uvicorn.run(create_app(), host="0.0.0.0", port=8091)


async def _reconcile_pending_configurations_until_cancelled(
    service: EnvironmentService,
    stopped: asyncio.Event,
    retry_seconds: float = 5.0,
) -> None:
    while not stopped.is_set():
        try:
            await service.reconcile_pending_configurations()
        except Exception as error:
            _LOGGER.warning("Account pool configuration reconcile failed: %s", error.__class__.__name__)
        try:
            await asyncio.wait_for(stopped.wait(), timeout=retry_seconds)
        except TimeoutError:
            continue


async def _restore_control_plane_connections(
    runtime: ComposeRuntime,
    records: tuple[EnvironmentRecord, ...],
) -> None:
    await asyncio.gather(*(_restore_control_plane_connection(runtime, record) for record in records))


async def _restore_control_plane_connection(runtime: ComposeRuntime, record: EnvironmentRecord) -> None:
    try:
        await runtime.ensure_control_plane_connections(record.id)
    except Exception as error:
        _LOGGER.warning(
            "Failed to restore account pool network for %s: %s",
            record.id,
            error.__class__.__name__,
        )
