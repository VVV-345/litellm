"""定义受控 OAuth 浏览器会话、一次性票据及其持久化端口。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from account_pool.config import validate_proxy_profile_url
from account_pool.domain import utc_now
from account_pool.shared.result import Failure, FailureCode, Result, Success


class OAuthBrowserSessionStatus(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"


class OAuthBrowserSession(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    environment_id: UUID
    status: OAuthBrowserSessionStatus
    proxy_profile_id: str = Field(min_length=1, max_length=120)
    proxy_url_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    ticket_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    callback_token_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    ticket_consumed_at: datetime | None = None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OAuthBrowserSessionGrant:
    session: OAuthBrowserSession
    ticket: str = field(repr=False)
    callback_token: str = field(repr=False)


class OAuthBrowserSessionRepository(Protocol):
    async def create_if_absent(self, session: OAuthBrowserSession) -> bool: ...

    async def get(self, session_id: UUID) -> OAuthBrowserSession | None: ...

    async def consume_ticket(
        self,
        session_id: UUID,
        ticket_digest: str,
        now: datetime,
    ) -> OAuthBrowserSession | None: ...

    async def transition(
        self,
        session_id: UUID,
        expected: tuple[OAuthBrowserSessionStatus, ...],
        target: OAuthBrowserSessionStatus,
    ) -> OAuthBrowserSession | None: ...


class OAuthBrowserSessionService:
    def __init__(
        self,
        repository: OAuthBrowserSessionRepository,
        *,
        fingerprint_key: bytes,
        ttl: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] = utc_now,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        session_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository: Final = repository
        self._fingerprint_key: Final = fingerprint_key
        self._ttl: Final = ttl
        self._clock: Final = clock
        self._token_factory: Final = token_factory
        self._session_id_factory: Final = session_id_factory

    async def start(
        self,
        environment_id: UUID,
        proxy_profile_id: str,
        proxy_url: str,
    ) -> Result[OAuthBrowserSessionGrant]:
        now: Final = self._clock()
        try:
            normalized_proxy_url: Final = validate_proxy_profile_url(proxy_url)
        except ValueError:
            return Failure(FailureCode.INVALID, "proxy profile URL is invalid")
        normalized_proxy_profile_id: Final = proxy_profile_id.strip()
        if not normalized_proxy_profile_id:
            return Failure(FailureCode.INVALID, "proxy profile is required")
        ticket: Final = self._token_factory()
        callback_token: Final = self._token_factory()
        session: Final = OAuthBrowserSession(
            id=self._session_id_factory(),
            environment_id=environment_id,
            status=OAuthBrowserSessionStatus.STARTING,
            proxy_profile_id=normalized_proxy_profile_id,
            proxy_url_fingerprint=hmac.new(
                self._fingerprint_key,
                normalized_proxy_url.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest(),
            ticket_digest=_digest(ticket),
            callback_token_digest=_digest(callback_token),
            created_at=now,
            expires_at=now + self._ttl,
        )
        if not await self._repository.create_if_absent(session):
            return Failure(FailureCode.CONFLICT, "an OAuth browser session is already active")
        return Success(OAuthBrowserSessionGrant(session, ticket, callback_token))

    async def activate(self, session_id: UUID) -> OAuthBrowserSession | None:
        return await self._repository.transition(
            session_id,
            (OAuthBrowserSessionStatus.STARTING,),
            OAuthBrowserSessionStatus.ACTIVE,
        )

    async def consume_ticket(
        self,
        session_id: UUID,
        ticket: str,
        *,
        now: datetime | None = None,
    ) -> OAuthBrowserSession | None:
        return await self._repository.consume_ticket(session_id, _digest(ticket), now or self._clock())

    async def transition(
        self,
        session_id: UUID,
        target: OAuthBrowserSessionStatus,
    ) -> OAuthBrowserSession | None:
        if target in {OAuthBrowserSessionStatus.STARTING, OAuthBrowserSessionStatus.ACTIVE}:
            return None
        return await self._repository.transition(
            session_id,
            (OAuthBrowserSessionStatus.STARTING, OAuthBrowserSessionStatus.ACTIVE),
            target,
        )

    @staticmethod
    def callback_token_matches(session: OAuthBrowserSession, token: str) -> bool:
        return hmac.compare_digest(session.callback_token_digest, _digest(token))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
