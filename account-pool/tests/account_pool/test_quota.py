"""验证 CLIProxy 额度解析模块保持窗口和冷却语义。"""

from datetime import datetime, timedelta, timezone
from typing import Final
from uuid import uuid4

from account_pool.domain import EnvironmentRecord, EnvironmentStatus, Provider, ProxyMode, QuotaSnapshot, utc_now
from account_pool.quota import (
    QuotaObservation,
    effective_cooldown_until,
    parse_antigravity_assist,
    parse_antigravity_quota,
    parse_provider_quota,
    parse_quota,
    parse_xai_billing_quota,
)


def _record(*, manual_cooldown: bool = False, enabled: bool = True):
    now: Final = utc_now()
    return EnvironmentRecord(
        id=uuid4(),
        name="test",
        provider=Provider.OPENAI,
        status=EnvironmentStatus.READY,
        enabled=enabled,
        manual_cooldown=manual_cooldown,
        concurrency_limit=1,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=(),
        enabled_models=(),
        auth_file_name="codex.json",
        auth_index=None,
        quota=QuotaSnapshot(),
        cooldown_until=now + timedelta(minutes=5),
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


def test_parse_quota_keeps_valid_windows_and_ignores_invalid_values() -> None:
    snapshot: Final = parse_quota(
        QuotaObservation(
            observed_at=utc_now(),
            signals={
                "x-codex-plan-type": "pro",
                "x-codex-five-hour-used-percent": "25",
                "x-codex-five-hour-window-minutes": "300",
                "x-codex-weekly-used-percent": "invalid",
                "x-codex-weekly-window-minutes": "10080",
            },
        )
    )

    assert snapshot.plan_type == "pro"
    assert len(snapshot.windows) == 1
    assert snapshot.windows[0].remaining_percent == 75


def test_parse_provider_quota_reads_unified_rate_limit_utilization() -> None:
    snapshot: Final = parse_provider_quota(
        QuotaObservation(
            observed_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
            signals={
                "Anthropic-Ratelimit-Unified-5h-Utilization": "0.25",
                "Anthropic-Ratelimit-Unified-5h-Reset": "1799000000",
                "plan_type": "max",
            },
        ),
        ("anthropic-ratelimit-unified-",),
        ("plan_type",),
    )

    assert snapshot.plan_type == "max"
    assert snapshot.windows[0].used_percent == 25
    assert snapshot.windows[0].window_minutes == 300


def test_parse_provider_quota_removes_the_complete_used_percent_suffix() -> None:
    snapshot: Final = parse_provider_quota(
        QuotaObservation(
            observed_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
            signals={
                "x-provider-5h-used-percent": "40",
                "x-provider-5h-window-minutes": "300",
            },
        ),
        ("x-provider-",),
    )

    assert len(snapshot.windows) == 1
    assert snapshot.windows[0].name == "5H"
    assert snapshot.windows[0].used_percent == 40


def test_effective_cooldown_preserves_manual_cooldown_when_upstream_value_elapsed() -> None:
    record: Final = _record(manual_cooldown=True)
    now: Final = utc_now()

    assert effective_cooldown_until(record, now - timedelta(seconds=1), now) == record.cooldown_until


def test_parse_xai_billing_quota_keeps_weekly_and_monthly_windows() -> None:
    observed_at: Final = datetime(2026, 9, 14, tzinfo=timezone.utc)
    refreshed: Final = parse_xai_billing_quota(
        """
        {
          "config": {
            "currentPeriod": {
              "type": "WEEKLY",
              "start": "2026-09-10T00:00:00Z",
              "end": "2026-09-17T00:00:00Z"
            },
            "creditUsagePercent": 25
          }
        }
        """,
        """
        {
          "config": {
            "monthlyLimit": {"val": "15000"},
            "used": {"val": 6000},
            "billingPeriodStart": "2026-09-01T00:00:00Z",
            "billingPeriodEnd": "2026-10-01T00:00:00Z"
          }
        }
        """,
        observed_at,
    )

    assert refreshed is not None
    assert refreshed.quota.plan_type == "supergrok"
    assert tuple(window.name for window in refreshed.quota.windows) == ("Weekly", "Monthly")
    assert tuple(window.remaining_percent for window in refreshed.quota.windows) == (75, 60)
    assert refreshed.quota.windows[0].window_minutes == 10080
    assert refreshed.quota.windows[1].resets_at == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_parse_antigravity_quota_keeps_real_model_percentages_and_reset_times() -> None:
    observed_at: Final = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    assist: Final = parse_antigravity_assist(
        """
        {
          "cloudaicompanionProject": {"id": "project-a"},
          "currentTier": {"id": "free-tier"},
          "paidTier": {"id": "pro-tier"}
        }
        """
    )
    refreshed: Final = parse_antigravity_quota(
        """
        {
          "models": {
            "gemini-2.5-pro": {
              "quotaInfo": {
                "remainingFraction": 0.375,
                "resetTime": "2026-09-14T05:00:00Z"
              }
            },
            "model-without-quota": {}
          }
        }
        """,
        assist,
        observed_at,
    )

    assert assist is not None
    assert assist.project_id == "project-a"
    assert refreshed is not None
    assert refreshed.quota.plan_type == "pro-tier"
    assert len(refreshed.model_quotas) == 1
    window: Final = refreshed.model_quotas[0].quota.windows[0]
    assert refreshed.model_quotas[0].model == "gemini-2.5-pro"
    assert window.used_percent == 62.5
    assert window.remaining_percent == 37.5
    assert window.window_minutes == 300
    assert window.resets_at == datetime(2026, 9, 14, 5, 0, tzinfo=timezone.utc)
