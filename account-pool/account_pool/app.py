"""本模块组装号池服务依赖，并提供生产 ASGI 应用入口。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Final
from uuid import UUID

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
from account_pool.quota_scheduler import QuotaRefreshScheduler, RefreshScheduler
from account_pool.repository import PostgresEnvironmentRepository, PostgresProxyProfileRepository
from account_pool.secrets import EnvironmentSecretDeriver
from account_pool.service import EnvironmentService
from account_pool.settings import AccountPoolSettings, PostgresAccountPoolSettingsRepository
from account_pool.upstream_sync import GitHubUpstreamSyncService

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
    upstream_sync: Final = GitHubUpstreamSyncService(resolved)
    secrets: Final = EnvironmentSecretDeriver(resolved.secret_seed)
    channels: Final = ChannelRegistry.default(resolved, secrets)
    channel: Final = channels.channel(ChannelKind.CLIPROXYAPI)
    cli_proxy: Final = channel
    runtime: Final = channel
    controller: Final = (
        ClashController(resolved.clash_controller_url, resolved.clash_secret) if resolved.clash_controller_url else None
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
        policies=policies,
    )
    batch_service: Final = BatchService(
        batches,
        environments,
        service,
        policies,
        logs,
        leases,
        sync_policy=service.sync_policy,
    )
    quota_scheduler: Final = QuotaRefreshScheduler(
        settings_repository,
        lambda: service.refresh_ready_quotas(resolved.quota_refresh_max_concurrency),
    )
    auth_refresh_scheduler: Final = RefreshScheduler(
        settings_repository,
        service.refresh_auth_files,
        interval=lambda values: values.auth_refresh_interval_minutes,
    )

    async def sync_global_settings(
        values: AccountPoolSettings,
        *,
        rollback_on_failure: bool = True,
    ) -> tuple[UUID, ...]:
        failed: Final = await service.sync_global_settings(values, rollback_on_failure=rollback_on_failure)
        if not failed:
            _notify_refresh_schedulers_after_settings_sync(quota_scheduler, auth_refresh_scheduler)
        return failed

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
        # 旧卡片可能在新增运行配置前已创建，启动时补一次同步以迁移插件目录等持久配置。
        try:
            failed_settings_cards: Final = await sync_global_settings(
                (await settings_repository.get()).values,
                rollback_on_failure=False,
            )
            if failed_settings_cards:
                _LOGGER.warning("Account pool startup settings sync failed for %d cards", len(failed_settings_cards))
        except Exception as error:
            _LOGGER.warning("Account pool startup settings sync failed: %s", error.__class__.__name__)
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
        quota_refresh_task: Final = asyncio.create_task(quota_scheduler.run_until_cancelled(retry_stopped))
        auth_refresh_task: Final = asyncio.create_task(auth_refresh_scheduler.run_until_cancelled(retry_stopped))
        try:
            yield
        finally:
            retry_stopped.set()
            retry_task.cancel()
            network_retry_task.cancel()
            log_retention_task.cancel()
            batch_task.cancel()
            quota_refresh_task.cancel()
            auth_refresh_task.cancel()
            await asyncio.gather(log_retention_task, return_exceptions=True)
            await asyncio.gather(batch_task, return_exceptions=True)
            await asyncio.gather(quota_refresh_task, return_exceptions=True)
            await asyncio.gather(auth_refresh_task, return_exceptions=True)
            try:
                await retry_task
            except asyncio.CancelledError:
                pass
            try:
                await network_retry_task
            except asyncio.CancelledError:
                pass
            for kind in (ChannelKind.OPENAI_COMPATIBLE, ChannelKind.CLIPROXYAPI):
                try:
                    await channels.channel(kind).close()
                except UnsupportedChannelError:
                    continue
            if controller is not None:
                await controller.aclose()
            await upstream_sync.close()

    app: Final = FastAPI(title="LiteLLM Account Pool Manager", version="0.1.0", lifespan=lifespan)
    app.include_router(
        create_router(
            service,
            resolved.manager_token,
            keys=keys,
            logs=logs,
            environments=environments,
            policies=policies,
            gateway_service=GatewayService(
                keys, environments, policies, leases, logs, service.gateway_environment, settings_repository
            ),
            batch_service=batch_service,
            settings=settings_repository,
            plugins=plugin_service,
            sync_settings=sync_global_settings,
            sync_policy=service.sync_policy,
            upstream_sync=upstream_sync,
            quota_scheduler=quota_scheduler,
            auth_refresh_scheduler=auth_refresh_scheduler,
        )
    )
    return app


def main() -> None:
    uvicorn.run(create_app(), host="0.0.0.0", port=8091)


def _notify_refresh_schedulers_after_settings_sync(
    quota_scheduler: QuotaRefreshScheduler,
    auth_refresh_scheduler: RefreshScheduler,
) -> None:
    quota_scheduler.settings_changed()
    auth_refresh_scheduler.settings_changed()


async def _reconcile_pending_configurations_until_cancelled(
    service: EnvironmentService,
    stopped: asyncio.Event,
    retry_seconds: float = 5.0,
) -> None:
    while not stopped.is_set():
        try:
            await service.reconcile_pending_configurations()
            await service.reconcile_pending_authorizations()
            await service.reconcile_pending_deletions()
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


async def _refresh_ready_quotas_until_cancelled(
    service: EnvironmentService,
    stopped: asyncio.Event,
    refresh_seconds: float = 300.0,
    max_concurrency: int = 3,
) -> None:
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=refresh_seconds)
            continue
        except TimeoutError:
            pass
        try:
            failed: Final = await service.refresh_ready_quotas(max_concurrency)
            if failed:
                _LOGGER.warning("Account pool quota refresh failed for %d ready cards", len(failed))
        except Exception as error:
            _LOGGER.warning("Account pool quota refresh failed: %s", error.__class__.__name__)


async def _restore_control_plane_connections(
    channels: ChannelRegistry,
    records: tuple[EnvironmentRecord, ...],
) -> None:
    await asyncio.gather(
        *(
            _restore_control_plane_connection(channels, record)
            for record in records
            if record.status is not EnvironmentStatus.DELETING and record.channel is ChannelKind.CLIPROXYAPI
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
