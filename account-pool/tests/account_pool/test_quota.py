"""验证 CLIProxy 额度解析模块保持窗口和冷却语义。"""

from datetime import timedelta
from typing import Final
from uuid import uuid4

from account_pool.domain import EnvironmentRecord, EnvironmentStatus, Provider, ProxyMode, QuotaSnapshot, utc_now
from account_pool.quota import QuotaObservation, effective_cooldown_until, parse_quota


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


def test_effective_cooldown_preserves_manual_cooldown_when_upstream_value_elapsed() -> None:
    record: Final = _record(manual_cooldown=True)
    now: Final = utc_now()

    assert effective_cooldown_until(record, now - timedelta(seconds=1), now) == record.cooldown_until
