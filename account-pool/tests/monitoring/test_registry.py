"""验证 Worker 监控状态机、停滞判定和 Prometheus 固定标签输出。"""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from account_pool.monitoring.models import WorkerName, WorkerStatus
from account_pool.monitoring.prometheus import render_prometheus_metrics
from account_pool.monitoring.registry import WorkerMonitorRegistry, WorkerRegistration

_NOW: Final = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _registry(clock: MutableClock) -> WorkerMonitorRegistry:
    return WorkerMonitorRegistry(
        (
            WorkerRegistration(WorkerName.LEASE_REAPER, True, 10),
            WorkerRegistration(WorkerName.ACTIVE_HEALTH_PROBE, False, 30),
        ),
        clock=clock,
    )


def test_registry_tracks_success_failure_recovery_and_stop() -> None:
    clock: Final = MutableClock(_NOW)
    registry: Final = _registry(clock)

    registry.started(WorkerName.LEASE_REAPER)
    registry.cycle_started(WorkerName.LEASE_REAPER)
    registry.failed(WorkerName.LEASE_REAPER, 0.25)
    clock.now = _NOW + timedelta(seconds=2)
    registry.cycle_started(WorkerName.LEASE_REAPER)
    registry.succeeded(WorkerName.LEASE_REAPER, 0.5)
    snapshot: Final = registry.snapshot()

    reaper: Final = next(item for item in snapshot.workers if item.worker == WorkerName.LEASE_REAPER)
    disabled: Final = next(item for item in snapshot.workers if item.worker == WorkerName.ACTIVE_HEALTH_PROBE)
    assert reaper.status == WorkerStatus.HEALTHY
    assert reaper.run_count == 2
    assert reaper.success_count == 1
    assert reaper.failure_count == 1
    assert reaper.consecutive_failures == 0
    assert reaper.last_duration_seconds == 0.5
    assert disabled.status == WorkerStatus.DISABLED

    registry.stopped(WorkerName.LEASE_REAPER)
    assert registry.snapshot().workers[1].status == WorkerStatus.STOPPED


def test_running_worker_becomes_stalled_after_two_expected_intervals() -> None:
    clock: Final = MutableClock(_NOW)
    registry: Final = _registry(clock)
    registry.started(WorkerName.LEASE_REAPER)
    registry.cycle_started(WorkerName.LEASE_REAPER)

    clock.now = _NOW + timedelta(seconds=21)
    state: Final = next(item for item in registry.snapshot().workers if item.worker == WorkerName.LEASE_REAPER)

    assert state.running is True
    assert state.status == WorkerStatus.STALLED


def test_prometheus_output_uses_only_fixed_worker_labels() -> None:
    clock: Final = MutableClock(_NOW)
    registry: Final = _registry(clock)
    registry.started(WorkerName.LEASE_REAPER)
    registry.cycle_started(WorkerName.LEASE_REAPER)
    registry.succeeded(WorkerName.LEASE_REAPER, 0.125)

    rendered: Final = render_prometheus_metrics(registry.snapshot())

    assert "# TYPE account_pool_worker_runs_total counter" in rendered
    assert 'account_pool_worker_up{worker="lease_reaper"} 1' in rendered
    assert 'account_pool_worker_enabled{worker="active_health_probe"} 0' in rendered
    assert 'account_pool_worker_expected_interval_seconds{worker="lease_reaper"} 10.000000' in rendered
    assert (
        f'account_pool_worker_process_started_timestamp_seconds{{worker="lease_reaper"}} {_NOW.timestamp():.6f}'
        in rendered
    )
    assert 'account_pool_worker_last_cycle_duration_seconds{worker="lease_reaper"} 0.125000' in rendered
    assert "api_key" not in rendered
    assert "authorization" not in rendered.casefold()
    assert "https://" not in rendered


@pytest.mark.parametrize("interval", (0, -1))
def test_registry_rejects_non_positive_intervals(interval: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        WorkerMonitorRegistry((WorkerRegistration(WorkerName.LEASE_REAPER, True, interval),))
