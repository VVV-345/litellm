"""解析 GLM Coding Plan 的只读用量，不将套餐额度解释为付款余额。"""

from datetime import datetime
from math import isfinite
from typing import Final

from pydantic import JsonValue

from account_pool.domain import QuotaSnapshot, QuotaWindow
from account_pool.providers.usage.common import _datetime_value, _json_value, _mapping, _number, _sequence, _text


def _limit_window(raw: JsonValue) -> QuotaWindow | None:
    limit: Final = _mapping(raw)
    if limit is None:
        return None
    kind: Final = limit.get("type")
    duration: Final = (_number(limit.get("unit")), _number(limit.get("number")))
    if kind == "TIME_LIMIT" and duration == (5, 1):
        name, minutes, unit = "MCP Monthly", 43200, "tasks"
    elif kind in ("TOKENS_LIMIT", "CREDIT_LIMIT") and duration in ((3, 5), (6, 1)):
        name, minutes = ("5 hour", 300) if duration == (3, 5) else ("Weekly", 10080)
        unit: Final = "credits" if kind == "CREDIT_LIMIT" else "tokens"
    else:
        return None
    percentage: Final = _number(limit.get("percentage"))
    if percentage is None or not 0 <= percentage <= 100:
        return None
    reset_ms: Final = _number(limit.get("nextResetTime"))
    used: Final = _number(limit.get("currentValue"))
    total: Final = _number(limit.get("usage"))
    remaining: Final = _number(limit.get("remaining"))
    return QuotaWindow(
        name=name,
        window_minutes=minutes,
        used_percent=percentage,
        remaining_percent=100 - percentage,
        resets_at=_datetime_value(reset_ms) if reset_ms is not None and isfinite(reset_ms) and reset_ms > 10**10 else None,
        used=used if used is not None and isfinite(used) else None,
        total=total if total is not None and isfinite(total) else None,
        remaining=remaining if remaining is not None and isfinite(remaining) else None,
        unit=unit,
    )


def parse_glm_coding_quota(body: str, observed_at: datetime) -> QuotaSnapshot | None:
    payload: Final = _mapping(_json_value(body))
    if payload is None:
        return None
    if payload.get("code") == 500 and payload.get("msg") == "当前用户不存在coding plan":
        return QuotaSnapshot(
            refresh_attempted_at=observed_at, refresh_status="unsupported",
            refresh_error="上游未识别到此 Key 的 GLM Coding Plan 个人套餐，请核对套餐和接入站点",
        )
    if payload.get("code") != 200 or ("success" in payload and payload["success"] is not True):
        return None
    data: Final = _mapping(payload.get("data"))
    if data is None:
        return None
    windows: Final = {
        window.name: window for raw in (_sequence(data.get("limits")) or ()) if (window := _limit_window(raw)) is not None
    }
    if not windows:
        return None
    level: Final = (_text(data.get("level")) or "").lower()
    return QuotaSnapshot(
        plan_type=level.capitalize() if level in ("lite", "pro", "max") else None,
        observed_at=observed_at,
        refresh_attempted_at=observed_at,
        source="provider_api",
        refresh_status="complete",
        windows=tuple(windows[name] for name in ("5 hour", "Weekly", "MCP Monthly") if name in windows),
    )
