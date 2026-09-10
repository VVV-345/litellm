"""本模块定义结构化日志、筛选和控制面事件采集，不保存请求正文或认证凭据。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Final, Literal, Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from account_pool.domain import ChannelKind, EnvironmentRecord, SupplierKind, utc_now
from account_pool.error_safety import safe_error

LogStage = Literal[
    "provisioning",
    "authorization",
    "validation",
    "configuration",
    "quota",
    "cleanup",
    "authentication",
    "routing",
    "connection",
    "upstream",
    "response",
    "card_key",
]
ErrorCategory = Literal[
    "authentication",
    "authorization",
    "rate_limit",
    "timeout",
    "connection",
    "invalid_request",
    "upstream",
    "configuration",
    "unknown",
]
_LOGGER: Final = logging.getLogger(__name__)
MODEL_REQUEST_OPERATION: Final = "model_request"


class ErrorLogRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: AwareDatetime = Field(default_factory=utc_now)
    finished_at: AwareDatetime | None = None
    channel: ChannelKind
    supplier: SupplierKind
    card_id: UUID
    environment_id: UUID
    account_id: UUID
    card_key_id: UUID | None = None
    request_id: UUID = Field(default_factory=uuid4)
    trace_id: UUID | None = None
    attempt: int = Field(default=1, ge=1)
    operation: str = Field(max_length=160)
    stage: LogStage
    model: str | None = Field(default=None, max_length=256)
    endpoint: str | None = Field(default=None, max_length=256)
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] | None = None
    error_category: ErrorCategory | None = None
    severity: Literal["info", "warning", "error"] = "error"
    http_status: int | None = Field(default=None, ge=100, le=599)
    upstream_code: str | None = Field(default=None, max_length=120)
    retryable: bool = False
    retry_count: int = Field(default=0, ge=0)
    switched_account: bool = False
    next_account_id: UUID | None = None
    message: str
    detail: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    final_status: Literal["failed", "retrying", "succeeded"] = "failed"

    @field_validator("message", "detail", "model", "endpoint", "upstream_code", "operation")
    @classmethod
    def redact_text(cls, value: str | None) -> str | None:
        return None if value is None else safe_error(RuntimeError(value))


class ErrorLogQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    occurred_from: AwareDatetime | None = None
    occurred_to: AwareDatetime | None = None
    channel: ChannelKind | None = None
    supplier: SupplierKind | None = None
    card_id: UUID | None = None
    environment_id: UUID | None = None
    account_id: UUID | None = None
    card_key_id: UUID | None = None
    request_id: UUID | None = None
    model: str | None = Field(default=None, max_length=256)
    stage: LogStage | None = None
    error_category: ErrorCategory | None = None
    retryable: bool | None = None
    switched_account: bool | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def ordered_time_range(self) -> ErrorLogQuery:
        if self.occurred_from and self.occurred_to and self.occurred_from > self.occurred_to:
            raise ValueError("occurred_from must not be after occurred_to")
        return self


class ErrorLogPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[ErrorLogRecord, ...]
    has_more: bool


class ErrorLogDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: ErrorLogRecord
    attempts: tuple[ErrorLogRecord, ...]
    has_more: bool


class ErrorStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    card_id: UUID | None = None
    account_id: UUID | None = None
    model: str | None = None
    total_requests: int = 0
    succeeded_requests: int = 0
    failed_requests: int = 0
    retried_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    average_duration_ms: float | None = None
    recent_errors: tuple[ErrorLogRecord, ...] = ()


class ErrorLogRepository(Protocol):
    async def append(self, event: ErrorLogRecord) -> None: ...

    async def query(self, query: ErrorLogQuery) -> ErrorLogPage: ...

    async def detail(self, event_id: UUID) -> ErrorLogDetail | None: ...

    async def stats(self, card_id: UUID | None, account_id: UUID | None, model: str | None) -> ErrorStats: ...

    async def prune(self, before: datetime) -> None: ...


class ErrorLogService:
    def __init__(self, repository: ErrorLogRepository, retention_days: int = 30) -> None:
        self.repository: Final = repository
        self.retention_days: Final = retention_days

    async def record(
        self,
        record: EnvironmentRecord,
        stage: LogStage,
        error: Exception | None,
        *,
        retryable: bool = False,
        started_at: datetime | None = None,
    ) -> None:
        now: Final = utc_now()
        status: Final = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        category: Final[ErrorCategory | None] = (
            None
            if error is None
            else "timeout"
            if isinstance(error, (TimeoutError, httpx.TimeoutException))
            else "connection"
            if isinstance(error, httpx.TransportError)
            else "authentication"
            if status == 401
            else "authorization"
            if status == 403
            else "rate_limit"
            if status == 429
            else "upstream"
            if status is not None
            else "configuration"
            if stage == "configuration"
            else "unknown"
        )
        # operation_id 可由客户端指定；使用命名空间 UUID 关联日志，避免原值夹带凭据。
        from uuid import uuid5

        request_id: Final = uuid5(record.id, record.operation_id or f"{stage}:{record.version}")
        event: Final = ErrorLogRecord(
            occurred_at=started_at or now,
            finished_at=now,
            channel=record.channel,
            supplier=record.supplier,
            card_id=record.id,
            environment_id=record.id,
            account_id=record.id,
            request_id=request_id,
            operation=stage,
            stage=stage,
            http_status=status,
            error_category=category,
            severity="info" if error is None else "error",
            retryable=retryable,
            message="Operation completed" if error is None else safe_error(error),
            duration_ms=None if started_at is None else max(0, int((now - started_at).total_seconds() * 1000)),
            final_status="succeeded" if error is None else "retrying" if retryable else "failed",
        )
        try:
            await self.repository.append(event)
        except Exception:
            _LOGGER.error("Account pool event persistence failed: card=%s event=%s", record.id, event.event_id)

    async def maintain(self, stopped: asyncio.Event) -> None:
        while not stopped.is_set():
            try:
                await self.repository.prune(utc_now() - timedelta(days=self.retention_days))
            except Exception:
                _LOGGER.error("Account pool log retention failed")
            try:
                await asyncio.wait_for(stopped.wait(), timeout=3600)
            except TimeoutError:
                continue
