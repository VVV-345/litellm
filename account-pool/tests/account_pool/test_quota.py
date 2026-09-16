"""验证 CLIProxy 额度解析模块保持窗口和冷却语义。"""

from datetime import datetime, timedelta, timezone
from typing import Final
from uuid import uuid4

import pytest
from account_pool.domain import EnvironmentRecord, EnvironmentStatus, Provider, ProxyMode, QuotaSnapshot, utc_now
from account_pool.provider_quota import (
    parse_claude_usage_quota,
    parse_codex_account_info,
    parse_codex_usage_quota,
)
from account_pool.quota import (
    QuotaObservation,
    effective_cooldown_until,
    parse_antigravity_assist,
    parse_antigravity_onboard_project,
    parse_antigravity_quota,
    parse_provider_quota,
    parse_quota,
    parse_xai_billing_quota,
)
from account_pool.quota_scheduler import QuotaRefreshScheduler
from account_pool.settings import AccountPoolSettings, AccountPoolSettingsView


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


class _AuthRefreshSettings:
    async def get(self) -> AccountPoolSettingsView:
        return AccountPoolSettingsView(version=0, values=AccountPoolSettings())


@pytest.mark.asyncio
async def test_auth_refresh_scheduler_uses_the_authentication_interval() -> None:
    scheduler: Final = QuotaRefreshScheduler(
        _AuthRefreshSettings(),
        lambda: _empty_refresh(),
        interval=lambda values: values.auth_refresh_interval_minutes,
    )

    status: Final = await scheduler.status()

    assert status.interval_minutes == 15


async def _empty_refresh() -> tuple[object, ...]:
    return ()


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
    assert refreshed.quota.plan_type == "SuperGrok"
    assert tuple(window.name for window in refreshed.quota.windows) == ("Weekly", "Monthly")
    assert tuple(window.remaining_percent for window in refreshed.quota.windows) == (75, 60)
    assert refreshed.quota.windows[0].window_minutes == 10080
    assert refreshed.quota.windows[1].resets_at == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_parse_codex_usage_keeps_session_weekly_review_and_reset_credits() -> None:
    observed_at: Final = datetime(2026, 9, 14, tzinfo=timezone.utc)
    refreshed: Final = parse_codex_usage_quota(
        """
        {
          "plan_type": "pro",
          "rate_limit": {
            "primary_window": {
              "used_percent": 20,
              "limit_window_seconds": 18000,
              "reset_after_seconds": 3600
            },
            "secondary_window": {
              "used_percent": 35,
              "limit_window_seconds": 604800,
              "reset_at": 1790000000
            }
          },
          "code_review_rate_limit": {
            "primary_window": {
              "used_percent": 50,
              "limit_window_seconds": 18000,
              "reset_after_seconds": 1800
            }
          },
          "rate_limit_reset_credits": {"available_count": 2}
        }
        """,
        observed_at,
        subscription_body="""
        {
          "subscription_plan": "pro",
          "active_start": "2026-09-01T00:00:00Z",
          "active_until": "2026-10-01T00:00:00Z",
          "status": "active"
        }
        """,
    )

    assert refreshed is not None
    assert refreshed.quota.reset_credits_available == 2
    assert refreshed.quota.subscription_status == "active"
    assert tuple(window.name for window in refreshed.quota.windows) == (
        "5 hour",
        "Weekly",
        "Code review 5 hour",
    )
    assert refreshed.quota.windows[0].window_minutes == 300
    assert refreshed.quota.windows[0].remaining_percent == 80


def test_parse_codex_usage_accepts_null_additional_limits() -> None:
    refreshed: Final = parse_codex_usage_quota(
        """
        {
          "plan_type": "plus",
          "rate_limit": {
            "primary_window": {"used_percent": 0, "limit_window_seconds": 18000},
            "secondary_window": {"used_percent": 11, "limit_window_seconds": 604800}
          },
          "additional_rate_limits": null,
          "rate_limit_reset_credits": {"available_count": 0}
        }
        """,
        datetime(2026, 9, 16, tzinfo=timezone.utc),
    )

    assert refreshed is not None
    assert refreshed.quota.reset_credits_available == 0
    assert tuple(window.remaining_percent for window in refreshed.quota.windows) == (100, 89)


def test_parse_codex_account_info_skips_unusable_accounts_and_matches_workspace() -> None:
    observed_at: Final = datetime(2026, 9, 14, tzinfo=timezone.utc)
    selected: Final = parse_codex_account_info(
        """
        {
          "accounts": {
            "org-expired": {
              "account": {"account_id": "expired", "plan_type": "team", "is_default": true},
              "entitlement": {"expires_at": "2026-09-01T00:00:00Z"}
            },
            "org-disabled": {
              "account": {"account_id": "disabled", "plan_type": "pro", "is_deactivated": true}
            },
            "org-team": {
              "account": {"account_id": "account-team", "plan_type": "team"},
              "entitlement": {
                "subscription_plan": "team",
                "status": "active",
                "active_start": "2026-08-01T00:00:00Z",
                "expires_at": "2027-08-01T00:00:00Z"
              }
            }
          }
        }
        """,
        "org-team",
        observed_at,
    )

    assert selected is not None
    assert selected.account_id == "account-team"
    assert selected.plan_type == "team"
    assert selected.subscription_status == "active"
    assert selected.subscription_active_until == datetime(2027, 8, 1, tzinfo=timezone.utc)


def test_parse_codex_usage_reads_additional_limits_and_authoritative_reset_credit_details() -> None:
    observed_at: Final = datetime(2026, 9, 14, tzinfo=timezone.utc)
    refreshed: Final = parse_codex_usage_quota(
        """
        {
          "plan_type": "pro",
          "rate_limit": {
            "primary_window": {"used_percent": 20, "limit_window_seconds": 18000}
          },
          "additional_rate_limits": [
            {
              "metered_feature": "codex_bengalfox",
              "rate_limit": {
                "primary_window": {"used_percent": 30, "limit_window_seconds": 18000},
                "secondary_window": {"used_percent": 40, "limit_window_seconds": 604800}
              }
            }
          ],
          "rate_limit_reset_credits": {"available_count": 7}
        }
        """,
        observed_at,
        subscription_body="""
        {
          "subscriptionPlan": "pro",
          "currentPeriodStart": "2026-09-01T00:00:00Z",
          "currentPeriodEnd": "2026-10-01T00:00:00Z",
          "subscriptionStatus": "active"
        }
        """,
        reset_credits_body="""
        {
          "credits": [
            {"type": "rate_limit_reset", "status": "available", "expiresAt": "2090-01-01T00:00:00Z"},
            {"resetType": "codex_rate_limits", "status": "granted", "expiresAt": "2090-01-01T00:00:00Z"},
            {"reset_type": "codex_rate_limits", "status": "redeemed", "expires_at": "2090-01-01T00:00:00Z"},
            {"status": "used", "expires_at": "2090-01-01T00:00:00Z"},
            {"status": "consumed", "expires_at": "2090-01-01T00:00:00Z"},
            {"status": "expired", "expires_at": "2090-01-01T00:00:00Z"},
            {"reset_type": "other", "expires_at": "2090-01-01T00:00:00Z"},
            {"status": "available", "expires_at": "2020-01-01T00:00:00Z"}
          ]
        }
        """,
    )

    assert refreshed is not None
    assert refreshed.quota.reset_credits_available == 3
    assert refreshed.quota.subscription_active_start == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert refreshed.quota.subscription_active_until == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert tuple(window.name for window in refreshed.quota.windows) == (
        "5 hour",
        "Codex Spark 5 hour",
        "Codex Spark Weekly",
    )


def test_parse_codex_usage_accepts_websocket_additional_limit_object() -> None:
    observed_at: Final = datetime(2026, 9, 14, tzinfo=timezone.utc)
    refreshed: Final = parse_codex_usage_quota(
        """
        {
          "plan_type": "pro",
          "rate_limit": {
            "primary_window": {"used_percent": 20, "limit_window_seconds": 18000}
          },
          "additional_rate_limits": {
            "GPT-5.3-Codex-Spark": {
              "primary": {"used_percent": 3, "window_minutes": 300, "reset_at": 1787231961},
              "secondary": {"used_percent": 63, "window_minutes": 10080, "reset_at": 1787290791}
            }
          }
        }
        """,
        observed_at,
    )

    assert refreshed is not None
    assert tuple(window.name for window in refreshed.quota.windows) == (
        "5 hour",
        "Gpt 5.3 Codex Spark 5 hour",
        "Gpt 5.3 Codex Spark Weekly",
    )
    assert refreshed.quota.windows[1].remaining_percent == 97
    assert refreshed.quota.windows[1].window_minutes == 300
    assert refreshed.quota.windows[2].remaining_percent == 37
    assert refreshed.quota.windows[2].window_minutes == 10080


def test_parse_claude_usage_keeps_all_windows_and_profile_plan() -> None:
    refreshed: Final = parse_claude_usage_quota(
        """
        {
          "five_hour": {"utilization": 12, "resets_at": "2026-09-14T05:00:00Z"},
          "seven_day": {"utilization": 34, "resets_at": "2026-09-21T00:00:00Z"},
          "seven_day_sonnet_4": {"utilization": 56, "resets_at": "2026-09-21T01:00:00Z"},
          "extra_usage": {
            "is_enabled": true,
            "utilization": 25,
            "used_credits": 500,
            "monthly_limit": 2000,
            "resets_at": "2026-10-01T00:00:00Z"
          }
        }
        """,
        """
        {
          "account": {"has_claude_max": true},
          "organization": {
            "organization_type": "claude_max",
            "rate_limit_tier": "default_claude_max_20x",
            "subscription_created_at": "2026-01-01T00:00:00Z",
            "has_extra_usage_enabled": true
          }
        }
        """,
        datetime(2026, 9, 14, tzinfo=timezone.utc),
    )

    assert refreshed is not None
    assert refreshed.quota.plan_type == "Max"
    assert refreshed.quota.extra_usage_enabled is True
    assert tuple(window.name for window in refreshed.quota.windows) == (
        "5 hour",
        "7 day",
        "7 day Sonnet",
        "Extra usage",
    )
    assert refreshed.quota.windows[-1].used == 500
    assert refreshed.quota.windows[-1].total == 2000


def test_parse_claude_usage_reads_dynamic_limits_fable_and_zero_extra_usage() -> None:
    refreshed: Final = parse_claude_usage_quota(
        """
        {
          "limits": [
            {"kind": "session", "percent": 0.2, "resets_at": "2026-09-14T05:00:00Z"},
            {
              "group": "weekly",
              "percent": 35,
              "resets_at": "2026-09-21T00:00:00Z",
              "scope": {"model": {"display_name": "Sonnet"}}
            },
            {
              "group": "weekly",
              "percent": 45,
              "resets_at": "2026-09-21T01:00:00Z",
              "scope": {"surface": {"display_name": "Fable"}}
            }
          ],
          "extra_usage": {
            "is_enabled": true,
            "utilization": 0,
            "used_credits": 0,
            "used_cents": 500,
            "limit_cents": 2000,
            "resets_at": "2026-10-01T00:00:00Z"
          }
        }
        """,
        None,
        datetime(2026, 9, 14, tzinfo=timezone.utc),
    )

    assert refreshed is not None
    assert tuple(window.name for window in refreshed.quota.windows) == (
        "5 hour",
        "7 day Sonnet",
        "7 day Fable",
        "Extra usage",
    )
    assert refreshed.quota.windows[0].used_percent == 20
    assert refreshed.quota.windows[-1].used == 0
    assert refreshed.quota.windows[-1].total == 2000
    assert refreshed.quota.windows[-1].used_percent == 0


def test_parse_xai_billing_keeps_credit_bags_products_tasks_and_subscription() -> None:
    refreshed: Final = parse_xai_billing_quota(
        """
        {
          "config": {
            "currentPeriod": {
              "start": "2026-09-10T00:00:00Z",
              "end": "2026-09-17T00:00:00Z"
            },
            "weeklyCredits": {"used": 120, "total": 1000},
            "productUsage": [
              {"product": "coding", "used": 40, "total": 200},
              {"product": "image", "usagePercent": 30}
            ],
            "onDemandUsed": 50,
            "onDemandCap": 500,
            "prepaidBalance": 700
          }
        }
        """,
        """
        {
          "config": {
            "monthlyLimit": {"val": 150000},
            "used": {"val": 30000},
            "billingPeriodStart": "2026-09-01T00:00:00Z",
            "billingPeriodEnd": "2026-10-01T00:00:00Z"
          }
        }
        """,
        datetime(2026, 9, 14, tzinfo=timezone.utc),
        user_body="""
        {
          "user": {
            "id": "user-1",
            "hasGrokCodeAccess": true,
            "subscription": {
              "tier": "SuperGrok Heavy",
              "status": "SUBSCRIPTION_STATUS_ACTIVE",
              "currentPeriodStart": "2026-09-01T00:00:00Z",
              "currentPeriodEnd": "2026-10-01T00:00:00Z"
            }
          }
        }
        """,
        task_usage_body="""
        {
          "usage": {
            "frequentUsage": 2,
            "frequentLimit": 20,
            "occasionalUsage": 3,
            "occasionalLimit": 10
          }
        }
        """,
    )

    assert refreshed is not None
    assert refreshed.quota.plan_type == "SuperGrok Heavy"
    assert refreshed.quota.subscription_status == "SUBSCRIPTION_STATUS_ACTIVE"
    assert refreshed.quota.prepaid_balance == 700
    assert refreshed.quota.has_grok_code_access is True
    assert tuple(window.name for window in refreshed.quota.windows) == (
        "Weekly",
        "Monthly",
        "Product: coding",
        "Product: image",
        "On-demand",
        "Tasks: Frequent",
        "Tasks: Occasional",
    )
    assert refreshed.quota.windows[0].used == 120
    assert refreshed.quota.windows[0].total == 1000
    assert refreshed.quota.windows[-1].remaining == 7


def test_parse_xai_billing_uses_monthly_fields_from_weekly_response_when_monthly_probe_fails() -> None:
    refreshed: Final = parse_xai_billing_quota(
        """
        {
          "config": {
            "currentPeriod": {
              "type": "WEEKLY",
              "start": "2026-09-10T00:00:00Z",
              "end": "2026-09-17T00:00:00Z"
            },
            "creditUsagePercent": 20,
            "monthlyLimit": {"val": 15000},
            "used": {"val": 3000},
            "billingPeriodStart": "2026-09-01T00:00:00Z",
            "billingPeriodEnd": "2026-10-01T00:00:00Z"
          }
        }
        """,
        None,
        datetime(2026, 9, 14, tzinfo=timezone.utc),
    )

    assert refreshed is not None
    assert tuple(window.name for window in refreshed.quota.windows) == ("Weekly", "Monthly")
    assert refreshed.quota.windows[1].remaining_percent == 80


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


def test_parse_antigravity_quota_prefers_summary_buckets_and_keeps_paid_credits() -> None:
    observed_at: Final = datetime(2026, 9, 14, tzinfo=timezone.utc)
    assist: Final = parse_antigravity_assist(
        """
        {
          "cloudaicompanionProject": "project-a",
          "paidTier": {
            "id": "pro-tier",
            "availableCredits": [
              {
                "creditType": "GOOGLE_ONE_AI",
                "creditAmount": "25000",
                "minimumCreditAmountForUsage": "50"
              }
            ]
          }
        }
        """
    )
    refreshed: Final = parse_antigravity_quota(
        """
        {
          "models": {
            "gemini-2.5-pro": {
              "quotaInfo": {"remainingFraction": 0.1, "resetTime": "2026-09-14T05:00:00Z"}
            }
          }
        }
        """,
        assist,
        observed_at,
        summary_body="""
        {
          "groups": [
            {
              "displayName": "5 hour",
              "buckets": [
                {
                  "bucketId": "gemini-2.5-pro",
                  "displayName": "Gemini 2.5 Pro",
                  "remainingFraction": 0.7,
                  "resetTime": "2026-09-14T05:00:00Z"
                }
              ]
            },
            {
              "displayName": "Weekly",
              "buckets": [
                {
                  "bucketId": "gemini-2.5-pro",
                  "displayName": "Gemini 2.5 Pro",
                  "remainingFraction": 0.4,
                  "resetTime": "2026-09-21T00:00:00Z"
                }
              ]
            }
          ]
        }
        """,
    )

    assert refreshed is not None
    assert refreshed.quota.balances[0].name == "GOOGLE_ONE_AI"
    assert refreshed.quota.balances[0].available == 25000
    assert refreshed.quota.balances[0].minimum_required == 50
    assert len(refreshed.model_quotas) == 1
    assert tuple(window.name for window in refreshed.model_quotas[0].quota.windows) == ("5 hour", "Weekly")
    assert tuple(window.remaining_percent for window in refreshed.model_quotas[0].quota.windows) == (70, 40)


def test_parse_antigravity_assist_detects_gcp_tos_from_tiers() -> None:
    current: Final = parse_antigravity_assist(
        '{"cloudaicompanionProject":"project-a","currentTier":{"id":"standard-tier","usesGcpTos":true}}'
    )
    default: Final = parse_antigravity_assist(
        '{"allowedTiers":[{"id":"standard-tier","isDefault":true,"usesGcpTos":true}]}'
    )
    mixed: Final = parse_antigravity_assist(
        '{"paidTier":{"id":"pro-tier"},"currentTier":{"id":"standard-tier","usesGcpTos":true}}'
    )

    assert current is not None and current.uses_gcp_tos is True
    assert default is not None and default.uses_gcp_tos is True
    assert mixed is not None and mixed.uses_gcp_tos is True
    assert default.onboard_tier_id == "standard-tier"


def test_parse_antigravity_onboard_project_reads_operation_and_completion() -> None:
    pending: Final = parse_antigravity_onboard_project('{"name":"operations/setup-1","done":false}')
    completed: Final = parse_antigravity_onboard_project(
        '{"done":true,"response":{"cloudaicompanionProject":{"id":"project-new"}}}'
    )

    assert pending == (None, "operations/setup-1", False)
    assert completed == ("project-new", None, True)
