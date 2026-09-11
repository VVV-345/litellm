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
from account_pool.batch_repository import PostgresBatchRepository
from account_pool.batch_service import BatchService
from account_pool.card_keys import CardKeyService
from account_pool.channels.base import UnsupportedChannelError
from account_pool.channels.registry import ChannelRegistry
from account_pool.clash import ClashController
from account_pool.config import Settings
from account_pool.domain import ChannelKind, EnvironmentRecord, EnvironmentStatus
from account_pool.error_logs import ErrorLogService
from account_pool.gateway_repository import PostgresLeaseRepository
from account_pool.gateway_service import GatewayService
from account_pool.management_repository import (
    PostgresCardKeyRepository,
    PostgresErrorLogRepository,
    initialize_management_schema,
)
from account_pool.plugins import PluginService, PostgresPluginRepository, parse_plugin_registry
from account_pool.policies import PostgresPolicyRepository
from account_pool.ports import EnvironmentRepository
from account_pool.proxy_gateways import ProxyGatewayService
from account_pool.repository import PostgresEnvironmentRepository, PostgresProxyProfileRepository
from account_pool.secrets import EnvironmentSecretDeriver
from account_pool.service import EnvironmentService
from account_pool.settings import PostgresAccountPoolSettingsRepository

_LOGGER: Final = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved: Final = settings or Settings()  # pyright: ignore[reportCallIssue]  # values come from environment
    environments: Final = PostgresEnvironmentRepository(resolved.database_url)
    profiles: Final = PostgresProxyProfileRepository(resolved.database_url)
    keys: Final = CardKeyService(PostgresCardKeyRepository(resolved.database_url))
    policies: Final = PostgresPolicyRepository(resolved.database_url)
    leases: Final = PostgresLeaseRepository(resolved.database_url)
    batches: Final = PostgresBatchRepository(resolved.database_url)
    settings_repository: Final = PostgresAccountPoolSettingsRepository(resolved.database_url)
    plugin_repository: Final = PostgresPluginRepository(resolved.database_url)
    plugin_service: Final = PluginService(plugin_repository, parse_plugin_registry(resolved.plugin_registry_json))
    logs: Final = ErrorLogService(PostgresErrorLogRepository(resolved.database_url), resolved.log_retention_days)
    secrets: Final = EnvironmentSecretDeriver(resolved.secret_seed)
    channels: Final = ChannelRegistry.default(resolved, secrets)
    channel: Final = channels.channel(ChannelKind.CLIPROXYAPI)
    cli_proxy: Final = channel
    runtime: Final = channel
    controller: Final = (
        ClashController(resolved.clash_controller_url, resolved.clash_secret)
        if resolved.clash_controller_url
        else None
    )
    proxy_gateways: Final = (
        ProxyGatewayService(resolved, profiles, controller)
        if controller is not None
        else ProxyGatewayService.disabled(resolved, profiles)
    )
    service: Final = EnvironmentService(
        settings=resolved,
        repository=environments,
        runtime=runtime,
        cli_proxy=cli_proxy,
        proxy_profiles=profiles,
        secrets=secrets,
        channels=channels,
        proxy_gateways=proxy_gateways,
        error_logs=logs,
        global_settings=settings_repository,
    )
    batch_service: Final = BatchService(batches, environments, service, policies, logs, leases)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        resolved.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        await environments.initialize()
        await initialize_management_schema(resolved.database_url)
        await policies.initialize()
        await leases.initialize()
        await batches.initialize()
        await settings_repository.initialize()
        await plugin_repository.initialize()
        if resolved.clash_controller_url and resolved.clash_gateway_ports:
            await proxy_gateways.sync_profiles()
        records: Final = await environments.list()
        await _restore_control_plane_connections(channels, records)
        # 启动后持续重试，Docker 或 CLIProxyAPI 短暂不可用时由后续轮次补偿。
        retry_stopped: Final = asyncio.Event()
        log_retention_task: Final = asyncio.create_task(logs.maintain(retry_stopped))
        batch_task: Final = asyncio.create_task(batch_service.run_until_cancelled(retry_stopped))
        retry_task: Final = asyncio.create_task(
            _reconcile_pending_configurations_until_cancelled(service, retry_stopped)
        )
        network_retry_task: Final = asyncio.create_task(
            _restore_control_plane_connections_until_cancelled(channels, environments, retry_stopped)
        )
        try:
            yield
        finally:
            retry_stopped.set()
            retry_task.cancel()
            network_retry_task.cancel()
            log_retention_task.cancel()
            batch_task.cancel()
            await asyncio.gather(log_retention_task, return_exceptions=True)
            await asyncio.gather(batch_task, return_exceptions=True)
            try:
                await retry_task
            except asyncio.CancelledError:
                pass
            try:
                await network_retry_task
            except asyncio.CancelledError:
                pass
            for kind in (ChannelKind.OPENAI_COMPATIBLE, ChannelKind.CLIPROXYAPI, ChannelKind.FREEBUFF2API):
                try:
                    await channels.channel(kind).close()
                except UnsupportedChannelError:
                    continue
            if controller is not None:
                await controller.aclose()

    app: Final = FastAPI(title="LiteLLM Account Pool Manager", version="0.1.0", lifespan=lifespan)
    app.include_router(create_router(
        service, resolved.manager_token, keys=keys, logs=logs, environments=environments, policies=policies,
        gateway_service=GatewayService(
            keys, environments, policies, leases, logs, service.gateway_environment, settings_repository
        ),
        batch_service=batch_service,
        settings=settings_repository,
        plugins=plugin_service,
    ))
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
            await service.reconcile_pending_authorizations()
        except Exception as error:
            _LOGGER.warning("Account pool configuration reconcile failed: %s", error.__class__.__name__)
        try:
            await asyncio.wait_for(stopped.wait(), timeout=retry_seconds)
        except TimeoutError:
            continue


async def _restore_control_plane_connections_until_cancelled(
    channels: ChannelRegistry,
    repository: EnvironmentRepository,
    stopped: asyncio.Event,
    retry_seconds: float = 5.0,
) -> None:
    while not stopped.is_set():
        try:
            records: Final = await repository.list()
            await _restore_control_plane_connections(channels, records)
        except Exception as error:
            _LOGGER.warning("Account pool network reconcile failed: %s", error.__class__.__name__)
        try:
            await asyncio.wait_for(stopped.wait(), timeout=retry_seconds)
        except TimeoutError:
            continue


async def _restore_control_plane_connections(
    channels: ChannelRegistry,
    records: tuple[EnvironmentRecord, ...],
) -> None:
    await asyncio.gather(
        *(
            _restore_control_plane_connection(channels, record)
            for record in records
            if record.status is not EnvironmentStatus.DELETING
        )
    )


async def _restore_control_plane_connection(channels: ChannelRegistry, record: EnvironmentRecord) -> None:
    if record.channel is ChannelKind.OPENAI_COMPATIBLE:
        return
    try:
        channels.get(record.channel)
    except UnsupportedChannelError:
        return
    try:
        await channels.channel(record.channel).ensure_control_plane_connections(record.id)
    except UnsupportedChannelError:
        return
    except Exception as error:
        _LOGGER.warning(
            "Failed to restore account pool network for %s: %s",
            record.id,
            error.__class__.__name__,
        )
