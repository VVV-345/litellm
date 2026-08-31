"""验证号池管理接口和内部鉴权。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast
from uuid import UUID, uuid4

import httpx
import pytest
from account_pool.app import Runtime, create_app
from account_pool.audit.models import AuditOutcome
from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.catalog.models import AdministrativeState, ChannelList, ChannelSummary
from account_pool.config import Settings
from account_pool.details import (
    ChannelAggregateDetail,
    ChannelAggregateFailure,
    DetailSection,
    DetailSectionFailure,
)
from account_pool.domain.provider_source import ProviderServiceManifest
from account_pool.events import (
    EventAuditSummary,
    EventLogEntry,
    EventLogFailure,
    EventLogFailureCode,
    EventLogPage,
    EventQuery,
    EventQueryOutcome,
)
from account_pool.health.models import (
    HealthActivity,
    HealthEventRecord,
    HealthProbeRequest,
    HealthProbeResult,
    HealthProbeStatus,
    HealthProbeTrigger,
    HealthRequestActivity,
)
from account_pool.health.repository import (
    HealthActivityLoadSuccess,
    HealthActivityWriteSuccess,
    HealthEventListSuccess,
    HealthLoadSuccess,
    HealthPersistenceFailure,
    HealthPersistenceFailureCode,
    HealthWriteSuccess,
)
from account_pool.health.service import ChannelHealthDetail, ChannelHealthDetailSuccess
from account_pool.models import (
    AccountSnapshot,
    AccountView,
    AcquireSuccess,
    ChannelPriority,
    Health,
    LiteLLMStatus,
    ManagementResult,
    ModelSummary,
    QuotaConfig,
    QuotaSnapshot,
    QuotaUnit,
    RouteEntry,
    StatsView,
)
from account_pool.monitoring.models import WorkerStateList
from account_pool.operational.request_lifecycle import RequestEventStateStore
from account_pool.operational.restrictions import RestrictionEventStateStore
from account_pool.overview import (
    AccountPoolOverview,
    AccountPoolOverviewFailure,
    ChannelActivityOverview,
    ChannelOverview,
    OverviewFailureCode,
    ParserOverview,
    ParserOverviewState,
)
from account_pool.parsing.imports.models import (
    SnapshotImportRequest,
    SnapshotImportResult,
    SnapshotImportSuccess,
)
from account_pool.parsing.models import ParsedChannelData, ParserRunStatus
from account_pool.parsing.overrides.commands import (
    OverrideEventResult,
    OverrideMutationResult,
    OverrideMutationSuccess,
    OverrideRevokeRequest,
    OverrideSetRequest,
)
from account_pool.parsing.overrides.models import OverrideAction
from account_pool.parsing.persistence import ParserExportState
from account_pool.parsing.service import (
    EffectiveParserData,
    EffectiveParserDataResult,
    ParserDataFailure,
    ParserDataFailureCode,
    ParserRunHistory,
    ParserRunHistoryResult,
    ParserRunSummary,
    ParserSnapshotResult,
)
from account_pool.parsing.snapshots import ParserSnapshot
from account_pool.parsing.tasks.models import (
    ParserTaskAccepted,
    ParserTaskRecord,
    ParserTaskStartRequest,
    ParserTaskStartResult,
    ParserTaskStatus,
    ParserTaskView,
    ParserTaskViewResult,
)
from account_pool.quota.durable import DurableQuotaStateStore
from account_pool.routing.latency_store import DurableLatencyStateStore
from account_pool.runtime_contract import RuntimeConfigSnapshot
from account_pool.store import MemoryStateStore
from account_pool.sync.contracts import (
    ChannelDeleteRequest,
    ChannelDetail,
    ChannelManagementFailure,
    ChannelMutation,
    ChannelOperationView,
    ChannelReconcileRequest,
    ExternalDeploymentDeleteRequest,
    ReconcilePassResult,
)
from account_pool.sync.models import SyncStatus
from account_pool.upstream_providers.models import UpstreamModelDiscoveryResult, UpstreamProviderManifest
from pydantic import TypeAdapter

_MODEL_SUMMARIES_ADAPTER: Final = TypeAdapter(tuple[ModelSummary, ...])
_ROUTE_ENTRIES_ADAPTER: Final = TypeAdapter(tuple[RouteEntry, ...])
_ACCOUNT_VIEWS_ADAPTER: Final = TypeAdapter(tuple[AccountView, ...])
_JSON_OBJECT_ADAPTER: Final = TypeAdapter(dict[str, object])
_PROVIDER_MANIFESTS_ADAPTER: Final = TypeAdapter(tuple[ProviderServiceManifest, ...])
_UPSTREAM_PROVIDER_MANIFESTS_ADAPTER: Final = TypeAdapter(tuple[UpstreamProviderManifest, ...])
_SNAPSHOT_DOCUMENT_ADAPTER: Final = TypeAdapter(dict[UUID, ParserSnapshot])
_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_PARSER_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_PARSED_AT: Final = datetime(2026, 8, 19, 19, 0, tzinfo=UTC)
_ACTOR_SECRET: Final = "actor-signing-secret-with-at-least-32-bytes"


class FakeChannelCatalogReader:
    async def list_channels(self) -> ChannelList:
        return ChannelList(
            channels=(
                ChannelSummary(
                    channel_id=_CHANNEL_ID,
                    display_name="OpenAI 主渠道",
                    provider="openai",
                    base_url_display="https://api.openai.com/v1",
                    administrative_state=AdministrativeState.ENABLED,
                    max_concurrency=8,
                    priority=ChannelPriority.HIGH,
                    weight=20,
                    key_mask="sk-***main",
                    binding_count=2,
                    enabled_binding_count=1,
                    models=("gpt-5.6",),
                    created_at=_PARSED_AT,
                    updated_at=_PARSED_AT,
                ),
            )
        )

    async def get_channel(self, channel_id: UUID) -> ChannelSummary | None:
        return next(
            (channel for channel in (await self.list_channels()).channels if channel.channel_id == channel_id), None
        )


class FakeOverviewReader:
    async def read(self) -> AccountPoolOverview:
        return AccountPoolOverview(
            channels=(
                ChannelOverview(
                    channel_id=_CHANNEL_ID,
                    display_name="OpenAI 主渠道",
                    provider="openai",
                    base_url_display="https://api.openai.com/v1",
                    key_mask="sk-***main",
                    administrative_state=AdministrativeState.ENABLED,
                    priority=ChannelPriority.HIGH,
                    configured_models=("gpt-5.6",),
                    schedulable_models=("gpt-5.6",),
                    unavailable_reason_codes=(),
                    binding_count=1,
                    enabled_binding_count=1,
                    parser=ParserOverview(state=ParserOverviewState.NOT_RUN, failure_code="run_not_found"),
                    activity=ChannelActivityOverview(persistence_available=False),
                ),
            ),
            channel_count=1,
            administratively_enabled_count=1,
            healthy_count=0,
            schedulable_count=1,
            configured_model_count=1,
            schedulable_model_count=1,
            inflight=0,
            max_concurrency=0,
        )


class UnavailableOverviewReader:
    async def read(self) -> AccountPoolOverviewFailure:
        return AccountPoolOverviewFailure(code=OverviewFailureCode.DEPENDENCY_UNAVAILABLE, retryable=True)


class FakeEventLogReader:
    def __init__(self) -> None:
        self.queries: list[EventQuery] = []

    async def list_events(self, query: EventQuery) -> EventLogPage:
        self.queries.append(query)
        return EventLogPage(
            events=(
                EventLogEntry(
                    event_id=UUID("70000000-0000-0000-0000-000000000001"),
                    event_type="channel_create",
                    occurred_at=_PARSED_AT,
                    channel_id=_CHANNEL_ID,
                    request_id="request-events",
                    actor_type="user",
                    actor_id="admin-user",
                    outcome=EventQueryOutcome.ACCEPTED,
                    safe_details={"kind": "channel_create", "outcome": {"status": "accepted"}},
                    audit=EventAuditSummary(
                        actor_role="proxy_admin",
                        actor_action=ActorAction.CHANNEL_CREATE,
                        actor_envelope_id=UUID("70000000-0000-0000-0000-000000000002"),
                        outcome=AuditOutcome.ACCEPTED,
                    ),
                ),
            )
        )


class FailedEventLogReader:
    def __init__(self, failure: EventLogFailure) -> None:
        self._failure: Final = failure

    async def list_events(self, query: EventQuery) -> EventLogFailure:
        return self._failure


class FakeChannelAggregateReader:
    async def read_channel(self, channel_id: UUID) -> ChannelAggregateDetail:
        channel: Final = await FakeChannelManagementService().detail(channel_id)
        assert isinstance(channel, ChannelDetail)
        return ChannelAggregateDetail(
            channel=channel,
            overview=DetailSection(status="loaded", data=(await FakeOverviewReader().read()).channels[0]),
            parser=DetailSection[EffectiveParserData](
                status="unavailable",
                failure=DetailSectionFailure(code="run_not_found", retryable=False),
            ),
            health=DetailSection[ChannelHealthDetail](
                status="unavailable",
                failure=DetailSectionFailure(code="runtime_unavailable", retryable=True),
            ),
            routes=DetailSection(status="loaded", data=()),
            events=DetailSection(status="loaded", data=(await FakeEventLogReader().list_events(EventQuery())).events),
        )


class FailedChannelAggregateReader:
    async def read_channel(self, channel_id: UUID) -> ChannelAggregateFailure:
        return ChannelAggregateFailure(code="channel_not_found", retryable=False)


class FakeChannelManagementService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, ActorContext]] = []
        self.requests: list[ChannelMutation] = []

    async def detail(self, channel_id: UUID) -> ChannelDetail | ChannelManagementFailure:
        assert channel_id == _CHANNEL_ID
        return ChannelDetail(
            channel_id=channel_id,
            display_name="OpenAI 主渠道",
            provider="openai_compatible",
            group=None,
            base_url_display="https://api.openai.com/v1",
            administrative_state=AdministrativeState.ENABLED,
            max_concurrency=8,
            priority=ChannelPriority.HIGH,
            weight=20,
            quotas=QuotaConfig(),
            key_mask="sk-***main",
            bindings=(),
        )

    async def operation(self, operation_id: UUID) -> ChannelOperationView | ChannelManagementFailure:
        return _channel_operation(operation_id)

    async def detail_by_legacy_account(self, account_id: str) -> ChannelDetail | ChannelManagementFailure:
        assert account_id == "legacy-channel"
        return await self.detail(_CHANNEL_ID)

    async def create(
        self,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelOperationView:
        assert request.api_key is not None and request.api_key.get_secret_value() == "one-time-secret"
        self.requests.append(request)
        self.calls.append(("create", idempotency_key, actor))
        return _channel_operation(UUID("60000000-0000-0000-0000-000000000001"))

    async def import_channel(
        self,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelOperationView:
        raise AssertionError("not used")

    async def update(
        self,
        channel_id: UUID,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelOperationView:
        assert channel_id == _CHANNEL_ID
        self.requests.append(request)
        self.calls.append(("update", idempotency_key, actor))
        return _channel_operation(UUID("60000000-0000-0000-0000-000000000002"))

    async def detach(
        self,
        channel_id: UUID,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelOperationView:
        raise AssertionError("not used")

    async def delete(
        self,
        channel_id: UUID,
        request: ChannelDeleteRequest,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelOperationView:
        assert channel_id == _CHANNEL_ID
        self.calls.append(("delete", idempotency_key, actor))
        return _channel_operation(UUID("60000000-0000-0000-0000-000000000003"))

    async def delete_external(
        self,
        channel_id: UUID,
        binding_id: UUID,
        request: ExternalDeploymentDeleteRequest,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelOperationView:
        raise AssertionError("not used")

    async def reconcile(
        self,
        channel_id: UUID,
        request: ChannelReconcileRequest,
        actor: ActorContext,
    ) -> ChannelOperationView:
        raise AssertionError("not used")

    async def reconcile_pending(self, limit: int = 100) -> ReconcilePassResult:
        assert limit == 100
        return ReconcilePassResult(inspected=0, items=())


def _channel_operation(operation_id: UUID) -> ChannelOperationView:
    return ChannelOperationView(
        status="accepted",
        operation_id=operation_id,
        channel_id=_CHANNEL_ID,
        operation_status=SyncStatus.APPLIED,
        requires_key=False,
        failure=None,
    )


class FakeParserDataReader:
    def __init__(
        self,
        history_result: ParserRunHistoryResult,
        effective_result: EffectiveParserDataResult,
        snapshot_result: ParserSnapshotResult,
    ) -> None:
        self._history_result: Final = history_result
        self._effective_result: Final = effective_result
        self._snapshot_result: Final = snapshot_result

    async def history(self, channel_id: UUID, limit: int) -> ParserRunHistoryResult:
        assert channel_id == _CHANNEL_ID
        assert limit == 5
        return self._history_result

    async def effective_data(self, channel_id: UUID) -> EffectiveParserDataResult:
        assert channel_id == _CHANNEL_ID
        return self._effective_result

    async def snapshot(self, channel_id: UUID) -> ParserSnapshotResult:
        assert channel_id == _CHANNEL_ID
        return self._snapshot_result


class FakeParserOverrideWriter:
    def __init__(self) -> None:
        self.actors: list[ActorContext] = []
        self.field_paths: list[str] = []

    async def set_override(
        self,
        channel_id: UUID,
        request: OverrideSetRequest,
        actor: ActorContext,
    ) -> OverrideMutationResult:
        assert channel_id == _CHANNEL_ID
        self.actors.append(actor)
        return _override_success(request.override_id, OverrideAction.SET, actor)

    async def revoke_override(
        self,
        channel_id: UUID,
        field_path: str,
        request: OverrideRevokeRequest,
        actor: ActorContext,
    ) -> OverrideMutationResult:
        assert channel_id == _CHANNEL_ID
        self.actors.append(actor)
        self.field_paths.append(field_path)
        return _override_success(request.override_id, OverrideAction.REVOKE, actor)


class FakeParserTaskManager:
    def __init__(self) -> None:
        self.actors: list[ActorContext] = []
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def start(
        self,
        channel_id: UUID,
        request: ParserTaskStartRequest,
        actor: ActorContext,
    ) -> ParserTaskStartResult:
        assert channel_id == _CHANNEL_ID
        assert request.api_key.get_secret_value() == "one-time-secret"
        self.actors.append(actor)
        return ParserTaskAccepted(
            task_id=UUID("30000000-0000-0000-0000-000000000003"),
            channel_id=channel_id,
            parser_run_id=_PARSER_RUN_ID,
        )

    async def view(self, channel_id: UUID, task_id: UUID) -> ParserTaskViewResult:
        assert channel_id == _CHANNEL_ID
        return ParserTaskView(
            task=ParserTaskRecord(
                task_id=task_id,
                channel_id=channel_id,
                parser_run_id=_PARSER_RUN_ID,
                provider_id="openai_compatible",
                openai_compatible=True,
                status=ParserTaskStatus.RUNNING,
                owner_instance_id=UUID("40000000-0000-0000-0000-000000000004"),
                actor_id="admin-user",
                actor_role="proxy_admin",
                request_id="request-parse",
                created_at=_PARSED_AT,
                heartbeat_at=_PARSED_AT,
            )
        )

    async def close(self, timeout_seconds: float = 10) -> None:
        assert timeout_seconds == 10
        self.closed = True


class FakeParserExportRetryManager:
    def __init__(self) -> None:
        self.started: Final = asyncio.Event()
        self.cancelled = False

    async def run(self) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class FakeSnapshotImporter:
    def __init__(self) -> None:
        self.actors: list[ActorContext] = []

    async def import_snapshot(
        self,
        channel_id: UUID,
        request: SnapshotImportRequest,
        actor: ActorContext,
    ) -> SnapshotImportResult:
        assert channel_id == _CHANNEL_ID
        self.actors.append(actor)
        snapshot: Final = request.document[channel_id]
        return SnapshotImportSuccess(
            status="created",
            import_id=request.import_id,
            channel_id=channel_id,
            source_parser_run_id=_PARSER_RUN_ID,
            effective_result=snapshot.effective_result,
        )


class FakeHealthProbeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, HealthProbeRequest]] = []
        self.due_calls = 0

    async def probe_channel(
        self,
        channel_id: UUID,
        request: HealthProbeRequest,
        trigger: HealthProbeTrigger = HealthProbeTrigger.MANUAL,
    ) -> HealthProbeResult:
        self.calls.append((channel_id, request))
        return HealthProbeResult(
            probe_id=UUID("90000000-0000-0000-0000-000000000001"),
            status=HealthProbeStatus.SUCCEEDED,
            trigger=trigger,
            channel_id=channel_id,
            account_id="channel-a",
            deployment_id=request.deployment_id or "deployment-a",
            public_model="model-a",
            response_status_code=200,
            latency_ms=12.5,
        )

    async def probe_account(
        self,
        account_id: str,
        request: HealthProbeRequest,
        trigger: HealthProbeTrigger = HealthProbeTrigger.MANUAL,
    ) -> HealthProbeResult:
        return HealthProbeResult(
            probe_id=UUID("90000000-0000-0000-0000-000000000002"),
            status=HealthProbeStatus.SUCCEEDED,
            trigger=trigger,
            account_id=account_id,
            deployment_id=request.deployment_id or "deployment-a",
            public_model="model-a",
            response_status_code=200,
            latency_ms=12.5,
        )

    async def probe_due(self) -> tuple[HealthProbeResult, ...]:
        self.due_calls += 1
        return ()


class FakeHealthEventRepository:
    def __init__(self) -> None:
        self.records: list[HealthEventRecord] = []
        self.request_activities: list[HealthRequestActivity] = []

    async def append(self, record: HealthEventRecord) -> HealthWriteSuccess:
        self.records.append(record)
        return HealthWriteSuccess(status="created", record=record)

    async def record_request(self, activity: HealthRequestActivity) -> HealthActivityWriteSuccess:
        self.request_activities.append(activity)
        return HealthActivityWriteSuccess(activity=activity)

    async def load(self, event_id: UUID) -> HealthLoadSuccess | HealthPersistenceFailure:
        record: Final = next((candidate for candidate in self.records if candidate.event.event_id == event_id), None)
        if record is None:
            return HealthPersistenceFailure(
                code=HealthPersistenceFailureCode.EVENT_NOT_FOUND,
                retryable=False,
            )
        return HealthLoadSuccess(record=record)

    async def load_activity(self) -> HealthActivityLoadSuccess:
        activities: Final = tuple(
            HealthActivity(
                channel_id=item.channel_id,
                account_id=item.account_id,
                model_id=item.model_id,
                deployment_id=item.deployment_id,
                last_request_at=item.observed_at,
                updated_at=item.observed_at,
            )
            for item in self.request_activities
        )
        return HealthActivityLoadSuccess(activities=activities)

    async def list_recent(self, channel_id: UUID, limit: int = 50) -> HealthEventListSuccess:
        records: Final = tuple(record for record in reversed(self.records) if record.event.channel_id == channel_id)[
            :limit
        ]
        return HealthEventListSuccess(records=records)


class FakeHealthDetailReader:
    async def read_channel(self, channel_id: UUID) -> ChannelHealthDetailSuccess:
        return ChannelHealthDetailSuccess(
            detail=ChannelHealthDetail(
                channel_id=channel_id,
                account_id="channel-a",
                runtime=AccountSnapshot(
                    account_id="channel-a",
                    enabled=True,
                    health=Health.HEALTHY,
                    inflight=0,
                    max_concurrency=2,
                    cooldown_until=None,
                    consecutive_failures=0,
                    quota=QuotaSnapshot(unit=QuotaUnit.TOKENS, total=100, five_hour=None, weekly=None),
                ),
                exclusions=(),
                activities=(),
                events=(),
                persistence_available=True,
            )
        )


def settings(
    config_path: Path | None = None,
    admin_key: str | None = None,
    internal_token: str | None = "test-service-token",
    actor_secret: str | None = None,
    database_url: str | None = None,
) -> Settings:
    return Settings(
        config_path=config_path or Path(__file__).resolve().parents[1] / "config" / "accounts.demo.yaml",
        store_mode="memory",
        redis_url="redis://unused",
        litellm_url="http://litellm.internal",
        litellm_admin_key=admin_key,
        lease_ttl_seconds=60,
        internal_token=internal_token,
        database_url=database_url,
        actor_secret=actor_secret,
    )


def test_build_store_enables_durable_quota_runtime_with_postgres() -> None:
    memory_runtime: Final = cast(Runtime, create_app(settings=settings()).state.runtime)
    durable_runtime: Final = cast(
        Runtime,
        create_app(settings=settings(database_url="postgresql://account-pool.invalid/test")).state.runtime,
    )

    assert isinstance(memory_runtime.store, MemoryStateStore)
    assert isinstance(durable_runtime.store, RequestEventStateStore)
    assert isinstance(
        durable_runtime.store._backend,  # pyright: ignore[reportPrivateUsage]  # 验证运行时装饰器的固定装配顺序
        RestrictionEventStateStore,
    )
    assert isinstance(
        durable_runtime.store._backend._backend,  # pyright: ignore[reportPrivateUsage]  # 验证运行时装饰器的固定装配顺序
        DurableLatencyStateStore,
    )
    assert isinstance(
        durable_runtime.store._backend._backend._backend,  # pyright: ignore[reportPrivateUsage]  # 验证运行时装饰器的固定装配顺序
        DurableQuotaStateStore,
    )


def _parser_data_reader() -> FakeParserDataReader:
    history: Final = ParserRunHistory(
        channel_id=_CHANNEL_ID,
        runs=(
            ParserRunSummary(
                parser_run_id=_PARSER_RUN_ID,
                parser_id="fixture-parser",
                parser_version="1.0.0",
                parsed_at=_PARSED_AT,
                status=ParserRunStatus.PARTIAL,
                discovered_models=("model-a",),
                export=ParserExportState(),
            ),
        ),
    )
    effective: Final = EffectiveParserData(
        channel_id=_CHANNEL_ID,
        parser_run_id=_PARSER_RUN_ID,
        parser_id="fixture-parser",
        parser_version="1.0.0",
        parsed_at=_PARSED_AT,
        parser_status=ParserRunStatus.PARTIAL,
        raw_result=ParsedChannelData(warnings=("需要人工确认",)),
        effective_result=ParsedChannelData(warnings=("管理员已确认",)),
    )
    snapshot: Final = ParserSnapshot(
        parser_id="fixture-parser",
        parser_version="1.0.0",
        parser_run_id=_PARSER_RUN_ID,
        parsed_at=_PARSED_AT,
        status=ParserRunStatus.PARTIAL,
        raw_result=effective.raw_result,
        effective_result=effective.effective_result,
        discovered_models=("model-a",),
    )
    return FakeParserDataReader(history, effective, snapshot)


def _override_success(
    override_id: UUID,
    action: OverrideAction,
    actor: ActorContext,
) -> OverrideMutationSuccess:
    return OverrideMutationSuccess(
        status="created",
        event=OverrideEventResult(
            override_id=override_id,
            field_path="/subscription/balance",
            action=action,
            source_parser_run_id=_PARSER_RUN_ID,
            actor_id=actor.user_id,
            occurred_at=_PARSED_AT,
        ),
        effective_result=ParsedChannelData(warnings=("管理员已确认",)),
    )


def _actor_token(action: ActorAction, request_id: str = "request-123") -> str:
    issued_at: Final = int(datetime.now(UTC).timestamp())
    header: Final = {"alg": "HS256", "typ": "JWT"}
    claims: Final = {
        "iss": "litellm-proxy",
        "aud": "account-pool",
        "sub": "admin-user",
        "role": "proxy_admin",
        "request_id": request_id,
        "action": action,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + 30,
        "jti": str(uuid4()),
    }
    encoded_header: Final = _encode_actor_segment(header)
    encoded_claims: Final = _encode_actor_segment(claims)
    signed: Final = f"{encoded_header}.{encoded_claims}"
    signature: Final = hmac.new(_ACTOR_SECRET.encode(), signed.encode("ascii"), hashlib.sha256).digest()
    encoded_signature: Final = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{signed}.{encoded_signature}"


def _encode_actor_segment(value: object) -> str:
    payload: Final = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_parser_export_retries() -> None:
    tasks: Final = FakeParserTaskManager()
    retries: Final = FakeParserExportRetryManager()
    app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        parser_tasks=tasks,
        parser_export_retries=retries,
    )

    async with app.router.lifespan_context(app):
        await retries.started.wait()

    assert tasks.initialized is True
    assert tasks.closed is True
    assert retries.cancelled is True


def test_injected_parser_tasks_do_not_create_unrelated_parser_workers() -> None:
    tasks: Final = FakeParserTaskManager()
    runtime: Final = cast(
        Runtime,
        create_app(
            settings=settings(database_url="postgresql://account-pool.invalid/test"),
            store=MemoryStateStore(),
            parser_tasks=tasks,
        ).state.runtime,
    )

    assert runtime.parser_tasks is tasks
    assert runtime.parser_export_retries is None
    assert runtime.public_metadata_tasks is None


@pytest.mark.asyncio
async def test_management_api_requires_configured_internal_token() -> None:
    app: Final = create_app(settings=settings(internal_token="service-secret"), store=MemoryStateStore())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            missing: Final = await client.get("/api/provider-services")
            invalid: Final = await client.get("/api/provider-services", headers={"x-account-pool-token": "wrong"})
            valid: Final = await client.get(
                "/api/provider-services", headers={"x-account-pool-token": "service-secret"}
            )
            upstream_missing: Final = await client.get("/api/upstream-providers")
            upstream: Final = await client.get(
                "/api/upstream-providers", headers={"x-account-pool-token": "service-secret"}
            )
            unknown_upstream: Final = await client.post(
                "/api/upstream-providers/discover-models",
                headers={"x-account-pool-token": "service-secret"},
                json={
                    "provider_id": "missing",
                    "api_base": "https://models.example/v1",
                    "api_key": "one-time-key",
                },
            )
            health: Final = await client.get("/healthz")
            metrics: Final = await client.get("/metrics")
            workers_missing: Final = await client.get("/api/workers")
            workers: Final = await client.get(
                "/api/workers",
                headers={"x-account-pool-token": "service-secret"},
            )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200
    manifests: Final = _PROVIDER_MANIFESTS_ADAPTER.validate_json(valid.content)
    manifest_ids: Final = tuple(manifest.provider_id for manifest in manifests)
    assert manifest_ids == ("generic",)
    assert upstream_missing.status_code == 401
    assert upstream.status_code == 200
    upstream_manifests: Final = _UPSTREAM_PROVIDER_MANIFESTS_ADAPTER.validate_json(upstream.content)
    assert "openai" in tuple(manifest.provider_id for manifest in upstream_manifests)
    upstream_failure: Final = UpstreamModelDiscoveryResult.model_validate_json(unknown_upstream.content)
    assert not upstream_failure.ok
    assert upstream_failure.failure_code == "unsupported_provider"
    assert "one-time-key" not in unknown_upstream.text
    assert health.status_code == 200
    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert 'account_pool_worker_enabled{worker="lease_reaper"} 1' in metrics.text
    assert workers_missing.status_code == 401
    assert workers.status_code == 200
    worker_states: Final = WorkerStateList.model_validate_json(workers.content)
    assert {item.worker.value for item in worker_states.workers} == {
        "active_health_probe",
        "channel_reconciler",
        "event_retention",
        "lease_reaper",
        "parser_export_retry",
        "public_metadata",
    }


@pytest.mark.asyncio
async def test_management_api_fails_closed_without_configured_token() -> None:
    app: Final = create_app(settings=settings(internal_token=None), store=MemoryStateStore())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.get("/api/accounts")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_runtime_config_requires_internal_token_and_omits_upstream_details() -> None:
    app: Final = create_app(settings=settings(internal_token="service-secret"), store=MemoryStateStore())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            missing: Final = await client.get("/internal/runtime-config")
            valid: Final = await client.get(
                "/internal/runtime-config",
                headers={"x-account-pool-token": "service-secret"},
            )

    assert missing.status_code == 401
    assert valid.status_code == 200
    snapshot: Final = RuntimeConfigSnapshot.model_validate_json(valid.content)
    assert snapshot.schema_version == 1
    assert snapshot.lease_ttl_seconds == 60
    assert snapshot.maximum_lease_seconds == 3_600
    assert snapshot.accounts
    assert "base_url" not in valid.text
    assert "provider_model" not in valid.text
    assert "api_key" not in valid.text


@pytest.mark.asyncio
async def test_control_plane_does_not_expose_client_gateway_routes() -> None:
    app: Final = create_app(settings=settings(), store=MemoryStateStore())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.post("/v1/chat/completions", json={"model": "gpt-4o"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_channel_health_probe_requires_matching_actor_action() -> None:
    probes: Final = FakeHealthProbeManager()
    app: Final = create_app(
        settings=settings(actor_secret=_ACTOR_SECRET),
        store=MemoryStateStore(),
        health_probes=probes,
    )
    request_id: Final = "request-health-probe"
    base_headers: Final = {
        "x-account-pool-token": "test-service-token",
        "x-account-pool-request-id": request_id,
    }
    valid_headers: Final = {
        **base_headers,
        "x-account-pool-actor": _actor_token(ActorAction.HEALTH_PROBE, request_id),
    }
    wrong_headers: Final = {
        **base_headers,
        "x-account-pool-actor": _actor_token(ActorAction.CHANNEL_UPDATE, request_id),
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            rejected: Final = await client.post(
                f"/api/channels/{_CHANNEL_ID}/health-probe",
                headers=wrong_headers,
                json={"deployment_id": "deployment-a"},
            )
            accepted: Final = await client.post(
                f"/api/channels/{_CHANNEL_ID}/health-probe",
                headers=valid_headers,
                json={"deployment_id": "deployment-a"},
            )

    result: Final = HealthProbeResult.model_validate_json(accepted.content)
    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert result.status == HealthProbeStatus.SUCCEEDED
    assert probes.calls == [(_CHANNEL_ID, HealthProbeRequest(deployment_id="deployment-a"))]


@pytest.mark.asyncio
async def test_channel_health_detail_returns_typed_runtime_view() -> None:
    app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        health_details=FakeHealthDetailReader(),
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.get(
                f"/api/channels/{_CHANNEL_ID}/health",
                headers={"x-account-pool-token": "test-service-token"},
            )

    detail: Final = ChannelHealthDetail.model_validate_json(response.content)
    assert response.status_code == 200
    assert detail.channel_id == _CHANNEL_ID
    assert detail.runtime.health == Health.HEALTHY
    assert detail.persistence_available is True


@pytest.mark.asyncio
async def test_channel_catalog_api_returns_only_redacted_postgres_identity_view() -> None:
    app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        catalog=FakeChannelCatalogReader(),
    )
    headers: Final = {"x-account-pool-token": "test-service-token"}

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.get("/api/channels", headers=headers)

    result: Final = ChannelList.model_validate_json(response.content)
    rendered: Final = response.text.casefold()
    assert response.status_code == 200
    assert result.channels[0].channel_id == _CHANNEL_ID
    assert result.channels[0].models == ("gpt-5.6",)
    assert "credential_ref" not in rendered
    assert "key_fingerprint" not in rendered


@pytest.mark.asyncio
async def test_overview_api_returns_redacted_aggregate_state() -> None:
    app: Final = create_app(settings=settings(), store=MemoryStateStore(), overview=FakeOverviewReader())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.get(
                "/api/overview",
                headers={"x-account-pool-token": "test-service-token"},
            )

    result: Final = AccountPoolOverview.model_validate_json(response.content)
    assert response.status_code == 200
    assert result.channels[0].schedulable_models == ("gpt-5.6",)
    assert "credential_ref" not in response.text.casefold()


@pytest.mark.asyncio
async def test_overview_api_maps_dependency_failure_to_service_unavailable() -> None:
    app: Final = create_app(settings=settings(), store=MemoryStateStore(), overview=UnavailableOverviewReader())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.get(
                "/api/overview",
                headers={"x-account-pool-token": "test-service-token"},
            )

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "dependency_unavailable", "retryable": True}}


@pytest.mark.asyncio
async def test_events_api_validates_and_forwards_structured_filters() -> None:
    events: Final = FakeEventLogReader()
    app: Final = create_app(settings=settings(), store=MemoryStateStore(), event_log=events)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.get(
                "/api/events",
                params={
                    "channel_id": str(_CHANNEL_ID),
                    "event_type": "channel_create",
                    "outcome": "accepted",
                    "request_id": "request-events",
                    "limit": 25,
                },
                headers={"x-account-pool-token": "test-service-token"},
            )

    result: Final = EventLogPage.model_validate_json(response.content)
    assert response.status_code == 200
    assert result.events[0].actor_id == "admin-user"
    assert events.queries == [
        EventQuery(
            channel_id=_CHANNEL_ID,
            event_type="channel_create",
            outcome=EventQueryOutcome.ACCEPTED,
            request_id="request-events",
            limit=25,
        )
    ]
    assert "api_key" not in response.text.casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "status_code"),
    [
        (EventLogFailure(code=EventLogFailureCode.INVALID_CURSOR, retryable=False), 422),
        (EventLogFailure(code=EventLogFailureCode.DATABASE_UNAVAILABLE, retryable=True), 503),
    ],
)
async def test_events_api_maps_typed_failures(failure: EventLogFailure, status_code: int) -> None:
    app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        event_log=FailedEventLogReader(failure),
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.get(
                "/api/events",
                headers={"x-account-pool-token": "test-service-token"},
            )

    assert response.status_code == status_code
    assert response.json() == {"detail": {"code": failure.code, "retryable": failure.retryable}}


@pytest.mark.asyncio
async def test_channel_aggregate_api_returns_redacted_partitioned_detail() -> None:
    app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        channel_details=FakeChannelAggregateReader(),
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.get(
                f"/api/channels/{_CHANNEL_ID}/aggregate",
                headers={"x-account-pool-token": "test-service-token"},
            )

    result: Final = ChannelAggregateDetail.model_validate_json(response.content)
    assert response.status_code == 200
    assert result.channel.channel_id == _CHANNEL_ID
    assert result.parser.failure is not None and result.parser.failure.code == "run_not_found"
    assert result.events.data is not None and result.events.data[0].event_type == "channel_create"
    assert "credential_ref" not in response.text.casefold()


@pytest.mark.asyncio
async def test_channel_aggregate_api_maps_missing_channel_to_not_found() -> None:
    app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        channel_details=FailedChannelAggregateReader(),
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.get(
                f"/api/channels/{_CHANNEL_ID}/aggregate",
                headers={"x-account-pool-token": "test-service-token"},
            )

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "channel_not_found", "retryable": False}}


@pytest.mark.asyncio
async def test_channel_create_requires_idempotency_and_verified_actor() -> None:
    lifecycle: Final = FakeChannelManagementService()
    app: Final = create_app(
        settings=settings(actor_secret=_ACTOR_SECRET),
        store=MemoryStateStore(),
        channel_management=lifecycle,
    )
    base_headers: Final = {"x-account-pool-token": "test-service-token"}
    body: Final = {
        "display_name": "Primary",
        "provider": "openai_compatible",
        "base_url_display": "https://api.openai.com/v1",
        "api_key": "one-time-secret",
        "bindings": [
            {
                "public_model": "gpt-5.6",
                "provider_model": "openai/gpt-5.6",
                "ownership": "pool_managed",
                "enabled": True,
            }
        ],
    }
    request_id: Final = "request-channel-create"
    headers: Final = {
        **base_headers,
        "idempotency-key": "channel-create-1",
        "x-account-pool-actor": _actor_token(ActorAction.CHANNEL_CREATE, request_id),
        "x-account-pool-request-id": request_id,
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            missing_key: Final = await client.post("/api/channels", headers=base_headers, json=body)
            created: Final = await client.post("/api/channels", headers=headers, json=body)

    assert missing_key.status_code == 400
    result: Final = ChannelOperationView.model_validate_json(created.content)
    assert result.operation_status == SyncStatus.APPLIED
    assert lifecycle.calls[0][0:2] == ("create", "channel-create-1")
    assert lifecycle.calls[0][2].request_id == request_id
    assert lifecycle.calls[0][2].action == ActorAction.CHANNEL_CREATE
    assert "one-time-secret" not in created.text


@pytest.mark.asyncio
async def test_channel_create_rejects_priority_outside_four_supported_tiers() -> None:
    lifecycle: Final = FakeChannelManagementService()
    app: Final = create_app(
        settings=settings(actor_secret=_ACTOR_SECRET),
        store=MemoryStateStore(),
        channel_management=lifecycle,
    )
    request_id: Final = "request-invalid-priority"
    headers: Final = {
        "x-account-pool-token": "test-service-token",
        "idempotency-key": "invalid-priority",
        "x-account-pool-actor": _actor_token(ActorAction.CHANNEL_CREATE, request_id),
        "x-account-pool-request-id": request_id,
    }
    body: Final = {
        "display_name": "Invalid priority",
        "provider": "openai_compatible",
        "base_url_display": "https://api.openai.com/v1",
        "priority": 250,
        "bindings": [
            {
                "public_model": "gpt-5.6",
                "provider_model": "openai/gpt-5.6",
                "ownership": "pool_managed",
            }
        ],
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.post("/api/channels", headers=headers, json=body)

    assert response.status_code == 422
    assert lifecycle.calls == []


@pytest.mark.asyncio
async def test_legacy_account_crud_aliases_use_channel_lifecycle_and_send_deprecation_headers() -> None:
    lifecycle: Final = FakeChannelManagementService()
    app: Final = create_app(
        settings=settings(actor_secret=_ACTOR_SECRET),
        store=MemoryStateStore(),
        channel_management=lifecycle,
    )
    body: Final = {
        "id": "legacy-channel",
        "display_name": "Legacy Channel",
        "provider": "openai_compatible",
        "base_url_display": "https://api.openai.com/v1",
        "max_concurrency": 4,
        "api_key": "one-time-secret",
        "deployments": [{"public_model": "gpt-5.6", "provider_model": "openai/gpt-5.6"}],
    }
    service_headers: Final = {"x-account-pool-token": "test-service-token"}
    create_headers: Final = {
        **service_headers,
        "idempotency-key": "legacy-create",
        "x-account-pool-request-id": "legacy-create-request",
        "x-account-pool-actor": _actor_token(ActorAction.CHANNEL_CREATE, "legacy-create-request"),
    }
    update_headers: Final = {
        **service_headers,
        "idempotency-key": "legacy-update",
        "x-account-pool-request-id": "legacy-update-request",
        "x-account-pool-actor": _actor_token(ActorAction.CHANNEL_UPDATE, "legacy-update-request"),
    }
    delete_headers: Final = {
        **service_headers,
        "idempotency-key": "legacy-delete",
        "x-account-pool-request-id": "legacy-delete-request",
        "x-account-pool-actor": _actor_token(ActorAction.CHANNEL_DELETE, "legacy-delete-request"),
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            created: Final = await client.post("/api/accounts", headers=create_headers, json=body)
            updated: Final = await client.put(
                "/api/accounts/legacy-channel",
                headers=update_headers,
                json={**body, "api_key": None, "display_name": "Legacy Updated"},
            )
            deleted: Final = await client.delete("/api/accounts/legacy-channel", headers=delete_headers)

    assert ManagementResult.model_validate_json(created.content).ok
    assert ManagementResult.model_validate_json(updated.content).ok
    assert ManagementResult.model_validate_json(deleted.content).ok
    assert created.headers["deprecation"] == "true"
    assert created.headers["link"] == '</api/channels>; rel="successor-version"'
    assert tuple(call[0] for call in lifecycle.calls) == ("create", "update", "delete")
    assert lifecycle.requests[0].legacy_account_id == "legacy-channel"
    assert lifecycle.requests[0].bindings[0].ownership.value == "pool_managed"
    assert lifecycle.requests[1].display_name == "Legacy Updated"


@pytest.mark.asyncio
async def test_parser_history_and_effective_data_require_token_and_return_safe_views() -> None:
    app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        parser_data=_parser_data_reader(),
    )
    base_path: Final = f"/api/channels/{_CHANNEL_ID}"

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            unauthorized: Final = await client.get(f"{base_path}/parser-runs?limit=5")
            history_response: Final = await client.get(
                f"{base_path}/parser-runs?limit=5",
                headers={"x-account-pool-token": "test-service-token"},
            )
            effective_response: Final = await client.get(
                f"{base_path}/effective-data",
                headers={"x-account-pool-token": "test-service-token"},
            )

    history: Final = ParserRunHistory.model_validate_json(history_response.content)
    effective: Final = EffectiveParserData.model_validate_json(effective_response.content)
    rendered: Final = f"{history_response.text}{effective_response.text}".casefold()
    assert unauthorized.status_code == 401
    assert history.runs[0].discovered_models == ("model-a",)
    assert effective.raw_result.warnings == ("需要人工确认",)
    assert effective.effective_result.warnings == ("管理员已确认",)
    assert "api_key" not in rendered
    assert "credential_ref" not in rendered
    assert "http://" not in rendered
    assert "https://" not in rendered


@pytest.mark.asyncio
async def test_snapshot_preview_and_export_return_the_same_redacted_channel_document() -> None:
    app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        parser_data=_parser_data_reader(),
    )
    headers: Final = {"x-account-pool-token": "test-service-token"}
    base_path: Final = f"/api/channels/{_CHANNEL_ID}"

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            preview: Final = await client.get(f"{base_path}/snapshot", headers=headers)
            exported: Final = await client.get(f"{base_path}/export", headers=headers)

    preview_document: Final = _SNAPSHOT_DOCUMENT_ADAPTER.validate_json(preview.content)
    exported_document: Final = _SNAPSHOT_DOCUMENT_ADAPTER.validate_json(exported.content)
    rendered: Final = exported.text.casefold()
    assert preview_document == exported_document
    assert tuple(exported_document) == (_CHANNEL_ID,)
    assert exported_document[_CHANNEL_ID].raw_result.warnings == ("需要人工确认",)
    assert exported_document[_CHANNEL_ID].effective_result.warnings == ("管理员已确认",)
    assert preview.headers["cache-control"] == "no-store"
    assert "content-disposition" not in preview.headers
    assert exported.headers["content-disposition"].endswith(f'{_CHANNEL_ID}-snapshot.json"')
    assert exported.headers["x-content-type-options"] == "nosniff"
    assert "api_key" not in rendered
    assert "credential_ref" not in rendered
    assert "http://" not in rendered
    assert "https://" not in rendered


@pytest.mark.asyncio
async def test_snapshot_import_requires_signed_action_and_returns_only_effective_data() -> None:
    importer: Final = FakeSnapshotImporter()
    app: Final = create_app(
        settings=settings(actor_secret=_ACTOR_SECRET),
        store=MemoryStateStore(),
        snapshot_importer=importer,
    )
    snapshot: Final = ParserSnapshot(
        parser_id="fixture-parser",
        parser_version="1.0.0",
        parser_run_id=_PARSER_RUN_ID,
        parsed_at=_PARSED_AT,
        status=ParserRunStatus.PARTIAL,
        raw_result=ParsedChannelData(warnings=("自动解析",)),
        effective_result=ParsedChannelData(warnings=("管理员导入",)),
    )
    body: Final = {
        "import_id": "30000000-0000-0000-0000-000000000003",
        "reason": "导入核对后的快照",
        "document": {str(_CHANNEL_ID): snapshot.model_dump(mode="json")},
    }
    path: Final = f"/api/channels/{_CHANNEL_ID}/import"
    service_headers: Final = {"x-account-pool-token": "test-service-token"}
    actor_headers: Final = {
        **service_headers,
        "x-account-pool-request-id": "request-import",
        "x-account-pool-actor": _actor_token(ActorAction.SNAPSHOT_IMPORT, request_id="request-import"),
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            missing_actor: Final = await client.post(path, headers=service_headers, json=body)
            imported_response: Final = await client.post(path, headers=actor_headers, json=body)

    imported: Final = SnapshotImportSuccess.model_validate_json(imported_response.content)
    rendered: Final = imported_response.text.casefold()
    assert missing_actor.status_code == 401
    assert imported.status == "created"
    assert imported.effective_result.warnings == ("管理员导入",)
    assert importer.actors[0].action == ActorAction.SNAPSHOT_IMPORT
    assert "api_key" not in rendered
    assert "api_base" not in rendered
    assert "http://" not in rendered
    assert "https://" not in rendered


@pytest.mark.asyncio
async def test_parser_data_api_reports_configuration_and_domain_failures() -> None:
    unavailable_app: Final = create_app(settings=settings(), store=MemoryStateStore())
    missing_reader: Final = FakeParserDataReader(
        ParserDataFailure(code=ParserDataFailureCode.CHANNEL_NOT_FOUND, retryable=False),
        ParserDataFailure(code=ParserDataFailureCode.RUN_NOT_FOUND, retryable=False),
        ParserDataFailure(code=ParserDataFailureCode.RUN_NOT_FOUND, retryable=False),
    )
    missing_app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        parser_data=missing_reader,
    )
    headers: Final = {"x-account-pool-token": "test-service-token"}
    base_path: Final = f"/api/channels/{_CHANNEL_ID}"

    async with unavailable_app.router.lifespan_context(unavailable_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=unavailable_app),
            base_url="http://account-pool",
        ) as client:
            unavailable: Final = await client.get(f"{base_path}/effective-data", headers=headers)
    async with missing_app.router.lifespan_context(missing_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=missing_app),
            base_url="http://account-pool",
        ) as client:
            missing_history: Final = await client.get(f"{base_path}/parser-runs?limit=5", headers=headers)
            missing_data: Final = await client.get(f"{base_path}/effective-data", headers=headers)

    assert unavailable.status_code == 503
    assert missing_history.status_code == 404
    assert missing_data.status_code == 404


@pytest.mark.asyncio
async def test_parser_task_api_requires_actor_and_never_returns_one_time_credentials() -> None:
    manager: Final = FakeParserTaskManager()
    app: Final = create_app(
        settings=settings(actor_secret=_ACTOR_SECRET),
        store=MemoryStateStore(),
        parser_tasks=manager,
    )
    task_id: Final = UUID("30000000-0000-0000-0000-000000000003")
    base_path: Final = f"/api/channels/{_CHANNEL_ID}"
    body: Final = {
        "provider_id": "openai_compatible",
        "api_key": "one-time-secret",
        "openai_compatible": True,
    }
    service_headers: Final = {"x-account-pool-token": "test-service-token"}
    actor_headers: Final = {
        **service_headers,
        "x-account-pool-request-id": "request-parse",
        "x-account-pool-actor": _actor_token(ActorAction.PARSER_START, request_id="request-parse"),
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            missing_actor: Final = await client.post(f"{base_path}/parse", headers=service_headers, json=body)
            legacy_response: Final = await client.post(
                f"{base_path}/parse",
                headers=actor_headers,
                json={**body, "api_base": "https://gateway.example.com/v1"},
            )
            accepted_response: Final = await client.post(f"{base_path}/parse", headers=actor_headers, json=body)
            task_response: Final = await client.get(
                f"{base_path}/parser-tasks/{task_id}",
                headers=service_headers,
            )

        assert manager.initialized

    accepted: Final = ParserTaskAccepted.model_validate_json(accepted_response.content)
    task: Final = ParserTaskView.model_validate_json(task_response.content)
    rendered: Final = f"{accepted_response.text}{task_response.text}".casefold()
    assert missing_actor.status_code == 401
    assert legacy_response.status_code == 422
    assert accepted_response.status_code == 202
    assert accepted.task_id == task_id
    assert task.task.status == ParserTaskStatus.RUNNING
    assert manager.actors[0].action == ActorAction.PARSER_START
    assert manager.closed
    assert "one-time-secret" not in rendered
    assert "gateway.example.com" not in rendered
    assert "api_key" not in rendered


@pytest.mark.asyncio
async def test_override_write_api_requires_request_bound_actor_envelope() -> None:
    writer: Final = FakeParserOverrideWriter()
    app: Final = create_app(
        settings=settings(actor_secret=_ACTOR_SECRET),
        store=MemoryStateStore(),
        parser_overrides=writer,
    )
    path: Final = f"/api/channels/{_CHANNEL_ID}/overrides"
    body: Final = {
        "override_id": "30000000-0000-0000-0000-000000000003",
        "target": {"kind": "subscription_field", "field": "balance"},
        "value": "20",
        "reason": "人工核对余额",
    }
    service_headers: Final = {"x-account-pool-token": "test-service-token"}
    valid_headers: Final = {
        **service_headers,
        "x-account-pool-request-id": "request-123",
        "x-account-pool-actor": _actor_token(ActorAction.OVERRIDE_SET),
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            missing_service: Final = await client.put(path, json=body)
            missing_actor: Final = await client.put(path, headers=service_headers, json=body)
            wrong_action: Final = await client.put(
                path,
                headers={
                    **service_headers,
                    "x-account-pool-request-id": "request-123",
                    "x-account-pool-actor": _actor_token(ActorAction.OVERRIDE_REVOKE),
                },
                json=body,
            )
            forged_actor_body: Final = await client.put(
                path,
                headers=valid_headers,
                json={**body, "actor_id": "forged-user"},
            )
            written: Final = await client.put(path, headers=valid_headers, json=body)

    result: Final = OverrideMutationSuccess.model_validate_json(written.content)
    assert missing_service.status_code == 401
    assert missing_actor.status_code == 401
    assert wrong_action.status_code == 403
    assert forged_actor_body.status_code == 422
    assert result.event.actor_id == "admin-user"
    assert writer.actors[0].request_id == "request-123"


@pytest.mark.asyncio
async def test_override_revoke_api_normalizes_field_path_and_requires_revoke_action() -> None:
    writer: Final = FakeParserOverrideWriter()
    app: Final = create_app(
        settings=settings(actor_secret=_ACTOR_SECRET),
        store=MemoryStateStore(),
        parser_overrides=writer,
    )
    path: Final = f"/api/channels/{_CHANNEL_ID}/overrides/subscription/balance"
    headers: Final = {
        "x-account-pool-token": "test-service-token",
        "x-account-pool-request-id": "request-456",
        "x-account-pool-actor": _actor_token(ActorAction.OVERRIDE_REVOKE, request_id="request-456"),
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://account-pool") as client:
            response: Final = await client.request(
                "DELETE",
                path,
                headers=headers,
                json={
                    "override_id": "30000000-0000-0000-0000-000000000004",
                    "expected_override_id": "30000000-0000-0000-0000-000000000003",
                    "reason": "恢复自动解析",
                },
            )

    result: Final = OverrideMutationSuccess.model_validate_json(response.content)
    assert response.status_code == 200
    assert result.event.action == OverrideAction.REVOKE
    assert writer.field_paths == ["/subscription/balance"]


@pytest.mark.asyncio
async def test_standalone_ui_uses_litellm_admin_authentication() -> None:
    def litellm(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/account_pool/authorize":
            return httpx.Response(status_code=404)
        token: Final = cast(str | None, request.headers.get("authorization"))
        if token == "Bearer admin-secret":
            return httpx.Response(status_code=200, json={"ok": True})
        return httpx.Response(status_code=401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(litellm)) as proxy_client:
        app: Final = create_app(
            settings=settings(admin_key="service-admin-key"),
            store=MemoryStateStore(),
            proxy_client=proxy_client,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://account-pool",
                follow_redirects=False,
            ) as client:
                root: Final = await client.get("/")
                ui: Final = await client.get("/ui/")
                ui_api: Final = await client.get("/ui/api.js")
                ui_app: Final = await client.get("/ui/app.js")
                channel_editor: Final = await client.get("/ui/channel-editor.js")
                missing: Final = await client.get("/ui-api/stats")
                invalid: Final = await client.get("/ui-api/stats", headers={"authorization": "Bearer invalid"})
                viewer: Final = await client.get("/ui-api/stats", headers={"authorization": "Bearer viewer-secret"})
                valid: Final = await client.get("/ui-api/stats", headers={"authorization": "Bearer admin-secret"})
                upstream: Final = await client.get(
                    "/ui-api/upstream-providers", headers={"authorization": "Bearer admin-secret"}
                )
                unsupported_discovery: Final = await client.post(
                    "/ui-api/upstream-providers/discover-models",
                    headers={"authorization": "Bearer admin-secret"},
                    json={
                        "provider_id": "not-a-parser",
                        "api_base": "https://models.example/v1",
                        "api_key": "one-time-key",
                    },
                )

    assert root.status_code == 307
    assert root.headers["location"] == "/ui/"
    assert ui.status_code == 200
    assert "号池调度器" in ui.text
    assert 'request("/channels")' in ui_api.text
    assert 'request("/upstream-providers")' in ui_api.text
    assert ui_app.status_code == 200
    assert 'createChannelEditor' in ui_app.text
    assert channel_editor.status_code == 200
    assert 'export const createChannelEditor' in channel_editor.text
    assert "provider-services" not in ui_api.text
    assert 'request("/accounts")' not in ui_api.text
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert viewer.status_code == 401
    assert valid.status_code == 200
    assert upstream.status_code == 200
    assert any(item["provider_id"] == "openai" for item in upstream.json())
    assert unsupported_discovery.status_code == 200
    assert unsupported_discovery.json()["failure_code"] == "unsupported_provider"


@pytest.mark.asyncio
async def test_standalone_ui_forwards_routing_writes_through_litellm() -> None:
    requests: list[httpx.Request] = []
    binding_id: Final = UUID("10000000-0000-0000-0000-000000000001")

    def litellm(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/account_pool/authorize":
            return httpx.Response(status_code=200, json={"ok": True})
        return httpx.Response(status_code=200, json={"status": "loaded", "version": 4})

    async with httpx.AsyncClient(transport=httpx.MockTransport(litellm)) as proxy_client:
        app: Final = create_app(settings=settings(), store=MemoryStateStore(), proxy_client=proxy_client)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://account-pool",
                headers={"authorization": "Bearer admin-secret"},
            ) as client:
                response: Final = await client.put(
                    "/ui-api/models/openai%2Fgpt-4o/routing-policy",
                    json={"expected_version": 3, "strategy": "lowest_latency"},
                )
                candidate: Final = await client.put(
                    f"/ui-api/models/openai%2Fgpt-4o/routing-candidates/{binding_id}",
                    json={"expected_version": 4, "weight": 5, "paused": False},
                )
                order: Final = await client.put(
                    "/ui-api/models/openai%2Fgpt-4o/routing-order",
                    json={"expected_version": 5, "binding_ids": [str(binding_id)]},
                )
                reset: Final = await client.request(
                    "DELETE",
                    f"/ui-api/models/openai%2Fgpt-4o/routing-candidates/{binding_id}",
                    json={"expected_version": 6},
                )

    assert (response.status_code, candidate.status_code, order.status_code, reset.status_code) == (200, 200, 200, 200)
    forwarded_requests: Final = tuple(request for request in requests if request.url.path != "/account_pool/authorize")
    policy_request, candidate_request, order_request, reset_request = forwarded_requests
    assert policy_request.method == "PUT"
    assert policy_request.url.raw_path == b"/account_pool/models/openai%2Fgpt-4o/routing-policy"
    assert candidate_request.url.raw_path == (
        f"/account_pool/models/openai%2Fgpt-4o/routing-candidates/{binding_id}".encode()
    )
    assert order_request.url.raw_path == b"/account_pool/models/openai%2Fgpt-4o/routing-order"
    assert reset_request.method == "DELETE"
    assert all(request.headers["authorization"] == "Bearer admin-secret" for request in forwarded_requests)
    assert json.loads(policy_request.content) == {"expected_version": 3, "strategy": "lowest_latency"}
    assert json.loads(order_request.content) == {"expected_version": 5, "binding_ids": [str(binding_id)]}


@pytest.mark.asyncio
async def test_standalone_ui_forwards_channel_writes_through_litellm() -> None:
    requests: list[httpx.Request] = []
    channel_id: Final = UUID("20000000-0000-0000-0000-000000000001")

    def litellm(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/account_pool/authorize":
            return httpx.Response(status_code=200, json={"ok": True})
        return httpx.Response(
            status_code=200,
            json={
                "status": "accepted",
                "operation_id": "30000000-0000-0000-0000-000000000001",
                "channel_id": str(channel_id),
                "operation_status": "applied",
                "requires_key": False,
                "failure": None,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(litellm)) as proxy_client:
        app: Final = create_app(settings=settings(), store=MemoryStateStore(), proxy_client=proxy_client)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://account-pool",
                headers={"authorization": "Bearer admin-secret"},
            ) as client:
                created: Final = await client.post(
                    "/ui-api/channels",
                    headers={"idempotency-key": "create-channel"},
                    json={"display_name": "Primary"},
                )
                updated: Final = await client.put(
                    f"/ui-api/channels/{channel_id}",
                    headers={"idempotency-key": "update-channel"},
                    json={"display_name": "Primary Updated"},
                )
                deleted: Final = await client.request(
                    "DELETE",
                    f"/ui-api/channels/{channel_id}",
                    headers={"idempotency-key": "delete-channel"},
                    json={"delete_mode": "delete_managed_deployment"},
                )

    assert (created.status_code, updated.status_code, deleted.status_code) == (200, 200, 200)
    forwarded_requests: Final = tuple(request for request in requests if request.url.path != "/account_pool/authorize")
    create_request, update_request, delete_request = forwarded_requests
    assert (create_request.method, update_request.method, delete_request.method) == ("POST", "PUT", "DELETE")
    assert create_request.url.path == "/account_pool/channels"
    assert update_request.url.path == f"/account_pool/channels/{channel_id}"
    assert delete_request.url.path == f"/account_pool/channels/{channel_id}"
    assert tuple(request.headers["idempotency-key"] for request in forwarded_requests) == (
        "create-channel",
        "update-channel",
        "delete-channel",
    )
    assert all(request.headers["authorization"] == "Bearer admin-secret" for request in forwarded_requests)
    assert json.loads(delete_request.content) == {"delete_mode": "delete_managed_deployment"}


@pytest.mark.asyncio
async def test_management_api_renders_pool_state() -> None:
    app: Final = create_app(settings=settings(), store=MemoryStateStore())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://account-pool",
            headers={"x-account-pool-token": "test-service-token"},
        ) as client:
            stats_response: Final = await client.get("/api/stats")
            models_response: Final = await client.get("/api/models")
            routes_response: Final = await client.get("/api/models/gpt-4o/routing-table")

    stats: Final = StatsView.model_validate_json(stats_response.content)
    models: Final = _MODEL_SUMMARIES_ADAPTER.validate_json(models_response.content)
    routes: Final = _ROUTE_ENTRIES_ADAPTER.validate_json(routes_response.content)
    assert stats == StatsView(models=2, accounts=3, available_accounts=3, inflight=0, max_concurrency=19)
    assert {item.model for item in models} == {"gpt-4o", "gpt-4o-mini"}
    assert all("api_key" not in json.dumps(item.model_dump()) for item in routes)


@pytest.mark.asyncio
async def test_internal_acquire_and_settle_share_health_event_recording() -> None:
    health_events: Final = FakeHealthEventRepository()
    app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        health_events=health_events,
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://account-pool",
            headers={"x-account-pool-token": "test-service-token"},
        ) as client:
            acquired_response: Final = await client.post(
                "/internal/acquire",
                json={"request_id": "internal-request", "model": "gpt-4o", "estimated_tokens": 10},
            )
            acquired: Final = AcquireSuccess.model_validate_json(acquired_response.content)
            settled: Final = await client.post(
                "/internal/settle",
                json={"lease_id": acquired.lease.lease_id, "success": True, "status_code": 200},
            )
            released: Final = await client.post(
                "/internal/release",
                json={"lease_id": acquired.lease.lease_id},
            )

    assert acquired_response.status_code == 200
    assert settled.status_code == 200
    assert released.status_code == 200
    assert health_events.request_activities[0].deployment_id == acquired.lease.deployment_id
    assert health_events.records[0].event.request_id == "internal-request"


@pytest.mark.asyncio
async def test_internal_gateway_event_ingestion_records_health_history() -> None:
    health_events: Final = FakeHealthEventRepository()
    app: Final = create_app(
        settings=settings(),
        store=MemoryStateStore(),
        health_events=health_events,
    )
    lease: Final = {
        "lease_id": "rust-lease",
        "request_id": "rust-request",
        "account_id": "primary-east",
        "deployment_id": "pool-gpt4o-primary",
        "public_model": "gpt-4o",
        "expires_at": 2.0,
        "absolute_expires_at": 3.0,
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://account-pool",
            headers={"x-account-pool-token": "test-service-token"},
        ) as client:
            activity: Final = await client.post("/internal/request-activity", json=lease)
            settlement: Final = await client.post(
                "/internal/settlement-event",
                json={
                    "lease": lease,
                    "settlement": {"lease_id": "rust-lease", "success": True, "status_code": 200},
                },
            )

    assert activity.status_code == 200
    assert settlement.status_code == 200
    assert health_events.request_activities[0].deployment_id == "pool-gpt4o-primary"
    assert health_events.records[0].event.request_id == "rust-request"


@pytest.mark.asyncio
async def test_internal_acquire_returns_structured_no_route_detail() -> None:
    app: Final = create_app(settings=settings(), store=MemoryStateStore())

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://account-pool",
            headers={"x-account-pool-token": "test-service-token"},
        ) as client:
            response: Final = await client.post(
                "/internal/acquire",
                json={"request_id": "missing-route", "model": "missing-model"},
            )

    payload: Final = _JSON_OBJECT_ADAPTER.validate_json(response.content)
    detail: Final = _JSON_OBJECT_ADAPTER.validate_python(payload["detail"])
    assert response.status_code == 503
    assert detail == {
        "status": "unavailable",
        "code": "no_available_route",
        "model": "missing-model",
        "reason_codes": ["model_not_configured"],
        "candidates": [],
        "retry_at": None,
        "reasons": ["model_not_configured"],
    }


@pytest.mark.asyncio
async def test_channel_crud_and_policy_updates_sync_litellm_without_persisting_api_key(tmp_path: Path) -> None:
    source: Final = Path(__file__).resolve().parents[1] / "config" / "accounts.demo.yaml"
    config_path: Final = tmp_path / "accounts.yaml"
    config_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    admin_requests: list[httpx.Request] = []

    def litellm(request: httpx.Request) -> httpx.Response:
        admin_requests.append(request)
        if request.url.path == "/health/liveliness":
            return httpx.Response(status_code=200, json={"status": "ok"})
        if request.url.path == "/model/info":
            return httpx.Response(status_code=200, json={"data": []})
        if request.url.path == "/model/new":
            payload: Final = _JSON_OBJECT_ADAPTER.validate_json(request.content)
            model_info: Final = _JSON_OBJECT_ADAPTER.validate_python(payload["model_info"])
            return httpx.Response(status_code=200, json={"model_id": model_info["id"]})
        if request.url.path.startswith("/model/") or request.url.path == "/model/delete":
            return httpx.Response(status_code=200, json={})
        return httpx.Response(status_code=404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(litellm)) as proxy_client:
        app: Final = create_app(
            settings=settings(config_path=config_path, admin_key="admin-secret"),
            store=MemoryStateStore(),
            proxy_client=proxy_client,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://account-pool",
                headers={"x-account-pool-token": "test-service-token"},
            ) as client:
                status_response: Final = await client.get("/api/litellm/status")
                create_response: Final = await client.post(
                    "/api/accounts",
                    json={
                        "id": "managed-channel",
                        "display_name": "Managed Channel",
                        "provider": "openai",
                        "base_url_display": "https://provider.example/v1",
                        "max_concurrency": 4,
                        "priority": 300,
                        "weight": 2,
                        "quotas": {"unit": "tokens", "total": 10000, "five_hour": 4000, "weekly": 8000},
                        "api_key": "provider-secret",
                        "deployments": [{"public_model": "managed-model", "provider_model": "openai/real-model"}],
                    },
                )
                accounts_response: Final = await client.get("/api/accounts")
                policy_response: Final = await client.put(
                    "/api/models/managed-model/policy",
                    json={"strategy": "priority"},
                )
                update_response: Final = await client.put(
                    "/api/accounts/managed-channel",
                    json={
                        "id": "managed-channel",
                        "display_name": "Managed Channel Updated",
                        "provider": "openai",
                        "base_url_display": "https://provider.example/v1",
                        "max_concurrency": 6,
                        "priority": 400,
                        "weight": 2,
                        "quotas": {"unit": "tokens", "total": 10000, "five_hour": 4000, "weekly": 8000},
                        "deployments": [
                            {
                                "public_model": "managed-model",
                                "provider_model": "openai/real-model-v2",
                                "litellm_model_id": _created_deployment_id(admin_requests),
                            }
                        ],
                    },
                )
                models_response: Final = await client.get("/api/models")
                delete_response: Final = await client.delete("/api/accounts/managed-channel")

    status: Final = LiteLLMStatus.model_validate_json(status_response.content)
    created: Final = ManagementResult.model_validate_json(create_response.content)
    accounts: Final = _ACCOUNT_VIEWS_ADAPTER.validate_json(accounts_response.content)
    policy: Final = ManagementResult.model_validate_json(policy_response.content)
    updated: Final = ManagementResult.model_validate_json(update_response.content)
    models: Final = _MODEL_SUMMARIES_ADAPTER.validate_json(models_response.content)
    deleted: Final = ManagementResult.model_validate_json(delete_response.content)
    persisted: Final = config_path.read_text(encoding="utf-8")
    new_request: Final = next(request for request in admin_requests if request.url.path == "/model/new")
    new_payload: Final = _JSON_OBJECT_ADAPTER.validate_json(new_request.content)
    new_params: Final = _JSON_OBJECT_ADAPTER.validate_python(new_payload["litellm_params"])
    patch_request: Final = next(request for request in admin_requests if request.method == "PATCH")
    patch_payload: Final = _JSON_OBJECT_ADAPTER.validate_json(patch_request.content)
    patch_params: Final = _JSON_OBJECT_ADAPTER.validate_python(patch_payload["litellm_params"])

    assert status.manageable
    assert created.ok and policy.ok and updated.ok and deleted.ok
    assert next(account for account in accounts if account.id == "managed-channel").deployments[0].managed_by_pool
    assert next(model for model in models if model.model == "managed-model").strategy == "priority"
    assert new_params["api_key"] == "provider-secret"
    assert "api_key" not in patch_params
    assert "provider-secret" not in persisted
    assert "/model/delete" in {request.url.path for request in admin_requests}


def _created_deployment_id(requests: list[httpx.Request]) -> str:
    request: Final = next(item for item in requests if item.url.path == "/model/new")
    payload: Final = _JSON_OBJECT_ADAPTER.validate_json(request.content)
    model_info: Final = _JSON_OBJECT_ADAPTER.validate_python(payload["model_info"])
    deployment_id: Final = model_info["id"]
    assert isinstance(deployment_id, str)
    return deployment_id
