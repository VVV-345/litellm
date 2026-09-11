"""本文件验证号池全局配置的版本控制、差异预览和回滚接口。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final
from uuid import UUID, uuid4

from account_pool.management_api import create_management_router
from account_pool.settings import (
    AccountPoolSettings,
    AccountPoolSettingsHistoryEntry,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
    StreamingRule,
    settings_preview,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


class MemorySettingsRepository:
    def __init__(self) -> None:
        defaults: Final = AccountPoolSettings()
        now: Final = datetime.now(timezone.utc)
        self._view: AccountPoolSettingsView = AccountPoolSettingsView(
            version=0,
            values=defaults,
            updated_at=now,
        )
        self._history: tuple[AccountPoolSettingsHistoryEntry, ...] = (
            AccountPoolSettingsHistoryEntry(version=0, values=defaults, created_at=now, source="initial"),
        )

    async def initialize(self) -> None:
        return None

    async def get(self) -> AccountPoolSettingsView:
        return self._view

    async def save(self, request: AccountPoolSettingsUpdate) -> AccountPoolSettingsView | None:
        if request.version != self._view.version:
            return None
        now: Final = datetime.now(timezone.utc)
        saved: Final = AccountPoolSettingsView(
            version=request.version + 1,
            values=request.values,
            updated_at=now,
            requires_reload=(
                request.values.file_logging_enabled
                or request.values.websocket_enabled
                or request.values.plugins_enabled
            ),
        )
        self._view = saved
        self._history = (
            AccountPoolSettingsHistoryEntry(
                version=saved.version,
                values=saved.values,
                created_at=now,
                source="update",
            ),
            *self._history,
        )
        return saved

    async def history(self) -> tuple[AccountPoolSettingsHistoryEntry, ...]:
        return self._history

    async def rollback(self, expected_version: int, target_version: int) -> AccountPoolSettingsView | None:
        if expected_version != self._view.version:
            return None
        target: Final = next((entry for entry in self._history if entry.version == target_version), None)
        if target is None:
            return None
        now: Final = datetime.now(timezone.utc)
        saved: Final = AccountPoolSettingsView(
            version=expected_version + 1,
            values=target.values,
            updated_at=now,
            requires_reload=(
                target.values.file_logging_enabled
                or target.values.websocket_enabled
                or target.values.plugins_enabled
            ),
        )
        self._view = saved
        self._history = (
            AccountPoolSettingsHistoryEntry(
                version=saved.version,
                values=saved.values,
                created_at=now,
                source="rollback",
            ),
            *self._history,
        )
        return saved


class MemoryEnvironments:
    def __init__(self, *card_ids: UUID) -> None:
        self._card_ids: Final = card_ids

    async def list(self) -> tuple[object, ...]:
        return tuple(type("Card", (), {"id": card_id})() for card_id in self._card_ids)

    async def get(self, _: UUID) -> None:
        return None


def test_settings_preview_reports_changes_and_affected_cards() -> None:
    card_id: Final = uuid4()
    current: Final = AccountPoolSettingsView(version=3, values=AccountPoolSettings())
    proposed: Final = AccountPoolSettings(default_concurrency_limit=4, plugins_enabled=True)

    preview: Final = settings_preview(current, proposed, (card_id,))

    assert preview.base_version == 3
    assert preview.affected_card_ids == (card_id,)
    assert {change.key for change in preview.changes} == {"default_concurrency_limit", "plugins_enabled"}
    assert preview.requires_reload is True


def test_settings_preview_requires_reload_when_runtime_features_are_disabled() -> None:
    current: Final = AccountPoolSettingsView(
        version=4,
        values=AccountPoolSettings(file_logging_enabled=True, websocket_enabled=True, plugins_enabled=True),
    )

    preview: Final = settings_preview(current, AccountPoolSettings(), ())

    assert preview.requires_reload is True


def test_streaming_rules_reject_duplicate_card_assignments() -> None:
    card_id: Final = uuid4()
    try:
        AccountPoolSettings(
            streaming_rules=(
                StreamingRule(id="one", name="允许", card_ids=(card_id,)),
                StreamingRule(id="two", name="禁止", card_ids=(card_id,)),
            )
        )
    except ValueError as error:
        assert "multiple streaming rules" in str(error)
    else:
        raise AssertionError("duplicate streaming rule assignments must be rejected")


def test_settings_management_api_supports_conflict_preview_history_and_rollback() -> None:
    repository: Final = MemorySettingsRepository()
    card_id: Final = uuid4()
    app: Final = FastAPI()
    app.include_router(
        create_management_router(
            object(),
            object(),
            MemoryEnvironments(card_id),
            lambda: None,
            object(),
            repository,
        )
    )

    with TestClient(app) as client:
        initial: Final = client.get("/api/settings")
        assert initial.status_code == 200
        assert initial.json()["version"] == 0
        update: Final = client.put(
            "/api/settings",
            json={
                "version": 0,
                "values": {
                    "default_route": "priority",
                    "default_concurrency_limit": 4,
                    "default_model_discovery": True,
                    "default_proxy_profile_id": None,
                    "max_attempts": 1,
                    "request_timeout_seconds": 120,
                    "file_logging_enabled": False,
                    "debug_logging_enabled": False,
                    "websocket_enabled": False,
                    "plugins_enabled": True,
                },
            },
        )
        assert update.status_code == 200
        assert update.json()["version"] == 1
        assert update.json()["requires_reload"] is True
        assert client.put("/api/settings", json={"version": 0, "values": {}}).status_code == 409
        preview: Final = client.post(
            "/api/settings/preview",
            json={
                "version": 1,
                "values": {
                    "default_route": "auto",
                    "default_concurrency_limit": 1,
                    "default_model_discovery": True,
                    "default_proxy_profile_id": None,
                    "max_attempts": 1,
                    "request_timeout_seconds": 120,
                    "file_logging_enabled": False,
                    "debug_logging_enabled": False,
                    "websocket_enabled": False,
                    "plugins_enabled": False,
                },
            },
        )
        assert preview.status_code == 200
        assert {item["key"] for item in preview.json()["changes"]} == {
            "default_route",
            "default_concurrency_limit",
            "plugins_enabled",
        }
        history: Final = client.get("/api/settings/history")
        assert [item["version"] for item in history.json()] == [1, 0]
        rollback: Final = client.post(
            "/api/settings/rollback",
            json={"expected_version": 1, "target_version": 0},
        )
        assert rollback.status_code == 200
        assert rollback.json()["version"] == 2
        assert rollback.json()["values"]["default_route"] == "auto"
