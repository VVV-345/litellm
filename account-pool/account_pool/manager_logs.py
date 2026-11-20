"""只采集与浏览器会话绑定的结构化 Manager 事件，不保存原始日志。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Final, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from account_pool.domain import utc_now
from account_pool.error_logs import ErrorLogRecord

if TYPE_CHECKING:
    from account_pool.ports import EnvironmentRepository

MANAGER_BROWSER_OPERATION: Final = "oauth_browser"
_LOGGER: Final = logging.getLogger(__name__)

BrowserStartupStep = Literal["login_script", "profile", "browser_launch", "page_navigation", "control_listener", "driver"]
BrowserPageStep = Literal["authorize", "login", "email", "password", "authenticator", "email_verification", "consent"]
BrowserPageReason = Literal[
    "browser_security_challenge", "email_verification", "navigation_http_error", "unexpected_origin",
    "unrecognized_route", "form_or_transition_not_confirmed", "page_observation_failed",
    "form_fill_timeout", "form_fill_failed", "form_submit_timeout", "form_submit_failed",
]
ManagerLogStep = BrowserStartupStep | BrowserPageStep | Literal["compose", "callback", "credentials", "model_check", "cleanup"]
ManagerLogState = Literal["started", "ready", "running", "waiting", "manual", "failed", "expired", "cancelled", "completed"]
ManagerLogSeverity = Literal["info", "warning", "error"]
ManagerLogReason = BrowserPageReason | Literal[
    "timeout", "proxy_error", "tls_error", "process_error", "docker_timeout", "compose_failed",
    "script_invalid", "script_mismatch", "card_changed", "session_changed", "startup_interrupted",
    "worker_unreachable", "worker_not_running", "browser_driver_failed", "verification_required",
    "account_verification_required", "manual_takeover", "account_mismatch", "confirmation_required",
    "job_timeout", "human_timeout", "credentials_unavailable", "model_unavailable", "model_check_failed",
    "model_check_interrupted", "browser_start_failed", "cleanup_pending",
]


class BrowserPageDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    step: BrowserPageStep
    reason: BrowserPageReason | None = None
    http_status: int | None = Field(default=None, ge=100, le=599, strict=True)


class ManagerDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    step: ManagerLogStep
    state: ManagerLogState
    reason: ManagerLogReason | None = None
    http_status: int | None = Field(default=None, ge=100, le=599, strict=True)
    duration_ms: int | None = Field(default=None, ge=0, le=3_600_000, strict=True)


class ManagerLogItem(ManagerDiagnostic):
    event_id: UUID
    occurred_at: AwareDatetime
    card_id: UUID
    session_id: UUID | None
    severity: ManagerLogSeverity


class ManagerLogQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: UUID | None = None
    card_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)


class ManagerLogPage(BaseModel):
    items: tuple[ManagerLogItem, ...]
    has_more: bool


class ManagerLogDeleteResult(BaseModel):
    deleted: int = Field(ge=0)


class ManagerLogRepository(Protocol):
    async def append(self, event: ErrorLogRecord) -> None: ...
    async def query_browser_logs(self, query: ManagerLogQuery) -> tuple[ErrorLogRecord, ...]: ...
    async def delete_browser_logs(self, before: datetime) -> int: ...


RecordManagerDiagnostic = Callable[[UUID, UUID | None, ManagerDiagnostic], Awaitable[None]]


def _public_event(event: ErrorLogRecord) -> ManagerLogItem | None:
    try:
        return ManagerLogItem.model_validate({
            "event_id": event.event_id,
            "occurred_at": event.occurred_at,
            "card_id": event.card_id,
            "session_id": event.session_id,
            "step": event.message,
            "state": event.detail,
            "reason": event.upstream_code,
            "severity": event.severity,
            "http_status": event.http_status,
            "duration_ms": event.duration_ms,
        })
    except ValidationError:
        return None


@dataclass(frozen=True, slots=True)
class ManagerLogs:
    repository: ManagerLogRepository
    environments: EnvironmentRepository
    clock: Callable[[], datetime] = utc_now

    async def record(self, card_id: UUID, session_id: UUID | None, diagnostic: ManagerDiagnostic) -> None:
        try:
            if session_id is not None and diagnostic.state in {"running", "waiting", "manual"}:
                recent: Final = await self.repository.query_browser_logs(
                    ManagerLogQuery(session_id=session_id, card_id=card_id, limit=1)
                )
                last: Final = recent[0] if recent else None
                if last is not None and (
                    last.message, last.detail, last.upstream_code, last.http_status, last.duration_ms
                ) == (
                    diagnostic.step, diagnostic.state, diagnostic.reason, diagnostic.http_status, diagnostic.duration_ms
                ):
                    return
            record: Final = await self.environments.get(card_id)
            if record is None:
                return
            severity: Final[ManagerLogSeverity] = (
                "error" if diagnostic.state == "failed"
                else "warning" if diagnostic.state in {"waiting", "expired"}
                else "info"
            )
            # 复用日志存储和现有留存，只向既有字段写入固定诊断代码。
            await self.repository.append(ErrorLogRecord(
                occurred_at=self.clock(), card_id=card_id, account_id=card_id, environment_id=card_id,
                channel=record.channel, supplier=record.supplier, operation=MANAGER_BROWSER_OPERATION,
                session_id=str(session_id) if session_id is not None else None,
                stage="authorization", message=diagnostic.step, detail=diagnostic.state,
                severity=severity, upstream_code=diagnostic.reason, http_status=diagnostic.http_status,
                duration_ms=diagnostic.duration_ms, final_status="failed" if severity == "error" else "succeeded",
            ))
        except Exception:
            _LOGGER.warning("Manager OAuth diagnostic persistence failed: card=%s session=%s", card_id, session_id)

    async def list(self, query: ManagerLogQuery) -> ManagerLogPage:
        events: Final = await self.repository.query_browser_logs(query)
        return ManagerLogPage(
            items=tuple(item for event in events[:query.limit] if (item := _public_event(event)) is not None),
            has_more=len(events) > query.limit,
        )

    async def delete(self, days: int) -> ManagerLogDeleteResult:
        return ManagerLogDeleteResult(deleted=await self.repository.delete_browser_logs(self.clock() - timedelta(days=days)))


def create_manager_logs_router(logs: ManagerLogs, authorize: Callable[..., None]) -> APIRouter:
    router: Final = APIRouter(prefix="/api/manager-logs", dependencies=[Depends(authorize)])

    async def list_manager_logs(query: Annotated[ManagerLogQuery, Query()], response: Response) -> ManagerLogPage:
        response.headers["Cache-Control"] = "no-store"
        return await logs.list(query)

    async def delete_manager_logs(
        older_than_days: Annotated[int, Query(ge=1, le=365)], response: Response,
    ) -> ManagerLogDeleteResult:
        response.headers["Cache-Control"] = "no-store"
        return await logs.delete(older_than_days)

    router.add_api_route("", list_manager_logs, methods=["GET"])
    router.add_api_route("", delete_manager_logs, methods=["DELETE"])
    return router
