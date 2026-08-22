"""验证资格限制变化事件的脱敏构造和状态存储接入。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from account_pool.eligibility import EligibilityScope, EligibilitySource, EligibilityState, activate_exclusion
from account_pool.models import (
    AccountConfig,
    DeploymentConfig,
    QuotaWindowConfig,
    ReserveSuccess,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
)
from account_pool.operational.models import OperationalEventRecord, OperationalEventType
from account_pool.operational.repository import OperationalWriteResult, OperationalWriteSuccess
from account_pool.operational.restrictions import (
    RestrictionEventRecorder,
    RestrictionEventStateStore,
    build_restriction_transition_records,
)
from account_pool.store import MemoryStateStore

_NOW: Final = datetime(2026, 8, 22, 13, 0, tzinfo=UTC)
_CHANNEL_ID: Final = UUID("95000000-0000-0000-0000-000000000001")


class RecordingRepository:
    def __init__(self) -> None:
        self.records: tuple[OperationalEventRecord, ...] = ()

    async def append(self, record: OperationalEventRecord) -> OperationalWriteResult:
        self.records = (*self.records, record)
        return OperationalWriteSuccess(status="created", record=record)


def _account() -> AccountConfig:
    return AccountConfig(
        id="channel-account",
        channel_id=_CHANNEL_ID,
        display_name="Channel",
        provider="openai_compatible",
        base_url_display="https://example.test",
        max_concurrency=2,
        quota_windows=(
            QuotaWindowConfig(
                window_id="five-hour",
                scope=RuntimeQuotaScope.CHANNEL,
                kind=RuntimeQuotaKind.TOKENS,
                window_type=RuntimeQuotaWindowType.ROLLING,
                duration_seconds=18_000,
                limit=Decimal("1"),
                remaining=Decimal("1"),
                observed_at=_NOW.timestamp(),
                source="provider-api",
                reason_code="five_hour_exhausted",
            ),
        ),
        deployments=(DeploymentConfig(public_model="model-a", litellm_model_id="deployment-a"),),
    )


async def test_reservation_activates_and_release_clears_quota_restriction() -> None:
    repository: Final = RecordingRepository()
    restriction_recorder: Final = RestrictionEventRecorder(repository, clock=lambda: _NOW)
    store: Final = RestrictionEventStateStore(MemoryStateStore(), restriction_recorder)
    account: Final = _account()
    await store.configure((account,))

    reserved: Final = await store.reserve(
        account=account,
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-1",
        estimated_tokens=1,
        ttl_seconds=60,
    )
    assert isinstance(reserved, ReserveSuccess)
    assert await store.release(reserved.lease.lease_id)

    restrictions: Final = tuple(
        record for record in repository.records if record.operational.source.value == "eligibility_transition"
    )
    assert tuple(record.event.event_type for record in restrictions) == (
        OperationalEventType.ELIGIBILITY_RESTRICTION_ACTIVATED,
        OperationalEventType.ELIGIBILITY_RESTRICTION_CLEARED,
    )
    assert restrictions[0].operational.operation_id == restrictions[1].operational.operation_id
    assert restrictions[0].event.reason_code == "five_hour_exhausted"
    assert restrictions[0].event.safe_details.model_dump(mode="json") == {
        "kind": "eligibility_restriction_activated",
        "restriction_id": str(restrictions[0].operational.operation_id),
        "scope": "channel",
        "source": "restriction",
        "state": "active",
        "billing_route_id": None,
        "starts_at": _NOW.timestamp(),
        "retry_at": None,
    }


def test_builder_records_updates_and_ignores_health_exclusions() -> None:
    account: Final = _account()
    previous: Final = activate_exclusion(
        scope=EligibilityScope.MODEL,
        source=EligibilitySource.RESTRICTION,
        account_id=account.id,
        model="model-a",
        deployment_id=None,
        billing_route_id=None,
        reason_code="monthly_exhausted",
        starts_at=_NOW.timestamp(),
        retry_at=_NOW.timestamp() + 60,
    )
    current: Final = previous.model_copy(
        update={"state": EligibilityState.HALF_OPEN, "retry_at": _NOW.timestamp() + 120}
    )
    health: Final = previous.model_copy(update={"source": EligibilitySource.HEALTH})

    records: Final = build_restriction_transition_records(
        accounts=(account,),
        before=(previous, health),
        after=(current,),
        occurred_at=_NOW,
    )

    assert len(records) == 1
    assert records[0].event.event_type == OperationalEventType.ELIGIBILITY_RESTRICTION_UPDATED
    assert records[0].event.safe_details.model_dump(mode="json") == {
        "kind": "eligibility_restriction_updated",
        "restriction_id": str(records[0].operational.operation_id),
        "scope": "model",
        "source": "restriction",
        "previous_state": "active",
        "state": "half_open",
        "billing_route_id": None,
        "starts_at": _NOW.timestamp(),
        "previous_retry_at": _NOW.timestamp() + 60,
        "retry_at": _NOW.timestamp() + 120,
    }
    assert "api_key" not in records[0].model_dump_json()
