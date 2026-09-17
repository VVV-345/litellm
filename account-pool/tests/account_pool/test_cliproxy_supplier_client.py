"""测试 CLIProxyAPI 按供应商选择管理端点和凭据。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Final
from uuid import uuid4

import httpx
import pytest
import yaml
from account_pool.channels.cliproxyapi.client import AuthorizationStart, HttpCLIProxyClient
from account_pool.channels.cliproxyapi.settings_sync import CLIProxySettingsSynchronizer
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.domain import (
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    OAuthCallback,
    Provider,
    ProxyMode,
    QuotaSnapshot,
    QuotaWindow,
    SupplierKind,
    utc_now,
)
from account_pool.policies import AccountPolicy, AntigravityPolicy, ClaudePolicy, CodexPolicy, KimiPolicy, XaiPolicy
from account_pool.secrets import EnvironmentSecretDeriver
from account_pool.settings import (
    AccountPoolSettings,
    OAuthRequestScopedErrorRule,
    PayloadModelRule,
    PayloadRule,
    PayloadSettings,
)


def _record() -> EnvironmentRecord:
    now: Final = utc_now()
    return EnvironmentRecord(
        id=uuid4(),
        name="test",
        provider=Provider.OPENAI,
        status=EnvironmentStatus.READY,
        enabled=True,
        manual_cooldown=False,
        concurrency_limit=2,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=("model-a",),
        enabled_models=("model-a",),
        auth_file_name=None,
        auth_index=None,
        quota=QuotaSnapshot(),
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected_path",
    (
        (SupplierKind.OPENAI_CODEX, "/v0/management/codex-auth-url"),
        (SupplierKind.ANTHROPIC_CLAUDE, "/v0/management/anthropic-auth-url"),
        (SupplierKind.GOOGLE_ANTIGRAVITY, "/v0/management/antigravity-auth-url"),
        (SupplierKind.KIMI, "/v0/management/kimi-auth-url"),
        (SupplierKind.XAI, "/v0/management/xai-auth-url"),
    ),
)
async def test_start_authorization_uses_exact_supplier_endpoint(kind: SupplierKind, expected_path: str) -> None:
    record: Final = _record()
    supplier: Final = SupplierRegistry.default().get(kind)
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if supplier.authorization_flow.value == "device_code":
            return httpx.Response(
                200, json={"status": "ok", "state": "state", "user_code": "code", "expires_in": 600}, request=request
            )
        return httpx.Response(
            200, json={"status": "ok", "url": "https://example.test/auth", "state": "state"}, request=request
        )

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    result: Final = await proxy.start_authorization(record, supplier)
    await client.aclose()

    assert paths == [expected_path]
    assert isinstance(result, AuthorizationStart)
    if kind in (SupplierKind.KIMI, SupplierKind.XAI):
        assert result.user_code == "code"
        assert result.expires_in_seconds == 600


@pytest.mark.asyncio
async def test_read_account_selects_matching_type_and_model_file() -> None:
    record: Final = _record()
    supplier: Final = SupplierRegistry.default().get(SupplierKind.ANTHROPIC_CLAUDE)
    model_names: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {"name": "wrong.json", "provider": "other", "type": "other"},
                        {"name": "selected.json", "provider": "other", "type": "claude"},
                    ]
                },
                request=request,
            )
        model_names.append(request.url.params["name"])
        return httpx.Response(200, json={"models": [{"id": "claude-model"}]}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier)
    await client.aclose()

    assert observed.auth_file_name == "selected.json"
    assert observed.available_models == ("claude-model",)
    assert model_names == ["selected.json"]


@pytest.mark.asyncio
@pytest.mark.parametrize("aggregate", (False, True))
async def test_model_cooldown_snapshot_is_isolated_from_healthy_models_and_credentials(aggregate: bool) -> None:
    record = _record().model_copy(update={"auth_file_name": "selected.json"})
    retry_at = "2099-09-17T15:00:00+00:00"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth-files"):
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "selected.json",
                            "provider": "codex",
                            "unavailable": aggregate,
                            "next_retry_after": retry_at if aggregate else None,
                            "model_states": {
                                "model-a": {
                                    "unavailable": True,
                                    "next_retry_after": retry_at,
                                    "reason": "upstream_error",
                                },
                                "model-b": {"unavailable": False},
                            },
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"models": [{"id": "model-a"}, {"id": "model-b"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
        snapshot = await proxy.read_account(record, SupplierRegistry.default().get(SupplierKind.OPENAI_CODEX))
        runtime = await proxy.read_model_cooldowns(record)
    assert snapshot.status == EnvironmentStatus.READY
    assert not snapshot.automatic_cooldown
    assert len(snapshot.model_cooldowns) == 1
    assert snapshot.model_cooldowns == runtime
    assert runtime[0].model == "model-a" and runtime[0].reason == "upstream_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,retry_at,disabled,cooling",
    (
        ("server_is_overloaded", "2000-01-01T00:00:00Z", False, False),
        ("server_is_overloaded", "2099-01-01T00:00:00Z", False, True),
        ("server_is_overloaded", "2000-01-01T00:00:00Z", True, True),
        ("refresh_token_reused", "2000-01-01T00:00:00Z", False, True),
        ("unknown", "2000-01-01T00:00:00Z", False, True),
    ),
)
async def test_expired_overload_allows_recovery_without_clearing_credential_failures(
    code: str, retry_at: str, disabled: bool, cooling: bool
) -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "selected.json"})

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth-files"):
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "selected.json",
                            "provider": "codex",
                            "unavailable": True,
                            "disabled": disabled,
                            "next_retry_after": retry_at,
                            "status_message": json.dumps({"error": {"code": code}}),
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"models": [{"id": "model-a"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
        snapshot: Final = await proxy.read_account(record, SupplierRegistry.default().get(SupplierKind.OPENAI_CODEX))
    assert snapshot.automatic_cooldown is cooling
    assert snapshot.status == (EnvironmentStatus.COOLING_DOWN if cooling else EnvironmentStatus.READY)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ("auth_unavailable", "refresh_token_invalidated", "refresh_token_reused"))
@pytest.mark.parametrize("status", ("error", "active"))
async def test_authentication_error_blocks_false_available_credentials(code: str, status: str) -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "selected.json"})

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth-files"):
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "selected.json",
                            "provider": "codex",
                            "unavailable": False,
                            "status": status,
                            "status_message": json.dumps({"error": {"code": code}}),
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"models": [{"id": "model-a"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
        snapshot: Final = await proxy.read_account(record, SupplierRegistry.default().get(SupplierKind.OPENAI_CODEX))
    assert snapshot.automatic_cooldown is (status == "error")
    assert snapshot.status == (EnvironmentStatus.COOLING_DOWN if status == "error" else EnvironmentStatus.READY)
    assert snapshot.last_error == ("上游认证失效，请重新认证" if status == "error" else None)


@pytest.mark.asyncio
async def test_read_account_prefers_the_auth_file_already_bound_to_the_card() -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "uploaded.json"})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.OPENAI_CODEX)
    model_names: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {"name": "older.json", "provider": "codex", "type": "codex"},
                        {"name": "uploaded.json", "provider": "codex", "type": "codex"},
                    ]
                },
                request=request,
            )
        model_names.append(request.url.params["name"])
        return httpx.Response(200, json={"models": [{"id": "gpt-5"}]}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier)
    await client.aclose()

    assert observed.auth_file_name == "uploaded.json"
    assert model_names == ["uploaded.json"]


@pytest.mark.asyncio
async def test_read_account_preserves_last_active_quota_when_passive_metadata_is_empty() -> None:
    previous_quota: Final = QuotaSnapshot(
        observed_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        refresh_status="complete",
        windows=(
            QuotaWindow(
                name="Weekly",
                used_percent=25,
                remaining_percent=75,
                window_minutes=10080,
            ),
        ),
    )
    record: Final = _record().model_copy(
        update={"supplier": SupplierKind.XAI, "quota": previous_quota, "auth_file_name": "xai.json"}
    )
    supplier: Final = SupplierRegistry.default().get(SupplierKind.XAI)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={"files": [{"name": "xai.json", "provider": "xai", "auth_index": "xai-index"}]},
                request=request,
            )
        return httpx.Response(200, json={"models": [{"id": "grok-code-fast-1"}]}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier)
    await client.aclose()

    assert observed.quota == previous_quota


@pytest.mark.asyncio
async def test_read_account_keeps_active_quota_ahead_of_older_cliproxy_cache() -> None:
    previous_quota: Final = QuotaSnapshot(
        observed_at=datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc),
        source="provider_api",
        refresh_status="complete",
        windows=(
            QuotaWindow(
                name="5 hour",
                used_percent=10,
                remaining_percent=90,
                window_minutes=300,
            ),
        ),
    )
    record: Final = _record().model_copy(
        update={"supplier": SupplierKind.OPENAI_CODEX, "quota": previous_quota, "auth_file_name": "codex.json"}
    )
    supplier: Final = SupplierRegistry.default().get(SupplierKind.OPENAI_CODEX)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "codex.json",
                            "provider": "codex",
                            "quota": {
                                "observed_at": "2026-09-15T13:00:00Z",
                                "signals": {
                                    "x-codex-five-hour-used-percent": "69",
                                    "x-codex-five-hour-window-minutes": "300",
                                },
                            },
                        }
                    ]
                },
                request=request,
            )
        return httpx.Response(200, json={"models": [{"id": "gpt-5-codex"}]}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier)
    await client.aclose()

    assert tuple(window.remaining_percent for window in observed.quota.windows) == (90.0,)
    assert observed.quota.source == "provider_api"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "auth_file_plan_type", "expected_auth_file_plan_type"),
    (
        ("codex-user-prolite.json", None, "prolite"),
        ("codex-user-pro-max.json", None, "promax"),
        ("codex-user.json", "pro_lite", "prolite"),
    ),
)
async def test_read_codex_account_exposes_subscription_and_auth_file_plan(
    name: str, auth_file_plan_type: str | None, expected_auth_file_plan_type: str
) -> None:
    record: Final = _record()
    supplier: Final = SupplierRegistry.default().get(SupplierKind.OPENAI_CODEX)
    auth_file: Final = {
        "name": name,
        "provider": "codex",
        "id_token": {
            "plan_type": "pro",
            "chatgpt_subscription_active_until": "2090-01-02T03:04:05Z",
        },
        **({"auth_file_plan_type": auth_file_plan_type} if auth_file_plan_type is not None else {}),
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(200, json={"files": [auth_file]}, request=request)
        return httpx.Response(200, json={"models": [{"id": "gpt-5-codex"}]}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier)
    await client.aclose()

    assert observed.quota.plan_type == "pro"
    assert observed.quota.auth_file_plan_type == expected_auth_file_plan_type
    assert observed.quota.subscription_active_until is not None
    assert observed.quota.subscription_active_until.isoformat() == "2090-01-02T03:04:05+00:00"


@pytest.mark.asyncio
async def test_codex_quota_refresh_reads_wham_windows_and_reset_credits() -> None:
    record: Final = _record().model_copy(update={"supplier": SupplierKind.OPENAI_CODEX})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.OPENAI_CODEX)
    api_calls: list[dict[str, object]] = []
    active_calls: set[str] = set()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "codex.json",
                            "provider": "codex",
                            "auth_index": "codex-index",
                            "id_token": {
                                "chatgpt_account_id": "account-1",
                                "plan_type": "pro",
                                "chatgpt_subscription_active_start": "2026-09-01T00:00:00Z",
                                "chatgpt_subscription_active_until": "2026-10-01T00:00:00Z",
                            },
                        }
                    ]
                },
                request=request,
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(200, json={"models": [{"id": "gpt-5-codex"}]}, request=request)
        payload: Final = json.loads(request.content)
        assert not active_calls, "quota endpoints must not refresh the same credential concurrently"
        active_calls.add(str(payload["url"]))
        await asyncio.sleep(0)
        active_calls.remove(str(payload["url"]))
        api_calls.append(payload)
        url: Final = str(payload["url"])
        body: Final = (
            {
                "accounts": {
                    "workspace-1": {
                        "account": {"account_id": "account-1", "plan_type": "pro", "is_default": True},
                        "entitlement": {
                            "subscription_plan": "pro",
                            "active_start": "2026-09-01T00:00:00Z",
                            "expires_at": "2090-10-01T00:00:00Z",
                        },
                    }
                }
            }
            if "/accounts/check/" in url
            else {"available_count": 4}
            if url.endswith("/rate-limit-reset-credits")
            else {
                "plan_type": "pro",
                "rate_limit": {
                    "primary_window": {"used_percent": 10, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 20, "limit_window_seconds": 604800},
                },
                "code_review_rate_limit": {"primary_window": {"used_percent": 30, "limit_window_seconds": 18000}},
                "rate_limit_reset_credits": {"available_count": 3},
            }
        )
        return httpx.Response(
            200,
            json={
                "status_code": 200,
                "header": {"request-id": ["req-1"]},
                "body": json.dumps(body),
            },
            request=request,
        )

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier, refresh_quota=True)
    await client.aclose()

    assert tuple(window.name for window in observed.quota.windows) == ("5 hour", "Weekly", "Code review 5 hour")
    assert observed.quota.reset_credits_available == 4
    assert observed.quota.subscription_active_start is not None
    assert observed.quota.subscription_active_until is not None
    assert observed.quota.refresh_status == "complete"
    assert str(api_calls[0]["url"]).startswith(
        "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27?timezone_offset_min="
    )
    assert tuple(call["url"] for call in api_calls[1:]) == (
        "https://chatgpt.com/backend-api/wham/usage",
        "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits",
    )
    assert api_calls[1]["header"]["ChatGPT-Account-Id"] == "account-1"
    assert api_calls[1]["header"]["User-Agent"].endswith("Chrome/147.0.0.0 Safari/537.36")
    assert "OpenAI-Beta" not in api_calls[0]["header"]
    assert "Content-Type" not in api_calls[0]["header"]


@pytest.mark.asyncio
@pytest.mark.parametrize("same_credential,same_environment", ((True, True), (False, True), (True, False)))
async def test_provider_calls_serialize_only_the_same_credential(same_credential: bool, same_environment: bool) -> None:
    from account_pool.channels.cliproxyapi.client import _AuthFile

    record: Final = _record()
    other_record: Final = record if same_environment else _record()
    credential: Final = _AuthFile(name="codex.json", auth_index="first")
    other: Final = credential if same_credential else _AuthFile(name="other.json", auth_index="second")
    active: set[tuple[str, str]] = set()
    concurrency: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        index: Final = (request.url.host, json.loads(request.content)["auth_index"])
        assert index not in active, "refresh token reuse"
        active.add(index)
        concurrency.append(len(active))
        await asyncio.sleep(0)
        active.remove(index)
        return httpx.Response(200, json={"status_code": 200, "body": "{}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
        results: Final = await asyncio.gather(
            proxy._provider_api_call(record, credential, "GET", "https://example.test/usage", {}),
            proxy._provider_api_call(other_record, other, "GET", "https://example.test/check", {}),
        )

    assert all(result.failure is None for result in results)
    assert max(concurrency) == (1 if same_credential and same_environment else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", (True, False))
async def test_provider_call_releases_credential_lock_after_cancellation_or_failure(cancel: bool) -> None:
    from account_pool.channels.cliproxyapi.client import _AuthFile

    record: Final = _record()
    credential: Final = _AuthFile(name="codex.json", auth_index="first")
    entered: Final = asyncio.Event()
    release: Final = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if not entered.is_set():
            entered.set()
            await release.wait()
            return httpx.Response(503)
        return httpx.Response(200, json={"status_code": 200, "body": "{}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
        first: Final = asyncio.create_task(
            proxy._provider_api_call(record, credential, "GET", "https://example.test/usage", {})
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        second: Final = asyncio.create_task(
            proxy._provider_api_call(record, credential, "GET", "https://example.test/check", {})
        )
        if cancel:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            release.set()
            failed: Final = await first
            assert failed.failure is not None
            assert failed.failure.status_code == 503
        recovered: Final = await asyncio.wait_for(second, timeout=1)
        assert recovered.failure is None
        assert recovered.body == "{}"


@pytest.mark.asyncio
async def test_codex_quota_refresh_falls_back_to_cliproxy_cache_when_provider_refresh_fails() -> None:
    record: Final = _record().model_copy(update={"supplier": SupplierKind.OPENAI_CODEX})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.OPENAI_CODEX)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "codex.json",
                            "provider": "codex",
                            "auth_index": "codex-index",
                            "quota": {
                                "observed_at": "2026-09-15T13:00:00Z",
                                "signals": {
                                    "x-codex-plan-type": "plus",
                                    "x-codex-five-hour-used-percent": "69",
                                    "x-codex-five-hour-window-minutes": "300",
                                },
                            },
                        }
                    ]
                },
                request=request,
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(200, json={"models": [{"id": "gpt-5-codex"}]}, request=request)
        payload: Final = json.loads(request.content)
        status_code: Final = 403 if "/accounts/check/" in str(payload["url"]) else 200
        return httpx.Response(
            200,
            json={"status_code": status_code, "header": {}, "body": "{}"},
            request=request,
        )

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier, refresh_quota=True)
    await client.aclose()

    assert tuple(window.remaining_percent for window in observed.quota.windows) == (31.0,)
    assert observed.quota.source == "cliproxyapi_cache"
    assert observed.quota.refresh_status == "failed"
    assert observed.quota.refresh_attempted_at is not None
    assert observed.quota.refresh_error is not None
    assert "wham/usage" in observed.quota.refresh_error


@pytest.mark.asyncio
async def test_codex_quota_refresh_falls_back_to_subscriptions_when_account_expiry_is_missing() -> None:
    record: Final = _record().model_copy(update={"supplier": SupplierKind.OPENAI_CODEX})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.OPENAI_CODEX)
    api_calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "codex.json",
                            "provider": "codex",
                            "auth_index": "codex-index",
                            "id_token": {"chatgpt_account_id": "account-1", "plan_type": "pro"},
                        }
                    ]
                },
                request=request,
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(200, json={"models": [{"id": "gpt-5-codex"}]}, request=request)
        payload: Final = json.loads(request.content)
        api_calls.append(payload)
        url: Final = str(payload["url"])
        body: Final = (
            {
                "accounts": {
                    "workspace-1": {"account": {"account_id": "account-1", "plan_type": "pro", "is_default": True}}
                }
            }
            if "/accounts/check/" in url
            else {
                "subscriptionPlan": "pro",
                "currentPeriodStart": "2026-09-01T00:00:00Z",
                "currentPeriodEnd": "2090-10-01T00:00:00Z",
                "subscriptionStatus": "active",
            }
            if "/subscriptions?" in url
            else {"available_count": 2}
            if url.endswith("/rate-limit-reset-credits")
            else {"rate_limit": {"primary_window": {"used_percent": 10, "limit_window_seconds": 18000}}}
        )
        return httpx.Response(
            200,
            json={"status_code": 200, "header": {}, "body": json.dumps(body)},
            request=request,
        )

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier, refresh_quota=True)
    await client.aclose()

    assert observed.quota.subscription_status == "active"
    assert observed.quota.subscription_active_until == datetime(2090, 10, 1, tzinfo=timezone.utc)
    assert observed.quota.reset_credits_available == 2
    assert any("/backend-api/subscriptions?account_id=account-1" in str(call["url"]) for call in api_calls)
    subscription_call: Final = next(call for call in api_calls if "/backend-api/subscriptions?" in str(call["url"]))
    assert subscription_call["header"]["ChatGPT-Account-Id"] == "account-1"
    assert subscription_call["header"]["x-openai-target-path"] == "/backend-api/subscriptions"
    assert "OpenAI-Beta" not in subscription_call["header"]
    assert "Content-Type" not in subscription_call["header"]


@pytest.mark.asyncio
async def test_claude_quota_refresh_keeps_usage_when_profile_is_rejected() -> None:
    record: Final = _record().model_copy(update={"supplier": SupplierKind.ANTHROPIC_CLAUDE})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.ANTHROPIC_CLAUDE)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={"files": [{"name": "claude.json", "provider": "claude", "auth_index": "claude-index"}]},
                request=request,
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(200, json={"models": [{"id": "claude-sonnet-4"}]}, request=request)
        payload: Final = json.loads(request.content)
        if str(payload["url"]).endswith("/profile"):
            return httpx.Response(
                200,
                json={"status_code": 403, "header": {"request-id": ["req-profile"]}, "body": "{}"},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "status_code": 200,
                "header": {},
                "body": json.dumps(
                    {
                        "five_hour": {"utilization": 25, "resets_at": "2090-01-01T05:00:00Z"},
                        "seven_day": {"utilization": 40, "resets_at": "2090-01-08T00:00:00Z"},
                    }
                ),
            },
            request=request,
        )

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier, refresh_quota=True)
    await client.aclose()

    assert tuple(window.name for window in observed.quota.windows) == ("5 hour", "7 day")
    assert observed.quota.refresh_status == "partial"
    assert observed.quota.refresh_error is not None
    assert "HTTP 403" in observed.quota.refresh_error
    assert "req-profile" in observed.quota.refresh_error


@pytest.mark.asyncio
async def test_xai_quota_refresh_uses_credential_scoped_billing_endpoints() -> None:
    record: Final = _record().model_copy(update={"supplier": SupplierKind.XAI})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.XAI)
    api_calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={"files": [{"name": "xai.json", "provider": "xai", "auth_index": "xai-index"}]},
                request=request,
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(200, json={"models": [{"id": "grok-code-fast-1"}]}, request=request)
        payload: Final = json.loads(request.content)
        api_calls.append(payload)
        url: Final = str(payload["url"])
        body: Final = (
            {
                "config": {
                    "currentPeriod": {
                        "type": "WEEKLY",
                        "start": "2026-09-10T00:00:00Z",
                        "end": "2026-09-17T00:00:00Z",
                    },
                    "creditUsagePercent": 20,
                }
            }
            if "format=credits" in url
            else {
                "config": {
                    "monthlyLimit": {"val": 150000},
                    "used": {"val": 30000},
                    "billingPeriodStart": "2026-09-01T00:00:00Z",
                    "billingPeriodEnd": "2026-10-01T00:00:00Z",
                }
            }
            if url.endswith("/v1/billing")
            else {
                "user": {
                    "id": "user-1",
                    "subscription": {"tier": "SuperGrok Heavy", "status": "SUBSCRIPTION_STATUS_ACTIVE"},
                }
            }
            if "include=subscription" in url
            else {"subscriptions": [{"tier": "SuperGrok Heavy", "status": "SUBSCRIPTION_STATUS_ACTIVE"}]}
            if url.endswith("/rest/subscriptions")
            else {"usage": {"frequentUsage": 1, "frequentLimit": 10}}
        )
        return httpx.Response(
            200,
            json={"status_code": 200, "header": {}, "body": json.dumps(body)},
            request=request,
        )

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier, refresh_quota=True)
    await client.aclose()

    assert observed.quota.plan_type == "SuperGrok Heavy"
    assert tuple(window.remaining_percent for window in observed.quota.windows[:2]) == (80, 80)
    assert observed.quota.subscription_status == "SUBSCRIPTION_STATUS_ACTIVE"
    assert observed.quota.refresh_status == "complete"
    assert tuple(call["auth_index"] for call in api_calls) == ("xai-index",) * 5
    assert tuple(call["url"] for call in api_calls) == (
        "https://cli-chat-proxy.grok.com/v1/billing?format=credits",
        "https://cli-chat-proxy.grok.com/v1/billing",
        "https://cli-chat-proxy.grok.com/v1/user?include=subscription",
        "https://grok.com/rest/tasks/usage",
        "https://grok.com/rest/subscriptions",
    )
    assert all(call["header"]["Authorization"] == "Bearer $TOKEN$" for call in api_calls)
    assert all(call["header"]["x-grok-client-version"] == "0.2.120" for call in api_calls)
    assert all(call["header"]["x-grok-client-identifier"] == "grok-shell" for call in api_calls)
    assert api_calls[-1]["header"]["x-userid"] == "user-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("cached, challenged", ((False, False), (False, True), (True, True)))
async def test_xai_metadata_without_quota_fails_and_preserves_cached_observation(
    cached: bool, challenged: bool
) -> None:
    previous: Final = (
        QuotaSnapshot(
            source="provider_api",
            observed_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
            windows=(QuotaWindow(name="Weekly", remaining_percent=75, used_percent=25, window_minutes=10080),),
        )
        if cached
        else QuotaSnapshot()
    )
    record: Final = _record().model_copy(update={"supplier": SupplierKind.XAI, "quota": previous})

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200, json={"files": [{"name": "xai.json", "provider": "xai", "auth_index": "xai-index"}]}
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(200, json={"models": [{"id": "grok-code-fast-1"}]})
        url: Final = json.loads(request.content)["url"]
        if challenged and url.endswith("/rest/tasks/usage"):
            return httpx.Response(
                200,
                json={
                    "status_code": 403,
                    "header": {"Cf-Mitigated": ["challenge"], "Content-Type": ["text/html"]},
                    "body": "<html>Just a moment. private-marker</html>",
                },
            )
        body: Final = (
            {"config": {"isUnifiedBillingUser": True, "prepaidBalance": {"val": 0}}}
            if "format=credits" in url
            else {"config": {"monthlyLimit": {"val": 0}, "used": {"val": 0}}}
            if url.endswith("/v1/billing")
            else {"subscriptionTier": "SuperGrokPro", "hasGrokCodeAccess": True}
            if "include=subscription" in url
            else {}
        )
        return httpx.Response(200, json={"status_code": 200, "header": {}, "body": json.dumps(body)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
        observed: Final = await proxy.read_account(
            record, SupplierRegistry.default().get(SupplierKind.XAI), refresh_quota=True
        )
    assert observed.quota.refresh_status == "failed"
    assert observed.quota.plan_type == "SuperGrokPro"
    assert observed.quota.has_grok_code_access is True
    assert observed.quota.prepaid_balance == 0
    assert observed.quota.windows == previous.windows
    assert observed.quota.observed_at == previous.observed_at
    assert observed.quota.source == "stored_cache"
    assert observed.quota.refresh_attempted_at is not None
    assert observed.quota.refresh_error is not None
    assert "quota_fields_missing" in observed.quota.refresh_error
    assert "private-marker" not in observed.quota.refresh_error
    if challenged:
        assert "cloudflare_challenge" in observed.quota.refresh_error
        assert observed.quota.refresh_failures[-1].upstream_code == "cloudflare_challenge"


@pytest.mark.asyncio
async def test_antigravity_quota_refresh_reads_tier_and_per_model_windows() -> None:
    record: Final = _record().model_copy(update={"supplier": SupplierKind.GOOGLE_ANTIGRAVITY})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.GOOGLE_ANTIGRAVITY)
    api_calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "antigravity.json",
                            "provider": "antigravity",
                            "auth_index": "antigravity-index",
                            "project_id": "project-a",
                            "quota": {
                                "observed_at": "2026-09-01T00:00:00Z",
                                "signals": {
                                    "antigravity-week-used-percent": "10",
                                    "antigravity-week-window-minutes": "10080",
                                },
                            },
                        }
                    ]
                },
                request=request,
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(200, json={"models": [{"id": "gemini-2.5-pro"}]}, request=request)
        payload: Final = json.loads(request.content)
        api_calls.append(payload)
        url: Final = str(payload["url"])
        body: Final = (
            {
                "cloudaicompanionProject": "project-a",
                "paidTier": {
                    "id": "pro-tier",
                    "availableCredits": [
                        {
                            "creditType": "GOOGLE_ONE_AI",
                            "creditAmount": "25000",
                            "minimumCreditAmountForUsage": "50",
                        }
                    ],
                },
            }
            if url.endswith("loadCodeAssist")
            else {
                "groups": [
                    {
                        "displayName": "5 hour",
                        "buckets": [
                            {
                                "bucketId": "gemini-2.5-pro",
                                "displayName": "Gemini 2.5 Pro",
                                "remainingFraction": 0.52,
                                "resetTime": "2090-01-02T03:04:05Z",
                            }
                        ],
                    }
                ]
            }
            if url.endswith("retrieveUserQuotaSummary")
            else {
                "models": {
                    "gemini-2.5-pro": {
                        "quotaInfo": {
                            "remainingFraction": 0.42,
                            "resetTime": "2090-01-02T03:04:05Z",
                        }
                    }
                }
            }
        )
        return httpx.Response(
            200,
            json={"status_code": 200, "header": {}, "body": json.dumps(body)},
            request=request,
        )

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier, refresh_quota=True)
    await client.aclose()

    assert observed.quota.plan_type == "pro-tier"
    assert observed.quota.observed_at is not None
    assert observed.quota.observed_at > datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert observed.quota.windows[0].remaining_percent == 90
    assert observed.model_quotas[0].model == "gemini-2.5-pro"
    assert observed.model_quotas[0].quota.windows[0].remaining_percent == 52
    assert observed.quota.balances[0].available == 25000
    assert observed.quota.refresh_status == "complete"
    assert tuple(call["auth_index"] for call in api_calls) == (
        "antigravity-index",
        "antigravity-index",
        "antigravity-index",
    )
    load_payload: Final = json.loads(str(api_calls[0]["data"]))
    models_payload: Final = json.loads(str(api_calls[1]["data"]))
    summary_payload: Final = json.loads(str(api_calls[2]["data"]))
    assert load_payload["mode"] == "FULL_ELIGIBILITY_CHECK"
    assert load_payload["metadata"]["ideType"] == "ANTIGRAVITY"
    assert load_payload["cloudaicompanionProject"] == "project-a"
    assert models_payload == {"project": "project-a"}
    assert summary_payload == {"project": "project-a"}
    assert tuple(call["url"] for call in api_calls) == (
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
    )
    assert all(call["header"]["Authorization"] == "Bearer $TOKEN$" for call in api_calls)
    assert api_calls[0]["header"]["User-Agent"] == ("antigravity/1.20.5 windows/amd64 google-api-nodejs-client/10.3.0")
    assert api_calls[0]["header"]["Accept-Encoding"] == "gzip, deflate, br"
    assert api_calls[0]["header"]["x-goog-api-client"] == "gl-node/22.21.1"


@pytest.mark.asyncio
async def test_antigravity_quota_refresh_falls_back_to_prod_and_keeps_gcp_tos_endpoint() -> None:
    record: Final = _record().model_copy(update={"supplier": SupplierKind.GOOGLE_ANTIGRAVITY})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.GOOGLE_ANTIGRAVITY)
    api_calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "antigravity.json",
                            "provider": "antigravity",
                            "auth_index": "antigravity-index",
                            "project_id": "project-a",
                        }
                    ]
                },
                request=request,
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(200, json={"models": [{"id": "gemini-2.5-pro"}]}, request=request)
        payload: Final = json.loads(request.content)
        api_calls.append(payload)
        url: Final = str(payload["url"])
        if url == "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist":
            return httpx.Response(
                200,
                json={"status_code": 403, "header": {"x-request-id": ["daily-denied"]}, "body": "{}"},
                request=request,
            )
        body: Final = (
            {
                "cloudaicompanionProject": "project-a",
                "currentTier": {"id": "standard-tier", "usesGcpTos": True},
            }
            if url.endswith("loadCodeAssist")
            else {"groups": []}
            if url.endswith("retrieveUserQuotaSummary")
            else {
                "models": {
                    "gemini-2.5-pro": {"quotaInfo": {"remainingFraction": 0.5, "resetTime": "2090-01-02T03:04:05Z"}}
                }
            }
        )
        return httpx.Response(
            200,
            json={"status_code": 200, "header": {}, "body": json.dumps(body)},
            request=request,
        )

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier, refresh_quota=True)
    await client.aclose()

    assert observed.quota.plan_type == "standard-tier"
    assert tuple(call["url"] for call in api_calls) == (
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
    )


@pytest.mark.asyncio
async def test_antigravity_quota_refresh_onboards_when_project_is_missing() -> None:
    record: Final = _record().model_copy(update={"supplier": SupplierKind.GOOGLE_ANTIGRAVITY})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.GOOGLE_ANTIGRAVITY)
    api_calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "antigravity.json",
                            "provider": "antigravity",
                            "auth_index": "antigravity-index",
                        }
                    ]
                },
                request=request,
            )
        if request.url.path == "/v0/management/auth-files/models":
            return httpx.Response(200, json={"models": [{"id": "gemini-2.5-pro"}]}, request=request)
        payload: Final = json.loads(request.content)
        api_calls.append(payload)
        url: Final = str(payload["url"])
        body: Final = (
            {"allowedTiers": [{"id": "pro-tier", "isDefault": True}]}
            if url.endswith("loadCodeAssist")
            else {"name": "operations/setup-1", "done": False}
            if url.endswith("onboardUser")
            else {"done": True, "response": {"cloudaicompanionProject": {"id": "project-new"}}}
            if url.endswith("operations/setup-1")
            else {"groups": []}
            if url.endswith("retrieveUserQuotaSummary")
            else {
                "models": {
                    "gemini-2.5-pro": {"quotaInfo": {"remainingFraction": 0.75, "resetTime": "2090-01-02T03:04:05Z"}}
                }
            }
        )
        return httpx.Response(
            200,
            json={"status_code": 200, "header": {}, "body": json.dumps(body)},
            request=request,
        )

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    observed: Final = await proxy.read_account(record, supplier, refresh_quota=True)
    await client.aclose()

    assert observed.model_quotas[0].quota.windows[0].remaining_percent == 75
    assert tuple(call["url"] for call in api_calls) == (
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:onboardUser",
        "https://daily-cloudcode-pa.googleapis.com/v1internal/operations/setup-1",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
    )
    onboard_payload: Final = json.loads(str(api_calls[1]["data"]))
    assert onboard_payload["tierId"] == "pro-tier"
    assert onboard_payload["metadata"]["ideType"] == "ANTIGRAVITY"
    assert json.loads(str(api_calls[3]["data"])) == {"project": "project-new"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "provider"),
    (
        (SupplierKind.OPENAI_CODEX, "codex"),
        (SupplierKind.ANTHROPIC_CLAUDE, "anthropic"),
        (SupplierKind.GOOGLE_ANTIGRAVITY, "antigravity"),
    ),
)
async def test_submit_callback_uses_supplier_provider_key(kind: SupplierKind, provider: str) -> None:
    record: Final = _record()
    supplier: Final = SupplierRegistry.default().get(kind)
    payloads: list[object] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    await proxy.submit_callback(record, supplier, OAuthCallback(state="state", code="code"))
    await client.aclose()

    assert payloads == [{"provider": provider, "state": "state", "code": "code", "error": ""}]


@pytest.mark.asyncio
async def test_apply_configuration_uses_supplier_exclusion_and_no_concurrency_endpoint() -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "claude.json"})
    supplier: Final = SupplierRegistry.default().get(SupplierKind.ANTHROPIC_CLAUDE)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(200, text="host: 0.0.0.0\nport: 8317\n", request=request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    await proxy.apply_configuration(
        record,
        supplier,
        EnvironmentConfiguration(
            name="test",
            concurrency_limit=2,
            enabled=True,
            manual_cooldown=False,
            proxy_mode=ProxyMode.DEFAULT_GATEWAY,
            enabled_models=("model-a",),
        ),
    )
    await client.aclose()

    assert "/v0/management/concurrency-limit" not in tuple(request.url.path for request in requests)
    exclusion: Final = next(request for request in requests if request.url.path.endswith("oauth-excluded-models"))
    assert json.loads(exclusion.content) == {"claude": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "path"),
    (
        (SupplierKind.GEMINI, "/v0/management/gemini-api-key"),
        (SupplierKind.GEMINI_INTERACTIONS, "/v0/management/interactions-api-key"),
    ),
)
async def test_write_direct_api_key_uses_supplier_array_contract(kind: SupplierKind, path: str) -> None:
    record: Final = _record().model_copy(update={"supplier": kind})
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    await proxy.write_direct_api_key(
        record,
        SupplierRegistry.default().get(kind),
        api_key="secret-key",
        prefix="team/",
        priority=4,
        weight=7,
        base_url="https://example.test/v1",
        headers={"x-team": "alpha"},
        proxy_url="http://proxy.test:7890",
    )
    await client.aclose()

    assert requests[0].url.path == path
    assert json.loads(requests[0].content) == [
        {
            "api-key": "secret-key",
            "prefix": "team/",
            "priority": 4,
            "weight": 7,
            "headers": {"x-team": "alpha"},
            "base-url": "https://example.test/v1",
            "proxy-url": "http://proxy.test:7890",
        }
    ]


@pytest.mark.asyncio
async def test_apply_configuration_patches_direct_key_excluded_models() -> None:
    record: Final = _record().model_copy(
        update={
            "supplier": SupplierKind.GEMINI,
            "available_models": ("gemini-2.5-pro", "gemini-2.5-flash"),
            "enabled_models": ("gemini-2.5-pro",),
        }
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    await proxy.apply_configuration(
        record,
        SupplierRegistry.default().get(SupplierKind.GEMINI),
        EnvironmentConfiguration(
            name="test",
            concurrency_limit=2,
            enabled=True,
            manual_cooldown=False,
            proxy_mode=ProxyMode.DEFAULT_GATEWAY,
            enabled_models=("gemini-2.5-pro",),
        ),
    )
    await client.aclose()

    patch: Final = next(request for request in requests if request.method == "PATCH")
    assert patch.url.path == "/v0/management/gemini-api-key"
    assert json.loads(patch.content) == {
        "index": 0,
        "value": {"excluded-models": ["gemini-2.5-flash"]},
    }
    assert not any(request.url.path.endswith("oauth-excluded-models") for request in requests)


@pytest.mark.asyncio
async def test_import_vertex_credential_uses_file_and_location_fields() -> None:
    record: Final = _record().model_copy(update={"supplier": SupplierKind.VERTEX})
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    await proxy.import_vertex_credential(record, "service.json", b'{"project_id":"demo"}', "asia-east1")
    await client.aclose()

    request: Final = requests[0]
    assert request.url.path == "/v0/management/vertex/import"
    assert b'name="location"' in request.content
    assert b"asia-east1" in request.content
    assert b'name="file"; filename="service.json"' in request.content


@pytest.mark.asyncio
async def test_auth_file_management_uses_cockpit_endpoints() -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "codex.json", "auth_index": "1"})
    requests: list[httpx.Request] = []
    files = [{"name": "codex.json", "auth_index": "1"}]

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/download"):
            return httpx.Response(
                200, content=b'{"type":"codex"}', headers={"content-type": "application/json"}, request=request
            )
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"id": "gpt-5-codex"}]}, request=request)
        if request.url.path.endswith("/auth-files") and request.method == "GET":
            return httpx.Response(200, json={"files": files})
        if request.method == "DELETE":
            files.clear()
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    content: Final = await proxy.download_auth_file(record, "codex.json")
    await proxy.patch_auth_file_status(record, "codex.json", "1", True)
    await proxy.patch_auth_file_fields(record, "codex.json", {"priority": 3})
    await proxy.delete_auth_file(record, "codex.json")
    models: Final = await proxy.get_auth_file_models(record, "codex.json")
    await client.aclose()

    assert content[0] == b'{"type":"codex"}'
    assert models == ("gpt-5-codex",)
    assert tuple(request.url.path for request in requests) == (
        "/v0/management/auth-files/download",
        "/v0/management/auth-files/status",
        "/v0/management/auth-files/fields",
        "/v0/management/auth-files",
        "/v0/management/auth-files",
        "/v0/management/auth-files",
        "/v0/management/auth-files/models",
    )
    assert json.loads(requests[1].content) == {"name": "codex.json", "auth_index": "1", "disabled": True}
    assert json.loads(requests[2].content) == {"name": "codex.json", "priority": 3}


@pytest.mark.asyncio
async def test_install_plugin_uses_version_and_source_query() -> None:
    record: Final = _record()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "installed"}, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    result: Final = await proxy.install_plugin(record, "example-plugin", "1.2.3", "official")
    await client.aclose()

    assert result == {"status": "installed"}
    assert len(requests) == 1
    assert requests[0].url.path == "/v0/management/plugin-store/example-plugin/install"
    assert dict(requests[0].url.params) == {"version": "1.2.3", "source": "official"}


@pytest.mark.asyncio
async def test_global_settings_do_not_overlap_configuration_writes() -> None:
    active: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert not active
        active.append(request.url.path)
        await asyncio.sleep(0.001)
        active.pop()
        return httpx.Response(204, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
        await CLIProxySettingsSynchronizer(proxy).apply_global_settings(_record(), AccountPoolSettings())


@pytest.mark.asyncio
async def test_apply_global_settings_syncs_oauth_maps_for_all_suppliers() -> None:
    record: Final = _record()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_global_settings(
        record,
        AccountPoolSettings(
            oauth_excluded_models=("gpt-4", "claude-3"),
            oauth_model_aliases={"codex": (("gpt-5", "gpt-5-codex"),)},
            oauth_request_scoped_errors={
                "codex": (
                    OAuthRequestScopedErrorRule(
                        status=400,
                        match=("context_window_exceeded",),
                        match_regexr=("context.*window",),
                        action="stop",
                    ),
                )
            },
            payload=PayloadSettings(
                override=(
                    PayloadRule(
                        models=(PayloadModelRule(name="gpt-*", protocol="responses"),),
                        params={"stream": True},
                    ),
                )
            ),
        ),
    )
    await client.aclose()

    excluded: Final = next(request for request in requests if request.url.path.endswith("oauth-excluded-models"))
    assert json.loads(excluded.content) == {
        "codex": ["gpt-4", "claude-3"],
        "claude": ["gpt-4", "claude-3"],
        "antigravity": ["gpt-4", "claude-3"],
        "kimi": ["gpt-4", "claude-3"],
        "xai": ["gpt-4", "claude-3"],
    }
    aliases: Final = next(request for request in requests if request.url.path.endswith("oauth-model-alias"))
    assert json.loads(aliases.content) == {"codex": [{"name": "gpt-5", "alias": "gpt-5-codex"}]}
    errors: Final = next(request for request in requests if request.url.path.endswith("oauth-request-scoped-errors"))
    assert json.loads(errors.content) == {
        "codex": [
            {
                "status": 400,
                "match": ["context_window_exceeded"],
                "match-regexr": ["context.*window"],
                "action": "stop",
            }
        ]
    }
    config: Final = next(
        request for request in requests if request.url.path.endswith("/config.yaml") and request.method == "PUT"
    )
    document: Final = yaml.safe_load(config.content)
    assert document["plugins"] == {"enabled": False, "dir": "/data/plugins"}
    assert document["payload"]["override"] == [
        {
            "models": [
                {
                    "name": "gpt-*",
                    "protocol": "responses",
                    "headers": {},
                    "from-protocol": "",
                    "match": [],
                    "not-match": [],
                    "exist": [],
                    "not-exist": [],
                }
            ],
            "params": {"stream": True},
        }
    ]


@pytest.mark.asyncio
async def test_apply_global_settings_clears_oauth_aliases_when_empty() -> None:
    record: Final = _record()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(
                200, text="host: 0.0.0.0\npayload:\n  override:\n    - params:\n        old: true\n", request=request
            )
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_global_settings(record, AccountPoolSettings())
    await client.aclose()

    aliases: Final = next(request for request in requests if request.url.path.endswith("oauth-model-alias"))
    assert json.loads(aliases.content) == {}
    errors: Final = next(request for request in requests if request.url.path.endswith("oauth-request-scoped-errors"))
    assert json.loads(errors.content) == {}
    config: Final = next(
        request for request in requests if request.url.path.endswith("/config.yaml") and request.method == "PUT"
    )
    document: Final = yaml.safe_load(config.content)
    assert document["plugins"] == {"enabled": False, "dir": "/data/plugins"}
    assert document["payload"] == {
        "default": [],
        "default-raw": [],
        "override": [],
        "override-raw": [],
        "filter": [],
    }


@pytest.mark.asyncio
async def test_apply_global_settings_enables_plugins_in_the_writable_data_directory() -> None:
    record: Final = _record()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(
                200,
                text="plugins:\n  enabled: false\n  dir: /CLIProxyAPI/plugins\n  configs:\n    sample:\n      enabled: true\n",
                request=request,
            )
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_global_settings(record, AccountPoolSettings(plugins_enabled=True))
    await client.aclose()

    config: Final = next(
        request for request in requests if request.url.path.endswith("/config.yaml") and request.method == "PUT"
    )
    assert yaml.safe_load(config.content)["plugins"] == {
        "enabled": True,
        "dir": "/data/plugins",
        "configs": {"sample": {"enabled": True}},
    }


@pytest.mark.asyncio
async def test_apply_policy_syncs_yaml_settings_without_an_auth_file() -> None:
    record: Final = _record()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(
                200,
                text=(
                    "host: 0.0.0.0\n"
                    "codex:\n  keep: unchanged\n"
                    "xai:\n  api-key:\n    - api-key: hidden\n"
                    "antigravity:\n  project-id: preserved\n"
                ),
                request=request,
            )
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_policy(
        record,
        AccountPolicy(
            codex=CodexPolicy(identity_confuse=True, disable_codex_cloaking=True),
            xai=XaiPolicy(inject_x_search=True),
            antigravity=AntigravityPolicy(
                sensitive_words=(" alpha ", "beta", "alpha"),
                signature_cache_enabled=False,
                signature_bypass_strict=True,
            ),
        ),
    )
    await client.aclose()

    config: Final = next(
        request for request in requests if request.url.path.endswith("/config.yaml") and request.method == "PUT"
    )
    document: Final = yaml.safe_load(config.content)
    assert document["codex"] == {
        "keep": "unchanged",
        "identity-confuse": True,
        "disable-codex-cloaking": True,
    }
    assert document["xai"] == {"api-key": [{"api-key": "hidden"}], "inject-x-search": True}
    assert document["antigravity"] == {"project-id": "preserved", "sensitive-words": ["alpha", "beta"]}
    assert document["antigravity-signature-cache-enabled"] is False
    assert document["antigravity-signature-bypass-strict"] is True
    assert not any(request.url.path.endswith("/auth-files/fields") for request in requests)


@pytest.mark.asyncio
async def test_apply_codex_policy_syncs_auth_file_metadata_and_yaml_settings() -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "codex.json"})
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(200, text="host: 0.0.0.0\ncodex:\n  keep: unchanged\n", request=request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_policy(
        record,
        AccountPolicy(
            codex=CodexPolicy(
                identity_fingerprint_mode="session",
                cli_only=True,
                allow_app_server=True,
                identity_confuse=True,
                disable_codex_cloaking=True,
            )
        ),
    )
    await client.aclose()

    patch_request: Final = next(request for request in requests if request.url.path.endswith("/auth-files/fields"))
    assert json.loads(patch_request.content) == {
        "name": "codex.json",
        "request_retry": 0,
        "codex_fingerprint_mode": "session",
        "codex_fingerprint_seed": str(record.id),
        "codex_cli_only": True,
        "codex_cli_only_allow_app_server": True,
    }
    config: Final = next(
        request for request in requests if request.url.path.endswith("/config.yaml") and request.method == "PUT"
    )
    assert yaml.safe_load(config.content)["codex"] == {
        "keep": "unchanged",
        "identity-confuse": True,
        "disable-codex-cloaking": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy,expected_fields",
    (
        (
            AccountPolicy(
                claude=ClaudePolicy(
                    fingerprint_profile="claude-code-cli",
                    cloak_mode="always",
                    rebuild_mid_system_message=True,
                )
            ),
            {
                "fingerprint_profile": "claude-code-cli",
                "cloak_mode": "always",
                "rebuild_mid_system_message": True,
            },
        ),
        (
            AccountPolicy(kimi=KimiPolicy(fingerprint_profile="claude-code-cli")),
            {"fingerprint_profile": "claude-code-cli"},
        ),
    ),
)
async def test_apply_policy_syncs_only_auth_file_metadata(
    policy: AccountPolicy, expected_fields: dict[str, object]
) -> None:
    record: Final = _record().model_copy(update={"auth_file_name": "provider.json"})
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/config.yaml") and request.method == "GET":
            return httpx.Response(200, text="host: 0.0.0.0\n", request=request)
        return httpx.Response(204, request=request)

    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=client)
    sync: Final = CLIProxySettingsSynchronizer(proxy)
    await sync.apply_policy(record, policy)
    await client.aclose()

    patch_request: Final = next(request for request in requests if request.url.path.endswith("/auth-files/fields"))
    assert json.loads(patch_request.content) == {"name": "provider.json", "request_retry": 0, **expected_fields}
    config_request: Final = next(
        request for request in requests if request.url.path.endswith("/config.yaml") and request.method == "PUT"
    )
    synced: Final = yaml.safe_load(config_request.content)
    assert synced["request-retry"] == 0
    assert synced["streaming"]["bootstrap-retries"] == 0
    assert synced["transient-error-cooldown-seconds"] == 1


@pytest.mark.asyncio
async def test_upload_removes_old_and_orphan_files_before_installing_one_credential() -> None:
    from account_pool.credential_ownership import CredentialOwnership

    record: Final = _record()
    files = ["old.json", "orphan.json"]
    operations: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operations.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"files": [{"name": name} for name in files]})
        if request.method == "DELETE":
            files.remove(request.url.params["name"])
            return httpx.Response(204)
        assert not files
        assert b'filename="replacement.json"' in request.content
        files.append("replacement.json")
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy: Final = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client, CredentialOwnership())
        await proxy.upload_auth_file(record, "replacement.json", b'{"refresh_token":"unique"}', "application/json")
        assert files == ["replacement.json"]
        assert tuple(method for method, _ in operations) == ("GET", "DELETE", "DELETE", "POST")
        await proxy.delete_auth_file(record, "replacement.json")
        assert files == []


@pytest.mark.asyncio
@pytest.mark.parametrize("enable", [False, True])
@pytest.mark.parametrize("disable_status", [204, 503])
async def test_duplicate_oauth_account_is_disabled_before_models_or_quota_calls(
    enable: bool, disable_status: int
) -> None:
    from account_pool.credential_ownership import CredentialConflict, CredentialOwnership, credential_identity

    registry: Final = CredentialOwnership()
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    content: Final = b'{"refresh_token":"shared-refresh","email":"shared@example.test"}'
    record: Final = _record()
    await registry.claim(uuid4(), credential_identity(content, record.supplier.value, secrets).fingerprints)
    disabled: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=content)
        if request.url.path.endswith("/auth-files"):
            return httpx.Response(
                200, json={"files": [{"name": "duplicate.json", "provider": "codex", "auth_index": "1"}]}
            )
        if request.url.path.endswith("/status"):
            payload: Final = json.loads(request.content)
            assert payload["disabled"] is True
            disabled.append(payload["name"])
            return httpx.Response(disable_status)
        raise AssertionError("duplicate account must not make model or quota requests")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy: Final = HttpCLIProxyClient(secrets, client, registry)
        operation: Final = (
            proxy.patch_auth_file_status(record, "duplicate.json", "1", False)
            if enable
            else proxy.read_account(record, refresh_quota=True)
        )
        with pytest.raises(CredentialConflict, match="已绑定其他卡片"):
            await operation
    assert disabled == ["duplicate.json"]
