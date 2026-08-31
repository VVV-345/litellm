"""集中注册号池的内部、管理和浏览器 API，保持三类访问边界分离。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI
from fastapi.params import Depends

Endpoint = Callable[..., object]


@dataclass(frozen=True, slots=True)
class AccountPoolRouteHandlers:
    """应用装配层提供的端点集合，路由层不依赖具体业务服务。"""

    healthz: Endpoint
    runtime_config: Endpoint
    metrics: Endpoint
    worker_states: Endpoint
    accounts: Endpoint
    create_account: Endpoint
    update_account: Endpoint
    delete_account: Endpoint
    models: Endpoint
    routing_table: Endpoint
    update_policy: Endpoint
    routing_policy: Endpoint
    update_routing_policy: Endpoint
    update_routing_candidate: Endpoint
    update_routing_order: Endpoint
    delete_routing_candidate: Endpoint
    litellm_status: Endpoint
    provider_service_manifests: Endpoint
    validate_provider: Endpoint
    upstream_provider_manifests: Endpoint
    discover_upstream_models: Endpoint
    channels: Endpoint
    overview_data: Endpoint
    event_log_data: Endpoint
    create_channel: Endpoint
    import_channel: Endpoint
    channel_detail: Endpoint
    channel_aggregate_detail: Endpoint
    update_channel: Endpoint
    detach_channel: Endpoint
    delete_channel: Endpoint
    delete_external_deployment: Endpoint
    reconcile_channel: Endpoint
    probe_channel_health: Endpoint
    channel_health_detail: Endpoint
    probe_account_health: Endpoint
    channel_operation: Endpoint
    start_parser_task: Endpoint
    parser_task: Endpoint
    parser_runs: Endpoint
    effective_parser_data: Endpoint
    parser_snapshot: Endpoint
    export_parser_snapshot: Endpoint
    import_parser_snapshot: Endpoint
    set_parser_override: Endpoint
    revoke_parser_override: Endpoint
    stats: Endpoint
    acquire: Endpoint
    settle: Endpoint
    record_request_activity: Endpoint
    record_settlement_event: Endpoint
    release: Endpoint
    heartbeat: Endpoint
    update_ui_routing_policy: Endpoint
    update_ui_routing_candidate: Endpoint
    update_ui_routing_order: Endpoint
    delete_ui_routing_candidate: Endpoint
    create_ui_channel: Endpoint
    update_ui_channel: Endpoint
    delete_ui_channel: Endpoint


def register_account_pool_routes(
    application: FastAPI,
    handlers: AccountPoolRouteHandlers,
    *,
    internal_dependencies: Sequence[Depends],
    management_dependencies: Sequence[Depends],
    ui_dependencies: Sequence[Depends],
) -> None:
    """按调用者身份注册端点，不让 UI 端点取得内部服务权限。"""

    application.add_api_route("/healthz", handlers.healthz, methods=["GET"])
    application.add_api_route("/metrics", handlers.metrics, methods=["GET"], include_in_schema=False)
    _register_internal_routes(application, handlers, internal_dependencies)
    _register_management_routes(application, handlers, management_dependencies)
    _register_ui_routes(application, handlers, ui_dependencies)


def _register_internal_routes(
    application: FastAPI,
    handlers: AccountPoolRouteHandlers,
    dependencies: Sequence[Depends],
) -> None:
    router: APIRouter = APIRouter(prefix="/internal", dependencies=dependencies)
    router.add_api_route("/runtime-config", handlers.runtime_config, methods=["GET"], include_in_schema=False)
    router.add_api_route("/acquire", handlers.acquire, methods=["POST"])
    router.add_api_route("/settle", handlers.settle, methods=["POST"])
    router.add_api_route("/request-activity", handlers.record_request_activity, methods=["POST"])
    router.add_api_route("/settlement-event", handlers.record_settlement_event, methods=["POST"])
    router.add_api_route("/release", handlers.release, methods=["POST"])
    router.add_api_route("/heartbeat", handlers.heartbeat, methods=["POST"])
    application.include_router(router)


def _register_management_routes(
    application: FastAPI,
    handlers: AccountPoolRouteHandlers,
    dependencies: Sequence[Depends],
) -> None:
    router: APIRouter = APIRouter(prefix="/api", dependencies=dependencies)
    router.add_api_route("/workers", handlers.worker_states, methods=["GET"])
    router.add_api_route("/accounts", handlers.accounts, methods=["GET"])
    router.add_api_route("/accounts", handlers.create_account, methods=["POST"])
    router.add_api_route("/accounts/{account_id}", handlers.update_account, methods=["PUT"])
    router.add_api_route("/accounts/{account_id}", handlers.delete_account, methods=["DELETE"])
    router.add_api_route("/models", handlers.models, methods=["GET"])
    router.add_api_route("/models/{model:path}/routing-table", handlers.routing_table, methods=["GET"])
    router.add_api_route("/models/{model:path}/policy", handlers.update_policy, methods=["PUT"])
    router.add_api_route("/models/{model:path}/routing-policy", handlers.routing_policy, methods=["GET"])
    router.add_api_route("/models/{model:path}/routing-policy", handlers.update_routing_policy, methods=["PUT"])
    router.add_api_route(
        "/models/{model:path}/routing-candidates/{binding_id}",
        handlers.update_routing_candidate,
        methods=["PUT"],
    )
    router.add_api_route("/models/{model:path}/routing-order", handlers.update_routing_order, methods=["PUT"])
    router.add_api_route(
        "/models/{model:path}/routing-candidates/{binding_id}",
        handlers.delete_routing_candidate,
        methods=["DELETE"],
    )
    router.add_api_route("/litellm/status", handlers.litellm_status, methods=["GET"])
    router.add_api_route("/provider-services", handlers.provider_service_manifests, methods=["GET"])
    router.add_api_route("/provider-services/validate", handlers.validate_provider, methods=["POST"])
    router.add_api_route("/upstream-providers", handlers.upstream_provider_manifests, methods=["GET"])
    router.add_api_route("/upstream-providers/discover-models", handlers.discover_upstream_models, methods=["POST"])
    router.add_api_route("/channels", handlers.channels, methods=["GET"])
    router.add_api_route("/overview", handlers.overview_data, methods=["GET"])
    router.add_api_route("/events", handlers.event_log_data, methods=["GET"])
    router.add_api_route("/channels", handlers.create_channel, methods=["POST"])
    router.add_api_route("/channels/import", handlers.import_channel, methods=["POST"])
    router.add_api_route("/channels/{channel_id}", handlers.channel_detail, methods=["GET"])
    router.add_api_route("/channels/{channel_id}/aggregate", handlers.channel_aggregate_detail, methods=["GET"])
    router.add_api_route("/channels/{channel_id}", handlers.update_channel, methods=["PUT"])
    router.add_api_route("/channels/{channel_id}/detach", handlers.detach_channel, methods=["POST"])
    router.add_api_route("/channels/{channel_id}", handlers.delete_channel, methods=["DELETE"])
    router.add_api_route(
        "/channels/{channel_id}/bindings/{binding_id}/delete-external-deployment",
        handlers.delete_external_deployment,
        methods=["POST"],
    )
    router.add_api_route("/channels/{channel_id}/reconcile", handlers.reconcile_channel, methods=["POST"])
    router.add_api_route("/channels/{channel_id}/health-probe", handlers.probe_channel_health, methods=["POST"])
    router.add_api_route("/channels/{channel_id}/health", handlers.channel_health_detail, methods=["GET"])
    router.add_api_route("/accounts/{account_id}/health-probe", handlers.probe_account_health, methods=["POST"])
    router.add_api_route("/operations/{operation_id}", handlers.channel_operation, methods=["GET"])
    router.add_api_route("/channels/{channel_id}/parse", handlers.start_parser_task, methods=["POST"], status_code=202)
    router.add_api_route("/channels/{channel_id}/parser-tasks/{task_id}", handlers.parser_task, methods=["GET"])
    router.add_api_route("/channels/{channel_id}/parser-runs", handlers.parser_runs, methods=["GET"])
    router.add_api_route("/channels/{channel_id}/effective-data", handlers.effective_parser_data, methods=["GET"])
    router.add_api_route("/channels/{channel_id}/snapshot", handlers.parser_snapshot, methods=["GET"])
    router.add_api_route("/channels/{channel_id}/export", handlers.export_parser_snapshot, methods=["GET"])
    router.add_api_route("/channels/{channel_id}/import", handlers.import_parser_snapshot, methods=["POST"])
    router.add_api_route("/channels/{channel_id}/overrides", handlers.set_parser_override, methods=["PUT"])
    router.add_api_route(
        "/channels/{channel_id}/overrides/{field_path:path}",
        handlers.revoke_parser_override,
        methods=["DELETE"],
    )
    router.add_api_route("/stats", handlers.stats, methods=["GET"])
    application.include_router(router)


def _register_ui_routes(
    application: FastAPI,
    handlers: AccountPoolRouteHandlers,
    dependencies: Sequence[Depends],
) -> None:
    router: APIRouter = APIRouter(prefix="/ui-api")
    router.add_api_route("/accounts", handlers.accounts, methods=["GET"], dependencies=dependencies)
    router.add_api_route("/accounts", handlers.create_account, methods=["POST"], dependencies=dependencies)
    router.add_api_route("/accounts/{account_id}", handlers.update_account, methods=["PUT"], dependencies=dependencies)
    router.add_api_route(
        "/accounts/{account_id}", handlers.delete_account, methods=["DELETE"], dependencies=dependencies
    )
    router.add_api_route("/models", handlers.models, methods=["GET"], dependencies=dependencies)
    router.add_api_route(
        "/models/{model:path}/routing-table", handlers.routing_table, methods=["GET"], dependencies=dependencies
    )
    router.add_api_route(
        "/models/{model:path}/policy", handlers.update_policy, methods=["PUT"], dependencies=dependencies
    )
    router.add_api_route(
        "/models/{model:path}/routing-policy", handlers.routing_policy, methods=["GET"], dependencies=dependencies
    )
    # 写处理器需要管理员令牌转发给 LiteLLM，因此在端点参数中单独依赖认证。
    router.add_api_route("/models/{model:path}/routing-policy", handlers.update_ui_routing_policy, methods=["PUT"])
    router.add_api_route(
        "/models/{model:path}/routing-candidates/{binding_id}",
        handlers.update_ui_routing_candidate,
        methods=["PUT"],
    )
    router.add_api_route("/models/{model:path}/routing-order", handlers.update_ui_routing_order, methods=["PUT"])
    router.add_api_route(
        "/models/{model:path}/routing-candidates/{binding_id}",
        handlers.delete_ui_routing_candidate,
        methods=["DELETE"],
    )
    router.add_api_route("/stats", handlers.stats, methods=["GET"], dependencies=dependencies)
    router.add_api_route("/litellm/status", handlers.litellm_status, methods=["GET"], dependencies=dependencies)
    router.add_api_route(
        "/provider-services", handlers.provider_service_manifests, methods=["GET"], dependencies=dependencies
    )
    router.add_api_route(
        "/accounts/{account_id}/health-probe",
        handlers.probe_account_health,
        methods=["POST"],
        dependencies=dependencies,
    )
    router.add_api_route(
        "/provider-services/validate", handlers.validate_provider, methods=["POST"], dependencies=dependencies
    )
    router.add_api_route(
        "/upstream-providers", handlers.upstream_provider_manifests, methods=["GET"], dependencies=dependencies
    )
    router.add_api_route(
        "/upstream-providers/discover-models",
        handlers.discover_upstream_models,
        methods=["POST"],
        dependencies=dependencies,
    )
    router.add_api_route("/channels", handlers.channels, methods=["GET"], dependencies=dependencies)
    router.add_api_route("/channels", handlers.create_ui_channel, methods=["POST"])
    router.add_api_route("/channels/{channel_id}", handlers.channel_detail, methods=["GET"], dependencies=dependencies)
    router.add_api_route("/channels/{channel_id}", handlers.update_ui_channel, methods=["PUT"])
    router.add_api_route("/channels/{channel_id}", handlers.delete_ui_channel, methods=["DELETE"])
    router.add_api_route("/overview", handlers.overview_data, methods=["GET"], dependencies=dependencies)
    router.add_api_route("/events", handlers.event_log_data, methods=["GET"], dependencies=dependencies)
    router.add_api_route(
        "/channels/{channel_id}/aggregate",
        handlers.channel_aggregate_detail,
        methods=["GET"],
        dependencies=dependencies,
    )
    router.add_api_route(
        "/channels/{channel_id}/parser-runs", handlers.parser_runs, methods=["GET"], dependencies=dependencies
    )
    router.add_api_route(
        "/channels/{channel_id}/effective-data",
        handlers.effective_parser_data,
        methods=["GET"],
        dependencies=dependencies,
    )
    router.add_api_route(
        "/channels/{channel_id}/snapshot", handlers.parser_snapshot, methods=["GET"], dependencies=dependencies
    )
    router.add_api_route(
        "/channels/{channel_id}/export", handlers.export_parser_snapshot, methods=["GET"], dependencies=dependencies
    )
    application.include_router(router)
