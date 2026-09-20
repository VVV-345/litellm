"""计算环境配置与冷却状态，不写入数据库或改变路由规则。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from account_pool.domain import EnvironmentRecord, EnvironmentStatus, UpdateEnvironmentRequest, utc_now


class _AutomaticCooldownState(StrEnum):
    NONE = "none"
    ACTIVE = "active"
    RECOVERED = "recovered"
    BLOCKED = "blocked"


def _configuration_requires_reconciliation(record: EnvironmentRecord) -> bool:
    # ERROR 只代表最近一次尝试失败，期望版本未观测时仍须继续补偿。
    return record.configuration_pending or record.desired_configuration_version > record.observed_configuration_version


def _cooldown_elapsed(record: EnvironmentRecord) -> bool:
    return (
        record.enabled
        and not record.manual_cooldown
        and record.cooldown_until is not None
        and record.cooldown_until <= utc_now()
    )


def _cooldown_active(record: EnvironmentRecord) -> bool:
    return record.cooldown_until is not None and record.cooldown_until > utc_now()


def _status_after_update(
    record: EnvironmentRecord,
    request: UpdateEnvironmentRequest,
    automatic_cooldown: _AutomaticCooldownState,
) -> EnvironmentStatus:
    # 授权失败时允许修正代理，但保存配置不能把未授权账号变成可用账号。
    if record.auth_file_name is None:
        return record.status
    if not request.enabled:
        return EnvironmentStatus.DISABLED
    if request.manual_cooldown:
        return EnvironmentStatus.COOLING_DOWN
    if automatic_cooldown in (_AutomaticCooldownState.ACTIVE, _AutomaticCooldownState.BLOCKED):
        return EnvironmentStatus.COOLING_DOWN
    if record.status in (EnvironmentStatus.AWAITING_AUTHORIZATION, EnvironmentStatus.VALIDATING):
        return record.status
    return EnvironmentStatus.READY


def _cooldown_until_after_update(
    record: EnvironmentRecord,
    manual_cooldown: bool,
    automatic_cooldown: _AutomaticCooldownState,
) -> datetime | None:
    if manual_cooldown:
        return record.cooldown_until
    return None if automatic_cooldown is _AutomaticCooldownState.RECOVERED else record.cooldown_until
