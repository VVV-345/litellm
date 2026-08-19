"""组装号池 API、渠道服务、调度器与 OpenAI 兼容网关。"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter

from account_pool.auth.actor import (
    ActorAction,
    ActorContext,
    ActorVerificationFailure,
    ActorVerificationFailureCode,
    verify_actor_envelope,
)
from account_pool.config import Settings, load_pool_config
from account_pool.domain.provider_source import (
    ProviderServiceManifest,
    ProviderValidationRequest,
    ProviderValidationResult,
)
from account_pool.gateway import Gateway
from account_pool.management import LiteLLMAdminClient, PoolManager
from account_pool.models import (
    AccountMutation,
    AccountView,
    AcquireRequest,
    AcquireSuccess,
    HeartbeatRequest,
    LiteLLMStatus,
    ManagementResult,
    ModelSummary,
    OperationResult,
    PolicyUpdate,
    ReleaseRequest,
    RouteEntry,
    SettleRequest,
    StatsView,
)
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
from account_pool.parsing.registry import ParserRegistry
from account_pool.parsing.service import (
    EffectiveParserData,
    ParserDataFailure,
    ParserDataFailureCode,
    ParserDataReader,
    ParserDataService,
    ParserRunHistory,
)
from account_pool.parsing.snapshots import ParserSnapshot
from account_pool.provider_services.glm import GlmOfficialProviderService
from account_pool.provider_services.openai_compatible import OpenAICompatibleProviderService
from account_pool.provider_services.parser_registry import build_parser_registry
from account_pool.provider_services.registry import ProviderServiceRegistry
from account_pool.scheduler import Scheduler
from account_pool.store import MemoryStateStore, RedisStateStore, StateStore

_SNAPSHOT_DOCUMENT: Final[TypeAdapter[dict[UUID, ParserSnapshot]]] = TypeAdapter(dict[UUID, ParserSnapshot])


@dataclass(frozen=True, slots=True)
class Runtime:
    settings: Settings
    scheduler: Scheduler
    store: StateStore
    gateway: Gateway
    manager: PoolManager
    admin: LiteLLMAdminClient
    provider_services: ProviderServiceRegistry
    parser_registry: ParserRegistry
    parser_data: ParserDataReader | None
    parser_overrides: ParserOverrideWriter | None


def create_app(
    settings: Settings | None = None,
    store: StateStore | None = None,
    proxy_client: httpx.AsyncClient | None = None,
    parser_data: ParserDataReader | None = None,
    parser_overrides: ParserOverrideWriter | None = None,
) -> FastAPI:
    resolved_settings: Final = settings or Settings.from_env()
    resolved_store: Final = store or _build_store(resolved_settings)
    owns_client: Final = proxy_client is None
    client: Final = proxy_client or httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5))
    config: Final = load_pool_config(resolved_settings.config_path)
    scheduler: Final = Scheduler(
        config=config,
        store=resolved_store,
        lease_ttl_seconds=resolved_settings.lease_ttl_seconds,
    )
    gateway: Final = Gateway(
        scheduler=scheduler,
        store=resolved_store,
        client=client,
        litellm_url=resolved_settings.litellm_url,
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
            OpenAICompatibleProviderService(client),
        )
    )
    parser_registry: Final = build_parser_registry()
    resolved_parser_data: Final = (
        parser_data if parser_data is not None else _build_parser_data(resolved_settings)
    )
    resolved_parser_overrides: Final = (
        parser_overrides if parser_overrides is not None else _build_parser_overrides(resolved_settings)
    )
    runtime: Final = Runtime(
        settings=resolved_settings,
        scheduler=scheduler,
        store=resolved_store,
        gateway=gateway,
        manager=manager,
        admin=admin,
        provider_services=provider_services,
        parser_registry=parser_registry,
        parser_data=resolved_parser_data,
        parser_overrides=resolved_parser_overrides,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        await scheduler.initialize()
        reaper: Final = asyncio.create_task(_run_reaper(resolved_store))
        try:
            yield
        finally:
            reaper.cancel()
            with suppress(asyncio.CancelledError):
                await reaper
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

    async def require_litellm_admin(authorization: str | None = Header(default=None)) -> None:
        scheme, _, access_token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not access_token or not await admin.authorize(access_token):
            raise HTTPException(status_code=401, detail="LiteLLM 管理令牌无效")

    async def healthz() -> OperationResult:
        return OperationResult(ok=True)

    async def accounts() -> tuple[AccountView, ...]:
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
            available_accounts=sum(1 for item in snapshots if item.enabled and item.health != "disabled"),
            inflight=sum(item.inflight for item in snapshots),
            max_concurrency=sum(item.max_concurrency for item in snapshots),
        )

    async def litellm_status() -> LiteLLMStatus:
        return await admin.status()

    async def provider_service_manifests() -> tuple[ProviderServiceManifest, ...]:
        return provider_services.manifests()

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

    async def create_account(body: AccountMutation) -> ManagementResult:
        return await manager.create_account(body)

    async def update_account(account_id: str, body: AccountMutation) -> ManagementResult:
        return await manager.update_account(account_id=account_id, request=body)

    async def delete_account(account_id: str) -> ManagementResult:
        return await manager.delete_account(account_id)

    async def update_policy(model: str, body: PolicyUpdate) -> ManagementResult:
        return await manager.update_policy(model=model, request=body)

    async def acquire(body: AcquireRequest) -> AcquireSuccess:
        result: Final = await scheduler.acquire(body)
        if not isinstance(result, AcquireSuccess):
            raise HTTPException(status_code=503, detail={"model": result.model, "reasons": result.reasons})
        return result

    async def settle(body: SettleRequest) -> OperationResult:
        return OperationResult(ok=await resolved_store.settle(body))

    async def release(body: ReleaseRequest) -> OperationResult:
        return OperationResult(ok=await resolved_store.release(body.lease_id))

    async def heartbeat(body: HeartbeatRequest) -> OperationResult:
        return OperationResult(ok=await resolved_store.heartbeat(body.lease_id, resolved_settings.lease_ttl_seconds))

    async def proxy(path: str, request: Request) -> Response:
        return await gateway.forward(path=path, request=request)

    internal_dependency: Final = [Depends(require_internal_token)]
    # 管理接口和调度接口共用服务令牌，防止绕过 LiteLLM Admin 代理直连 4100 端口。
    management_dependency: Final = [Depends(require_internal_token)]
    application.add_api_route("/healthz", healthz, methods=["GET"])
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
        "/api/models/{model}/routing-table", routing_table, methods=["GET"], dependencies=management_dependency
    )
    application.add_api_route(
        "/api/models/{model}/policy", update_policy, methods=["PUT"], dependencies=management_dependency
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
    application.add_api_route("/internal/release", release, methods=["POST"], dependencies=internal_dependency)
    application.add_api_route("/internal/heartbeat", heartbeat, methods=["POST"], dependencies=internal_dependency)
    application.add_api_route("/v1/{path:path}", proxy, methods=["POST"])

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
        "/ui-api/models/{model}/routing-table", routing_table, methods=["GET"], dependencies=ui_dependency
    )
    application.add_api_route(
        "/ui-api/models/{model}/policy", update_policy, methods=["PUT"], dependencies=ui_dependency
    )
    application.add_api_route("/ui-api/stats", stats, methods=["GET"], dependencies=ui_dependency)
    application.add_api_route(
        "/ui-api/litellm/status", litellm_status, methods=["GET"], dependencies=ui_dependency
    )
    application.add_api_route(
        "/ui-api/provider-services", provider_service_manifests, methods=["GET"], dependencies=ui_dependency
    )
    application.add_api_route(
        "/ui-api/provider-services/validate", validate_provider, methods=["POST"], dependencies=ui_dependency
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


async def _run_reaper(store: StateStore) -> None:
    while True:
        await asyncio.sleep(1)
        await store.sweep_expired()


async def _model_summary(
    scheduler: Scheduler,
    model: str,
) -> ModelSummary:
    routes: Final = await scheduler.route_table(model)
    return ModelSummary(
        model=model,
        strategy=scheduler.policy(model).strategy,
        accounts=len(routes),
        available_accounts=sum(1 for route in routes if route.available),
        inflight=sum(route.inflight for route in routes),
        max_concurrency=sum(route.max_concurrency for route in routes),
    )


def _build_store(settings: Settings) -> StateStore:
    if settings.store_mode == "redis":
        return RedisStateStore(settings.redis_url)
    return MemoryStateStore()


def _build_parser_data(settings: Settings) -> ParserDataReader | None:
    if settings.database_url is None:
        return None
    return ParserDataService(
        parser_runs=PostgresParserRunRepository(settings.database_url, schema=settings.database_schema),
        overrides=PostgresOverrideEventRepository(settings.database_url, schema=settings.database_schema),
    )


def _build_parser_overrides(settings: Settings) -> ParserOverrideWriter | None:
    if settings.database_url is None:
        return None
    return ParserOverrideService(
        parser_runs=PostgresParserRunRepository(settings.database_url, schema=settings.database_schema),
        overrides=PostgresOverrideEventRepository(settings.database_url, schema=settings.database_schema),
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
    if failure.code == OverrideMutationFailureCode.DATABASE_UNAVAILABLE:
        return HTTPException(status_code=503, detail=detail)
    return HTTPException(status_code=500, detail=detail)


app = create_app()
