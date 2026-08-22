"""定义后台 Worker 的固定名称、健康状态和脱敏运行快照。"""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from account_pool.models import FrozenModel


class WorkerName(StrEnum):
    LEASE_REAPER = "lease_reaper"
    CHANNEL_RECONCILER = "channel_reconciler"
    PARSER_EXPORT_RETRY = "parser_export_retry"
    PUBLIC_METADATA = "public_metadata"
    ACTIVE_HEALTH_PROBE = "active_health_probe"
    EVENT_RETENTION = "event_retention"


class WorkerStatus(StrEnum):
    DISABLED = "disabled"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALLED = "stalled"
    STOPPED = "stopped"


class WorkerState(FrozenModel):
    worker: WorkerName
    enabled: bool
    running: bool
    status: WorkerStatus
    expected_interval_seconds: float = Field(gt=0)
    run_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    consecutive_failures: int = Field(ge=0)
    process_started_at: AwareDatetime | None = None
    last_cycle_started_at: AwareDatetime | None = None
    last_success_at: AwareDatetime | None = None
    last_failure_at: AwareDatetime | None = None
    last_duration_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if not self.enabled and (self.running or self.status != WorkerStatus.DISABLED):
            raise ValueError("disabled workers cannot be running")
        if self.running and self.status in (WorkerStatus.DISABLED, WorkerStatus.STOPPED):
            raise ValueError("running workers require an active status")
        if not self.running and self.status in (WorkerStatus.HEALTHY, WorkerStatus.DEGRADED, WorkerStatus.STALLED):
            raise ValueError("inactive workers cannot report a running status")
        return self


class WorkerStateList(FrozenModel):
    workers: tuple[WorkerState, ...]
    observed_at: AwareDatetime
