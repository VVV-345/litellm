"""本文件验证卡片凭据隔离、日志脱敏及版本化策略的管理接口。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final, Literal
from uuid import UUID, uuid4

import httpx
import pytest
from account_pool.api import _validate_upload_filename, create_router
from account_pool.card_keys import CardKeyRecord, CardKeyService, matches_card_key
from account_pool.domain import EnvironmentRecord, EnvironmentStatus, utc_now
from account_pool.error_logs import (
    MODEL_REQUEST_OPERATION,
    ErrorLogDetail,
    ErrorLogPage,
    ErrorLogQuery,
    ErrorLogRecord,
    ErrorLogService,
    ErrorStats,
)
from account_pool.management_api import create_management_router
from account_pool.policies import AccountPolicy, PolicyUpdate, PolicyView
from account_pool.quota import ProviderEndpointFailure, ProviderQuotaError
from account_pool.quota_scheduler import QuotaRefreshScheduler
from account_pool.result import Failure, Success
from account_pool.secrets import EnvironmentSecretDeriver
from account_pool.service import EnvironmentService, _plugin_store_approves
from account_pool.settings import AccountPoolSettings, AccountPoolSettingsUpdate, AccountPoolSettingsView
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from test_account_pool import (
    EmptyProfiles,
    FakeCLIProxy,
    FakeRuntime,
    MemoryRepository,
    _fake_channels,
    _record,
    _settings,
)


@pytest.mark.parametrize("filename", ("../service-account.json", "..\\service-account.json", ".."))
def test_credential_upload_filename_rejects_path_components(filename: str) -> None:
    with pytest.raises(HTTPException) as error:
        _validate_upload_filename(filename)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "plugin_id,version,source,approved",
    (
        ("sample", "1.2.3", "official", True),
        ("sample", "1.2.3", None, False),
        ("sample", "0.9.0", "official", True),
        ("sample", "v1.2.3", "official", True),
        ("sample", "../../plugin", "official", False),
        ("missing", "1.2.3", "official", False),
        ("sample", "1.2.3", "unknown", False),
    ),
)
def test_card_plugin_install_requires_an_approved_id_source_and_safe_version(
    plugin_id: str, version: str, source: str | None, approved: bool
) -> None:
    store: Final = {
        "plugins": [
            {"id": "sample", "version": "1.2.3", "source_id": "official"},
            {"id": "sample", "version": "1.2.3", "source_id": "community"},
        ]
    }
    assert _plugin_store_approves(store, plugin_id, version, source) is approved


def test_card_plugin_install_allows_omitted_source_for_unique_plugin() -> None:
    store: Final = {"plugins": [{"id": "sample", "version": "1.2.3", "source_id": "official"}]}
    assert _plugin_store_approves(store, "sample", "0.9.0", None)


class MemoryKeys:
    def __init__(self) -> None:
        self.records: dict[UUID, CardKeyRecord] = {}

    async def get(self, card_id: UUID) -> CardKeyRecord | None:
        return self.records.get(card_id)

    async def find_by_hash(self, key_hash: str) -> CardKeyRecord | None:
        return next((record for record in self.records.values() if record.key_hash == key_hash), None)

    async def save(self, record: CardKeyRecord, expected_key_id: UUID | None) -> bool:
        current: Final = self.records.get(record.card_id)
        if expected_key_id is None and current is not None and current.revoked_at is None:
            return False
        if expected_key_id is not None and (current is None or current.key_id != expected_key_id or current.revoked_at):
            return False
        self.records[record.card_id] = record
        return True

    async def revoke(self, card_id: UUID, expected_key_id: UUID, revoked_at: datetime) -> bool:
        current: Final = self.records.get(card_id)
        if current is None or current.key_id != expected_key_id:
            return False
        self.records[card_id] = replace(current, revoked_at=current.revoked_at or revoked_at)
        return True


class MemoryLogs:
    def __init__(self) -> None:
        self.events: tuple[ErrorLogRecord, ...] = ()

    async def append(self, event: ErrorLogRecord) -> None:
        self.events = (*self.events, event)

    async def query(self, query: ErrorLogQuery) -> ErrorLogPage:
        return ErrorLogPage(
            items=tuple(
                event
                for event in self.events
                if (query.card_id is None or event.card_id == query.card_id)
                and (query.occurred_from is None or event.occurred_at >= query.occurred_from)
            ),
            has_more=False,
        )

    async def detail(self, event_id: UUID) -> ErrorLogDetail | None:
        event: Final = next((item for item in self.events if item.event_id == event_id), None)
        return None if event is None else ErrorLogDetail(event=event, attempts=(event,), has_more=False)

    async def stats(
        self, card_id: UUID | None, account_id: UUID | None, model: str | None, query: ErrorLogQuery | None = None
    ) -> ErrorStats:
        matching: Final = tuple(
            event
            for event in self.events
            if event.operation == MODEL_REQUEST_OPERATION
            and (card_id is None or event.card_id == card_id)
            and (account_id is None or event.account_id == account_id)
            and (model is None or event.model == model)
        )
        return ErrorStats(
            card_id=card_id,
            account_id=account_id,
            model=model,
            total_requests=len(matching),
            succeeded_requests=sum(event.final_status == "succeeded" for event in matching),
            failed_requests=sum(event.final_status == "failed" for event in matching),
            retried_requests=sum(event.retry_count > 0 for event in matching),
            input_tokens=sum(event.input_tokens or 0 for event in matching),
            output_tokens=sum(event.output_tokens or 0 for event in matching),
            known_cost_requests=sum(event.cost_usd is not None for event in matching),
            total_cost_usd=(
                sum(event.cost_usd or 0 for event in matching)
                if any(event.cost_usd is not None for event in matching)
                else None
            ),
            recent_errors=tuple(event for event in matching if event.final_status == "failed")[-10:],
        )

    async def prune(self, before: datetime) -> None:
        self.events = tuple(event for event in self.events if event.occurred_at >= before)

    async def clear(self) -> int:
        deleted: Final = len(self.events)
        self.events = ()
        return deleted


class MemoryPolicies:
    def __init__(self) -> None:
        self.records: dict[UUID, PolicyView] = {}

    async def list(self) -> tuple[PolicyView, ...]:
        return tuple(self.records.values())

    async def get(self, card_id: UUID) -> PolicyView:
        return self.records.get(card_id, PolicyView(card_id=card_id))

    async def save(self, card_id: UUID, request: PolicyUpdate) -> PolicyView | None:
        current: Final = await self.get(card_id)
        if request.version != current.version:
            return None
        saved: Final = PolicyView(card_id=card_id, version=current.version + 1, policy=request.policy)
        self.records[card_id] = saved
        return saved

    async def set_runtime_status(
        self,
        card_id: UUID,
        version: int,
        status: Literal["partial", "synced", "failed"],
        error: str | None = None,
    ) -> PolicyView | None:
        current: Final = self.records.get(card_id)
        if current is None or current.version != version:
            return None
        saved: Final = current.model_copy(
            update={
                "runtime_status": status,
                "runtime_error": error,
                "runtime_updated_at": datetime.now(timezone.utc),
            }
        )
        self.records[card_id] = saved
        return saved


@pytest.mark.asyncio
async def test_key_rotation_rejects_stale_and_cross_card_changes() -> None:
    repository: Final = MemoryKeys()
    service: Final = CardKeyService(repository)
    a, b = uuid4(), uuid4()
    first: Final = await service.issue(a)
    other: Final = await service.issue(b)
    assert isinstance(first, Success) and isinstance(other, Success)
    assert isinstance(await service.issue(a), Failure)
    stored: Final = await repository.get(a)
    assert stored is not None
    assert first.value.key not in repr(stored)
    assert matches_card_key(first.value.key, stored)
    assert not matches_card_key(other.value.key, stored)
    assert isinstance(await service.issue(a, other.value.status.key_id), Failure)
    results: Final = await asyncio.gather(*(service.issue(a, first.value.status.key_id) for _ in range(2)))
    assert sum(isinstance(result, Success) for result in results) == 1
    updated: Final = await repository.get(a)
    assert updated is not None and not matches_card_key(first.value.key, updated)
    assert isinstance(await service.revoke(a, first.value.status.key_id), Failure)
    assert isinstance(await service.revoke(a, updated.key_id), Success)
    revoked: Final = await repository.get(a)
    assert revoked is not None and revoked.revoked_at is not None
    assert isinstance(await service.issue(a), Success)


class MemoryRefreshSettings:
    def __init__(self) -> None:
        self.view: AccountPoolSettingsView = AccountPoolSettingsView(version=0, values=AccountPoolSettings())

    async def get(self) -> AccountPoolSettingsView:
        return self.view

    async def save(self, request: AccountPoolSettingsUpdate) -> AccountPoolSettingsView | None:
        if request.version != self.view.version:
            return None
        saved: Final = AccountPoolSettingsView(version=request.version + 1, values=request.values)
        self.view = saved
        return saved


def test_auth_file_refresh_endpoints_return_status_update_interval_and_refresh(tmp_path: Path) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    environments: Final = MemoryRepository(record)
    runtime: Final = FakeRuntime()
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        _settings(tmp_path),
        environments,
        runtime,
        cli,
        EmptyProfiles(),
        EnvironmentSecretDeriver("s" * 32),
        channels=_fake_channels(runtime, cli),
    )
    settings: Final = MemoryRefreshSettings()
    scheduler: Final = QuotaRefreshScheduler(
        settings,
        service.refresh_auth_files,
        interval=lambda values: values.auth_refresh_interval_minutes,
    )
    app: Final = FastAPI()
    app.include_router(
        create_router(
            service,
            "m" * 32,
            environments=environments,
            settings=settings,
            auth_refresh_scheduler=scheduler,
        )
    )

    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + "m" * 32
        initial: Final = client.get("/api/auth-files/refresh/status")
        updated: Final = client.put("/api/auth-files/refresh/interval", json={"interval_minutes": 30})
        refreshed: Final = client.post("/api/auth-files/refresh")

    assert initial.status_code == 200
    assert initial.json()["interval_minutes"] == 15
    assert updated.status_code == 200
    assert updated.json()["interval_minutes"] == 30
    assert refreshed.status_code == 200
    assert cli.refresh_quota_calls == [False]


@pytest.fixture
def management(tmp_path):
    record: Final = _record(status=EnvironmentStatus.READY)
    environments: Final = MemoryRepository(record)
    keys: Final = MemoryKeys()
    logs: Final = MemoryLogs()
    runtime: Final = FakeRuntime()
    cli: Final = FakeCLIProxy()
    service: Final = EnvironmentService(
        _settings(tmp_path),
        environments,
        runtime,
        cli,
        EmptyProfiles(),
        EnvironmentSecretDeriver("s" * 32),
        channels=_fake_channels(runtime, cli),
        error_logs=ErrorLogService(logs),
    )
    app: Final = FastAPI()
    app.include_router(
        create_router(
            service,
            "m" * 32,
            keys=CardKeyService(keys),
            logs=ErrorLogService(logs),
            environments=environments,
            policies=MemoryPolicies(),
        )
    )
    with TestClient(app) as client:
        yield client, record, keys, logs, service


def test_manager_auth_and_single_disclosure(management) -> None:
    client, record, keys, logs, _ = management
    path: Final = f"/api/cards/{record.id}/key"
    assert client.post(path).status_code == 401
    client.headers["Authorization"] = "Bearer " + "m" * 32
    response: Final = client.post(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    status: Final = client.get(path + "/status")
    assert status.json()["key_id"] == response.json()["status"]["key_id"]
    assert response.json()["key"] not in status.text
    assert "key_hash" not in status.text
    assert response.json()["key"] not in str(logs.events)
    assert client.post(path).status_code == 409
    assert client.post(f"/api/cards/{uuid4()}/key").status_code == 404


def test_policy_versions_and_supplier_scope(management) -> None:
    client, record, _, _, _ = management
    client.headers["Authorization"] = "Bearer " + "m" * 32
    path: Final = f"/api/environments/{record.id}/policy"
    first: Final = client.put(
        path, json={"version": 0, "policy": {"tags": [" test ", "test"], "routing": {"weight": 4}}}
    )
    assert first.status_code == 200
    assert first.json()["policy"]["tags"] == ["test"]
    assert first.json()["runtime_status"] == "partial"
    capabilities: Final = {item["name"]: item["status"] for item in first.json()["capabilities"]}
    assert capabilities["responses_compact"] == "gateway"
    assert "desktop_compact" not in capabilities
    assert capabilities["identity"] == "gateway"
    assert capabilities["provider_settings"] == "gateway"
    assert capabilities["plan_expiry"] == "gateway"
    assert client.put(path, json={"version": 0, "policy": {}}).status_code == 409
    assert client.get(path).json()["policy"]["routing"]["weight"] == 4
    assert client.put(path, json={"version": 1, "policy": {"unknown_setting": True}}).status_code == 422
    migrated: Final = client.put(
        path,
        json={
            "version": 1,
            "policy": {
                "openai_compatible": {"support_prompt_cache_key": True},
                "codex": {
                    "responses_compact_enabled": True,
                    "compact_ui": True,
                    "model_context_window": 200000,
                    "model_auto_compact_token_limit": 180000,
                    "experimental_context_management": True,
                },
            },
        },
    )
    assert migrated.status_code == 200
    assert "openai_compatible" not in migrated.json()["policy"]
    assert migrated.json()["policy"]["codex"] == {
        "identity_fingerprint_mode": "off",
        "cli_only": False,
        "allow_app_server": False,
        "allow_app_server_clients": [],
        "responses_compact_enabled": True,
        "identity_confuse": False,
        "disable_codex_cloaking": False,
    }
    for provider_field in ("claude", "kimi", "xai", "antigravity"):
        assert client.put(path, json={"version": 2, "policy": {provider_field: {}}}).status_code == 422
    assert (
        client.put(
            path, json={"version": 2, "policy": {"model_aliases": [{"alias": "alias", "target": "missing"}]}}
        ).status_code
        == 422
    )
    assert client.put(path, json={"version": 2, "policy": {"routing": {"strategy": "plan"}}}).status_code == 200
    assert client.put(path, json={"version": 3, "policy": {"transport": {"websocket": "enabled"}}}).status_code == 200
    assert (
        client.put(path, json={"version": 4, "policy": {"transport": {"debug_log_enabled": True}}}).status_code == 200
    )
    assert client.put(path, json={"version": 5, "policy": {"account_ids": [str(uuid4())]}}).status_code == 422
    assert (
        client.put(
            path,
            json={
                "version": 5,
                "policy": {
                    "routing": {"preferred_account_ids": [str(uuid4())]},
                },
            },
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    ("sync_fails", "expected_status_code", "expected_runtime_status"),
    ((False, 200, "synced"), (True, 502, "failed")),
)
def test_policy_update_persists_runtime_synchronization_status(
    sync_fails: bool,
    expected_status_code: int,
    expected_runtime_status: str,
) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    environments: Final = MemoryRepository(record)
    policies: Final = MemoryPolicies()

    async def sync_policy(_: EnvironmentRecord, __: AccountPolicy) -> None:
        if sync_fails:
            raise RuntimeError("runtime synchronization failed")

    app: Final = FastAPI()
    app.include_router(
        create_management_router(
            object(),
            object(),
            environments,
            lambda: None,
            policies,
            sync_policy=sync_policy,
        )
    )

    with TestClient(app) as client:
        response: Final = client.put(
            f"/api/environments/{record.id}/policy",
            json={"version": 0, "policy": {"routing": {"strategy": "priority"}}},
        )

    assert response.status_code == expected_status_code
    stored: Final = policies.records[record.id]
    assert stored.runtime_status == expected_runtime_status
    assert stored.runtime_error == ("policy runtime synchronization failed" if sync_fails else None)
    assert stored.runtime_updated_at is not None


@pytest.mark.parametrize(
    "secret_text,secret",
    [
        ('{"refresh_token":"secret-refresh"}', "secret-refresh"),
        ("Bearer private-bearer", "private-bearer"),
        ("https://u:proxy-password@example.test/path?code=oauth-code", "proxy-password"),
        ("card key cpk_private-key", "cpk_private-key"),
        ('{"Authorization": "Basic private-basic"}', "private-basic"),
        ('{"password":"secret-pass"}', "secret-pass"),
    ],
)
def test_logs_redact_credentials_in_every_free_text_field(secret_text: str, secret: str) -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    event: Final = ErrorLogRecord(
        channel=record.channel,
        supplier=record.supplier,
        card_id=record.id,
        environment_id=record.id,
        account_id=record.id,
        operation="configuration",
        stage="configuration",
        message=secret_text,
        detail=secret_text,
        model=secret_text,
    )
    assert secret not in event.model_dump_json()
    assert "[redacted]" in event.model_dump_json()


def test_logs_keep_retired_channel_records_readable() -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    current: Final = ErrorLogRecord(
        channel=record.channel,
        supplier=record.supplier,
        card_id=record.id,
        environment_id=record.id,
        account_id=record.id,
        operation="configuration",
        stage="configuration",
        message="Historical event",
    )
    payload: Final = {
        **current.model_dump(mode="json"),
        "channel": "freebuff2api",
        "supplier": "freebuff",
    }

    restored: Final = ErrorLogRecord.model_validate(payload)

    assert restored.channel == "freebuff2api"
    assert restored.supplier == "freebuff"


@pytest.mark.asyncio
async def test_error_capture_preserves_context_and_http_status(management) -> None:
    client, record, _, logs, service = management
    request: Final = httpx.Request("GET", "https://example.test")
    error: Final = httpx.HTTPStatusError(
        "Bearer secret-value", request=request, response=httpx.Response(429, request=request)
    )
    await service._log_event(record, "quota", error, retryable=True)
    event: Final = logs.events[-1]
    assert (event.card_id, event.channel, event.supplier) == (record.id, record.channel, record.supplier)
    assert event.http_status == 429 and event.error_category == "rate_limit"
    assert event.final_status == "retrying"
    await logs.append(
        ErrorLogRecord(
            channel=record.channel,
            supplier=record.supplier,
            card_id=record.id,
            environment_id=record.id,
            account_id=record.id,
            operation=MODEL_REQUEST_OPERATION,
            stage="response",
            model="gpt-5",
            endpoint="/v1/responses",
            method="POST",
            severity="info",
            http_status=200,
            message="Request completed",
            duration_ms=20,
            input_tokens=10,
            output_tokens=5,
            routing_reason="preferred_account",
            cost_usd=0.00042,
            final_status="succeeded",
        )
    )
    client.headers["Authorization"] = "Bearer " + "m" * 32
    assert client.get("/api/logs", params={"card_id": str(record.id)}).json()["items"][0]["event_id"] == str(
        event.event_id
    )
    assert (
        client.get("/api/logs", params={"occurred_from": (utc_now() + timedelta(days=1)).isoformat()}).json()["items"]
        == []
    )
    assert client.get(f"/api/logs/{event.event_id}").json()["event"]["card_id"] == str(record.id)
    stats: Final = client.get("/api/stats", params={"card_id": str(record.id)}).json()
    assert stats["total_requests"] == 1
    assert stats["input_tokens"] == 10 and stats["output_tokens"] == 5
    assert stats["known_cost_requests"] == 1 and stats["total_cost_usd"] == 0.00042
    assert stats["recent_errors"] == []
    dashboard: Final = client.get("/api/dashboard").json()
    assert dashboard["summary"]["total_requests"] == 1
    assert dashboard["cards"][0]["card_id"] == str(record.id)
    assert client.get(f"/api/logs/{uuid4()}").status_code == 404
    assert client.get("/api/logs?limit=5000").status_code == 422
    assert client.get("/api/logs?occurred_from=invalid").status_code == 422
    assert (
        client.get("/api/logs?occurred_from=2026-09-11T00:00:00Z&occurred_to=2026-09-10T00:00:00Z").status_code == 422
    )


@pytest.mark.asyncio
async def test_provider_quota_error_logs_safe_endpoint_status_and_request_id(management) -> None:
    _, record, _, logs, service = management
    error: Final = ProviderQuotaError(
        (
            ProviderEndpointFailure(
                method="GET",
                endpoint="https://api.anthropic.com/api/oauth/usage",
                message="provider quota endpoint rejected the request",
                status_code=429,
                request_id="req-safe",
                upstream_code="rate_limit_error",
            ),
        ),
        "Claude quota refresh failed",
    )

    await service._log_event(record, "quota", error)

    event: Final = logs.events[-1]
    assert event.endpoint == "https://api.anthropic.com/api/oauth/usage"
    assert event.method == "GET"
    assert event.http_status == 429
    assert event.upstream_request_id == "req-safe"
    assert event.upstream_code == "rate_limit_error"
    assert event.error_category == "rate_limit"
    assert event.retryable is True
    assert "req-safe" in event.message
