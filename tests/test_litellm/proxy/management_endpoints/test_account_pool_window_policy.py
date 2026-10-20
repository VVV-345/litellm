"""验证卡片分窗口保留比例和协议限制在管理契约与实际候选筛选中一致。"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from account_pool.policies import RoutingPolicy as ManagerPolicy
from pydantic import ValidationError
from starlette.datastructures import Headers

from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPolicy,
    RoutingPolicy,
    TransportPolicy,
)
from litellm.proxy.management_endpoints.account_pool_native_routing import (
    RoutingSnapshot,
    available,
    unavailable_reason,
)
from litellm.proxy.management_endpoints.account_pool_reconciler import QuotaSnapshot
from litellm.proxy.management_endpoints.account_pool_routing import protocol_policy

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def quota(five, week):
    return QuotaSnapshot.model_validate({"observed_at": NOW, "windows": [
        {"name": "primary", "window_minutes": 300, "remaining_percent": five, "resets_at": NOW + timedelta(hours=2)},
        {"name": "secondary", "window_minutes": 10080, "remaining_percent": week, "resets_at": NOW + timedelta(days=4)},
    ]}).routing_quota()


@pytest.mark.parametrize("five,week,expected", [(9,50,False),(50,19,False),(11,21,True),(10,21,False),(11,20,False)])
def test_independent_reserves_use_window_duration(five, week, expected):
    snapshot = RoutingSnapshot(quota=quota(five,week), quota_reserve_percent=0, quota_reserve_5h_percent=10, quota_reserve_weekly_percent=20)
    assert available(snapshot, "shared", NOW) is expected


def test_weekly_override_does_not_apply_to_other_window():
    value = QuotaSnapshot.model_validate({"observed_at": NOW, "windows": [
        {"name": "daily", "window_minutes": 1440, "remaining_percent": 15, "resets_at": NOW + timedelta(hours=2)},
    ]}).routing_quota()
    assert available(RoutingSnapshot(quota=value, quota_reserve_percent=5, quota_reserve_weekly_percent=20), "shared", NOW)


@pytest.mark.parametrize("model", [ManagerPolicy, RoutingPolicy])
def test_old_policy_inherits_and_new_reserves_roundtrip(model):
    old = model(quota_reserve_percent=7)
    assert old.quota_reserve_5h_percent is None and old.quota_reserve_weekly_percent is None
    value = model(quota_reserve_5h_percent=0, quota_reserve_weekly_percent=100)
    assert model.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        model(quota_reserve_weekly_percent=101)


def test_gateway_window_policy_matches_native():
    from litellm.proxy.management_endpoints.account_pool_gateway_contracts import Candidate
    from litellm.proxy.management_endpoints.account_pool_routing import quota_available
    policy = AccountPolicy(routing=RoutingPolicy(quota_reserve_5h_percent=10, quota_reserve_weekly_percent=20))
    account = Candidate(id=uuid4(), channel="cliproxyapi", supplier="openai_codex", environment_version=1, policy_version=1, enabled_models=("shared",), api_base="http://example.test/v1", api_key="unused", concurrency_limit=1, policy=policy, remaining_percent=15, quota_observed_at=datetime.now(timezone.utc), quota_windows=({"window_minutes":10080,"remaining_percent":15},))
    assert not quota_available(account, policy, "shared")


def test_selected_protocols_are_enforced_before_forwarding():
    policy = AccountPolicy(transport=TransportPolicy(supported_protocols=("responses",), passthrough_enabled=True))
    rejection = protocol_policy(policy, "/v1/chat/completions", Headers(), False)
    assert rejection is not None and rejection.code == "account_pool_protocol_disabled"
    assert protocol_policy(policy, "/v1/responses", Headers(), False) is None
    assert protocol_policy(AccountPolicy(), "/v1/chat/completions", Headers(), False) is None


@pytest.mark.parametrize("five,week,expected", [(1,21,True), (0,21,False), (50,100,True)])
def test_explicit_zero_override_does_not_inherit_general_reserve(five, week, expected):
    assert available(RoutingSnapshot(quota=quota(five,week), quota_reserve_percent=50, quota_reserve_5h_percent=0, quota_reserve_weekly_percent=20), "shared", NOW) is expected


def test_stale_snapshot_stays_usable_within_the_grace_window() -> None:
    stale = quota(50, 50).model_copy(update={"observed_at": NOW - timedelta(seconds=301)})
    expired = quota(50, 50).model_copy(update={"observed_at": NOW - timedelta(seconds=901)})

    assert available(RoutingSnapshot(quota=stale, quota_reserve_5h_percent=10), "shared", NOW)
    assert not available(RoutingSnapshot(quota=expired, quota_reserve_5h_percent=10), "shared", NOW)


def test_window_past_its_reset_does_not_invalidate_a_fresh_snapshot() -> None:
    value = QuotaSnapshot.model_validate({"observed_at": NOW, "windows": [
        {"name": "primary", "window_minutes": 300, "remaining_percent": 90, "resets_at": NOW - timedelta(minutes=1)},
        {"name": "secondary", "window_minutes": 10080, "remaining_percent": 90, "resets_at": NOW + timedelta(days=4)},
    ]}).routing_quota()

    assert available(RoutingSnapshot(quota=value, quota_reserve_percent=10), "shared", NOW)


def test_missing_observation_is_unavailable_only_when_reserve_is_positive() -> None:
    value = QuotaSnapshot.model_validate({"windows": [
        {"name": "primary", "window_minutes": 300, "remaining_percent": 90, "resets_at": NOW + timedelta(hours=1)},
    ]}).routing_quota()

    assert not available(RoutingSnapshot(quota=value, quota_reserve_percent=10), "shared", NOW)
    assert available(RoutingSnapshot(quota=value, quota_reserve_percent=0), "shared", NOW)

@pytest.mark.parametrize(
    ("snapshot", "expected"),
    (
        (RoutingSnapshot(quota_reserve_percent=10, model_cooldowns={"shared": NOW + timedelta(seconds=30)}), "model_cooldown"),
        (
            RoutingSnapshot(quota_reserve_percent=10, subscription_active_until=NOW - timedelta(seconds=1)),
            "subscription_expired",
        ),
        (RoutingSnapshot(quota=quota(5, 90), quota_reserve_percent=10), "quota_exhausted"),
        (
            RoutingSnapshot(
                quota=quota(90, 90).model_copy(update={"observed_at": NOW - timedelta(seconds=901)}),
                quota_reserve_percent=10,
            ),
            "quota_snapshot_expired",
        ),
        (
            RoutingSnapshot(
                quota=quota(90, 90).model_copy(update={"observed_at": None}),
                quota_reserve_percent=10,
            ),
            "quota_snapshot_missing",
        ),
        (RoutingSnapshot(quota=quota(90, 90), quota_reserve_percent=10), None),
    ),
)
def test_unavailable_reason_names_the_blocking_gate(snapshot: RoutingSnapshot, expected: str | None) -> None:
    assert unavailable_reason(snapshot, "shared", NOW) == expected
