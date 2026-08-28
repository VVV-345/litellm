"""组装号池 API、渠道服务和调度控制面。"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Literal
from urllib.parse import quote
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import SecretStr, TypeAdapter

from account_pool.audit.postgres import PostgresManagementAuditRepository
from account_pool.auth.actor import (
    ActorAction,
    ActorContext,
    ActorVerificationFailure,
    ActorVerificationFailureCode,
    verify_actor_envelope,
)
from account_pool.catalog.models import AdministrativeState, BindingOwnership, ChannelList
from account_pool.catalog.postgres import PostgresCatalogRepository
from account_pool.catalog.query import ChannelCatalogQueryService, ChannelCatalogReader
from account_pool.catalog.service import CatalogService
from account_pool.config import Settings, load_pool_config
from account_pool.details import (
    ChannelAggregateDetail,
    ChannelAggregateFailure,
    ChannelAggregateReader,
    ChannelAggregateService,
)
from account_pool.domain.provider_source import (
    ProviderServiceManifest,
    ProviderValidationRequest,
    ProviderValidationResult,
)
from account_pool.events import (
    EventLogFailure,
    EventLogFailureCode,
    EventLogPage,
    EventLogReader,
    EventQuery,
    PostgresEventLogRepository,
)
from account_pool.health.models import HealthProbeRequest, HealthProbeResult
from account_pool.health.postgres import PostgresHealthEventRepository
from account_pool.health.probe import ActiveHealthProbeService, HealthProbeManager
from account_pool.health.repository import HealthEventRepository
from account_pool.health.service import (
    ChannelHealthDetail,
    ChannelHealthDetailFailure,
    ChannelHealthDetailReader,
    ChannelHealthQueryService,
    HealthEventRecorder,
)
from account_pool.management import LiteLLMAdminClient, PoolManager
from account_pool.models import (
    AccountMutation,
    AccountView,
    AcquireRequest,
    AcquireSuccess,
    DeploymentInput,
    Health,
    HeartbeatRequest,
    Lease,
    LiteLLMStatus,
    ManagementResult,
    ModelSummary,
    OperationResult,
    PolicyUpdate,
    ReleaseRequest,
    RouteEntry,
    SettlementEventRequest,
    SettleRequest,
    StatsView,
)
from account_pool.monitoring import (
    PROMETHEUS_CONTENT_TYPE,
    WorkerMonitorRegistry,
    WorkerName,
    WorkerRegistration,
    WorkerStateList,
    render_prometheus_metrics,
    run_monitored_service,
    run_worker_loop,
)
from account_pool.operational.postgres import PostgresOperationalEventRepository
from account_pool.operational.request_lifecycle import RequestEventRecorder, RequestEventStateStore
from account_pool.operational.restrictions import RestrictionEventRecorder, RestrictionEventStateStore
from account_pool.overview import (
    AccountPoolOverview,
    AccountPoolOverviewFailure,
    AccountPoolOverviewReader,
    AccountPoolOverviewService,
)
from account_pool.parsing.export_retry import ParserExportRetryLoop, ParserExportRetryManager
from account_pool.parsing.imports.models import (
    SnapshotImportFailure,
    SnapshotImportFailureCode,
    SnapshotImportRequest,
    SnapshotImportSuccess,
)
from account_pool.parsing.imports.service import SnapshotImporter, SnapshotImportService
from account_pool.parsing.overrides.commands import (
    OverrideMutationFailure,
    OverrideMutationFailureCode,
    OverrideMutationSuccess,
    OverrideRevokeRequest,
    OverrideSetRequest,
)
from account_pool.parsing.overrides.postgres import PostgresOverrideEventRepository
from account_pool.parsing.overrides.service import ParserOverrideService, ParserOverrideWriter
from account_pool.parsing.postgres import PostgresParserRunRepository
from account_pool.parsing.projection import ParserRuntimeConfigEnricher
from account_pool.parsing.public_metadata.postgres import PostgresPublicMetadataTaskRepository
from account_pool.parsing.public_metadata.service import PublicMetadataTaskLoop, PublicMetadataTaskManager
from account_pool.parsing.public_metadata.source import PublicMetadataSourceRegistry
from account_pool.parsing.registry import ParserRegistry
from account_pool.parsing.service import (
    EffectiveParserData,
    ParserDataFailure,
    ParserDataFailureCode,
    ParserDataReader,
    ParserDataService,
    ParserRunHistory,
)
from account_pool.parsing.snapshots import ParserSnapshot, ParserSnapshotStore
from account_pool.parsing.tasks.models import (
    ParserTaskAccepted,
    ParserTaskOperationFailure,
    ParserTaskOperationFailureCode,
    ParserTaskStartRequest,
    ParserTaskView,
)
from account_pool.parsing.tasks.postgres import PostgresParserTaskRepository
from account_pool.parsing.tasks.service import ParserTaskManager, ParserTaskService
from account_pool.parsing.worker import ParserWorker
from account_pool.provider_services.glm import GlmOfficialProviderService
from account_pool.provider_services.lmu_static_metadata import LmuStaticMetadataProviderService
from account_pool.provider_services.new_api import NewApiProviderService
from account_pool.provider_services.openai_compatible import OpenAICompatibleProviderService
from account_pool.provider_services.parser_registry import build_parser_registry
from account_pool.provider_services.registry import ProviderServiceRegistry
from account_pool.quota.durable import DurableQuotaStateStore
from account_pool.quota.postgres import PostgresQuotaRuntimeRepository
from account_pool.retention import (
    EncryptedEventArchive,
    EventRetentionService,
    PostgresRetentionRepository,
    RetentionFailure,
    RetentionPolicy,
    RetentionRunner,
    decode_archive_key,
)
from account_pool.routing.latency_postgres import PostgresLatencyMetricRepository
from account_pool.routing.latency_store import DurableLatencyStateStore
from account_pool.routing.models import (
    RoutingCandidateMutation,
    RoutingFailure,
    RoutingFailureCode,
    RoutingPolicyMutation,
    RoutingPolicyResult,
    RoutingPolicyState,
    RoutingVersionMutation,
)
from account_pool.routing.postgres import PostgresRoutingPolicyRepository
from account_pool.routing.service import RoutingPolicyService
from account_pool.runtime_contract import RuntimeConfigSnapshot, build_runtime_config_snapshot
from account_pool.runtime_projection import RuntimeProjector
from account_pool.scheduler import Scheduler
from account_pool.store import MemoryStateStore, RedisStateStore, StateStore
from account_pool.sync.litellm import LiteLLMDeploymentSyncAdapter
from account_pool.sync.models import DeleteMode
from account_pool.sync.postgres import PostgresSyncOperationRepository
from account_pool.sync.service import (
    ChannelBindingMutation,
    ChannelDeleteRequest,
    ChannelDetail,
    ChannelManagementFailure,
    ChannelManagementResult,
    ChannelManagementService,
    ChannelManager,
    ChannelMutation,
    ChannelOperationView,
    ChannelReconcileRequest,
    ExternalDeploymentDeleteRequest,
)

_SNAPSHOT_DOCUMENT: Final[TypeAdapter[dict[UUID, ParserSnapshot]]] = TypeAdapter(dict[UUID, ParserSnapshot])
_LOGGER: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Runtime:
    settings: Settings
    scheduler: Scheduler
    store: StateStore
    manager: PoolManager
    admin: LiteLLMAdminClient
    provider_services: ProviderServiceRegistry
    parser_registry: ParserRegistry
    catalog: ChannelCatalogReader | None
    channel_management: ChannelManager | None
    parser_data: ParserDataReader | None
    parser_overrides: ParserOverrideWriter | None
    parser_tasks: ParserTaskManager | None
    parser_export_retries: ParserExportRetryManager | None
    public_metadata_tasks: PublicMetadataTaskManager | None
    snapshot_importer: SnapshotImporter | None
    health_events: HealthEventRepository | None
    health_details: ChannelHealthDetailReader
    health_probes: HealthProbeManager
    routing_policies: RoutingPolicyService | None
    overview: AccountPoolOverviewReader | None
    event_log: EventLogReader | None
    channel_details: ChannelAggregateReader | None
    retention: RetentionRunner | None
    worker_monitor: WorkerMonitorRegistry


def create_app(
    settings: Settings | None = None,
    store: StateStore | None = None,
    proxy_client: httpx.AsyncClient | None = None,
    catalog: ChannelCatalogReader | None = None,
    channel_management: ChannelManager | None = None,
    parser_data: ParserDataReader | None = None,
    parser_overrides: ParserOverrideWriter | None = None,
    parser_tasks: ParserTaskManager | None = None,
    parser_export_retries: ParserExportRetryManager | None = None,
    public_metadata_tasks: PublicMetadataTaskManager | None = None,
    public_metadata_sources: PublicMetadataSourceRegistry | None = None,
    snapshot_importer: SnapshotImporter | None = None,
    health_events: HealthEventRepository | None = None,
    health_details: ChannelHealthDetailReader | None = None,
    health_probes: HealthProbeManager | None = None,
    routing_policies: RoutingPolicyService | None = None,
    overview: AccountPoolOverviewReader | None = None,
    event_log: EventLogReader | None = None,
    channel_details: ChannelAggregateReader | None = None,
    retention: RetentionRunner | None = None,
    worker_monitor: WorkerMonitorRegistry | None = None,
) -> FastAPI:
    resolved_settings: Final = settings or Settings.from_env()
    base_store: Final = store or _build_store(resolved_settings)
    request_events: Final = _build_request_events(resolved_settings)
    restriction_events: Final = _build_restriction_events(resolved_settings)
    restricted_store: Final = (
        base_store if restriction_events is None else RestrictionEventStateStore(base_store, restriction_events)
    )
    resolved_store: Final = (
        restricted_store if request_events is None else RequestEventStateStore(restricted_store, request_events)
    )
    owns_client: Final = proxy_client is None
    client: Final = proxy_client or httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5))
    config: Final = load_pool_config(resolved_settings.config_path)
    scheduler: Final = Scheduler(
        config=config,
        store=resolved_store,
        lease_ttl_seconds=resolved_settings.lease_ttl_seconds,
        request_events=request_events,
    )
    resolved_health_events: Final = (
        health_events if health_events is not None else _build_health_events(resolved_settings)
    )
    resolved_health_recorder: Final = (
        None
        if resolved_health_events is None
        else HealthEventRecorder(accounts=scheduler, events=resolved_health_events)
    )
    resolved_health_details: Final = (
        health_details
        if health_details is not None
        else ChannelHealthQueryService(
            accounts=scheduler,
            store=resolved_store,
            events=resolved_health_events,
        )
    )
    admin: Final = LiteLLMAdminClient(
        client=client,
        base_url=resolved_settings.litellm_url,
        admin_key=resolved_settings.litellm_admin_key,
    )
    manager: Final = PoolManager(
        scheduler=scheduler,
        admin=admin,
        config_path=resolved_settings.config_path,
    )
    provider_services: Final = ProviderServiceRegistry(
        (
            GlmOfficialProviderService(client),
            LmuStaticMetadataProviderService(),
            OpenAICompatibleProviderService(client),
            NewApiProviderService(client),
        )
    )
    parser_registry: Final = build_parser_registry()
    resolved_public_metadata_sources: Final = public_metadata_sources or PublicMetadataSourceRegistry(())
    resolved_retention: Final = retention if retention is not None else _build_retention(resolved_settings)
    resolved_catalog: Final = catalog if catalog is not None else _build_catalog(resolved_settings)
    resolved_parser_data: Final = parser_data if parser_data is not None else _build_parser_data(resolved_settings)
    resolved_routing_policies: Final = (
        routing_policies
        if routing_policies is not None
        else _build_routing_policies(
            settings=resolved_settings,
            scheduler=scheduler,
            parser_data=resolved_parser_data,
        )
    )
    resolved_channel_management: Final = (
        channel_management
        if channel_management is not None
        else _build_channel_management(
            settings=resolved_settings,
            scheduler=scheduler,
            client=client,
            parser_data=resolved_parser_data,
        )
    )
    resolved_parser_overrides: Final = (
        parser_overrides if parser_overrides is not None else _build_parser_overrides(resolved_settings)
    )
    build_public_metadata_tasks: Final = (
        public_metadata_tasks is None
        and resolved_catalog is not None
        and bool(resolved_public_metadata_sources.provider_ids)
    )
    build_parser_tasks: Final = parser_tasks is None
    build_parser_export_retries: Final = parser_export_retries is None and (
        build_parser_tasks or build_public_metadata_tasks
    )
    resolved_worker_monitor: Final = worker_monitor or _build_worker_monitor(
        settings=resolved_settings,
        reconciler_enabled=resolved_channel_management is not None,
        parser_export_enabled=(
            parser_export_retries is not None
            or (resolved_settings.database_url is not None and build_parser_export_retries)
        ),
        public_metadata_enabled=(
            public_metadata_tasks is not None
            or (resolved_settings.database_url is not None and build_public_metadata_tasks)
        ),
        retention_enabled=resolved_retention is not None,
    )
    parser_runtime: Final = (
        None
        if not (build_parser_tasks or build_parser_export_retries or build_public_metadata_tasks)
        else _build_parser_runtime(
            settings=resolved_settings,
            providers=provider_services,
            registry=parser_registry,
            catalog=resolved_catalog,
            public_metadata_sources=resolved_public_metadata_sources,
            build_tasks=build_parser_tasks,
            build_export_retries=build_parser_export_retries,
            build_public_metadata_tasks=build_public_metadata_tasks,
            worker_monitor=resolved_worker_monitor,
        )
    )
    resolved_parser_tasks: Final = parser_tasks if parser_tasks is not None else _runtime_tasks(parser_runtime)
    resolved_parser_export_retries: Final = (
        parser_export_retries if parser_export_retries is not None else _runtime_export_retries(parser_runtime)
    )
    resolved_public_metadata_tasks: Final = (
        public_metadata_tasks if public_metadata_tasks is not None else _runtime_public_metadata_tasks(parser_runtime)
    )
    resolved_snapshot_importer: Final = (
        snapshot_importer if snapshot_importer is not None else _build_snapshot_importer(resolved_settings)
    )
    resolved_health_probes: Final = health_probes or ActiveHealthProbeService(
        accounts=scheduler,
        store=resolved_store,
        client=client,
        litellm_url=resolved_settings.litellm_url,
        admin_key=resolved_settings.litellm_admin_key,
        lease_ttl_seconds=resolved_settings.lease_ttl_seconds,
        events=resolved_health_events,
        recorder=resolved_health_recorder,
        idle_probe_after_seconds=resolved_settings.health_idle_probe_after_seconds,
    )
    resolved_overview: Final = (
        overview
        if overview is not None
        else (
            None
            if resolved_catalog is None
            else AccountPoolOverviewService(
                catalog=resolved_catalog,
                runtime=scheduler,
                parser_data=resolved_parser_data,
                health_events=resolved_health_events,
            )
        )
    )
    resolved_event_log: Final = (
        event_log
        if event_log is not None
        else (
            None
            if resolved_settings.database_url is None
            else PostgresEventLogRepository(
                resolved_settings.database_url,
                schema=resolved_settings.database_schema,
            )
        )
    )
    resolved_channel_details: Final = (
        channel_details
        if channel_details is not None
        else (
            None
            if resolved_channel_management is None or resolved_overview is None
            else ChannelAggregateService(
                channels=resolved_channel_management,
                overview=resolved_overview,
                parser_data=resolved_parser_data,
                health=resolved_health_details,
                routing=scheduler,
                events=resolved_event_log,
            )
        )
    )
    runtime: Final = Runtime(
        settings=resolved_settings,
        scheduler=scheduler,
        store=resolved_store,
        manager=manager,
        admin=admin,
        provider_services=provider_services,
        parser_registry=parser_registry,
        catalog=resolved_catalog,
        channel_management=resolved_channel_management,
        parser_data=resolved_parser_data,
        parser_overrides=resolved_parser_overrides,
        parser_tasks=resolved_parser_tasks,
        parser_export_retries=resolved_parser_export_retries,
        public_metadata_tasks=resolved_public_metadata_tasks,
        snapshot_importer=resolved_snapshot_importer,
        health_events=resolved_health_events,
        health_details=resolved_health_details,
        health_probes=resolved_health_probes,
        routing_policies=resolved_routing_policies,
        overview=resolved_overview,
        event_log=resolved_event_log,
        channel_details=resolved_channel_details,
        retention=resolved_retention,
        worker_monitor=resolved_worker_monitor,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        if resolved_settings.database_url is not None:
            await RuntimeProjector(
                CatalogService(
                    PostgresCatalogRepository(
                        resolved_settings.database_url,
                        schema=resolved_settings.database_schema,
                    )
                ),
                scheduler,
                enricher=_parser_runtime_enricher(resolved_parser_data),
            ).project()
        else:
            await scheduler.initialize()
        if resolved_parser_tasks is not None:
            await resolved_parser_tasks.initialize()
        if resolved_public_metadata_tasks is not None:
            await resolved_public_metadata_tasks.initialize()
        reaper: Final = asyncio.create_task(_run_reaper(resolved_store, resolved_worker_monitor))
        reconciler: Final = (
            None
            if resolved_channel_management is None
            else asyncio.create_task(
                _run_reconciler(
                    resolved_channel_management,
                    resolved_settings.reconcile_interval_seconds,
                    resolved_worker_monitor,
                )
            )
        )
        parser_export_retry_task: Final = (
            None
            if resolved_parser_export_retries is None
            else asyncio.create_task(
                run_monitored_service(
                    worker=WorkerName.PARSER_EXPORT_RETRY,
                    service=resolved_parser_export_retries.run,
                    monitor=resolved_worker_monitor,
                    logger=_LOGGER,
                    failure_message="Parser export retry worker stopped unexpectedly",
                )
            )
        )
        public_metadata_task: Final = (
            None
            if resolved_public_metadata_tasks is None
            else asyncio.create_task(
                run_monitored_service(
                    worker=WorkerName.PUBLIC_METADATA,
                    service=resolved_public_metadata_tasks.run,
                    monitor=resolved_worker_monitor,
                    logger=_LOGGER,
                    failure_message="Public metadata worker stopped unexpectedly",
                )
            )
        )
        health_probe_task: Final = (
            None
            if resolved_settings.health_probe_interval_seconds <= 0
            else asyncio.create_task(
                _run_health_probes(
                    resolved_health_probes,
                    resolved_settings.health_probe_interval_seconds,
                    resolved_worker_monitor,
                )
            )
        )
        retention_task: Final = (
            None
            if resolved_retention is None
            else asyncio.create_task(
                run_worker_loop(
                    worker=WorkerName.EVENT_RETENTION,
                    cycle=resolved_retention.run_once,
                    interval_seconds=resolved_settings.retention_interval_seconds,
                    monitor=resolved_worker_monitor,
                    logger=_LOGGER,
                    failure_message="Event retention worker cycle crashed",
                    initial_delay=True,
                    result_is_success=lambda result: not isinstance(result, RetentionFailure),
                )
            )
        )
        try:
            yield
        finally:
            reaper.cancel()
            with suppress(asyncio.CancelledError):
                await reaper
            if reconciler is not None:
                reconciler.cancel()
                with suppress(asyncio.CancelledError):
                    await reconciler
            if parser_export_retry_task is not None:
                parser_export_retry_task.cancel()
                with suppress(asyncio.CancelledError):
                    await parser_export_retry_task
            if public_metadata_task is not None:
                public_metadata_task.cancel()
                with suppress(asyncio.CancelledError):
                    await public_metadata_task
            if health_probe_task is not None:
                health_probe_task.cancel()
                with suppress(asyncio.CancelledError):
                    await health_probe_task
            if retention_task is not None:
                retention_task.cancel()
                with suppress(asyncio.CancelledError):
                    await retention_task
            if resolved_parser_tasks is not None:
                await resolved_parser_tasks.close()
            await resolved_store.close()
            if owns_client:
                await client.aclose()

    application: Final = FastAPI(
        title="LiteLLM Account Pool",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
    )
    application.state.runtime = runtime

    def require_internal_token(x_account_pool_token: str | None = Header(default=None)) -> None:
        expected: Final = resolved_settings.internal_token
        if expected is None:
            raise HTTPException(status_code=503, detail="Account-pool service token is not configured")
        if x_account_pool_token is None or not secrets.compare_digest(x_account_pool_token, expected):
            raise HTTPException(status_code=401, detail="Invalid account-pool service token")

    async def require_litellm_admin(authorization: str | None = Header(default=None)) -> str:
        scheme, _, access_token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not access_token or not await admin.authorize(access_token):
            raise HTTPException(status_code=401, detail="LiteLLM 管理令牌无效")
        return access_token

    async def forward_ui_management_request(
        request: Request,
        method: Literal["POST", "PUT", "DELETE"],
        path: str,
        access_token: str,
    ) -> Response:
        idempotency_key: Final = request.headers.get("idempotency-key")
        headers: Final = {
            "accept": "application/json",
            "authorization": f"Bearer {access_token}",
            "content-type": "application/json",
            **({"idempotency-key": idempotency_key} if idempotency_key is not None else {}),
        }
        try:
            upstream: Final = await client.request(
                method=method,
                url=f"{resolved_settings.litellm_url.rstrip('/')}/account_pool{path}",
                headers=headers,
                content=await request.body(),
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="LiteLLM 管理接口连接失败") from exc
        response_headers: Final = {
            name: value
            for name, value in upstream.headers.items()
            if name.casefold() in {"content-type", "cache-control"}
        }
        return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)

    async def healthz() -> OperationResult:
        return OperationResult(ok=True)

    async def runtime_config() -> RuntimeConfigSnapshot:
        return build_runtime_config_snapshot(
            scheduler.config(),
            lease_ttl_seconds=resolved_settings.lease_ttl_seconds,
            maximum_lease_seconds=resolved_settings.maximum_lease_seconds,
        )

    async def worker_states() -> WorkerStateList:
        return resolved_worker_monitor.snapshot()

    async def metrics() -> Response:
        return Response(
            content=render_prometheus_metrics(resolved_worker_monitor.snapshot()),
            headers={"content-type": PROMETHEUS_CONTENT_TYPE, "cache-control": "no-store"},
        )

    async def accounts(response: Response) -> tuple[AccountView, ...]:
        _mark_accounts_compatibility_deprecated(response)
        snapshots: Final = {item.account_id: item for item in await scheduler.account_snapshots()}
        return tuple(
            AccountView(
                id=account.id,
                display_name=account.display_name,
                provider=account.provider,
                group=account.group,
                base_url_display=account.base_url_display,
                models=tuple(deployment.public_model for deployment in account.deployments if deployment.enabled),
                priority=account.priority,
                weight=account.weight,
                quotas=account.quotas,
                deployments=account.deployments,
                runtime=snapshots[account.id],
            )
            for account in scheduler.account_configs()
        )

    async def models() -> tuple[ModelSummary, ...]:
        return tuple(
            await asyncio.gather(*(_model_summary(scheduler=scheduler, model=model) for model in scheduler.models()))
        )

    async def routing_table(model: str) -> tuple[RouteEntry, ...]:
        return await scheduler.route_table(model)

    async def stats() -> StatsView:
        snapshots: Final = await scheduler.account_snapshots()
        return StatsView(
            models=len(scheduler.models()),
            accounts=len(snapshots),
            available_accounts=sum(
                1
                for item in snapshots
                if item.enabled and item.health not in (Health.DISABLED, Health.UNHEALTHY, Health.COOLDOWN)
            ),
            inflight=sum(item.inflight for item in snapshots),
            max_concurrency=sum(item.max_concurrency for item in snapshots),
        )

    async def litellm_status() -> LiteLLMStatus:
        return await admin.status()

    async def provider_service_manifests() -> tuple[ProviderServiceManifest, ...]:
        return provider_services.manifests()

    async def channels() -> ChannelList:
        if resolved_catalog is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        return await resolved_catalog.list_channels()

    async def overview_data() -> AccountPoolOverview:
        if resolved_overview is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        result: Final = await resolved_overview.read()
        match result:
            case AccountPoolOverview():
                return result
            case AccountPoolOverviewFailure():
                raise HTTPException(status_code=503, detail={"code": result.code, "retryable": result.retryable})

    async def event_log_data(query: Annotated[EventQuery, Depends()]) -> EventLogPage:
        if resolved_event_log is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        result: Final = await resolved_event_log.list_events(query)
        match result:
            case EventLogPage():
                return result
            case EventLogFailure():
                status_code: Final = {
                    EventLogFailureCode.INVALID_CURSOR: 422,
                    EventLogFailureCode.INVALID_STORED_DATA: 500,
                    EventLogFailureCode.DATABASE_UNAVAILABLE: 503,
                }[result.code]
                raise HTTPException(
                    status_code=status_code, detail={"code": result.code, "retryable": result.retryable}
                )

    async def channel_detail(channel_id: UUID) -> ChannelDetail:
        service: Final = _require_channel_management(resolved_channel_management)
        result: Final = await service.detail(channel_id)
        if isinstance(result, ChannelManagementFailure):
            raise _channel_management_http_error(result)
        return result

    async def channel_aggregate_detail(channel_id: UUID) -> ChannelAggregateDetail:
        if resolved_channel_details is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        result: Final = await resolved_channel_details.read_channel(channel_id)
        if isinstance(result, ChannelAggregateFailure):
            status_code: Final = 404 if result.code == "channel_not_found" else 503
            raise HTTPException(status_code=status_code, detail={"code": result.code, "retryable": result.retryable})
        return result

    async def channel_operation(operation_id: UUID) -> ChannelOperationView:
        service: Final = _require_channel_management(resolved_channel_management)
        result: Final = await service.operation(operation_id)
        if isinstance(result, ChannelManagementFailure):
            raise _channel_management_http_error(result)
        return result

    async def create_channel(
        body: ChannelMutation,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ChannelOperationView:
        return await _channel_mutation_result(
            _require_channel_management(resolved_channel_management).create(
                body,
                _required_idempotency_key(idempotency_key),
                _verified_actor(
                    token=x_account_pool_actor,
                    request_id=x_account_pool_request_id,
                    expected_action=ActorAction.CHANNEL_CREATE,
                    secret=resolved_settings.actor_secret,
                ),
            )
        )

    async def import_channel(
        body: ChannelMutation,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ChannelOperationView:
        return await _channel_mutation_result(
            _require_channel_management(resolved_channel_management).import_channel(
                body,
                _required_idempotency_key(idempotency_key),
                _verified_actor(
                    token=x_account_pool_actor,
                    request_id=x_account_pool_request_id,
                    expected_action=ActorAction.CHANNEL_IMPORT,
                    secret=resolved_settings.actor_secret,
                ),
            )
        )

    async def update_channel(
        channel_id: UUID,
        body: ChannelMutation,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ChannelOperationView:
        return await _channel_mutation_result(
            _require_channel_management(resolved_channel_management).update(
                channel_id,
                body,
                _required_idempotency_key(idempotency_key),
                _verified_actor(
                    token=x_account_pool_actor,
                    request_id=x_account_pool_request_id,
                    expected_action=ActorAction.CHANNEL_UPDATE,
                    secret=resolved_settings.actor_secret,
                ),
            )
        )

    async def detach_channel(
        channel_id: UUID,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ChannelOperationView:
        return await _channel_mutation_result(
            _require_channel_management(resolved_channel_management).detach(
                channel_id,
                _required_idempotency_key(idempotency_key),
                _verified_actor(
                    token=x_account_pool_actor,
                    request_id=x_account_pool_request_id,
                    expected_action=ActorAction.CHANNEL_DETACH,
                    secret=resolved_settings.actor_secret,
                ),
            )
        )

    async def delete_channel(
        channel_id: UUID,
        body: ChannelDeleteRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ChannelOperationView:
        return await _channel_mutation_result(
            _require_channel_management(resolved_channel_management).delete(
                channel_id,
                body,
                _required_idempotency_key(idempotency_key),
                _verified_actor(
                    token=x_account_pool_actor,
                    request_id=x_account_pool_request_id,
                    expected_action=ActorAction.CHANNEL_DELETE,
                    secret=resolved_settings.actor_secret,
                ),
            )
        )

    async def delete_external_deployment(
        channel_id: UUID,
        binding_id: UUID,
        body: ExternalDeploymentDeleteRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ChannelOperationView:
        return await _channel_mutation_result(
            _require_channel_management(resolved_channel_management).delete_external(
                channel_id,
                binding_id,
                body,
                _required_idempotency_key(idempotency_key),
                _verified_actor(
                    token=x_account_pool_actor,
                    request_id=x_account_pool_request_id,
                    expected_action=ActorAction.CHANNEL_DELETE_EXTERNAL_DEPLOYMENT,
                    secret=resolved_settings.actor_secret,
                ),
            )
        )

    async def reconcile_channel(
        channel_id: UUID,
        body: ChannelReconcileRequest,
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ChannelOperationView:
        return await _channel_mutation_result(
            _require_channel_management(resolved_channel_management).reconcile(
                channel_id,
                body,
                _verified_actor(
                    token=x_account_pool_actor,
                    request_id=x_account_pool_request_id,
                    expected_action=ActorAction.CHANNEL_RECONCILE,
                    secret=resolved_settings.actor_secret,
                ),
            )
        )

    async def validate_provider(body: ProviderValidationRequest) -> ProviderValidationResult:
        return await provider_services.validate(body)

    async def parser_runs(channel_id: UUID, limit: int = 25) -> ParserRunHistory:
        if resolved_parser_data is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        result: Final = await resolved_parser_data.history(channel_id=channel_id, limit=limit)
        if isinstance(result, ParserDataFailure):
            raise _parser_data_http_error(result)
        return result

    async def effective_parser_data(channel_id: UUID) -> EffectiveParserData:
        if resolved_parser_data is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        result: Final = await resolved_parser_data.effective_data(channel_id)
        if isinstance(result, ParserDataFailure):
            raise _parser_data_http_error(result)
        return result

    async def parser_snapshot(channel_id: UUID) -> Response:
        snapshot: Final = await _load_parser_snapshot(resolved_parser_data, channel_id)
        return _snapshot_response(channel_id=channel_id, snapshot=snapshot, download=False)

    async def export_parser_snapshot(channel_id: UUID) -> Response:
        snapshot: Final = await _load_parser_snapshot(resolved_parser_data, channel_id)
        return _snapshot_response(channel_id=channel_id, snapshot=snapshot, download=True)

    async def start_parser_task(
        channel_id: UUID,
        body: ParserTaskStartRequest,
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ParserTaskAccepted:
        if resolved_parser_tasks is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        actor: Final = _verified_actor(
            token=x_account_pool_actor,
            request_id=x_account_pool_request_id,
            expected_action=ActorAction.PARSER_START,
            secret=resolved_settings.actor_secret,
        )
        result: Final = await resolved_parser_tasks.start(channel_id=channel_id, request=body, actor=actor)
        if isinstance(result, ParserTaskOperationFailure):
            raise _parser_task_http_error(result)
        return result

    async def parser_task(channel_id: UUID, task_id: UUID) -> ParserTaskView:
        if resolved_parser_tasks is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        result: Final = await resolved_parser_tasks.view(channel_id=channel_id, task_id=task_id)
        if isinstance(result, ParserTaskOperationFailure):
            raise _parser_task_http_error(result)
        return result

    async def import_parser_snapshot(
        channel_id: UUID,
        body: SnapshotImportRequest,
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> SnapshotImportSuccess:
        if resolved_snapshot_importer is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        actor: Final = _verified_actor(
            token=x_account_pool_actor,
            request_id=x_account_pool_request_id,
            expected_action=ActorAction.SNAPSHOT_IMPORT,
            secret=resolved_settings.actor_secret,
        )
        result: Final = await resolved_snapshot_importer.import_snapshot(channel_id, body, actor)
        if isinstance(result, SnapshotImportFailure):
            raise _snapshot_import_http_error(result)
        return result

    async def set_parser_override(
        channel_id: UUID,
        body: OverrideSetRequest,
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> OverrideMutationSuccess:
        if resolved_parser_overrides is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        actor: Final = _verified_actor(
            token=x_account_pool_actor,
            request_id=x_account_pool_request_id,
            expected_action=ActorAction.OVERRIDE_SET,
            secret=resolved_settings.actor_secret,
        )
        result: Final = await resolved_parser_overrides.set_override(channel_id, body, actor)
        if isinstance(result, OverrideMutationFailure):
            raise _override_mutation_http_error(result)
        return result

    async def revoke_parser_override(
        channel_id: UUID,
        field_path: str,
        body: OverrideRevokeRequest,
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> OverrideMutationSuccess:
        if resolved_parser_overrides is None:
            raise HTTPException(status_code=503, detail="Account-pool database is not configured")
        actor: Final = _verified_actor(
            token=x_account_pool_actor,
            request_id=x_account_pool_request_id,
            expected_action=ActorAction.OVERRIDE_REVOKE,
            secret=resolved_settings.actor_secret,
        )
        normalized_path: Final = f"/{field_path.lstrip('/')}"
        result: Final = await resolved_parser_overrides.revoke_override(
            channel_id,
            normalized_path,
            body,
            actor,
        )
        if isinstance(result, OverrideMutationFailure):
            raise _override_mutation_http_error(result)
        return result

    async def create_account(
        body: AccountMutation,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ManagementResult:
        _mark_accounts_compatibility_deprecated(response)
        if resolved_channel_management is None:
            return await manager.create_account(body)
        result: Final = await resolved_channel_management.create(
            _legacy_channel_mutation(body),
            _required_idempotency_key(idempotency_key),
            _verified_actor(
                token=x_account_pool_actor,
                request_id=x_account_pool_request_id,
                expected_action=ActorAction.CHANNEL_CREATE,
                secret=resolved_settings.actor_secret,
            ),
        )
        return _legacy_management_result(result, "渠道创建已提交")

    async def update_account(
        account_id: str,
        body: AccountMutation,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ManagementResult:
        _mark_accounts_compatibility_deprecated(response)
        if resolved_channel_management is None:
            return await manager.update_account(account_id=account_id, request=body)
        detail: Final = await resolved_channel_management.detail_by_legacy_account(account_id)
        if isinstance(detail, ChannelManagementFailure):
            raise _channel_management_http_error(detail)
        result: Final = await resolved_channel_management.update(
            detail.channel_id,
            _legacy_channel_mutation(body, detail),
            _required_idempotency_key(idempotency_key),
            _verified_actor(
                token=x_account_pool_actor,
                request_id=x_account_pool_request_id,
                expected_action=ActorAction.CHANNEL_UPDATE,
                secret=resolved_settings.actor_secret,
            ),
        )
        return _legacy_management_result(result, "渠道更新已提交")

    async def delete_account(
        account_id: str,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> ManagementResult:
        _mark_accounts_compatibility_deprecated(response)
        if resolved_channel_management is None:
            return await manager.delete_account(account_id)
        detail: Final = await resolved_channel_management.detail_by_legacy_account(account_id)
        if isinstance(detail, ChannelManagementFailure):
            raise _channel_management_http_error(detail)
        result: Final = await resolved_channel_management.delete(
            detail.channel_id,
            ChannelDeleteRequest(delete_mode=DeleteMode.DELETE_MANAGED_DEPLOYMENT),
            _required_idempotency_key(idempotency_key),
            _verified_actor(
                token=x_account_pool_actor,
                request_id=x_account_pool_request_id,
                expected_action=ActorAction.CHANNEL_DELETE,
                secret=resolved_settings.actor_secret,
            ),
        )
        return _legacy_management_result(result, "渠道删除已提交")

    async def update_policy(model: str, body: PolicyUpdate) -> ManagementResult:
        return await manager.update_policy(model=model, request=body)

    async def routing_policy(model: str) -> RoutingPolicyState:
        return _routing_result(await _require_routing_policies(resolved_routing_policies).read(model))

    async def update_routing_policy(
        model: str,
        body: RoutingPolicyMutation,
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> RoutingPolicyState:
        service: Final = _require_routing_policies(resolved_routing_policies)
        actor: Final = _verified_actor(
            token=x_account_pool_actor,
            request_id=x_account_pool_request_id,
            expected_action=ActorAction.ROUTING_POLICY_UPDATE,
            secret=resolved_settings.actor_secret,
        )
        return _routing_result(await service.update_policy(model, body, actor))

    async def update_routing_candidate(
        model: str,
        binding_id: UUID,
        body: RoutingCandidateMutation,
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> RoutingPolicyState:
        service: Final = _require_routing_policies(resolved_routing_policies)
        actor: Final = _verified_actor(
            token=x_account_pool_actor,
            request_id=x_account_pool_request_id,
            expected_action=ActorAction.ROUTING_CANDIDATE_UPDATE,
            secret=resolved_settings.actor_secret,
        )
        return _routing_result(await service.update_candidate(model, binding_id, body, actor))

    async def delete_routing_candidate(
        model: str,
        binding_id: UUID,
        body: RoutingVersionMutation,
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> RoutingPolicyState:
        service: Final = _require_routing_policies(resolved_routing_policies)
        actor: Final = _verified_actor(
            token=x_account_pool_actor,
            request_id=x_account_pool_request_id,
            expected_action=ActorAction.ROUTING_CANDIDATE_DELETE,
            secret=resolved_settings.actor_secret,
        )
        return _routing_result(await service.delete_candidate(model, binding_id, body, actor))

    async def update_ui_routing_policy(
        model: str,
        request: Request,
        access_token: str = Depends(require_litellm_admin),
    ) -> Response:
        encoded_model: Final = quote(model, safe="")
        return await forward_ui_management_request(
            request=request,
            method="PUT",
            path=f"/models/{encoded_model}/routing-policy",
            access_token=access_token,
        )

    async def update_ui_routing_candidate(
        model: str,
        binding_id: UUID,
        request: Request,
        access_token: str = Depends(require_litellm_admin),
    ) -> Response:
        encoded_model: Final = quote(model, safe="")
        return await forward_ui_management_request(
            request=request,
            method="PUT",
            path=f"/models/{encoded_model}/routing-candidates/{binding_id}",
            access_token=access_token,
        )

    async def delete_ui_routing_candidate(
        model: str,
        binding_id: UUID,
        request: Request,
        access_token: str = Depends(require_litellm_admin),
    ) -> Response:
        encoded_model: Final = quote(model, safe="")
        return await forward_ui_management_request(
            request=request,
            method="DELETE",
            path=f"/models/{encoded_model}/routing-candidates/{binding_id}",
            access_token=access_token,
        )

    async def create_ui_channel(
        request: Request,
        access_token: str = Depends(require_litellm_admin),
    ) -> Response:
        return await forward_ui_management_request(
            request=request,
            method="POST",
            path="/channels",
            access_token=access_token,
        )

    async def update_ui_channel(
        channel_id: UUID,
        request: Request,
        access_token: str = Depends(require_litellm_admin),
    ) -> Response:
        return await forward_ui_management_request(
            request=request,
            method="PUT",
            path=f"/channels/{channel_id}",
            access_token=access_token,
        )

    async def delete_ui_channel(
        channel_id: UUID,
        request: Request,
        access_token: str = Depends(require_litellm_admin),
    ) -> Response:
        return await forward_ui_management_request(
            request=request,
            method="DELETE",
            path=f"/channels/{channel_id}",
            access_token=access_token,
        )

    async def probe_channel_health(
        channel_id: UUID,
        body: HealthProbeRequest,
        x_account_pool_actor: str | None = Header(default=None),
        x_account_pool_request_id: str | None = Header(default=None),
    ) -> HealthProbeResult:
        _verified_actor(
            token=x_account_pool_actor,
            request_id=x_account_pool_request_id,
            expected_action=ActorAction.HEALTH_PROBE,
            secret=resolved_settings.actor_secret,
        )
        result: Final = await resolved_health_probes.probe_channel(channel_id, body)
        if result.reason_code in {"channel_not_found", "deployment_not_found"}:
            raise HTTPException(status_code=404, detail={"code": result.reason_code})
        if result.reason_code == "litellm_admin_key_missing":
            raise HTTPException(status_code=503, detail={"code": result.reason_code})
        return result

    async def channel_health_detail(channel_id: UUID) -> ChannelHealthDetail:
        result: Final = await resolved_health_details.read_channel(channel_id)
        if isinstance(result, ChannelHealthDetailFailure):
            status_code: Final = 404 if result.code == "channel_not_found" else 503
            raise HTTPException(
                status_code=status_code,
                detail={"code": result.code, "retryable": result.retryable},
            )
        return result.detail

    async def probe_account_health(account_id: str, body: HealthProbeRequest) -> HealthProbeResult:
        result: Final = await resolved_health_probes.probe_account(account_id, body)
        if result.reason_code in {"channel_not_found", "deployment_not_found"}:
            raise HTTPException(status_code=404, detail={"code": result.reason_code})
        if result.reason_code == "litellm_admin_key_missing":
            raise HTTPException(status_code=503, detail={"code": result.reason_code})
        return result

    async def acquire(body: AcquireRequest) -> AcquireSuccess:
        result: Final = await scheduler.acquire(body)
        if not isinstance(result, AcquireSuccess):
            raise HTTPException(
                status_code=503,
                detail={**result.model_dump(mode="json"), "reasons": result.reasons},
            )
        if resolved_health_recorder is not None:
            await resolved_health_recorder.record_request(result.lease)
        return result

    async def settle(body: SettleRequest) -> OperationResult:
        lease: Final = await resolved_store.read_lease(body.lease_id)
        settled: Final = await resolved_store.settle(body)
        if lease is not None and resolved_health_recorder is not None:
            await resolved_health_recorder.record_passive(lease, body)
        return OperationResult(ok=settled)

    async def record_request_activity(body: Lease) -> OperationResult:
        if resolved_health_recorder is None:
            return OperationResult(ok=True)
        return OperationResult(ok=await resolved_health_recorder.record_request(body))

    async def record_settlement_event(body: SettlementEventRequest) -> OperationResult:
        if resolved_health_recorder is None:
            return OperationResult(ok=True)
        return OperationResult(ok=await resolved_health_recorder.record_passive(body.lease, body.settlement))

    async def release(body: ReleaseRequest) -> OperationResult:
        return OperationResult(ok=await resolved_store.release(body.lease_id))

    async def heartbeat(body: HeartbeatRequest) -> OperationResult:
        return OperationResult(ok=await resolved_store.heartbeat(body.lease_id, resolved_settings.lease_ttl_seconds))

    internal_dependency: Final = [Depends(require_internal_token)]
    # 管理接口和调度接口共用服务令牌，防止绕过 LiteLLM Admin 代理直连 4100 端口。
    management_dependency: Final = [Depends(require_internal_token)]
    application.add_api_route("/healthz", healthz, methods=["GET"])
    application.add_api_route(
        "/internal/runtime-config",
        runtime_config,
        methods=["GET"],
        dependencies=internal_dependency,
        include_in_schema=False,
    )
    application.add_api_route("/metrics", metrics, methods=["GET"], include_in_schema=False)
    application.add_api_route("/api/workers", worker_states, methods=["GET"], dependencies=management_dependency)
    application.add_api_route("/api/accounts", accounts, methods=["GET"], dependencies=management_dependency)
    application.add_api_route("/api/accounts", create_account, methods=["POST"], dependencies=management_dependency)
    application.add_api_route(
        "/api/accounts/{account_id}", update_account, methods=["PUT"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/accounts/{account_id}", delete_account, methods=["DELETE"], dependencies=management_dependency
    )
    application.add_api_route("/api/models", models, methods=["GET"], dependencies=management_dependency)
    application.add_api_route(
        "/api/models/{model:path}/routing-table", routing_table, methods=["GET"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/models/{model:path}/policy", update_policy, methods=["PUT"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/models/{model:path}/routing-policy",
        routing_policy,
        methods=["GET"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/models/{model:path}/routing-policy",
        update_routing_policy,
        methods=["PUT"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/models/{model:path}/routing-candidates/{binding_id}",
        update_routing_candidate,
        methods=["PUT"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/models/{model:path}/routing-candidates/{binding_id}",
        delete_routing_candidate,
        methods=["DELETE"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/litellm/status", litellm_status, methods=["GET"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/provider-services", provider_service_manifests, methods=["GET"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/provider-services/validate", validate_provider, methods=["POST"], dependencies=management_dependency
    )
    application.add_api_route("/api/channels", channels, methods=["GET"], dependencies=management_dependency)
    application.add_api_route("/api/overview", overview_data, methods=["GET"], dependencies=management_dependency)
    application.add_api_route("/api/events", event_log_data, methods=["GET"], dependencies=management_dependency)
    application.add_api_route("/api/channels", create_channel, methods=["POST"], dependencies=management_dependency)
    application.add_api_route(
        "/api/channels/import", import_channel, methods=["POST"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/channels/{channel_id}", channel_detail, methods=["GET"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/channels/{channel_id}/aggregate",
        channel_aggregate_detail,
        methods=["GET"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}", update_channel, methods=["PUT"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/channels/{channel_id}/detach", detach_channel, methods=["POST"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/channels/{channel_id}", delete_channel, methods=["DELETE"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/channels/{channel_id}/bindings/{binding_id}/delete-external-deployment",
        delete_external_deployment,
        methods=["POST"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/reconcile",
        reconcile_channel,
        methods=["POST"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/health-probe",
        probe_channel_health,
        methods=["POST"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/health",
        channel_health_detail,
        methods=["GET"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/accounts/{account_id}/health-probe",
        probe_account_health,
        methods=["POST"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/operations/{operation_id}",
        channel_operation,
        methods=["GET"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/parse",
        start_parser_task,
        methods=["POST"],
        status_code=202,
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/parser-tasks/{task_id}",
        parser_task,
        methods=["GET"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/parser-runs",
        parser_runs,
        methods=["GET"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/effective-data",
        effective_parser_data,
        methods=["GET"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/snapshot",
        parser_snapshot,
        methods=["GET"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/export",
        export_parser_snapshot,
        methods=["GET"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/import",
        import_parser_snapshot,
        methods=["POST"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/overrides",
        set_parser_override,
        methods=["PUT"],
        dependencies=management_dependency,
    )
    application.add_api_route(
        "/api/channels/{channel_id}/overrides/{field_path:path}",
        revoke_parser_override,
        methods=["DELETE"],
        dependencies=management_dependency,
    )
    application.add_api_route("/api/stats", stats, methods=["GET"], dependencies=management_dependency)
    application.add_api_route("/internal/acquire", acquire, methods=["POST"], dependencies=internal_dependency)
    application.add_api_route("/internal/settle", settle, methods=["POST"], dependencies=internal_dependency)
    application.add_api_route(
        "/internal/request-activity",
        record_request_activity,
        methods=["POST"],
        dependencies=internal_dependency,
    )
    application.add_api_route(
        "/internal/settlement-event",
        record_settlement_event,
        methods=["POST"],
        dependencies=internal_dependency,
    )
    application.add_api_route("/internal/release", release, methods=["POST"], dependencies=internal_dependency)
    application.add_api_route("/internal/heartbeat", heartbeat, methods=["POST"], dependencies=internal_dependency)

    ui_dependency: Final = [Depends(require_litellm_admin)]
    application.add_api_route("/ui-api/accounts", accounts, methods=["GET"], dependencies=ui_dependency)
    application.add_api_route("/ui-api/accounts", create_account, methods=["POST"], dependencies=ui_dependency)
    application.add_api_route(
        "/ui-api/accounts/{account_id}", update_account, methods=["PUT"], dependencies=ui_dependency
    )
    application.add_api_route(
        "/ui-api/accounts/{account_id}", delete_account, methods=["DELETE"], dependencies=ui_dependency
    )
    application.add_api_route("/ui-api/models", models, methods=["GET"], dependencies=ui_dependency)
    application.add_api_route(
        "/ui-api/models/{model:path}/routing-table", routing_table, methods=["GET"], dependencies=ui_dependency
    )
    application.add_api_route(
        "/ui-api/models/{model:path}/policy", update_policy, methods=["PUT"], dependencies=ui_dependency
    )
    application.add_api_route(
        "/ui-api/models/{model:path}/routing-policy",
        routing_policy,
        methods=["GET"],
        dependencies=ui_dependency,
    )
    application.add_api_route(
        "/ui-api/models/{model:path}/routing-policy",
        update_ui_routing_policy,
        methods=["PUT"],
    )
    application.add_api_route(
        "/ui-api/models/{model:path}/routing-candidates/{binding_id}",
        update_ui_routing_candidate,
        methods=["PUT"],
    )
    application.add_api_route(
        "/ui-api/models/{model:path}/routing-candidates/{binding_id}",
        delete_ui_routing_candidate,
        methods=["DELETE"],
    )
    application.add_api_route("/ui-api/stats", stats, methods=["GET"], dependencies=ui_dependency)
    application.add_api_route("/ui-api/litellm/status", litellm_status, methods=["GET"], dependencies=ui_dependency)
    application.add_api_route(
        "/ui-api/provider-services", provider_service_manifests, methods=["GET"], dependencies=ui_dependency
    )
    application.add_api_route(
        "/ui-api/accounts/{account_id}/health-probe",
        probe_account_health,
        methods=["POST"],
        dependencies=ui_dependency,
    )
    application.add_api_route(
        "/ui-api/provider-services/validate", validate_provider, methods=["POST"], dependencies=ui_dependency
    )
    application.add_api_route("/ui-api/channels", channels, methods=["GET"], dependencies=ui_dependency)
    application.add_api_route("/ui-api/channels", create_ui_channel, methods=["POST"])
    application.add_api_route(
        "/ui-api/channels/{channel_id}", channel_detail, methods=["GET"], dependencies=ui_dependency
    )
    application.add_api_route("/ui-api/channels/{channel_id}", update_ui_channel, methods=["PUT"])
    application.add_api_route("/ui-api/channels/{channel_id}", delete_ui_channel, methods=["DELETE"])
    application.add_api_route("/ui-api/overview", overview_data, methods=["GET"], dependencies=ui_dependency)
    application.add_api_route("/ui-api/events", event_log_data, methods=["GET"], dependencies=ui_dependency)
    application.add_api_route(
        "/ui-api/channels/{channel_id}/aggregate",
        channel_aggregate_detail,
        methods=["GET"],
        dependencies=ui_dependency,
    )
    application.add_api_route(
        "/ui-api/channels/{channel_id}/parser-runs",
        parser_runs,
        methods=["GET"],
        dependencies=ui_dependency,
    )
    application.add_api_route(
        "/ui-api/channels/{channel_id}/effective-data",
        effective_parser_data,
        methods=["GET"],
        dependencies=ui_dependency,
    )
    application.add_api_route(
        "/ui-api/channels/{channel_id}/snapshot",
        parser_snapshot,
        methods=["GET"],
        dependencies=ui_dependency,
    )
    application.add_api_route(
        "/ui-api/channels/{channel_id}/export",
        export_parser_snapshot,
        methods=["GET"],
        dependencies=ui_dependency,
    )

    async def ui_redirect() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    ui_path: Final = Path(__file__).resolve().parent / "ui"
    application.add_api_route("/", ui_redirect, methods=["GET"], include_in_schema=False)
    application.mount("/ui", StaticFiles(directory=ui_path, html=True), name="account-pool-ui")

    return application


async def _run_reaper(store: StateStore, monitor: WorkerMonitorRegistry) -> None:
    await run_worker_loop(
        worker=WorkerName.LEASE_REAPER,
        cycle=store.sweep_expired,
        interval_seconds=1,
        monitor=monitor,
        logger=_LOGGER,
        failure_message="Account Pool lease reaper pass failed",
        initial_delay=True,
    )


async def _run_health_probes(
    service: HealthProbeManager,
    interval_seconds: int,
    monitor: WorkerMonitorRegistry,
) -> None:
    await run_worker_loop(
        worker=WorkerName.ACTIVE_HEALTH_PROBE,
        cycle=service.probe_due,
        interval_seconds=interval_seconds,
        monitor=monitor,
        logger=_LOGGER,
        failure_message="Account Pool active health probe pass failed",
    )


async def _run_reconciler(
    service: ChannelManager,
    interval_seconds: int,
    monitor: WorkerMonitorRegistry,
) -> None:
    await run_worker_loop(
        worker=WorkerName.CHANNEL_RECONCILER,
        cycle=service.reconcile_pending,
        interval_seconds=interval_seconds,
        monitor=monitor,
        logger=_LOGGER,
        failure_message="Account Pool reconcile pass failed",
        initial_delay=True,
    )


async def _model_summary(
    scheduler: Scheduler,
    model: str,
) -> ModelSummary:
    routes: Final = await scheduler.route_table(model)
    return ModelSummary(
        model=model,
        strategy=scheduler.policy(model).strategy,
        version=scheduler.policy(model).version,
        accounts=len(routes),
        available_accounts=sum(1 for route in routes if route.available),
        inflight=sum(route.inflight for route in routes),
        max_concurrency=sum(route.max_concurrency for route in routes),
    )


def _build_store(settings: Settings) -> StateStore:
    backend: Final = (
        RedisStateStore(settings.redis_url, maximum_lease_seconds=settings.maximum_lease_seconds)
        if settings.store_mode == "redis"
        else MemoryStateStore(maximum_lease_seconds=settings.maximum_lease_seconds)
    )
    if settings.database_url is None:
        return backend
    return DurableLatencyStateStore(
        backend=DurableQuotaStateStore(
            backend=backend,
            repository=PostgresQuotaRuntimeRepository(
                settings.database_url,
                schema=settings.database_schema,
            ),
            maximum_lease_seconds=settings.maximum_lease_seconds,
        ),
        repository=PostgresLatencyMetricRepository(
            settings.database_url,
            schema=settings.database_schema,
        ),
    )


def _build_request_events(settings: Settings) -> RequestEventRecorder | None:
    if settings.database_url is None:
        return None
    return RequestEventRecorder(
        PostgresOperationalEventRepository(
            settings.database_url,
            schema=settings.database_schema,
        )
    )


def _build_restriction_events(settings: Settings) -> RestrictionEventRecorder | None:
    if settings.database_url is None:
        return None
    return RestrictionEventRecorder(
        PostgresOperationalEventRepository(
            settings.database_url,
            schema=settings.database_schema,
        )
    )


def _build_catalog(settings: Settings) -> ChannelCatalogReader | None:
    if settings.database_url is None:
        return None
    return ChannelCatalogQueryService(PostgresCatalogRepository(settings.database_url, schema=settings.database_schema))


def _build_health_events(settings: Settings) -> HealthEventRepository | None:
    if settings.database_url is None:
        return None
    return PostgresHealthEventRepository(settings.database_url, schema=settings.database_schema)


def _build_routing_policies(
    settings: Settings,
    scheduler: Scheduler,
    parser_data: ParserDataReader | None,
) -> RoutingPolicyService | None:
    if settings.database_url is None:
        return None
    catalog_repository: Final = PostgresCatalogRepository(
        settings.database_url,
        schema=settings.database_schema,
    )
    return RoutingPolicyService(
        repository=PostgresRoutingPolicyRepository(
            settings.database_url,
            schema=settings.database_schema,
        ),
        projector=RuntimeProjector(
            CatalogService(catalog_repository),
            scheduler,
            enricher=_parser_runtime_enricher(parser_data),
        ),
        audit=PostgresManagementAuditRepository(
            settings.database_url,
            schema=settings.database_schema,
        ),
    )


def _build_channel_management(
    settings: Settings,
    scheduler: Scheduler,
    client: httpx.AsyncClient,
    parser_data: ParserDataReader | None,
) -> ChannelManagementService | None:
    if settings.database_url is None or settings.litellm_admin_key is None:
        return None
    catalog_repository: Final = PostgresCatalogRepository(
        settings.database_url,
        schema=settings.database_schema,
    )
    return ChannelManagementService(
        catalog_repository=catalog_repository,
        operations=PostgresSyncOperationRepository(
            settings.database_url,
            schema=settings.database_schema,
        ),
        synchronizer=LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint=settings.litellm_url,
            admin_key=SecretStr(settings.litellm_admin_key),
        ),
        runtime_projector=RuntimeProjector(
            CatalogService(catalog_repository),
            scheduler,
            enricher=_parser_runtime_enricher(parser_data),
        ),
        audit=PostgresManagementAuditRepository(
            settings.database_url,
            schema=settings.database_schema,
        ),
        operational_events=PostgresOperationalEventRepository(
            settings.database_url,
            schema=settings.database_schema,
        ),
    )


def _require_channel_management(service: ChannelManager | None) -> ChannelManager:
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL and LiteLLM management credentials are required for channel mutations",
        )
    return service


def _require_routing_policies(service: RoutingPolicyService | None) -> RoutingPolicyService:
    if service is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is required for routing policy management")
    return service


def _routing_result(result: RoutingPolicyResult) -> RoutingPolicyState:
    if isinstance(result, RoutingFailure):
        raise _routing_http_error(result)
    return result


def _routing_http_error(failure: RoutingFailure) -> HTTPException:
    status_code: Final = {
        RoutingFailureCode.INVALID_ACTOR: 403,
        RoutingFailureCode.MODEL_NOT_FOUND: 404,
        RoutingFailureCode.BINDING_NOT_FOUND: 404,
        RoutingFailureCode.VERSION_CONFLICT: 409,
        RoutingFailureCode.CANDIDATE_CONFLICT: 409,
        RoutingFailureCode.DATABASE_UNAVAILABLE: 503,
        RoutingFailureCode.RUNTIME_PROJECTION_FAILED: 503,
        RoutingFailureCode.AUDIT_UNAVAILABLE: 503,
    }[failure.code]
    return HTTPException(
        status_code=status_code,
        detail={
            "code": failure.code,
            "retryable": failure.retryable,
            "current_version": failure.current_version,
        },
    )


def _required_idempotency_key(value: str | None) -> str:
    normalized: Final = (value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    if len(normalized) > 255:
        raise HTTPException(status_code=400, detail="Idempotency-Key is too long")
    return normalized


async def _channel_mutation_result(
    pending: Awaitable[ChannelManagementResult],
) -> ChannelOperationView:
    result: Final = await pending
    if isinstance(result, ChannelManagementFailure):
        raise _channel_management_http_error(result)
    return result


def _channel_management_http_error(failure: ChannelManagementFailure) -> HTTPException:
    status_code: Final = {
        "channel_not_found": 404,
        "external_binding_not_found": 404,
        "operation_not_found": 404,
        "idempotency_conflict": 409,
        "state_conflict": 409,
        "database_unavailable": 503,
    }.get(failure.code, 502 if failure.retryable else 400)
    return HTTPException(status_code=status_code, detail={"code": failure.code, "retryable": failure.retryable})


def _legacy_channel_mutation(
    request: AccountMutation,
    current: ChannelDetail | None = None,
) -> ChannelMutation:
    return ChannelMutation(
        legacy_account_id=request.id,
        display_name=request.display_name,
        provider=request.provider,
        group=request.group,
        base_url_display=request.base_url_display,
        administrative_state=(AdministrativeState.ENABLED if request.enabled else AdministrativeState.DISABLED),
        max_concurrency=request.max_concurrency,
        priority=request.priority,
        weight=request.weight,
        quotas=request.quotas,
        api_key=request.api_key,
        bindings=tuple(_legacy_binding(deployment, current) for deployment in request.deployments),
    )


def _legacy_binding(
    deployment: DeploymentInput,
    current: ChannelDetail | None,
) -> ChannelBindingMutation:
    matched: Final = next(
        (
            binding
            for binding in (() if current is None else current.bindings)
            if binding.litellm_deployment_id == deployment.litellm_model_id
        ),
        None,
    )
    ownership: Final = (
        matched.ownership
        if matched is not None
        else (
            BindingOwnership.EXTERNALLY_MANAGED
            if deployment.litellm_model_id is not None
            else BindingOwnership.POOL_MANAGED
        )
    )
    return ChannelBindingMutation(
        binding_id=None if matched is None else matched.binding_id,
        public_model=deployment.public_model,
        provider_model=(
            deployment.provider_model
            if deployment.provider_model is not None
            else (None if matched is None else matched.provider_model)
        ),
        litellm_deployment_id=deployment.litellm_model_id,
        ownership=ownership,
        enabled=deployment.enabled,
    )


def _legacy_management_result(result: ChannelManagementResult, message: str) -> ManagementResult:
    if isinstance(result, ChannelManagementFailure):
        raise _channel_management_http_error(result)
    return ManagementResult(ok=True, message=f"{message}，operation_id={result.operation_id}")


def _mark_accounts_compatibility_deprecated(response: Response) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</api/channels>; rel="successor-version"'
    response.headers["Warning"] = '299 account-pool "Deprecated endpoint; use /api/channels"'


def _build_parser_data(settings: Settings) -> ParserDataReader | None:
    if settings.database_url is None:
        return None
    return ParserDataService(
        parser_runs=PostgresParserRunRepository(settings.database_url, schema=settings.database_schema),
        overrides=PostgresOverrideEventRepository(settings.database_url, schema=settings.database_schema),
    )


def _parser_runtime_enricher(parser_data: ParserDataReader | None) -> ParserRuntimeConfigEnricher | None:
    return None if parser_data is None else ParserRuntimeConfigEnricher(parser_data)


def _build_parser_overrides(settings: Settings) -> ParserOverrideWriter | None:
    if settings.database_url is None:
        return None
    return ParserOverrideService(
        parser_runs=PostgresParserRunRepository(settings.database_url, schema=settings.database_schema),
        overrides=PostgresOverrideEventRepository(settings.database_url, schema=settings.database_schema),
        audit=PostgresManagementAuditRepository(settings.database_url, schema=settings.database_schema),
    )


@dataclass(frozen=True, slots=True)
class _ParserRuntime:
    tasks: ParserTaskManager | None
    export_retries: ParserExportRetryManager | None
    public_metadata_tasks: PublicMetadataTaskManager | None


def _build_parser_runtime(
    settings: Settings,
    providers: ProviderServiceRegistry,
    registry: ParserRegistry,
    catalog: ChannelCatalogReader | None,
    public_metadata_sources: PublicMetadataSourceRegistry,
    build_tasks: bool,
    build_export_retries: bool,
    build_public_metadata_tasks: bool,
    worker_monitor: WorkerMonitorRegistry,
) -> _ParserRuntime | None:
    if settings.database_url is None or catalog is None:
        return None
    parser_runs: Final = PostgresParserRunRepository(settings.database_url, schema=settings.database_schema)
    overrides: Final = PostgresOverrideEventRepository(settings.database_url, schema=settings.database_schema)
    operations: Final = PostgresOperationalEventRepository(settings.database_url, schema=settings.database_schema)
    worker: Final = ParserWorker(
        registry=registry,
        repository=parser_runs,
        overrides=overrides,
        snapshots=ParserSnapshotStore(root=settings.parser_snapshot_root)
        if settings.parser_snapshot_root is not None
        else ParserSnapshotStore(),
        operations=operations,
    )
    return _ParserRuntime(
        tasks=(
            ParserTaskService(
                providers=providers,
                worker=worker,
                repository=PostgresParserTaskRepository(settings.database_url, schema=settings.database_schema),
                audit=PostgresManagementAuditRepository(settings.database_url, schema=settings.database_schema),
                operations=operations,
                catalog=catalog,
            )
            if build_tasks
            else None
        ),
        export_retries=(
            ParserExportRetryLoop(
                worker,
                interval_seconds=settings.parser_export_retry_interval_seconds,
                batch_size=settings.parser_export_retry_batch_size,
                monitor=worker_monitor,
            )
            if build_export_retries
            else None
        ),
        public_metadata_tasks=(
            None
            if not build_public_metadata_tasks or catalog is None or not public_metadata_sources.provider_ids
            else PublicMetadataTaskLoop(
                catalog=catalog,
                sources=public_metadata_sources,
                repository=PostgresPublicMetadataTaskRepository(
                    settings.database_url,
                    schema=settings.database_schema,
                ),
                worker=worker,
                operations=operations,
                interval_seconds=settings.public_metadata_poll_interval_seconds,
                refresh_interval_seconds=settings.public_metadata_refresh_interval_seconds,
                retry_base_seconds=settings.public_metadata_retry_base_seconds,
                batch_size=settings.public_metadata_batch_size,
                max_attempts=settings.public_metadata_max_attempts,
                monitor=worker_monitor,
            )
        ),
    )


def _runtime_tasks(runtime: _ParserRuntime | None) -> ParserTaskManager | None:
    return None if runtime is None else runtime.tasks


def _runtime_export_retries(runtime: _ParserRuntime | None) -> ParserExportRetryManager | None:
    return None if runtime is None else runtime.export_retries


def _runtime_public_metadata_tasks(runtime: _ParserRuntime | None) -> PublicMetadataTaskManager | None:
    return None if runtime is None else runtime.public_metadata_tasks


def _build_worker_monitor(
    *,
    settings: Settings,
    reconciler_enabled: bool,
    parser_export_enabled: bool,
    public_metadata_enabled: bool,
    retention_enabled: bool,
) -> WorkerMonitorRegistry:
    health_interval: Final = max(settings.health_probe_interval_seconds, 1)
    return WorkerMonitorRegistry(
        (
            WorkerRegistration(WorkerName.LEASE_REAPER, True, 1),
            WorkerRegistration(
                WorkerName.CHANNEL_RECONCILER,
                reconciler_enabled,
                settings.reconcile_interval_seconds,
            ),
            WorkerRegistration(
                WorkerName.PARSER_EXPORT_RETRY,
                parser_export_enabled,
                settings.parser_export_retry_interval_seconds,
            ),
            WorkerRegistration(
                WorkerName.PUBLIC_METADATA,
                public_metadata_enabled,
                settings.public_metadata_poll_interval_seconds,
            ),
            WorkerRegistration(
                WorkerName.ACTIVE_HEALTH_PROBE,
                settings.health_probe_interval_seconds > 0,
                health_interval,
            ),
            WorkerRegistration(
                WorkerName.EVENT_RETENTION,
                retention_enabled,
                settings.retention_interval_seconds,
            ),
        )
    )


def _build_retention(settings: Settings) -> RetentionRunner | None:
    configured: Final = (settings.event_archive_path is not None, settings.event_archive_key is not None)
    if configured == (False, False):
        return None
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is required when event archive retention is configured")
    if not all(configured):
        raise ValueError("event archive path and key must be configured together")
    archive_path: Final = settings.event_archive_path
    archive_key: Final = settings.event_archive_key
    if archive_path is None or archive_key is None:
        raise ValueError("event archive configuration is incomplete")
    policy: Final = RetentionPolicy(
        event_retention_days=settings.event_retention_days,
        audit_retention_days=settings.audit_event_retention_days,
        batch_size=settings.retention_batch_size,
    )
    return EventRetentionService(
        repository=PostgresRetentionRepository(settings.database_url, schema=settings.database_schema),
        archive=EncryptedEventArchive(
            archive_path,
            decode_archive_key(archive_key.get_secret_value()),
            settings.event_archive_key_id,
        ),
        policy=policy,
    )


def _build_snapshot_importer(settings: Settings) -> SnapshotImporter | None:
    if settings.database_url is None:
        return None
    parser_runs: Final = PostgresParserRunRepository(settings.database_url, schema=settings.database_schema)
    overrides: Final = PostgresOverrideEventRepository(settings.database_url, schema=settings.database_schema)
    return SnapshotImportService(
        parser_runs=parser_runs,
        overrides=overrides,
        batch_writer=overrides,
        audit=PostgresManagementAuditRepository(settings.database_url, schema=settings.database_schema),
    )


async def _load_parser_snapshot(
    parser_data: ParserDataReader | None,
    channel_id: UUID,
) -> ParserSnapshot:
    if parser_data is None:
        raise HTTPException(status_code=503, detail="Account-pool database is not configured")
    result: Final = await parser_data.snapshot(channel_id)
    if isinstance(result, ParserDataFailure):
        raise _parser_data_http_error(result)
    return result


def _snapshot_response(channel_id: UUID, snapshot: ParserSnapshot, download: bool) -> Response:
    payload: Final = _SNAPSHOT_DOCUMENT.dump_json({channel_id: snapshot}, indent=2)
    headers: Final = {
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        **(
            {"content-disposition": f'attachment; filename="account-pool-{channel_id}-snapshot.json"'}
            if download
            else {}
        ),
    }
    return Response(content=payload, media_type="application/json", headers=headers)


def _parser_data_http_error(failure: ParserDataFailure) -> HTTPException:
    detail: Final = {"code": failure.code, "retryable": failure.retryable}
    if failure.code in (ParserDataFailureCode.CHANNEL_NOT_FOUND, ParserDataFailureCode.RUN_NOT_FOUND):
        return HTTPException(status_code=404, detail=detail)
    if failure.code == ParserDataFailureCode.INVALID_REQUEST:
        return HTTPException(status_code=422, detail=detail)
    if failure.code == ParserDataFailureCode.DATABASE_UNAVAILABLE:
        return HTTPException(status_code=503, detail=detail)
    return HTTPException(status_code=500, detail=detail)


def _verified_actor(
    token: str | None,
    request_id: str | None,
    expected_action: ActorAction,
    secret: str | None,
) -> ActorContext:
    result: Final = verify_actor_envelope(
        token=token,
        request_id=request_id,
        expected_action=expected_action,
        secret=secret,
    )
    if not isinstance(result, ActorVerificationFailure):
        return result.actor
    if result.code == ActorVerificationFailureCode.CONFIGURATION:
        raise HTTPException(status_code=503, detail={"code": result.code})
    if result.code in (
        ActorVerificationFailureCode.REQUEST_MISMATCH,
        ActorVerificationFailureCode.ACTION_MISMATCH,
    ):
        raise HTTPException(status_code=403, detail={"code": result.code})
    raise HTTPException(status_code=401, detail={"code": result.code})


def _override_mutation_http_error(failure: OverrideMutationFailure) -> HTTPException:
    detail: Final = {
        "code": failure.code,
        "retryable": failure.retryable,
        **({"apply_failure_code": failure.apply_failure_code} if failure.apply_failure_code else {}),
    }
    if failure.code in (
        OverrideMutationFailureCode.CHANNEL_NOT_FOUND,
        OverrideMutationFailureCode.RUN_NOT_FOUND,
        OverrideMutationFailureCode.OVERRIDE_NOT_FOUND,
    ):
        return HTTPException(status_code=404, detail=detail)
    if failure.code in (
        OverrideMutationFailureCode.PREDECESSOR_CONFLICT,
        OverrideMutationFailureCode.CONTENT_CONFLICT,
    ):
        return HTTPException(status_code=409, detail=detail)
    if failure.code in (
        OverrideMutationFailureCode.INVALID_REQUEST,
        OverrideMutationFailureCode.INVALID_VALUE,
    ):
        return HTTPException(status_code=422, detail=detail)
    if failure.code in (
        OverrideMutationFailureCode.DATABASE_UNAVAILABLE,
        OverrideMutationFailureCode.AUDIT_UNAVAILABLE,
    ):
        return HTTPException(status_code=503, detail=detail)
    return HTTPException(status_code=500, detail=detail)


def _parser_task_http_error(failure: ParserTaskOperationFailure) -> HTTPException:
    detail: Final = {"code": failure.code, "retryable": failure.retryable}
    if failure.code in (
        ParserTaskOperationFailureCode.CHANNEL_NOT_FOUND,
        ParserTaskOperationFailureCode.TASK_NOT_FOUND,
    ):
        return HTTPException(status_code=404, detail=detail)
    if failure.code == ParserTaskOperationFailureCode.INVALID_REQUEST:
        return HTTPException(status_code=422, detail=detail)
    if failure.code == ParserTaskOperationFailureCode.CONFLICT:
        return HTTPException(status_code=409, detail=detail)
    if failure.code in (
        ParserTaskOperationFailureCode.DATABASE_UNAVAILABLE,
        ParserTaskOperationFailureCode.AUDIT_UNAVAILABLE,
    ):
        return HTTPException(status_code=503, detail=detail)
    return HTTPException(status_code=500, detail=detail)


def _snapshot_import_http_error(failure: SnapshotImportFailure) -> HTTPException:
    detail: Final = {"code": failure.code, "retryable": failure.retryable}
    if failure.code in (
        SnapshotImportFailureCode.CHANNEL_NOT_FOUND,
        SnapshotImportFailureCode.RUN_NOT_FOUND,
    ):
        return HTTPException(status_code=404, detail=detail)
    if failure.code in (
        SnapshotImportFailureCode.INVALID_REQUEST,
        SnapshotImportFailureCode.INVALID_DATA,
    ):
        return HTTPException(status_code=422, detail=detail)
    if failure.code in (
        SnapshotImportFailureCode.PREDECESSOR_CONFLICT,
        SnapshotImportFailureCode.CONTENT_CONFLICT,
    ):
        return HTTPException(status_code=409, detail=detail)
    if failure.code in (
        SnapshotImportFailureCode.DATABASE_UNAVAILABLE,
        SnapshotImportFailureCode.AUDIT_UNAVAILABLE,
    ):
        return HTTPException(status_code=503, detail=detail)
    return HTTPException(status_code=500, detail=detail)


app = create_app()
