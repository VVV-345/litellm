"""验证健康事实只保留标准化结果，并为重复结算生成稳定事件标识。"""

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import pytest
from account_pool.eligibility import EligibilityScope
from account_pool.health.models import (
    ActiveProbeHealthDetails,
    HealthObservationOutcome,
    HealthProbeResult,
    HealthProbeStatus,
    HealthProbeTrigger,
    build_active_probe_record,
    build_passive_health_record,
    equivalent_health_records,
)
from account_pool.health.settlement import classify_settlement
from account_pool.models import AccountConfig, DeploymentConfig, Lease, SettleRequest
from pydantic import ValidationError

_NOW: Final = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)
_CHANNEL_ID: Final = UUID("81000000-0000-0000-0000-000000000001")


def _account() -> AccountConfig:
    return AccountConfig(
        id="channel-a",
        channel_id=_CHANNEL_ID,
        display_name="Channel A",
        provider="test",
        base_url_display="https://provider.example/v1",
        max_concurrency=2,
        deployments=(DeploymentConfig(public_model="model-a", litellm_model_id="deployment-a"),),
    )


def _lease() -> Lease:
    return Lease(
        lease_id="lease-a",
        request_id="request-a",
        account_id="channel-a",
        deployment_id="deployment-a",
        public_model="model-a",
        expires_at=(_NOW + timedelta(minutes=1)).timestamp(),
    )


def test_passive_record_is_deterministic_and_contains_no_channel_configuration() -> None:
    request: Final = SettleRequest(
        lease_id="lease-a",
        success=False,
        status_code=429,
        provider_error_code="insufficient_quota",
        retry_after_seconds=30,
        latency_ms=125,
    )

    first: Final = build_passive_health_record(
        account=_account(),
        lease=_lease(),
        request=request,
        occurred_at=_NOW,
        scope=EligibilityScope.DEPLOYMENT,
    )
    repeated: Final = build_passive_health_record(
        account=_account(),
        lease=_lease(),
        request=request,
        occurred_at=_NOW,
        scope=EligibilityScope.DEPLOYMENT,
    )
    serialized: Final = first.model_dump_json()

    assert first == repeated
    assert first.health.outcome == HealthObservationOutcome.FAILED
    assert first.event.reason_code == "quota_signal_unscoped"
    assert first.health.retry_at == _NOW + timedelta(seconds=30)
    assert "provider.example" not in serialized
    assert "insufficient_quota" not in serialized


def test_passive_record_idempotency_ignores_only_observation_time_fields() -> None:
    request: Final = SettleRequest(
        lease_id="lease-a",
        success=False,
        status_code=429,
        retry_after_seconds=30,
    )
    first: Final = build_passive_health_record(
        account=_account(),
        lease=_lease(),
        request=request,
        occurred_at=_NOW,
        scope=EligibilityScope.DEPLOYMENT,
    )
    repeated: Final = build_passive_health_record(
        account=_account(),
        lease=_lease(),
        request=request,
        occurred_at=_NOW + timedelta(seconds=1),
        scope=EligibilityScope.DEPLOYMENT,
    )
    changed_scope: Final = repeated.model_copy(
        update={"health": repeated.health.model_copy(update={"scope": EligibilityScope.CHANNEL})}
    )

    assert equivalent_health_records(first, repeated)
    assert not equivalent_health_records(first, changed_scope)


def test_active_probe_record_uses_probe_id_and_rejects_unregistered_details() -> None:
    result: Final = HealthProbeResult(
        probe_id=UUID("81000000-0000-0000-0000-000000000002"),
        status=HealthProbeStatus.SUCCEEDED,
        trigger=HealthProbeTrigger.IDLE,
        channel_id=_CHANNEL_ID,
        account_id="channel-a",
        deployment_id="deployment-a",
        public_model="model-a",
        response_status_code=200,
        latency_ms=42,
    )
    transition: Final = classify_settlement(SettleRequest(lease_id="probe", success=True), _NOW.timestamp())
    record: Final = build_active_probe_record(result=result, transition=transition, occurred_at=_NOW)

    assert record is not None
    assert record.event.event_id == result.probe_id
    assert record.health.probe_trigger == HealthProbeTrigger.IDLE
    with pytest.raises(ValidationError):
        ActiveProbeHealthDetails.model_validate(
            {**record.event.safe_details.model_dump(), "raw_response": "must-not-be-stored"}
        )
