"""分类 Cockpit 管理失败；各功能仍保留自己的公开错误码和状态映射。"""

from __future__ import annotations

from typing import Final

from account_pool.cockpit_admin import CockpitFailure


def unavailable(failure: CockpitFailure) -> bool:
    return failure.category == "transport" or failure.status_code == 503


def precondition(failure: CockpitFailure) -> bool:
    return failure.category == "precondition"


def invalid_payload(failure: CockpitFailure) -> bool:
    return failure.category == "invalid_response"


def server_error(failure: CockpitFailure) -> bool:
    return (failure.status_code or 0) >= 500


def write_outcome_unknown(failure: CockpitFailure) -> bool:
    return failure.category in {"transport", "invalid_response"} or server_error(failure)


READ_FAILURE_STATUS: Final = 502
UNAVAILABLE_STATUS: Final = 503
