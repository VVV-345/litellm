from __future__ import annotations

from typing import Final, Literal

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management import create_management_router
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPoolSettings,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
)
from litellm.proxy.management_endpoints.request_log_endpoints import create_request_log_router


class SettingsManager:
    def __init__(self) -> None:
        self.settings = AccountPoolSettingsView(
            version=7,
            values=AccountPoolSettings(
                default_concurrency_limit=9,
                quota_refresh_interval_minutes=15,
                oauth_excluded_models=["private-model"],
            ),
            requires_reload=False,
        )
        self.writes: list[AccountPoolSettingsUpdate] = []

    async def request(
        self, method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"], path: str, body: bytes | None
    ) -> httpx.Response:
        if method == "PUT":
            request: Final = AccountPoolSettingsUpdate.model_validate_json(body or b"")
            self.writes.append(request)
            self.settings = AccountPoolSettingsView(version=8, values=request.values, requires_reload=True)
        return httpx.Response(200, content=self.settings.model_dump_json())


def authorize(user: object) -> None:
    if user != "admin":
        raise HTTPException(403)


def test_log_settings_preserve_account_policy_and_reject_stale_updates() -> None:
    manager: Final = SettingsManager()
    app: Final = FastAPI()
    app.include_router(create_request_log_router(manager.request, authorize))
    app.dependency_overrides[user_api_key_auth] = lambda: "admin"
    with TestClient(app) as client:
        current: Final = client.get("/logs/settings")
        assert current.status_code == 200
        assert current.headers["cache-control"] == "no-store"
        assert "oauth_excluded_models" not in current.json()["values"]
        updated: Final = {**current.json(), "values": {**current.json()["values"], "full_logging_enabled": True}}
        saved: Final = client.put("/logs/settings", json=updated)
        assert saved.status_code == 200
        assert saved.json()["requires_reload"] is True
        assert manager.writes[0].values.full_logging_enabled
        assert manager.writes[0].values.default_concurrency_limit == 9
        assert manager.writes[0].values.quota_refresh_interval_minutes == 15
        assert manager.writes[0].values.oauth_excluded_models == ("private-model",)
        assert client.put("/logs/settings", json=updated).status_code == 409
        assert len(manager.writes) == 1


def test_migrated_routes_require_admin_and_pool_no_longer_owns_logs() -> None:
    manager: Final = SettingsManager()
    app: Final = FastAPI()
    app.include_router(create_request_log_router(manager.request, authorize))
    app.include_router(create_management_router(manager.request, authorize), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: "viewer"
    with TestClient(app) as client:
        for path in ("/logs/settings", "/logs/operations", "/logs/full", "/logs/timing/00000000-0000-0000-0000-000000000001"):
            assert client.get(path).status_code == 403
        for path in ("/account_pool/logs", "/account_pool/full-logs", "/account_pool/stats"):
            assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "key,value",
    [
        ("full_log_sample_percent", 101),
        ("full_log_max_body_kb", 0),
        ("runtime_log_level", "TRACE"),
        ("daily_log_max_rows", -1),
    ],
)
def test_log_settings_validate_new_controls(key, value):
    from pydantic import ValidationError

    from litellm.proxy.management_endpoints.request_log_endpoints import RequestLogSettings

    with pytest.raises(ValidationError):
        RequestLogSettings.model_validate({key: value})


def test_runtime_formatter_reuses_redaction_and_optional_stacktrace():
    import logging

    from litellm._logging import JsonFormatter
    from litellm.proxy.management_endpoints.request_log_runtime import RuntimeFormatter

    try:
        raise ValueError("private-stack-marker")
    except ValueError as error:
        record = logging.LogRecord(
            "LiteLLM Proxy",
            logging.ERROR,
            "test.py",
            1,
            "request failed",
            (),
            (type(error), error, error.__traceback__),
        )
    without = RuntimeFormatter(JsonFormatter(), False).format(record)
    with_trace = RuntimeFormatter(JsonFormatter(), True).format(record)
    assert "private-stack-marker" not in without and "private-stack-marker" in with_trace
    assert record.exc_info is not None


def test_async_runtime_output_is_bounded_and_drains_without_losing_traceback():
    import io
    import logging
    from queue import Queue

    from litellm._logging import JsonFormatter
    from litellm.proxy.management_endpoints.request_log_runtime import (
        RuntimeFormatter,
        RuntimeQueueHandler,
        RuntimeQueueListener,
    )

    output = io.StringIO()
    sink = logging.StreamHandler(output)
    sink.setFormatter(RuntimeFormatter(JsonFormatter(), True))
    pending = Queue(maxsize=1)
    handler = RuntimeQueueHandler(pending)
    try:
        raise ValueError("traceback-preserved")
    except ValueError as error:
        record = logging.LogRecord("qa", logging.ERROR, "qa.py", 1, "event", (), (type(error), error, error.__traceback__))
    handler.handle(record)
    handler.handle(record)
    assert handler.dropped == 1
    assert output.getvalue() == ""
    listener = RuntimeQueueListener(pending, sink)
    listener.start()
    listener.stop()
    assert "traceback-preserved" in output.getvalue()
    assert record.exc_info is not None
