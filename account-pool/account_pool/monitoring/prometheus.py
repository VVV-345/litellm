"""把固定标签的 Worker 运行快照渲染为 Prometheus exposition 文本。"""

from typing import Final

from pydantic import AwareDatetime

from account_pool.monitoring.models import WorkerState, WorkerStateList, WorkerStatus

PROMETHEUS_CONTENT_TYPE: Final = "text/plain; version=0.0.4; charset=utf-8"


def render_prometheus_metrics(snapshot: WorkerStateList) -> str:
    states: Final = snapshot.workers
    families: Final = (
        (
            "account_pool_worker_enabled",
            "Whether the worker is configured to run.",
            "gauge",
            tuple(_enabled(item) for item in states),
        ),
        (
            "account_pool_worker_up",
            "Whether the worker is running without a stale or failed cycle.",
            "gauge",
            tuple(_up(item) for item in states),
        ),
        (
            "account_pool_worker_expected_interval_seconds",
            "Configured interval between worker cycles.",
            "gauge",
            tuple(item.expected_interval_seconds for item in states),
        ),
        (
            "account_pool_worker_process_started_timestamp_seconds",
            "Unix timestamp when the worker process started.",
            "gauge",
            tuple(_timestamp(item.process_started_at) for item in states),
        ),
        (
            "account_pool_worker_runs_total",
            "Worker cycles started in this process.",
            "counter",
            tuple(item.run_count for item in states),
        ),
        (
            "account_pool_worker_successes_total",
            "Worker cycles completed successfully in this process.",
            "counter",
            tuple(item.success_count for item in states),
        ),
        (
            "account_pool_worker_failures_total",
            "Worker cycles failed in this process.",
            "counter",
            tuple(item.failure_count for item in states),
        ),
        (
            "account_pool_worker_consecutive_failures",
            "Current consecutive worker cycle failures.",
            "gauge",
            tuple(item.consecutive_failures for item in states),
        ),
        (
            "account_pool_worker_last_success_timestamp_seconds",
            "Unix timestamp of the last successful worker cycle.",
            "gauge",
            tuple(_timestamp(item.last_success_at) for item in states),
        ),
        (
            "account_pool_worker_last_failure_timestamp_seconds",
            "Unix timestamp of the last failed worker cycle.",
            "gauge",
            tuple(_timestamp(item.last_failure_at) for item in states),
        ),
        (
            "account_pool_worker_last_cycle_duration_seconds",
            "Duration of the last completed worker cycle.",
            "gauge",
            tuple(item.last_duration_seconds or 0 for item in states),
        ),
    )
    lines: Final = tuple(
        line
        for name, description, metric_type, values in families
        for line in _family(name, description, metric_type, tuple(zip(states, values, strict=True)))
    )
    return "\n".join((*lines, ""))


def _family(
    name: str,
    description: str,
    metric_type: str,
    values: tuple[tuple[WorkerState, int | float], ...],
) -> tuple[str, ...]:
    samples: Final = tuple(
        f'{name}{{worker="{state.worker.value}"}} {_number(value)}'
        for state, value in values
    )
    return (f"# HELP {name} {description}", f"# TYPE {name} {metric_type}", *samples)


def _enabled(state: WorkerState) -> int:
    return int(state.enabled)


def _up(state: WorkerState) -> int:
    return int(state.running and state.status in (WorkerStatus.STARTING, WorkerStatus.HEALTHY))


def _timestamp(value: AwareDatetime | None) -> float:
    return 0 if value is None else value.timestamp()


def _number(value: float) -> str:
    return str(value) if isinstance(value, int) else format(value, ".6f")
