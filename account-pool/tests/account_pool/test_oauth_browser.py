"""验证受控 OAuth 浏览器会话票据的生命周期和保密边界。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final
from uuid import UUID, uuid4

import pytest
from account_pool.oauth_browser import (
    OAuthBrowserSession,
    OAuthBrowserSessionGrant,
    OAuthBrowserSessionRepository,
    OAuthBrowserSessionService,
    OAuthBrowserSessionStatus,
)
from account_pool.repository import (
    PostgresEnvironmentRepository,
    PostgresOAuthBrowserSessionRepository,
    database_connection,
)
from account_pool.shared.result import Failure


@dataclass
class MemoryBrowserSessions:
    sessions: dict[UUID, OAuthBrowserSession]
    lock: asyncio.Lock

    @classmethod
    def create(cls) -> MemoryBrowserSessions:
        return cls({}, asyncio.Lock())

    async def create_if_absent(self, session: OAuthBrowserSession) -> bool:
        async with self.lock:
            if any(
                current.environment_id == session.environment_id
                and current.status in {OAuthBrowserSessionStatus.STARTING, OAuthBrowserSessionStatus.ACTIVE}
                and current.expires_at > session.created_at
                for current in self.sessions.values()
            ):
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


def _service(
    repository: OAuthBrowserSessionRepository,
    now: datetime,
) -> OAuthBrowserSessionService:
    tokens: Final = iter(("browser-ticket-secret", "callback-relay-secret"))
    return OAuthBrowserSessionService(
        repository,
        fingerprint_key=b"fingerprint-key-for-tests-123456",
        ttl=timedelta(minutes=5),
        clock=lambda: now,
        token_factory=lambda: next(tokens),
        session_id_factory=uuid4,
    )


@pytest.mark.asyncio
async def test_start_persists_only_digests_of_browser_and_callback_tickets() -> None:
    now: Final = datetime(2026, 10, 7, tzinfo=timezone.utc)
    repository: Final = MemoryBrowserSessions.create()
    service: Final = _service(repository, now)

    result: Final = await service.start(uuid4(), "proxy-1", "https://proxy.example:8443")

    assert not isinstance(result, Failure)
    grant: Final = result.value
    assert isinstance(grant, OAuthBrowserSessionGrant)
    assert grant.ticket == "browser-ticket-secret"
    assert grant.callback_token == "callback-relay-secret"
    assert grant.session.ticket_digest != grant.ticket
    assert grant.session.callback_token_digest != grant.callback_token
    assert "browser-ticket-secret" not in repr(grant.session)
    assert "callback-relay-secret" not in repr(grant.session)
    assert OAuthBrowserSessionService.callback_token_matches(grant.session, grant.callback_token)
    assert not OAuthBrowserSessionService.callback_token_matches(grant.session, "wrong-token")


@pytest.mark.asyncio
async def test_only_one_unexpired_session_can_be_started_for_an_environment() -> None:
    now: Final = datetime(2026, 10, 7, tzinfo=timezone.utc)
    repository: Final = MemoryBrowserSessions.create()
    environment_id: Final = uuid4()

    first: Final = await _service(repository, now).start(environment_id, "proxy-1", "http://proxy.example:8080")
    second: Final = await _service(repository, now).start(environment_id, "proxy-1", "http://proxy.example:8080")

    assert not isinstance(first, Failure)
    assert isinstance(second, Failure)


@pytest.mark.asyncio
async def test_browser_ticket_is_single_use_and_rejected_after_expiry() -> None:
    now: Final = datetime(2026, 10, 7, tzinfo=timezone.utc)
    repository: Final = MemoryBrowserSessions.create()
    service: Final = _service(repository, now)
    started: Final = await service.start(uuid4(), "proxy-1", "http://proxy.example:8080")
    assert not isinstance(started, Failure)
    activated: Final = await service.activate(started.value.session.id)
    assert activated is not None

    consumed: Final = await service.consume_ticket(started.value.session.id, started.value.ticket)
    replayed: Final = await service.consume_ticket(started.value.session.id, started.value.ticket)

    assert consumed is not None
    assert consumed.ticket_consumed_at == now
    assert replayed is None
    expired_start: Final = await _service(repository, now + timedelta(minutes=6)).start(
        uuid4(),
        "proxy-1",
        "http://proxy.example:8080",
    )
    assert not isinstance(expired_start, Failure)
    expired_activation: Final = await _service(repository, now + timedelta(minutes=6)).activate(
        expired_start.value.session.id
    )
    assert expired_activation is not None
    expired: Final = await service.consume_ticket(
        expired_start.value.session.id,
        expired_start.value.ticket,
        now=now + timedelta(minutes=12),
    )
    assert expired is None


@pytest.mark.asyncio
async def test_parallel_session_start_has_a_single_winner() -> None:
    now: Final = datetime(2026, 10, 7, tzinfo=timezone.utc)
    repository: Final = MemoryBrowserSessions.create()
    environment_id: Final = uuid4()

    first, second = await asyncio.gather(
        _service(repository, now).start(environment_id, "proxy-1", "http://proxy.example:8080"),
        _service(repository, now).start(environment_id, "proxy-1", "http://proxy.example:8080"),
    )

    assert sum(not isinstance(result, Failure) for result in (first, second)) == 1


@pytest.mark.asyncio
async def test_cancelled_session_cannot_be_activated_or_consume_its_ticket() -> None:
    now: Final = datetime(2026, 10, 7, tzinfo=timezone.utc)
    repository: Final = MemoryBrowserSessions.create()
    service: Final = _service(repository, now)
    started: Final = await service.start(uuid4(), "proxy-1", "http://proxy.example:8080")
    assert not isinstance(started, Failure)

    cancelled: Final = await service.transition(started.value.session.id, OAuthBrowserSessionStatus.CANCELLED)
    activated: Final = await service.activate(started.value.session.id)
    consumed: Final = await service.consume_ticket(started.value.session.id, started.value.ticket)

    assert cancelled is not None
    assert cancelled.status is OAuthBrowserSessionStatus.CANCELLED
    assert activated is None
    assert consumed is None


@pytest.mark.asyncio(loop_factories=["selector"])
async def test_postgres_repository_enforces_active_session_and_ticket_uniqueness() -> None:
    database_url: Final[str | None] = os.getenv("ACCOUNT_POOL_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("ACCOUNT_POOL_TEST_DATABASE_URL is not configured")

    environment_id: Final = uuid4()
    environment_repository: Final = PostgresEnvironmentRepository(database_url)
    await environment_repository.initialize()
    async with database_connection(database_url) as connection:
        await connection.execute(
            "INSERT INTO account_pool_environments (id, payload, updated_at) VALUES (%s, '{}'::jsonb, %s)",
            (environment_id, datetime.now(timezone.utc)),
        )

    repository: Final = PostgresOAuthBrowserSessionRepository(database_url)
    service: Final = _service(repository, datetime.now(timezone.utc))
    started: Final = await service.start(environment_id, "proxy-1", "http://proxy.example:8080")
    duplicate: Final = await _service(repository, datetime.now(timezone.utc)).start(
        environment_id,
        "proxy-1",
        "http://proxy.example:8080",
    )
    try:
        assert not isinstance(started, Failure)
        assert isinstance(duplicate, Failure)
        activated: Final = await service.activate(started.value.session.id)
        assert activated is not None
        consumed: Final = await service.consume_ticket(started.value.session.id, started.value.ticket)
        replayed: Final = await service.consume_ticket(started.value.session.id, started.value.ticket)
        assert consumed is not None
        assert replayed is None
    finally:
        async with database_connection(database_url) as connection:
            await connection.execute("DELETE FROM account_pool_environments WHERE id = %s", (environment_id,))
