"""计算命名配置与卡片基线的差异，不执行配置同步。"""

from __future__ import annotations

from typing import Final
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from account_pool.domain import (
    CommonSettingsProfileBaseline,
    EnvironmentRecord,
    NetworkSettingsProfileBaseline,
    ProxyMode,
    SettingsProfileBaselines,
    UpdateEnvironmentRequest,
)
from account_pool.settings import AccountPoolSettings


class _ExplicitProfileUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: UpdateEnvironmentRequest
    baselines: SettingsProfileBaselines


def explicit_profile_update(
    record: EnvironmentRecord,
    settings: AccountPoolSettings,
) -> _ExplicitProfileUpdate | None:
    common: Final = next(
        (
            profile
            for profile in settings.common_profiles
            if record.id in profile.card_ids and not profile.inherit_global
        ),
        None,
    )
    network: Final = next(
        (
            profile
            for profile in settings.network_profiles
            if record.id in profile.card_ids and not profile.inherit_global
        ),
        None,
    )
    baselines: Final = record.settings_profile_baselines
    concurrency_limit: Final = (
        baselines.common.concurrency_limit
        if common is None and baselines.common is not None
        else record.concurrency_limit
        if common is None
        else common.values.default_concurrency_limit
    )
    common_baseline: Final = (
        None
        if common is None
        else CommonSettingsProfileBaseline(
            profile_id=common.id,
            concurrency_limit=(
                record.concurrency_limit if baselines.common is None else baselines.common.concurrency_limit
            ),
        )
    )
    proxy_profile_id: Final = (
        baselines.network.proxy_profile_id
        if network is None and baselines.network is not None
        else record.proxy_profile_id
        if network is None
        else network.values.default_proxy_profile_id
    )
    proxy_mode: Final = (
        baselines.network.proxy_mode
        if network is None and baselines.network is not None
        else record.proxy_mode
        if network is None
        else ProxyMode.DEFAULT_GATEWAY
        if proxy_profile_id is None
        else ProxyMode.PROFILE
    )
    network_baseline: Final = (
        None
        if network is None
        else NetworkSettingsProfileBaseline(
            profile_id=network.id,
            proxy_mode=record.proxy_mode if baselines.network is None else baselines.network.proxy_mode,
            proxy_profile_id=(
                record.proxy_profile_id if baselines.network is None else baselines.network.proxy_profile_id
            ),
        )
    )
    next_baselines: Final = SettingsProfileBaselines(common=common_baseline, network=network_baseline)
    unchanged: Final = (
        record.concurrency_limit == concurrency_limit
        and record.proxy_mode is proxy_mode
        and record.proxy_profile_id == proxy_profile_id
        and baselines == next_baselines
    )
    if unchanged:
        return None
    return _ExplicitProfileUpdate(
        request=UpdateEnvironmentRequest(
            version=record.version,
            operation_id=f"settings-sync-{uuid4()}",
            name=record.name,
            concurrency_limit=concurrency_limit,
            enabled=record.enabled,
            manual_cooldown=record.manual_cooldown,
            proxy_mode=proxy_mode,
            proxy_profile_id=proxy_profile_id,
            enabled_models=record.enabled_models,
        ),
        baselines=next_baselines,
    )
