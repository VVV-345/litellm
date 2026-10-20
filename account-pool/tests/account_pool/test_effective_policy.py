"""验证全局设置合并、独立卡片保真和快照有效期下限的一致性。"""

from __future__ import annotations

from account_pool.application.effective_policy import effective_policy, minimum_snapshot_max_age
from account_pool.policies import AccountPolicy, RoutingPolicy, TransportPolicy
from account_pool.settings import AccountPoolSettings


def test_inheriting_card_takes_routing_and_timeout_from_settings() -> None:
    policy = AccountPolicy(
        routing=RoutingPolicy(strategy="plan", max_attempts=1),
        transport=TransportPolicy(request_timeout_seconds=30),
    )
    settings = AccountPoolSettings(
        default_route="quota",
        max_attempts=4,
        request_timeout_seconds=180,
        quota_refresh_interval_minutes=5,
    )

    merged = effective_policy(policy, settings, inherit=True)

    assert merged.routing.strategy == "quota"
    assert merged.routing.max_attempts == 4
    assert merged.transport.request_timeout_seconds == 180


def test_independent_card_keeps_its_routing_but_still_gets_the_freshness_floor() -> None:
    policy = AccountPolicy(routing=RoutingPolicy(strategy="random", quota_snapshot_max_age=300))

    for minutes, expected in ((5, 660), (15, 1860), (30, 3660), (60, 7260)):
        merged = effective_policy(
            policy,
            AccountPoolSettings(quota_refresh_interval_minutes=minutes),
            inherit=False,
        )
        assert merged.routing.strategy == "random"
        assert merged.routing.quota_snapshot_max_age == expected
        assert expected == minimum_snapshot_max_age(
            AccountPoolSettings(quota_refresh_interval_minutes=minutes)
        )


def test_snapshot_max_age_above_the_floor_is_kept() -> None:
    policy = AccountPolicy(routing=RoutingPolicy(quota_snapshot_max_age=7200))
    merged = effective_policy(
        policy,
        AccountPoolSettings(quota_refresh_interval_minutes=15),
        inherit=False,
    )

    assert merged.routing.quota_snapshot_max_age == 7200


def test_missing_settings_leave_the_card_policy_untouched() -> None:
    policy = AccountPolicy(routing=RoutingPolicy(strategy="expiry", quota_snapshot_max_age=120))

    assert effective_policy(policy, None, inherit=True) == policy
    assert effective_policy(policy, None, inherit=False) == policy


def test_inheriting_card_takes_quota_thresholds_from_settings() -> None:
    policy = AccountPolicy(routing=RoutingPolicy(quota_reserve_percent=0))
    settings = AccountPoolSettings(
        quota_reserve_percent=75,
        quota_reserve_5h_percent=20,
        quota_reserve_weekly_percent=30,
        quota_snapshot_max_age=1800,
    )

    merged = effective_policy(policy, settings, inherit=True)

    assert merged.routing.quota_reserve_percent == 75
    assert merged.routing.quota_reserve_5h_percent == 20
    assert merged.routing.quota_reserve_weekly_percent == 30
    assert merged.routing.quota_snapshot_max_age == 1800


def test_independent_card_keeps_its_own_quota_thresholds() -> None:
    policy = AccountPolicy(routing=RoutingPolicy(quota_reserve_percent=40, quota_reserve_5h_percent=5))
    settings = AccountPoolSettings(quota_reserve_percent=75)

    merged = effective_policy(policy, settings, inherit=False)

    assert merged.routing.quota_reserve_percent == 40
    assert merged.routing.quota_reserve_5h_percent == 5
