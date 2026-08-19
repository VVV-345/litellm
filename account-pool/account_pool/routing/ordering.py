"""使用纯函数按模型策略排列合格与被排除候选，并生成排序原因。"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from typing import Final

from account_pool.models import Strategy
from account_pool.routing.models import RoutingCandidate, RoutingOrder


def order_candidates(
    candidates: tuple[RoutingCandidate, ...],
    strategy: Strategy,
    model: str,
    request_id: str | None = None,
    sequence: int | None = None,
) -> tuple[RoutingOrder, ...]:
    ordered: Final = tuple(sorted(candidates, key=_sort_key(strategy, model, request_id)))
    if strategy == Strategy.WEIGHTED_ROUND_ROBIN:
        weighted: Final = _weighted_order(ordered, sequence or 1)
        return tuple(_result(candidate, strategy) for candidate in weighted)
    return tuple(_result(candidate, strategy) for candidate in ordered)


def _sort_key(
    strategy: Strategy,
    model: str,
    request_id: str | None,
) -> Callable[[RoutingCandidate], tuple[object, ...]]:
    def key(candidate: RoutingCandidate) -> tuple[object, ...]:
        availability: Final = 0 if candidate.available else 1
        stable: Final = candidate.stable_id()
        priority: Final = -candidate.priority
        if strategy == Strategy.PRIORITY:
            return (availability, _missing(candidate.manual_order), candidate.manual_order or 0, priority, stable)
        if strategy == Strategy.RANDOM:
            return (availability, _random_rank(model, request_id, stable), priority, stable)
        if strategy == Strategy.LOWEST_LATENCY:
            return (availability, _missing(candidate.latency_ewma_ms), candidate.latency_ewma_ms or 0, priority, stable)
        if strategy == Strategy.HIGHEST_REMAINING_QUOTA:
            return (
                availability,
                _missing(candidate.remaining_quota_ratio),
                -(candidate.remaining_quota_ratio or 0),
                _inflight_ratio(candidate),
                priority,
                stable,
            )
        if strategy == Strategy.LOWEST_EFFECTIVE_COST:
            return (availability, _missing(candidate.effective_cost), candidate.effective_cost or 0, priority, stable)
        if strategy == Strategy.LEAST_INFLIGHT:
            return (availability, _inflight_ratio(candidate), priority, stable)
        if strategy == Strategy.QUOTA_AWARE_LEAST_INFLIGHT:
            return (
                availability,
                _inflight_ratio(candidate),
                _missing(candidate.remaining_quota_ratio),
                -(candidate.remaining_quota_ratio or 0),
                priority,
                stable,
            )
        return (availability, priority, stable)

    return key


def _weighted_order(candidates: tuple[RoutingCandidate, ...], sequence: int) -> tuple[RoutingCandidate, ...]:
    available: Final = tuple(candidate for candidate in candidates if candidate.available)
    unavailable: Final = tuple(candidate for candidate in candidates if not candidate.available)
    if not available:
        return unavailable
    wheel: Final = tuple(candidate for candidate in available for _ in range(candidate.weight))
    pivot: Final = (sequence - 1) % len(wheel)
    rotated: Final = wheel[pivot:] + wheel[:pivot]
    unique: Final = tuple(
        candidate
        for index, candidate in enumerate(rotated)
        if candidate.stable_id() not in frozenset(item.stable_id() for item in rotated[:index])
    )
    return unique + unavailable


def _result(candidate: RoutingCandidate, strategy: Strategy) -> RoutingOrder:
    evidence: Final = {
        Strategy.PRIORITY: ("manual_order" if candidate.manual_order is not None else "channel_priority",),
        Strategy.RANDOM: ("request_random",),
        Strategy.LOWEST_LATENCY: (
            "latency_ewma" if candidate.latency_ewma_ms is not None else "latency_unknown",
        ),
        Strategy.HIGHEST_REMAINING_QUOTA: (
            "remaining_quota_ratio" if candidate.remaining_quota_ratio is not None else "quota_unknown",
        ),
        Strategy.LOWEST_EFFECTIVE_COST: (
            "effective_cost" if candidate.effective_cost is not None else "cost_unknown",
        ),
        Strategy.LEAST_INFLIGHT: ("inflight_ratio",),
        Strategy.WEIGHTED_ROUND_ROBIN: ("configured_weight",),
        Strategy.QUOTA_AWARE_LEAST_INFLIGHT: ("inflight_ratio", "remaining_quota_ratio"),
    }
    return RoutingOrder(
        candidate=candidate,
        reason_codes=evidence[strategy],
        dynamic=strategy in (Strategy.RANDOM, Strategy.WEIGHTED_ROUND_ROBIN),
    )


def _random_rank(model: str, request_id: str | None, stable_id: str) -> bytes:
    seed: Final = request_id or "preview"
    return sha256(f"{model}\x00{seed}\x00{stable_id}".encode()).digest()


def _inflight_ratio(candidate: RoutingCandidate) -> float:
    return candidate.inflight / candidate.max_concurrency


def _missing(value: object | None) -> int:
    return 1 if value is None else 0
