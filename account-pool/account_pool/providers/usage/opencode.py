"""解析 OpenCode Go 套餐窗口，不推算充值余额或模型额度。"""

from datetime import datetime
from math import isfinite
from typing import Final

from pydantic import JsonValue

from account_pool.domain import QuotaSnapshot, QuotaWindow
from account_pool.providers.usage.common import _datetime_value, _json_value, _mapping, _number

OPENCODE_GO_PLAN: Final = "OpenCode Go"
_WINDOWS: Final = (("rolling", "5 hour", 300), ("weekly", "Weekly", 10080), ("monthly", "Monthly", 43200))


def _go_window(raw: JsonValue | None, name: str, minutes: int) -> QuotaWindow | None:
    limit: Final = _mapping(raw)
    if limit is None:
        return None
    percent: Final = _number(limit.get("percent"))
    if percent is None or not isfinite(percent):
        return None
    used: Final = min(percent, 100)
    return QuotaWindow(
        name=name, window_minutes=minutes, used_percent=used, remaining_percent=100 - used,
        resets_at=_datetime_value(limit.get("resetsAt")),
    )


def parse_opencode_go_quota(body: str, observed_at: datetime) -> QuotaSnapshot | None:
    payload: Final = _mapping(_json_value(body))
    usage: Final = _mapping(payload.get("usage")) if payload is not None else None
    if usage is None:
        return None
    windows: Final = tuple(
        window for field, name, minutes in _WINDOWS if (window := _go_window(usage.get(field), name, minutes)) is not None
    )
    if not windows:
        return None
    return QuotaSnapshot(
        plan_type=OPENCODE_GO_PLAN,
        observed_at=observed_at,
        refresh_attempted_at=observed_at,
        source="provider_api",
        refresh_status="complete" if len(windows) == len(_WINDOWS) and all(w.resets_at for w in windows) else "partial",
        windows=windows,
    )
