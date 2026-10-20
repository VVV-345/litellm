"""验证运行参数分区只更新自己负责的字段。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPoolSettings,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
)
from litellm.proxy.management_endpoints.runtime_configuration_endpoints import merge_runtime_settings


def test_policy_section_updates_routing_defaults_and_keeps_other_sections() -> None:
    current: AccountPoolSettingsView = AccountPoolSettingsView(
        version=3,
        values=AccountPoolSettings(default_concurrency_limit=5, quota_switch_project=True),
    )
    patch: AccountPoolSettingsUpdate = AccountPoolSettingsUpdate(
        version=3,
        values=AccountPoolSettings(
            default_route="quota",
            max_attempts=4,
            quota_reserve_percent=75,
            quota_snapshot_max_age=1800,
        ),
    )

    merged: AccountPoolSettingsUpdate = merge_runtime_settings(current, patch, "policy")

    assert merged.values.default_route == "quota"
    assert merged.values.max_attempts == 4
    assert merged.values.quota_reserve_percent == 75
    assert merged.values.quota_snapshot_max_age == 1800
    assert merged.values.default_concurrency_limit == 5
    assert merged.values.quota_switch_project is True


def test_policy_section_rejects_stale_versions() -> None:
    current: AccountPoolSettingsView = AccountPoolSettingsView(version=3, values=AccountPoolSettings())
    patch: AccountPoolSettingsUpdate = AccountPoolSettingsUpdate(
        version=2, values=AccountPoolSettings(quota_reserve_percent=75)
    )

    with pytest.raises(HTTPException) as error:
        merge_runtime_settings(current, patch, "policy")

    assert error.value.status_code == 409
