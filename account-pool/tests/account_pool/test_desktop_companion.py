"""验证桌面伴侣票据的安全状态转换、过期处理与管理接口。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final
from uuid import UUID

import pytest
from account_pool.desktop_companion import (
    DesktopCodexCompactAction,
    DesktopStatusAction,
    DesktopTicketCompleteRequest,
    DesktopTicketRecord,
    DesktopTicketService,
)
from account_pool.domain import EnvironmentStatus
from account_pool.management_api import create_management_router
from account_pool.policies import CodexPolicy, PolicyUpdate, PolicyView
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_account_pool import MemoryRepository, _record


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


class Clock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class MemoryDesktopTickets:
    def __init__(self) -> None:
        self.records: dict[UUID, DesktopTicketRecord] = {}

    async def create(self, record: DesktopTicketRecord) -> None:
        self.records[record.ticket_id] = record

    async def get(self, ticket_id: UUID, now: datetime) -> DesktopTicketRecord | None:
        record: Final = self.records.get(ticket_id)
        if record is None:
            return None
        if record.status in ("pending", "claimed") and record.expires_at <= now:
            expired: Final = record.model_copy(update={"status": "expired", "completed_at": now})
            self.records[ticket_id] = expired
            return expired
        return record

    async def claim(self, ticket_id: UUID, secret_hash: str, now: datetime) -> DesktopTicketRecord | None:
        record: Final = await self.get(ticket_id, now)
        if (
            record is None
            or record.status != "pending"
            or record.expires_at <= now
            or record.secret_hash != secret_hash
        ):
            return None
        claimed: Final = record.model_copy(update={"status": "claimed", "claimed_at": now})
        self.records[ticket_id] = claimed
        return claimed

    async def complete(
        self,
        ticket_id: UUID,
        secret_hash: str,
        request: DesktopTicketCompleteRequest,
        now: datetime,
    ) -> DesktopTicketRecord | None:
        record: Final = await self.get(ticket_id, now)
        if record is None or record.status != "claimed" or record.secret_hash != secret_hash:
            return None
        completed: Final = record.model_copy(
            update={
                "status": request.status,
                "completed_at": now,
                "result": request.result,
                "error": request.error,
            }
        )
        self.records[ticket_id] = completed
        return completed


@pytest.mark.asyncio
async def test_ticket_can_only_be_claimed_and_completed_once() -> None:
    now: Final = datetime(2026, 9, 14, tzinfo=timezone.utc)
    repository: Final = MemoryDesktopTickets()
    service: Final = DesktopTicketService(repository, clock=Clock(now), secret_factory=lambda: "s" * 43)
    created: Final = await service.issue(DesktopStatusAction())

    assert created.secret not in repr(repository.records[created.ticket_id])
    assert await service.claim(created.ticket_id, "x" * 43) is None
    claimed: Final = await service.claim(created.ticket_id, created.secret)
    assert claimed is not None and claimed.action.kind == "status"
    assert await service.claim(created.ticket_id, created.secret) is None

    completion: Final = DesktopTicketCompleteRequest(
        secret=created.secret,
        status="succeeded",
        result={"codex_instances": [], "cursor_instances": []},
    )
    completed: Final = await service.complete(created.ticket_id, completion)
    assert completed is not None and completed.status == "succeeded"
    assert completed.result == {"codex_instances": [], "cursor_instances": []}
    assert await service.complete(created.ticket_id, completion) is None


@pytest.mark.asyncio
async def test_pending_ticket_expires_before_claim() -> None:
    now: Final = datetime(2026, 9, 14, tzinfo=timezone.utc)
    clock: Final = Clock(now)
    service: Final = DesktopTicketService(
        MemoryDesktopTickets(),
        lifetime=timedelta(seconds=30),
        clock=clock,
        secret_factory=lambda: "s" * 43,
    )
    created: Final = await service.issue(DesktopStatusAction())
    clock.current = now + timedelta(seconds=31)

    view: Final = await service.get(created.ticket_id)
    assert view is not None and view.status == "expired"
    assert await service.claim(created.ticket_id, created.secret) is None


@pytest.mark.asyncio
async def test_claimed_ticket_expires_before_completion() -> None:
    now: Final = datetime(2026, 9, 14, tzinfo=timezone.utc)
    clock: Final = Clock(now)
    service: Final = DesktopTicketService(
        MemoryDesktopTickets(),
        lifetime=timedelta(seconds=30),
        clock=clock,
        secret_factory=lambda: "s" * 43,
    )
    created: Final = await service.issue(DesktopStatusAction())
    assert await service.claim(created.ticket_id, created.secret) is not None
    clock.current = now + timedelta(seconds=31)

    completion: Final = DesktopTicketCompleteRequest(secret=created.secret, status="succeeded", result={"ok": True})
    assert await service.complete(created.ticket_id, completion) is None
    view: Final = await service.get(created.ticket_id)
    assert view is not None and view.status == "expired"


def test_compact_action_rejects_a_limit_at_or_above_the_context_window() -> None:
    with pytest.raises(ValueError):
        DesktopCodexCompactAction(
            instance_id="__default__",
            model_context_window=100_000,
            auto_compact_token_limit=100_000,
        )


def test_codex_policy_preserves_desktop_compact_settings() -> None:
    policy: Final = CodexPolicy(
        compact_ui=True,
        model_context_window=1_000_000,
        model_auto_compact_token_limit=900_000,
        experimental_context_management=True,
    )

    assert policy.compact_ui is True
    assert policy.model_context_window == 1_000_000
    assert policy.model_auto_compact_token_limit == 900_000
    assert policy.experimental_context_management is True

    with pytest.raises(ValueError):
        CodexPolicy(
            compact_ui=True,
            model_context_window=100_000,
            model_auto_compact_token_limit=100_000,
        )


def test_management_api_keeps_ticket_secret_out_of_status_responses() -> None:
    service: Final = DesktopTicketService(MemoryDesktopTickets(), secret_factory=lambda: "s" * 43)
    app: Final = FastAPI()
    app.include_router(
        create_management_router(
            object(),
            object(),
            MemoryRepository(_record(status=EnvironmentStatus.READY)),
            lambda: None,
            MemoryPolicies(),
            desktop_tickets=service,
        )
    )

    with TestClient(app) as client:
        created: Final = client.post("/api/desktop/tickets", json={"action": {"kind": "status"}})
        ticket_id: Final = created.json()["ticket_id"]
        secret: Final = created.json()["secret"]
        status_response: Final = client.get(f"/api/desktop/tickets/{ticket_id}")
        claimed: Final = client.post(f"/api/desktop/tickets/{ticket_id}/claim", json={"secret": secret})
        repeated: Final = client.post(f"/api/desktop/tickets/{ticket_id}/claim", json={"secret": secret})
        completed: Final = client.post(
            f"/api/desktop/tickets/{ticket_id}/complete",
            json={"secret": secret, "status": "succeeded", "result": {"ok": True}},
        )

    assert created.status_code == 200
    assert status_response.status_code == 200 and secret not in status_response.text
    assert claimed.status_code == 200 and claimed.json()["action"] == {"kind": "status"}
    assert repeated.status_code == 409
    assert completed.status_code == 200 and completed.json()["result"] == {"ok": True}
