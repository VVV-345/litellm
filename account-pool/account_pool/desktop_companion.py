"""定义桌面伴侣一次性票据的模型、状态转换与安全边界。"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Annotated, Final, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class DesktopStatusAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["status"] = "status"


class DesktopInstanceAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["start_instance", "stop_instance"]
    application: Literal["codex", "cursor"]
    instance_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class DesktopCodexCompactAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["apply_codex_compact"] = "apply_codex_compact"
    instance_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    enabled: bool = True
    model_context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
    auto_compact_token_limit: int | None = Field(default=None, ge=1024, le=10_000_000)
    experimental_context_management: bool = False

    @model_validator(mode="after")
    def compact_limit_precedes_context_window(self) -> DesktopCodexCompactAction:
        if (
            self.enabled
            and self.model_context_window is not None
            and self.auto_compact_token_limit is not None
            and self.auto_compact_token_limit >= self.model_context_window
        ):
            raise ValueError("auto_compact_token_limit must be lower than model_context_window")
        return self


class DesktopCodexWslAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["configure_codex_wsl"] = "configure_codex_wsl"
    enabled: bool
    config_dir: str = Field(default="", max_length=1024)

    @model_validator(mode="after")
    def enabled_sync_requires_a_directory(self) -> DesktopCodexWslAction:
        if self.enabled and not self.config_dir.strip():
            raise ValueError("config_dir is required when WSL synchronization is enabled")
        return self


DesktopAction = Annotated[
    DesktopStatusAction | DesktopInstanceAction | DesktopCodexCompactAction | DesktopCodexWslAction,
    Field(discriminator="kind"),
]
DesktopTicketStatus = Literal["pending", "claimed", "succeeded", "failed", "cancelled", "expired"]
DesktopCompletionStatus = Literal["succeeded", "failed", "cancelled"]


class DesktopTicketCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: DesktopAction


class DesktopTicketRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: UUID
    secret_hash: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    action: DesktopAction
    status: DesktopTicketStatus
    created_at: AwareDatetime
    expires_at: AwareDatetime
    claimed_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    result: dict[str, object] | None = None
    error: str | None = Field(default=None, max_length=2048)


class DesktopTicketCreated(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticket_id: UUID
    secret: str = Field(min_length=40, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", repr=False)
    expires_at: AwareDatetime


class DesktopTicketView(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticket_id: UUID
    action: DesktopAction
    status: DesktopTicketStatus
    created_at: AwareDatetime
    expires_at: AwareDatetime
    claimed_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    result: dict[str, object] | None = None
    error: str | None = None

    @classmethod
    def from_record(cls, record: DesktopTicketRecord) -> DesktopTicketView:
        return cls(**record.model_dump(exclude={"secret_hash"}))


class DesktopTicketClaimRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    secret: str = Field(min_length=40, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", repr=False)


class DesktopTicketClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticket_id: UUID
    action: DesktopAction
    expires_at: AwareDatetime


class DesktopTicketCompleteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    secret: str = Field(min_length=40, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", repr=False)
    status: DesktopCompletionStatus
    result: dict[str, object] | None = None
    error: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def failed_completion_requires_an_error(self) -> DesktopTicketCompleteRequest:
        if self.status == "failed" and not (self.error or "").strip():
            raise ValueError("error is required for failed desktop operations")
        if self.status != "failed" and self.error is not None:
            raise ValueError("error is only accepted for failed desktop operations")
        return self


class DesktopTicketRepository(Protocol):
    async def create(self, record: DesktopTicketRecord) -> None: ...

    async def get(self, ticket_id: UUID, now: datetime) -> DesktopTicketRecord | None: ...

    async def claim(self, ticket_id: UUID, secret_hash: str, now: datetime) -> DesktopTicketRecord | None: ...

    async def complete(
        self,
        ticket_id: UUID,
        secret_hash: str,
        request: DesktopTicketCompleteRequest,
        now: datetime,
    ) -> DesktopTicketRecord | None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class DesktopTicketService:
    def __init__(
        self,
        repository: DesktopTicketRepository,
        *,
        lifetime: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = _utc_now,
        secret_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
    ) -> None:
        self._repository: Final = repository
        self._lifetime: Final = lifetime
        self._clock: Final = clock
        self._secret_factory: Final = secret_factory

    async def issue(self, action: DesktopAction) -> DesktopTicketCreated:
        now: Final = self._clock()
        secret: Final = self._secret_factory()
        record: Final = DesktopTicketRecord(
            ticket_id=uuid4(),
            secret_hash=_secret_hash(secret),
            action=action,
            status="pending",
            created_at=now,
            expires_at=now + self._lifetime,
        )
        await self._repository.create(record)
        return DesktopTicketCreated(ticket_id=record.ticket_id, secret=secret, expires_at=record.expires_at)

    async def get(self, ticket_id: UUID) -> DesktopTicketView | None:
        record: Final = await self._repository.get(ticket_id, self._clock())
        return None if record is None else DesktopTicketView.from_record(record)

    async def claim(self, ticket_id: UUID, secret: str) -> DesktopTicketClaim | None:
        record: Final = await self._repository.claim(ticket_id, _secret_hash(secret), self._clock())
        return (
            None
            if record is None
            else DesktopTicketClaim(ticket_id=record.ticket_id, action=record.action, expires_at=record.expires_at)
        )

    async def complete(self, ticket_id: UUID, request: DesktopTicketCompleteRequest) -> DesktopTicketView | None:
        record: Final = await self._repository.complete(
            ticket_id,
            _secret_hash(request.secret),
            request,
            self._clock(),
        )
        return None if record is None else DesktopTicketView.from_record(record)
