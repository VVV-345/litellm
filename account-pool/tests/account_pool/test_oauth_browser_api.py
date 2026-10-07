"""验证 Manager 受控 OAuth 浏览器接口的鉴权、绑定和清理边界。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import cycle
from pathlib import Path
from typing import Final, cast
from uuid import UUID, uuid4

import httpx
import pytest
from account_pool.api import create_router
from account_pool.domain import AuthorizationView, EnvironmentRecord, EnvironmentStatus, OAuthCallback, to_view, utc_now
from account_pool.oauth_browser import OAuthBrowserSession, OAuthBrowserSessionService, OAuthBrowserSessionStatus
from account_pool.ports import ProxyProfileRepository
from account_pool.result import Success
from account_pool.service import EnvironmentService
from fastapi import FastAPI
from test_account_pool import MemoryRepository, _record

MANAGER_TOKEN: Final = "m" * 32
CALLBACK_TOKEN: Final = "callback-relay-secret"
BROWSER_TICKET: Final = "browser-ticket-secret"
PROXY_URL: Final = "https://proxy.example:8443"


@dataclass
class MemoryBrowserSessions:
    sessions: dict[UUID, OAuthBrowserSession]
    lock: asyncio.Lock

    @classmethod
    def create(cls) -> MemoryBrowserSessions:
        return cls({}, asyncio.Lock())

    async def create_if_absent(self, session: OAuthBrowserSession) -> bool:
        async with self.lock:
            active: Final = any(
                item.environment_id == session.environment_id
                and item.status
                in {
                    OAuthBrowserSessionStatus.STARTING,
                    OAuthBrowserSessionStatus.ACTIVE,
                    OAuthBrowserSessionStatus.CALLBACK_PENDING,
                }
                and item.expires_at > session.created_at
                for item in self.sessions.values()
            )
            if active:
                return False
            self.sessions[session.id] = session
            return True

    async def get(self, session_id: UUID) -> OAuthBrowserSession | None:
        return self.sessions.get(session_id)

    async def consume_ticket(
        self,
        session_id: UUID,
        ticket_digest: str,
        now: datetime,
    ) -> OAuthBrowserSession | None:
        async with self.lock:
            current: Final = self.sessions.get(session_id)
            if (
                current is None
                or current.status is not OAuthBrowserSessionStatus.ACTIVE
                or current.ticket_digest != ticket_digest
                or current.ticket_consumed_at is not None
                or current.expires_at <= now
            ):
                return None
            consumed: Final = current.model_copy(update={"ticket_consumed_at": now})
            self.sessions[session_id] = consumed
            return consumed

    async def transition(
        self,
        session_id: UUID,
        expected: tuple[OAuthBrowserSessionStatus, ...],
        target: OAuthBrowserSessionStatus,
    ) -> OAuthBrowserSession | None:
        async with self.lock:
            current: Final = self.sessions.get(session_id)
            if current is None or current.status not in expected:
                return None
            updated: Final = current.model_copy(update={"status": target})
            self.sessions[session_id] = updated
            return updated

    async def claim_callback(
        self,
        session_id: UUID,
        token_digest: str,
        now: datetime,
    ) -> OAuthBrowserSession | None:
        async with self.lock:
            current: Final = self.sessions.get(session_id)
            if (
                current is None
                or current.status is not OAuthBrowserSessionStatus.ACTIVE
                or current.callback_token_digest != token_digest
                or current.expires_at <= now
            ):
                return None
            pending: Final = current.model_copy(update={"status": OAuthBrowserSessionStatus.CALLBACK_PENDING})
            self.sessions[session_id] = pending
            return pending

    async def expire_due(self, now: datetime) -> tuple[OAuthBrowserSession, ...]:
        async with self.lock:
            due: Final = tuple(
                item
                for item in self.sessions.values()
                if item.status in {OAuthBrowserSessionStatus.STARTING, OAuthBrowserSessionStatus.ACTIVE}
                and item.expires_at <= now
            )
            for item in due:
                self.sessions[item.id] = item.model_copy(update={"status": OAuthBrowserSessionStatus.EXPIRED})
            return tuple(self.sessions[item.id] for item in due)

    async def list_cleanup_due(self, now: datetime) -> tuple[OAuthBrowserSession, ...]:
        return tuple(
            item
            for item in self.sessions.values()
            if item.cleanup_pending
            and (
                item.status
                in {
                    OAuthBrowserSessionStatus.COMPLETED,
                    OAuthBrowserSessionStatus.CANCELLED,
                    OAuthBrowserSessionStatus.FAILED,
                    OAuthBrowserSessionStatus.EXPIRED,
                }
                or item.expires_at <= now
            )
        )

    async def mark_cleaned(self, session_id: UUID) -> None:
        current: Final = self.sessions[session_id]
        self.sessions[session_id] = current.model_copy(update={"cleanup_pending": False})


class Profiles:
    def __init__(self, url: str | None = PROXY_URL) -> None:
        self.url = url

    async def get_url(self, profile_id: str) -> str | None:
        return self.url if profile_id == "profile-current" else None

    async def list(self):
        return ()


class BrowserRuntime:
    def __init__(self) -> None:
        self.started: list[tuple[UUID, str, str, str, int, str]] = []
        self.removed: list[UUID] = []

    async def start_oauth_browser(
        self,
        session: OAuthBrowserSession,
        *,
        proxy_url: str,
        authorization_url: str,
        callback_token: str,
        callback_port: int,
        callback_path: str,
    ) -> None:
        self.started.append((session.id, proxy_url, authorization_url, callback_token, callback_port, callback_path))

    async def remove_oauth_browser(self, session_id: UUID) -> None:
        self.removed.append(session_id)


class BrowserManagerService:
    def __init__(self, callback_result: object | None = None) -> None:
        self.callback_result = callback_result or Success(None)
        self.callbacks: list[OAuthCallback] = []
        self.authorizations: list[tuple[UUID, str | None]] = []
        self.cancellations: list[UUID] = []
        self.authorization_started = asyncio.Event()
        self.authorization_gate: asyncio.Event | None = None

    async def authorize_environment(self, environment_id: UUID, operation_id: str | None = None) -> object:
        self.authorizations.append((environment_id, operation_id))
        self.authorization_started.set()
        if self.authorization_gate is not None:
            await self.authorization_gate.wait()
        record: Final = _browser_record(environment_id=environment_id)
        return Success(
            AuthorizationView(
                environment=to_view(record),
                flow="browser_oauth",
                authorization_url="https://auth.example/authorize?state=oauth-state",
                ssh_command=None,
                user_code=None,
                expires_at=utc_now() + timedelta(minutes=5),
            )
        )

    def oauth_callback_target(self, record: EnvironmentRecord) -> tuple[int, str] | None:
        return 1455, "/auth/callback"

    async def submit_oauth_callback(self, callback: OAuthCallback, environment_id: UUID) -> object:
        self.callbacks.append(callback)
        return self.callback_result

    async def cancel_oauth_session(self, environment_id: UUID) -> object:
        self.cancellations.append(environment_id)
        return Success(None)


def _browser_record(
    *, environment_id: UUID, status: EnvironmentStatus = EnvironmentStatus.AWAITING_AUTHORIZATION
) -> EnvironmentRecord:
    return _record(status=status).model_copy(
        update={
            "id": environment_id,
            "proxy_profile_id": "profile-current",
            "oauth_state": "signed-oauth-state-123456789",
            "oauth_authorization_url": "https://auth.example/authorize?state=signed-oauth-state-123456789",
            "oauth_expires_at": utc_now() + timedelta(minutes=5),
        }
    )


def _client(tmp_path: Path, *, profile_url: str | None = PROXY_URL, callback_result: object | None = None):
    environment_id: Final = uuid4()
    record: Final = _browser_record(environment_id=environment_id)
    environments: Final = MemoryRepository(record)
    repository: Final = MemoryBrowserSessions.create()
    token_values: Final = cycle((BROWSER_TICKET, CALLBACK_TOKEN))
    browser_sessions: Final = OAuthBrowserSessionService(
        repository,
        fingerprint_key=b"browser-fingerprint-key-for-tests",
        ttl=timedelta(minutes=5),
        token_factory=lambda: next(token_values),
    )
    runtime: Final = BrowserRuntime()
    manager_service: Final = BrowserManagerService(callback_result)
    profiles: Final = Profiles(profile_url)
    app: Final = FastAPI()
    app.include_router(
        create_router(
            cast(EnvironmentService, manager_service),
            MANAGER_TOKEN,
            environments=environments,
            proxy_profiles=cast(ProxyProfileRepository, profiles),
            browser_sessions=browser_sessions,
            browser_runtime=runtime,
        )
    )
    return (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://manager"),
        environment_id,
        repository,
        runtime,
        manager_service,
        profiles,
    )


@pytest.mark.asyncio
async def test_browser_session_start_requires_manager_auth_environment_and_proxy(tmp_path: Path) -> None:
    client, environment_id, _, runtime, _, _ = _client(tmp_path)
    async with client:
        unauthenticated: Final = await client.post(f"/api/environments/{environment_id}/oauth-browser-sessions")
        missing_proxy_client, missing_proxy_environment_id, _, _, _, _ = _client(tmp_path, profile_url=None)
        async with missing_proxy_client:
            missing_proxy_client.headers["Authorization"] = f"Bearer {MANAGER_TOKEN}"
            missing_proxy: Final = await missing_proxy_client.post(
                f"/api/environments/{missing_proxy_environment_id}/oauth-browser-sessions"
            )
        client.headers["Authorization"] = f"Bearer {MANAGER_TOKEN}"
        missing_environment: Final = await client.post(f"/api/environments/{uuid4()}/oauth-browser-sessions")

    assert unauthenticated.status_code == 401
    assert missing_proxy.status_code == 409
    assert missing_environment.status_code == 404
    assert runtime.started == []


@pytest.mark.asyncio
async def test_browser_session_start_duplicate_and_status_redact_credentials(tmp_path: Path) -> None:
    client, environment_id, repository, runtime, service, _ = _client(tmp_path)
    async with client:
        client.headers["Authorization"] = f"Bearer {MANAGER_TOKEN}"
        started: Final = await client.post(f"/api/environments/{environment_id}/oauth-browser-sessions")
        session_id: Final = started.json()["id"]
        status_response: Final = await client.get(f"/api/oauth-browser-sessions/{session_id}")
        duplicate: Final = await client.post(f"/api/environments/{environment_id}/oauth-browser-sessions")

    assert started.status_code == 201
    assert started.json()["ticket"] == BROWSER_TICKET
    assert CALLBACK_TOKEN not in started.text
    assert PROXY_URL not in started.text
    assert "ticket_digest" not in status_response.text
    assert "callback_token" not in status_response.text
    assert duplicate.status_code == 409
    assert len(runtime.started) == 1
    assert len(service.authorizations) == 1
    stored: Final = await repository.get(UUID(session_id))
    assert stored is not None
    assert stored.callback_token_digest != CALLBACK_TOKEN


@pytest.mark.asyncio
async def test_browser_session_reservation_prevents_losing_authorization_from_overwriting_state(
    tmp_path: Path,
) -> None:
    client, environment_id, _, _, service, _ = _client(tmp_path)
    service.authorization_gate = asyncio.Event()
    async with client:
        client.headers["Authorization"] = f"Bearer {MANAGER_TOKEN}"
        first_task: Final = asyncio.create_task(
            client.post(f"/api/environments/{environment_id}/oauth-browser-sessions")
        )
        await service.authorization_started.wait()
        second: Final = await client.post(f"/api/environments/{environment_id}/oauth-browser-sessions")
        service.authorization_gate.set()
        first: Final = await first_task

    assert first.status_code == 201
    assert second.status_code == 409
    assert len(service.authorizations) == 1


@pytest.mark.asyncio
async def test_browser_ticket_is_manager_independent_single_use_and_expiry_cleans_worker(tmp_path: Path) -> None:
    client, environment_id, repository, runtime, _, _ = _client(tmp_path)
    async with client:
        client.headers["Authorization"] = f"Bearer {MANAGER_TOKEN}"
        started: Final = await client.post(f"/api/environments/{environment_id}/oauth-browser-sessions")
        session_id: Final = started.json()["id"]
        client.headers.pop("Authorization")
        consumed: Final = await client.post(
            f"/internal/oauth-browser-sessions/{session_id}/ticket/consume",
            headers={"Authorization": f"Bearer {BROWSER_TICKET}"},
        )
        replayed: Final = await client.post(
            f"/internal/oauth-browser-sessions/{session_id}/ticket/consume",
            headers={"Authorization": f"Bearer {BROWSER_TICKET}"},
        )
        invalid: Final = await client.post(
            f"/internal/oauth-browser-sessions/{session_id}/ticket/consume",
            headers={"Authorization": f"Bearer {MANAGER_TOKEN}"},
        )
        stored: Final = await repository.get(UUID(session_id))
        assert stored is not None
        expired: Final = stored.model_copy(update={"expires_at": utc_now() - timedelta(seconds=1)})
        repository.sessions[expired.id] = expired
        expired_status: Final = await client.get(
            f"/api/oauth-browser-sessions/{session_id}",
            headers={"Authorization": f"Bearer {MANAGER_TOKEN}"},
        )

    assert consumed.status_code == 200
    assert replayed.status_code == 401
    assert invalid.status_code == 401
    assert expired_status.status_code == 200
    assert expired_status.json()["status"] == "expired"
    assert UUID(session_id) in runtime.removed
    durable: Final = await repository.get(UUID(session_id))
    assert durable is not None and durable.cleanup_pending is False


@pytest.mark.asyncio
async def test_callback_requires_short_lived_token_current_proxy_and_single_state_consumption(tmp_path: Path) -> None:
    client, environment_id, repository, runtime, service, profiles = _client(tmp_path)
    async with client:
        client.headers["Authorization"] = f"Bearer {MANAGER_TOKEN}"
        started: Final = await client.post(f"/api/environments/{environment_id}/oauth-browser-sessions")
        session_id: Final = started.json()["id"]
        client.headers.pop("Authorization")
        payload: Final = {"state": "signed-oauth-state-123456789", "code": "authorization-code-secret"}
        invalid: Final = await client.post(
            f"/internal/oauth-browser-sessions/{session_id}/callback",
            headers={"Authorization": "Bearer wrong-token"},
            json=payload,
        )
        profiles.url = "https://changed-proxy.example:8443"
        changed_proxy: Final = await client.post(
            f"/internal/oauth-browser-sessions/{session_id}/callback",
            headers={"Authorization": f"Bearer {CALLBACK_TOKEN}"},
            json=payload,
        )
        stored: Final = await repository.get(UUID(session_id))

    assert invalid.status_code == 401
    assert changed_proxy.status_code == 409
    assert service.callbacks == []
    assert UUID(session_id) in runtime.removed
    assert CALLBACK_TOKEN not in changed_proxy.text
    assert "authorization-code-secret" not in changed_proxy.text
    assert stored is not None and stored.status is OAuthBrowserSessionStatus.FAILED


@pytest.mark.asyncio
async def test_callback_success_and_replay_cleanup_worker_without_leaking_fields(tmp_path: Path) -> None:
    client, environment_id, repository, runtime, service, _ = _client(tmp_path)
    async with client:
        client.headers["Authorization"] = f"Bearer {MANAGER_TOKEN}"
        started: Final = await client.post(f"/api/environments/{environment_id}/oauth-browser-sessions")
        session_id: Final = started.json()["id"]
        client.headers.pop("Authorization")
        payload: Final = {
            "state": "signed-oauth-state-123456789",
            "code": "authorization-code-secret",
        }
        callback: Final = await client.post(
            f"/internal/oauth-browser-sessions/{session_id}/callback",
            headers={"Authorization": f"Bearer {CALLBACK_TOKEN}"},
            json=payload,
        )
        replayed: Final = await client.post(
            f"/internal/oauth-browser-sessions/{session_id}/callback",
            headers={"Authorization": f"Bearer {CALLBACK_TOKEN}"},
            json=payload,
        )
        status_response: Final = await client.get(
            f"/api/oauth-browser-sessions/{session_id}",
            headers={"Authorization": f"Bearer {MANAGER_TOKEN}"},
        )

    assert callback.status_code == 204
    assert replayed.status_code == 409
    assert len(service.callbacks) == 1
    assert service.callbacks[0].code == "authorization-code-secret"
    assert UUID(session_id) in runtime.removed
    assert status_response.json()["status"] == "completed"
    assert "authorization-code-secret" not in status_response.text
    stored: Final = await repository.get(UUID(session_id))
    assert stored is not None and stored.cleanup_pending is False


@pytest.mark.asyncio
async def test_cancel_and_expired_callback_clean_runtime_and_expired_token_is_rejected(tmp_path: Path) -> None:
    client, environment_id, repository, runtime, _, _ = _client(tmp_path)
    async with client:
        client.headers["Authorization"] = f"Bearer {MANAGER_TOKEN}"
        started: Final = await client.post(f"/api/environments/{environment_id}/oauth-browser-sessions")
        session_id: Final = started.json()["id"]
        cancelled: Final = await client.delete(f"/api/oauth-browser-sessions/{session_id}")
        expired_start: Final = await client.post(f"/api/environments/{environment_id}/oauth-browser-sessions")
        expired_id: Final = expired_start.json()["id"]
        expired_session: Final = await repository.get(UUID(expired_id))
        assert expired_session is not None
        repository.sessions[expired_session.id] = expired_session.model_copy(
            update={"expires_at": utc_now() - timedelta(seconds=1)}
        )
        client.headers.pop("Authorization")
        expired_callback: Final = await client.post(
            f"/internal/oauth-browser-sessions/{expired_id}/callback",
            headers={"Authorization": f"Bearer {CALLBACK_TOKEN}"},
            json={"state": "signed-oauth-state-123456789", "code": "expired-code"},
        )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert expired_callback.status_code == 410
    assert UUID(session_id) in runtime.removed
    assert UUID(expired_id) in runtime.removed
