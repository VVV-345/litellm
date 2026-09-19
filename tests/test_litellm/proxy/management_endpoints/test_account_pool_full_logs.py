"""本文件验证完整日志开关、流式中断、脱敏、独立保留和管理员访问边界。"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final, Literal
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_accounting import PriceSnapshot
from litellm.proxy.management_endpoints.account_pool_full_logs import (
    FullLogQuery,
    FullLogRecord,
    FullLogStore,
    full_log_store,
)
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import FinishRequest, Lease
from litellm.proxy.management_endpoints.account_pool_request_log import RequestLog, clean_content, conversation_id
from litellm.proxy.management_endpoints.account_pool_routing import Route
from litellm.proxy.management_endpoints.request_log_endpoints import create_request_log_router
from tests.test_litellm.proxy.management_endpoints.test_account_pool_gateway import _KEY, setup_gateway


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[FullLogStore]:
    monkeypatch.setenv("ACCOUNT_POOL_FULL_LOG_DIR", str(tmp_path / "full"))
    full_log_store.cache_clear()
    yield full_log_store()
    full_log_store.cache_clear()


def test_disabled_full_logging_never_creates_content_storage(store: FullLogStore) -> None:
    client, control = setup_gateway(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "private answer"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 9, "prompt_tokens_details": {"cached_tokens": 80}},
            },
        )
    )
    with client:
        response: Final = client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "messages": [{"role": "user", "content": "private input"}]},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 200
    assert not store.path.exists()
    assert control.finished[0].input_tokens == 100
    assert control.finished[0].cache_read_input_tokens == 80
    assert "private" not in control.finished[0].model_dump_json()


@pytest.mark.parametrize("skip,complete", [(True, False), (True, True), (False, False)])
def test_failed_full_log_switch_preserves_daily_usage(store: FullLogStore, skip: bool, complete: bool) -> None:
    stream = 'data: {"usage":{"prompt_tokens":10,"completion_tokens":4}}\n\n'
    if complete:
        stream += "data: [DONE]\n\n"
    client, control = setup_gateway(
        lambda _: httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"})
    )
    control.resolution = control.resolution.model_copy(
        update={"full_logging_enabled": True, "full_log_skip_failed": skip}
    )
    with client:
        client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "stream": True, "messages": [{"role": "user", "content": "private"}]},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert len(control.finished) == 1
    assert control.finished[0].input_tokens == 10
    assert control.finished[0].output_tokens == 4
    assert store.storage().row_count == int(complete or not skip)


@pytest.mark.asyncio
async def test_full_log_filters_apply_to_rows_and_totals(store: FullLogStore) -> None:
    log = request_log("http")
    log.observe({"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
    await log.finish(
        FinishRequest(lease_id=log.lease.lease_id, endpoint="/v1/responses", http_status=200, message="ok")
    )
    other = request_log("http")
    await other.finish(
        FinishRequest(lease_id=other.lease.lease_id, endpoint="/v1/responses", http_status=500, message="error")
    )
    page = store.query(FullLogQuery(model="model-a", incomplete=False, http_status=200))
    assert len(page.items) == 1
    assert page.totals.attempts == 1
    assert page.totals.input_tokens == 7
    assert store.query(FullLogQuery(occurred_to=datetime.now(timezone.utc) - timedelta(days=1))).totals.attempts == 0
    assert store.query(FullLogQuery(model="' OR 1=1 --")).items == ()
    scoped = store.query(FullLogQuery(key_id=log.lease.key_id, limit=1))
    assert len(scoped.items) == scoped.totals.attempts == 1
    assert scoped.items[0].key_id == log.lease.key_id
    assert store.query(FullLogQuery(key_id=log.lease.key_id, offset=1)).items == ()
    assert store.query(FullLogQuery(key_id=log.lease.key_id, offset=1)).totals.attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_first", [False, True])
async def test_parent_guardrail_failure_removes_body_even_when_finish_races(
    store: FullLogStore, reject_first: bool
) -> None:
    log = request_log("http")
    log.skip_failed = True
    if reject_first:
        store.reject_request(log.lease.request_id)
    await log.finish(
        FinishRequest(lease_id=log.lease.lease_id, endpoint="/v1/responses", http_status=200, message="ok")
    )
    if not reject_first:
        assert store.storage().row_count == 1
        store.reject_request(log.lease.request_id)
    assert store.storage().row_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("transport,status", [("http", 500), ("websocket", 200)])
async def test_skip_failed_keeps_usage_for_http_error_and_interrupted_websocket(
    store: FullLogStore, transport: Literal["http", "websocket"], status: int
) -> None:
    log = request_log(transport)
    log.skip_failed = True
    log.input_tokens = 7
    log.output_tokens = 3
    log.websocket_pending = transport == "websocket"
    result = await log.finish(
        FinishRequest(lease_id=log.lease.lease_id, endpoint="/v1/responses", http_status=status, message="error")
    )
    assert result.input_tokens == 7
    assert result.output_tokens == 3
    assert store.storage().row_count == 0


@pytest.mark.parametrize("complete", [False, True])
def test_full_sse_retains_partial_reply_and_never_puts_body_in_daily_log(store: FullLogStore, complete: bool) -> None:
    stream = 'data: {"choices":[{"delta":{"content":"hello private answer"}}]}\n\n'
    if complete:
        stream += 'data: {"usage":{"prompt_tokens":10,"completion_tokens":4}}\n\ndata: [DONE]\n\n'
    client, control = setup_gateway(
        lambda _: httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"})
    )
    control.resolution = control.resolution.model_copy(update={"full_logging_enabled": True})
    with client:
        client.post(
            "/v1/chat/completions",
            json={
                "model": "model-a",
                "stream": True,
                "messages": [{"role": "user", "content": f"private input {_KEY}"}],
            },
            headers={"Authorization": f"Bearer {_KEY}", "X-LiteLLM-Session-ID": "same-conversation"},
        )
    page: Final = store.query(FullLogQuery())
    assert page.items
    assert len(page.items) == 1
    saved: Final = store.detail(page.items[0].event_id)
    assert saved is not None
    assert saved.incomplete is not complete
    assert "hello private answer" in saved.model_dump_json()
    assert _KEY not in saved.model_dump_json()
    assert "private input" in saved.model_dump_json()
    assert saved.session_id == control.finished[0].session_id
    assert control.finished[0].full_log_state == "stored"
    assert saved.result.spend_sync_state == control.finished[0].spend_sync_state
    assert "private" not in control.finished[0].model_dump_json()


def test_session_is_scoped_to_key_and_independent_of_model() -> None:
    headers: Final = Headers({"X-LiteLLM-Session-ID": "conversation-1"})
    assert conversation_id(headers, "key-1") == conversation_id(headers, "key-1")
    assert conversation_id(headers, "key-1") != conversation_id(headers, "key-2")
    assert conversation_id(Headers({"x-client-request-id": "per-request"}), "key-1") is None


def test_full_log_storage_is_idempotent_and_prunes_only_expired_bodies(tmp_path: Path) -> None:
    store: Final = FullLogStore(tmp_path)
    now: Final = datetime.now(timezone.utc)
    record: Final = FullLogRecord(
        event_id=uuid4(),
        request_id=uuid4(),
        card_id=uuid4(),
        account_id=uuid4(),
        key_id=uuid4(),
        session_id="session-one",
        started_at=now - timedelta(days=40),
        finished_at=now,
        model="model-a",
        requested_model="alias",
        attempt=1,
        transport="http",
        result=FinishRequest(lease_id=uuid4(), http_status=200, message="done", endpoint="/v1/responses"),
        request={"input": "question"},
        response={"output": "answer"},
    )
    store.append(record)
    store.append(record)
    recent: Final = record.model_copy(update={"event_id": uuid4(), "started_at": now})
    store.append(recent)
    assert store.storage().row_count == 2
    assert store.query(FullLogQuery(session_id="session-one")).items[0].event_id == record.event_id
    page: Final = store.query(FullLogQuery(session_id="session-one", limit=1))
    assert page.has_more
    assert page.totals.attempts == 2
    assert page.totals.requests == 1
    assert page.totals.unknown_cost_attempts == 2
    assert page.totals.cost_usd is None
    assert store.prune(30) == 1
    assert store.detail(record.event_id) is None
    assert store.detail(recent.event_id).request == {"input": "question"}
    assert store.prune(None) == 1
    assert store.storage().row_count == 0


def test_complete_log_redacts_nested_credentials_and_inline_attachment_data() -> None:
    cleaned: Final = clean_content(
        {
            "messages": [{"content": "known-token"}],
            "tools": [{"access_token": "unknown-token"}],
            "image_url": "data:image/png;base64,abcd",
        },
        ("known-token",),
    )
    assert cleaned["messages"][0]["content"] == "[REDACTED]"
    assert cleaned["tools"][0]["access_token"] == "[REDACTED]"
    assert "abcd" not in cleaned["image_url"]
    assert "secret-value" not in clean_content('data: {"access_token":"secret-value"}', ())


def test_full_log_api_requires_admin_and_does_not_call_daily_log_cleanup(store: FullLogStore) -> None:
    async def forbidden_manager(*args: object) -> object:
        raise AssertionError("Full log cleanup must never call daily log APIs")

    def require_admin(user: object) -> None:
        if user != "admin":
            raise HTTPException(403)

    app: Final = FastAPI()
    app.include_router(create_request_log_router(forbidden_manager, require_admin))
    app.dependency_overrides[user_api_key_auth] = lambda: "viewer"
    with TestClient(app) as client:
        assert client.get("/logs/full").status_code == 403
        assert client.delete("/logs/full").status_code == 403
        app.dependency_overrides[user_api_key_auth] = lambda: "admin"
        response: Final = client.get("/logs/full")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert client.delete("/logs/full?older_than_days=30").json() == {"deleted": 0}
        assert client.get(f"/logs/full/{uuid4()}").status_code == 404


def test_storage_failure_does_not_change_response_or_skip_daily_log(store: FullLogStore) -> None:
    store.root.write_text("directory unavailable")
    client, control = setup_gateway(
        lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})
    )
    control.resolution = control.resolution.model_copy(update={"full_logging_enabled": True})
    with client:
        response: Final = client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "messages": []},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert response.status_code == 200
    assert len(control.finished) == 1
    assert control.finished[0].http_status == 200
    assert control.finished[0].full_log_state == "failed"


def test_switching_off_preserves_existing_full_records(store: FullLogStore) -> None:
    client, control = setup_gateway(lambda _: httpx.Response(200, json={"choices": []}))
    with client:
        control.resolution = control.resolution.model_copy(update={"full_logging_enabled": True})
        client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "messages": []},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
        control.resolution = control.resolution.model_copy(update={"full_logging_enabled": False})
        client.post(
            "/v1/chat/completions",
            json={"model": "model-a", "messages": []},
            headers={"Authorization": f"Bearer {_KEY}"},
        )
    assert store.storage().row_count == 1
    assert [result.full_log_state for result in control.finished] == ["stored", "disabled"]


def request_log(transport: Literal["http", "sse", "websocket"] = "websocket") -> RequestLog:
    _, control = setup_gateway(lambda _: httpx.Response(200, json={}))
    lease: Final = Lease(
        lease_id=uuid4(),
        request_id=uuid4(),
        card_id=control.resolution.card_id,
        key_id=control.resolution.key_id,
        account_id=control.resolution.card_id,
        channel="cliproxyapi",
        supplier="openai_codex",
        model="model-a",
        started_at=datetime.now(timezone.utc),
    )
    return RequestLog(
        lease,
        Route(control.resolution.candidates[0], "model-a", "automatic"),
        control.resolution.model_copy(update={"full_logging_enabled": True}),
        Headers(),
        {"model": "model-a"},
        _KEY,
        transport,
    )


@pytest.mark.asyncio
async def test_websocket_prices_each_response_and_deduplicates_terminal_events(store: FullLogStore) -> None:
    log: Final = request_log()
    log.price = PriceSnapshot(
        model="model-a",
        model_id="deployment",
        source="configured",
        rates={"input_cost_per_token": 0.001, "output_cost_per_token": 0.004, "cache_read_input_token_cost": 0.0001},
        tiered_pricing=[
            {
                "range": [0, 150],
                "input_cost_per_token": 0.001,
                "output_cost_per_token": 0.004,
                "cache_read_input_token_cost": 0.0001,
            },
            {
                "range": [150, 1000000],
                "input_cost_per_token": 0.01,
                "output_cost_per_token": 0.04,
                "cache_read_input_token_cost": 0.001,
            },
        ],
    )
    for identifier in ("response-1", "response-2", "response-2"):
        log.observe(
            {
                "type": "response.completed",
                "response": {
                    "id": identifier,
                    "usage": {"input_tokens": 100, "output_tokens": 5, "input_tokens_details": {"cached_tokens": 80}},
                },
            }
        )
    result: Final = await log.finish(
        FinishRequest(lease_id=log.lease.lease_id, endpoint="/v1/responses", http_status=200, message="done")
    )
    assert result.input_tokens == 200
    assert result.output_tokens == 10
    assert result.cache_read_input_tokens == 160
    assert result.cost_usd == pytest.approx(2 * (0.02 + 0.008 + 0.02))


@pytest.mark.asyncio
async def test_native_stream_usage_and_structural_redaction(store: FullLogStore) -> None:
    log: Final = request_log("sse")
    log.observe(
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 10,
                }
            },
        }
    )
    log.observe({"type": "message_delta", "usage": {"output_tokens": 5}})
    log.observe({"type": "message_stop"})
    log.capture(
        b'data: {"image_url":"data:image/png;base64,private-image", "url":"https://user:password@host/path?token=private-query"}\n\n'
    )
    result: Final = await log.finish(
        FinishRequest(lease_id=log.lease.lease_id, endpoint="/v1/messages", http_status=200, message="done")
    )
    assert (result.input_tokens, result.output_tokens, result.cache_read_input_tokens) == (10, 5, 80)
    saved: Final = store.detail(log.lease.lease_id)
    assert saved is not None
    assert not saved.incomplete
    assert "private-image" not in saved.model_dump_json()
    assert "private-query" not in saved.model_dump_json()


@pytest.mark.asyncio
async def test_full_content_limit_is_explicit_and_usage_still_collected(store: FullLogStore) -> None:
    log: Final = request_log("http")
    log.capture(b"x" * (16 * 1024 * 1024 + 1))
    log.observe({"usage": {"prompt_tokens": 3, "completion_tokens": 9}})
    result: Final = await log.finish(
        FinishRequest(lease_id=log.lease.lease_id, endpoint="/v1/chat/completions", http_status=200, message="done")
    )
    assert result.full_log_state == "truncated"
    assert result.output_tokens == 9
    saved: Final = store.detail(log.lease.lease_id)
    assert saved is not None
    assert len(saved.response) == 16 * 1024 * 1024


def test_timing_is_persistent_request_scoped_and_independent_of_body_storage(store: FullLogStore) -> None:
    from litellm.proxy.management_endpoints.account_pool_timing import TimingPhase

    request_id: Final = uuid4()
    phase: Final = TimingPhase(attempt=1, phase="acquire", duration_ms=125, status=200)
    store.save_timing(request_id, (phase,))
    reopened: Final = FullLogStore(store.root)
    assert reopened.timing(request_id) == (phase,)
    assert reopened.timing(uuid4()) == ()
    assert not store.path.exists()
    with store.timing_connection() as connection:
        connection.execute("UPDATE request_timings SET recorded_at = 0")
    store.save_timing(uuid4(), (phase,))
    assert reopened.timing(request_id) == ()


@pytest.mark.parametrize("percent,success,expected", [(0, True, 0), (100, True, 1), (0, False, 1)])
def test_success_sampling_preserves_failures_and_usage(store, percent, success, expected):
    client, control = setup_gateway(
        lambda _: httpx.Response(
            200 if success else 400,
            json={
                "choices": [{"message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )
    )
    control.resolution = control.resolution.model_copy(
        update={"full_logging_enabled": True, "full_log_sample_percent": percent}
    )
    with client:
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {_KEY}"},
            json={"model": "model-a", "messages": [{"role": "user", "content": "go"}]},
        )
    assert store.storage().row_count == expected
    assert len(control.finished) == 1
    if success:
        assert control.finished[0].input_tokens == 5
        assert control.finished[0].output_tokens == 2


def test_custom_redaction_and_capture_limit_do_not_modify_upstream_payload(store):
    captured = []

    def upstream(request):
        captured.append(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x" * 3000}}]})

    client, control = setup_gateway(upstream)
    control.resolution = control.resolution.model_copy(
        update={"full_logging_enabled": True, "full_log_max_body_kb": 1, "log_redact_fields": ("email",)}
    )
    with client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {_KEY}"},
            json={
                "model": "model-a",
                "email": "private@example.com",
                "messages": [{"role": "user", "content": "go"}],
                "encrypted_content": "secret-thinking",
            },
        )
    assert response.status_code == 200 and b"private@example.com" in captured[0]
    record = store.detail(store.query(FullLogQuery()).items[0].event_id)
    assert record.request["email"] == record.request["encrypted_content"] == "[REDACTED]"
    assert record.truncated and record.result.full_log_state == "truncated"


@pytest.mark.asyncio
async def test_full_log_capacity_keeps_newest_records_and_reuses_existing_store(store):
    import secrets

    log = request_log("http")
    await log.finish(
        FinishRequest(lease_id=log.lease.lease_id, endpoint="/v1/responses", http_status=200, message="ok")
    )
    original = store.detail(log.lease.lease_id)
    for index in range(4):
        record = original.model_copy(
            update={
                "event_id": uuid4(),
                "started_at": original.started_at + timedelta(seconds=index + 1),
                "response": secrets.token_hex(400000),
            }
        )
        store.append(record)
    store.limit_storage(1)
    with store.connection() as connection:
        total = connection.execute("SELECT sum(length(body) + length(summary)) FROM conversations").fetchone()[0]
    assert total <= 1024 * 1024
    assert store.query(FullLogQuery()).items[0].event_id == record.event_id
    assert store.storage().row_count < 5
