"""复用供应商额度中的数值、时间、JSON 和窗口解析，不发送网络请求。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Final, TypeVar

from pydantic import BaseModel, ConfigDict, JsonValue, RootModel, ValidationError

from account_pool.domain import QuotaWindow

ModelT = TypeVar("ModelT", bound=BaseModel)


class _MoneyValue(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    val: float | int | str | None = None


class _JSONPayload(RootModel[JsonValue]):
    model_config = ConfigDict(frozen=True)


def _parse_model(model: type[ModelT], body: str | None) -> ModelT | None:
    if body is None:
        return None
    try:
        return model.model_validate_json(body)
    except ValidationError:
        return None


def _json_value(body: str | None) -> JsonValue | None:
    if body is None:
        return None
    try:
        return _JSONPayload.model_validate_json(body).root
    except ValidationError:
        return None


def _mapping(value: JsonValue | Mapping[str, JsonValue] | None) -> Mapping[str, JsonValue] | None:
    return value if isinstance(value, Mapping) else None


def _sequence(value: JsonValue | Mapping[str, JsonValue] | None) -> Sequence[JsonValue] | None:
    return value if isinstance(value, list) else None


def _number(value: JsonValue | _MoneyValue | None) -> float | None:
    raw: Final = value.val if isinstance(value, _MoneyValue) else value
    if isinstance(raw, Mapping):
        return _number(raw.get("val"))
    if raw is None or isinstance(raw, (bool, list, dict)):
        return None
    try:
        parsed: Final = float(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _text(value: JsonValue | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized: Final = value.strip()
    return normalized or None


def _datetime_value(value: JsonValue | None) -> datetime | None:
    if value is None or isinstance(value, (bool, list, dict)):
        return None
    if isinstance(value, (int, float)):
        seconds: Final = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    raw: Final = value.strip()
    if not raw:
        return None
    try:
        numeric: Final = float(raw)
    except ValueError:
        try:
            parsed: Final = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    numeric_seconds: Final = numeric / 1000 if numeric > 10_000_000_000 else numeric
    try:
        return datetime.fromtimestamp(numeric_seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _window(
    name: str,
    used_percent: float,
    starts_at: datetime | None,
    resets_at: datetime | None,
    fallback_minutes: int,
    *,
    used: float | None = None,
    total: float | None = None,
    remaining: float | None = None,
    unit: str | None = None,
) -> QuotaWindow | None:
    if used_percent < 0 or used_percent > 100:
        return None
    duration: Final = (
        int((resets_at - starts_at).total_seconds() / 60)
        if starts_at is not None and resets_at is not None and resets_at > starts_at
        else fallback_minutes
    )
    return QuotaWindow(
        name=name,
        used_percent=used_percent,
        remaining_percent=100 - used_percent,
        window_minutes=max(1, duration),
        starts_at=starts_at,
        resets_at=resets_at,
        used=used,
        total=total,
        remaining=remaining,
        unit=unit,
    )


def _first_text(
    sources: tuple[Mapping[str, JsonValue] | None, ...],
    keys: tuple[str, ...],
) -> str | None:
    return next(
        (
            text
            for source in sources
            if source is not None
            for key in keys
            if (text := _text(source.get(key))) is not None
        ),
        None,
    )


def _first_datetime(
    sources: tuple[Mapping[str, JsonValue] | None, ...],
    keys: tuple[str, ...],
) -> datetime | None:
    return next(
        (
            parsed
            for source in sources
            if source is not None
            for key in keys
            if (parsed := _datetime_value(source.get(key))) is not None
        ),
        None,
    )


def _first_bool(
    sources: tuple[Mapping[str, JsonValue] | None, ...],
    keys: tuple[str, ...],
) -> bool | None:
    return next(
        (
            value
            for source in sources
            if source is not None
            for key in keys
            if isinstance((value := source.get(key)), bool)
        ),
        None,
    )


def _text_value(value: str | None) -> str | None:
    normalized: Final = "" if value is None else value.strip()
    return normalized or None
