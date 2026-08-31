"""验证正式调度策略的稳定排序、缺失证据和硬排除优先级。"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Final, cast

from account_pool.models import Strategy
from account_pool.routing import RoutingCandidate, RoutingOrder, order_candidates

_CONFORMANCE_FIXTURE: Final = Path(__file__).resolve().parents[2] / "testdata" / "routing_conformance.json"


def candidate(
    account_id: str,
    *,
    available: bool = True,
    priority: int = 0,
    weight: int = 1,
    manual_order: int | None = None,
    inflight: int = 0,
    quota: float | None = None,
    latency: float | None = None,
    cost: str | None = None,
) -> RoutingCandidate:
    return RoutingCandidate(
        account_id=account_id,
        deployment_id=f"deployment-{account_id}",
        available=available,
        priority=priority,
        weight=weight,
        manual_order=manual_order,
        inflight=inflight,
        max_concurrency=10,
        remaining_quota_ratio=quota,
        latency_ewma_ms=latency,
        effective_cost=None if cost is None else Decimal(cost),
        cost_currency=None if cost is None else "USD",
        cost_unit=None if cost is None else "million_tokens",
    )


def ids(ordered: tuple[RoutingOrder, ...]) -> tuple[str, ...]:
    return tuple(item.candidate.account_id for item in ordered)


def test_priority_prefers_channel_priority_before_saved_drag_order() -> None:
    ordered: Final = order_candidates(
        candidates=(
            candidate("high", priority=400),
            candidate("manual-second", priority=100, manual_order=1),
            candidate("manual-first", priority=100, manual_order=0),
        ),
        strategy=Strategy.PRIORITY,
        model="model-a",
    )

    assert ids(ordered) == ("high", "manual-first", "manual-second")
    assert ordered[0].reason_codes == ("channel_priority",)


def test_random_is_stable_for_request_and_changes_between_requests() -> None:
    candidates: Final = tuple(candidate(f"account-{index}") for index in range(6))

    first: Final = order_candidates(candidates, Strategy.RANDOM, "model-a", request_id="request-a")
    duplicate: Final = order_candidates(candidates, Strategy.RANDOM, "model-a", request_id="request-a")
    second: Final = order_candidates(candidates, Strategy.RANDOM, "model-a", request_id="request-b")

    assert ids(first) == ids(duplicate)
    assert ids(first) != ids(second)
    assert all(item.dynamic for item in first)


def test_latency_and_cost_put_unknown_evidence_after_known_values() -> None:
    candidates: Final = (
        candidate("unknown"),
        candidate("slow-expensive", latency=300, cost="4"),
        candidate("fast-cheap", latency=50, cost="1"),
    )

    by_latency: Final = order_candidates(candidates, Strategy.LOWEST_LATENCY, "model-a")
    by_cost: Final = order_candidates(candidates, Strategy.LOWEST_EFFECTIVE_COST, "model-a")

    assert ids(by_latency) == ("fast-cheap", "slow-expensive", "unknown")
    assert ids(by_cost) == ("fast-cheap", "slow-expensive", "unknown")
    assert by_latency[-1].reason_codes == ("latency_unknown",)
    assert by_cost[-1].reason_codes == ("cost_unknown",)


def test_cost_does_not_compare_different_currency_or_units() -> None:
    usd: Final = candidate("usd", cost="2")
    credits: Final = usd.model_copy(
        update={
            "account_id": "credits",
            "deployment_id": "deployment-credits",
            "effective_cost": Decimal("1"),
            "cost_currency": "CREDITS",
        }
    )

    ordered: Final = order_candidates((credits, usd), Strategy.LOWEST_EFFECTIVE_COST, "model-a")

    assert all(item.reason_codes == ("cost_basis_conflict",) for item in ordered)


def test_remaining_quota_prefers_largest_tightest_window_ratio() -> None:
    ordered: Final = order_candidates(
        candidates=(
            candidate("unknown"),
            candidate("low", quota=0.1),
            candidate("high", quota=0.8),
        ),
        strategy=Strategy.HIGHEST_REMAINING_QUOTA,
        model="model-a",
    )

    assert ids(ordered) == ("high", "low", "unknown")


def test_unavailable_candidate_stays_last_for_every_strategy() -> None:
    strategies: Final = tuple(Strategy)
    candidates: Final = (
        candidate("unavailable", available=False, priority=400, latency=1, cost="0", quota=1),
        candidate("available", priority=100, latency=100, cost="10", quota=0.1),
    )

    results: Final = tuple(
        order_candidates(candidates, strategy, "model-a", request_id="request") for strategy in strategies
    )

    assert all(ids(result)[-1] == "unavailable" for result in results)


def test_available_subscription_is_first_for_lowest_cost_but_exhausted_subscription_is_last() -> None:
    included: Final = candidate("subscription").model_copy(
        update={"effective_cost": Decimal("0"), "cost_currency": None, "cost_unit": None, "cost_included": True}
    )
    metered: Final = candidate("metered", cost="2")
    exhausted: Final = included.model_copy(update={"account_id": "exhausted", "available": False})

    ordered: Final = order_candidates(
        (metered, exhausted, included),
        Strategy.LOWEST_EFFECTIVE_COST,
        "model-a",
    )

    assert ids(ordered) == ("subscription", "metered", "exhausted")
    assert ordered[0].reason_codes == ("subscription_included",)


def test_weighted_round_robin_preview_uses_sequence_without_dropping_fallbacks() -> None:
    candidates: Final = (candidate("heavy", weight=3), candidate("light", weight=1))

    first: Final = order_candidates(candidates, Strategy.WEIGHTED_ROUND_ROBIN, "model-a", sequence=1)
    fourth: Final = order_candidates(candidates, Strategy.WEIGHTED_ROUND_ROBIN, "model-a", sequence=4)

    assert ids(first) == ("heavy", "light")
    assert ids(fourth) == ("light", "heavy")


def test_matches_the_shared_rust_routing_conformance_fixture() -> None:
    fixture: Final = cast(dict[str, object], json.loads(_CONFORMANCE_FIXTURE.read_text(encoding="utf-8")))
    raw_candidates: Final = cast(list[dict[str, object]], fixture["candidates"])
    candidates: Final = tuple(_fixture_candidate(raw) for raw in raw_candidates)
    expected: Final = cast(dict[str, list[str]], fixture["expected"])
    model: Final = cast(str, fixture["model"])
    request_id: Final = cast(str, fixture["request_id"])
    sequence: Final = cast(int, fixture["sequence"])

    for strategy in Strategy:
        ordered: Final = order_candidates(
            candidates,
            strategy,
            model,
            request_id=request_id,
            sequence=sequence,
        )
        assert list(ids(ordered)) == expected[strategy.value]


def _fixture_candidate(raw: dict[str, object]) -> RoutingCandidate:
    effective_cost: Final = raw["effective_cost"]
    return RoutingCandidate(
        account_id=cast(str, raw["account_id"]),
        deployment_id=cast(str, raw["deployment_id"]),
        billing_route_id=cast(str | None, raw["billing_route_id"]),
        available=cast(bool, raw["available"]),
        priority=cast(int, raw["priority"]),
        weight=cast(int, raw["weight"]),
        manual_order=cast(int | None, raw["manual_order"]),
        inflight=cast(int, raw["inflight"]),
        max_concurrency=cast(int, raw["max_concurrency"]),
        remaining_quota_ratio=cast(float | None, raw["remaining_quota_ratio"]),
        latency_ewma_ms=cast(float | None, raw["latency_ewma_ms"]),
        effective_cost=None if effective_cost is None else Decimal(cast(str, effective_cost)),
        cost_currency=cast(str | None, raw["cost_currency"]),
        cost_unit=cast(str | None, raw["cost_unit"]),
        cost_partial=cast(bool, raw["cost_partial"]),
        cost_included=cast(bool, raw["cost_included"]),
    )
