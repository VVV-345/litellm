"""本文件验证号池全局配置的版本控制、差异预览和回滚接口。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final
from uuid import UUID, uuid4

import pytest
from account_pool.management_api import create_management_router
from account_pool.settings import (
    AccessSettingsProfile,
    AccessSettingsValues,
    AccountPoolSettings,
    AccountPoolSettingsHistoryEntry,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
    AdvancedSettingsProfile,
    AdvancedSettingsValues,
    CommonSettingsProfile,
    CommonSettingsValues,
    NetworkSettingsProfile,
    NetworkSettingsValues,
    OAuthRequestScopedErrorRule,
    PayloadModelRule,
    PayloadRule,
    PayloadSettings,
    QuotaSettingsProfile,
    QuotaSettingsValues,
    StreamingRule,
    StreamingSettingsProfile,
    StreamingSettingsValues,
    settings_for_card,
    settings_preview,
    streaming_mode_for_card,
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
                target.values.file_logging_enabled or target.values.websocket_enabled or target.values.plugins_enabled
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


def test_named_profiles_reject_duplicate_cards_within_each_module() -> None:
    card_id: Final = uuid4()

    with pytest.raises(ValueError, match="multiple network profiles"):
        AccountPoolSettings(
            network_profiles=(
                NetworkSettingsProfile(id="one", name="线路一", card_ids=(card_id,)),
                NetworkSettingsProfile(id="two", name="线路二", card_ids=(card_id,)),
            )
        )

    settings: Final = AccountPoolSettings(
        common_profiles=(CommonSettingsProfile(id="common", name="常用", card_ids=(card_id,)),),
        network_profiles=(NetworkSettingsProfile(id="network", name="网络", card_ids=(card_id,)),),
    )
    assert settings.common_profiles[0].card_ids == settings.network_profiles[0].card_ids


def test_settings_for_card_resolves_custom_modules_and_global_inheritance() -> None:
    card_id: Final = uuid4()
    settings: Final = AccountPoolSettings(
        default_route="quota",
        default_concurrency_limit=6,
        default_proxy_profile_id="global-proxy",
        max_attempts=2,
        oauth_excluded_models=("global-model",),
        quota_switch_project=True,
        plugins_enabled=True,
        streaming_enabled=True,
        common_profiles=(
            CommonSettingsProfile(
                id="common",
                name="高并发",
                card_ids=(card_id,),
                inherit_global=False,
                values=CommonSettingsValues(
                    default_route="priority",
                    default_concurrency_limit=20,
                    default_model_discovery=False,
                ),
            ),
        ),
        access_profiles=(
            AccessSettingsProfile(
                id="access",
                name="认证",
                card_ids=(card_id,),
                inherit_global=False,
                values=AccessSettingsValues(oauth_excluded_models=("card-model",)),
            ),
        ),
        network_profiles=(
            NetworkSettingsProfile(
                id="network",
                name="继承网络",
                card_ids=(card_id,),
                inherit_global=True,
                values=NetworkSettingsValues(default_proxy_profile_id="ignored", max_attempts=5),
            ),
        ),
        quota_profiles=(
            QuotaSettingsProfile(
                id="quota",
                name="独立配额",
                card_ids=(card_id,),
                inherit_global=False,
                values=QuotaSettingsValues(quota_switch_preview_model=True),
            ),
        ),
        streaming_profiles=(
            StreamingSettingsProfile(
                id="streaming",
                name="禁止流式",
                card_ids=(card_id,),
                inherit_global=False,
                values=StreamingSettingsValues(enabled=False),
            ),
        ),
        advanced_profiles=(
            AdvancedSettingsProfile(
                id="advanced",
                name="关闭插件",
                card_ids=(card_id,),
                inherit_global=False,
                values=AdvancedSettingsValues(plugins_enabled=False, request_retry=4),
            ),
        ),
    )

    effective: Final = settings_for_card(settings, card_id)

    assert effective.default_route == "priority"
    assert effective.default_concurrency_limit == 20
    assert effective.oauth_excluded_models == ("card-model",)
    assert effective.default_proxy_profile_id == "global-proxy"
    assert effective.max_attempts == 2
    assert effective.quota_switch_project is False
    assert effective.quota_switch_preview_model is True
    assert effective.plugins_enabled is False
    assert effective.request_retry == 4
    assert streaming_mode_for_card(settings, card_id) == "disabled"
    assert streaming_mode_for_card(settings, uuid4()) == "inherit"


def test_payload_and_oauth_error_rules_use_cliproxy_wire_names() -> None:
    settings: Final = AccountPoolSettings(
        oauth_request_scoped_errors={
            "codex": (OAuthRequestScopedErrorRule(status=400, match_regexr=("context.*window",), action="stop"),)
        },
        payload=PayloadSettings(
            default_raw=(
                PayloadRule(
                    models=(PayloadModelRule(name="gpt-*", from_protocol="responses", not_exist=("metadata.skip",)),),
                    params={"metadata.source": '"pool"'},
                ),
            )
        ),
    )

    payload: Final = settings.model_dump(mode="json", by_alias=True)

    assert payload["oauth_request_scoped_errors"]["codex"][0]["match-regexr"] == ["context.*window"]
    assert payload["payload"]["default-raw"][0]["models"][0] == {
        "name": "gpt-*",
        "protocol": "",
        "headers": {},
        "from-protocol": "responses",
        "match": [],
        "not-match": [],
        "exist": [],
        "not-exist": ["metadata.skip"],
    }


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
