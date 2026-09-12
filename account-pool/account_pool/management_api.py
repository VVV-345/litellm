"""本模块提供卡片 Key 和日志管理接口，所有操作沿用 Manager 管理鉴权。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Final, Literal, TypeVar, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from account_pool.card_keys import CardKeyChange, CardKeyIssue, CardKeyService, CardKeyStatus
from account_pool.domain import EnvironmentRecord
from account_pool.error_logs import ErrorLogDetail, ErrorLogPage, ErrorLogQuery, ErrorLogService, ErrorStats
from account_pool.policies import (
    AccountPolicy,
    PolicyRepository,
    PolicyUpdate,
    PolicyView,
    policy_capabilities,
    policy_validation_error,
)
from account_pool.ports import EnvironmentRepository
from account_pool.result import Failure, Result
from account_pool.settings import (
    AccountPoolSettings,
    AccountPoolSettingsHistoryEntry,
    AccountPoolSettingsPreview,
    AccountPoolSettingsRepository,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
    settings_preview,
)

T = TypeVar("T")


def create_management_router(
    keys: CardKeyService,
    logs: ErrorLogService,
    environments: EnvironmentRepository,
    authorize: Callable[..., None],
    policies: PolicyRepository,
    settings: AccountPoolSettingsRepository | None = None,
    sync_settings: Callable[[AccountPoolSettings], Awaitable[tuple[UUID, ...]]] | None = None,
    sync_policy: Callable[[EnvironmentRecord, AccountPolicy], Awaitable[None]] | None = None,
) -> APIRouter:
    router: Final = APIRouter(prefix="/api", dependencies=[Depends(authorize)])

    async def card(card_id: UUID) -> EnvironmentRecord:
        record: Final = await environments.get(card_id)
        if record is None:
            raise HTTPException(404, "Card not found")
        return record

    @router.get("/cards/{card_id}/key/status")
    async def key_status(card_id: UUID, response: Response) -> CardKeyStatus | None:
        await card(card_id)
        response.headers["Cache-Control"] = "no-store"
        return await keys.status(card_id)

    @router.post("/cards/{card_id}/key")
    async def create_key(card_id: UUID, response: Response) -> CardKeyIssue:
        record: Final = await card(card_id)
        result: Final = unwrap(await keys.issue(card_id))
        response.headers["Cache-Control"] = "no-store"
        await logs.record(record, "card_key", None)
        return result

    @router.post("/cards/{card_id}/key/rotate")
    async def rotate_key(card_id: UUID, request: CardKeyChange, response: Response) -> CardKeyIssue:
        record: Final = await card(card_id)
        result: Final = unwrap(await keys.issue(card_id, request.expected_key_id))
        response.headers["Cache-Control"] = "no-store"
        await logs.record(record, "card_key", None)
        return result

    @router.delete("/cards/{card_id}/key", status_code=204)
    async def revoke_key(card_id: UUID, request: CardKeyChange) -> None:
        record: Final = await card(card_id)
        unwrap(await keys.revoke(card_id, request.expected_key_id))
        await logs.record(record, "card_key", None)

    @router.get("/logs")
    async def list_logs(query: Annotated[ErrorLogQuery, Query()]) -> ErrorLogPage:
        return await logs.repository.query(query)

    @router.get("/logs/export", response_class=PlainTextResponse)
    async def export_logs(query: Annotated[ErrorLogQuery, Query()]) -> PlainTextResponse:
        page: Final = await logs.repository.query(query)
        payload: Final = "\n".join(item.model_dump_json() for item in page.items)
        return PlainTextResponse(
            payload,
            headers={"Content-Disposition": "attachment; filename=account-pool-logs.ndjson"},
            media_type="application/x-ndjson",
        )

    @router.delete("/logs", response_model=LogClearResult)
    async def clear_logs() -> LogClearResult:
        return LogClearResult(deleted=await logs.repository.clear())

    @router.get("/environments/{card_id}/policy")
    async def get_policy(card_id: UUID) -> PolicyView:
        record: Final = await card(card_id)
        policy: Final = await policies.get(card_id)
        return policy.model_copy(update={"capabilities": policy_capabilities(record.supplier)})

    @router.get("/policies")
    async def list_policies() -> tuple[PolicyView, ...]:
        records: Final = {record.id: record for record in await environments.list()}
        return tuple(
            policy.model_copy(
                update={"capabilities": policy_capabilities(records[policy.card_id].supplier)}
            )
            for policy in await policies.list()
            if policy.card_id in records
        )

    @router.put("/environments/{card_id}/policy")
    async def update_policy(card_id: UUID, request: PolicyUpdate) -> PolicyView:
        record: Final = await card(card_id)
        validation_error: Final = await policy_validation_error(record, request.policy, environments)
        if validation_error is not None:
            raise HTTPException(422, validation_error)
        saved: Final = await policies.save(card_id, request)
        if saved is None:
            raise HTTPException(409, "Card is unavailable or policy has changed; refresh before retrying")
        if sync_policy is not None:
            try:
                await sync_policy(record, saved.policy)
            except Exception as error:
                await _set_policy_runtime_status(policies, saved, "failed", "policy runtime synchronization failed")
                raise HTTPException(status_code=502, detail="policy runtime synchronization failed") from error
            runtime_saved: Final = await _set_policy_runtime_status(policies, saved, "synced")
            return runtime_saved.model_copy(update={"capabilities": policy_capabilities(record.supplier)})
        return saved.model_copy(update={"capabilities": policy_capabilities(record.supplier)})

    @router.get("/logs/{event_id}")
    async def log_detail(event_id: UUID) -> ErrorLogDetail:
        result: Final = await logs.repository.detail(event_id)
        if result is None:
            raise HTTPException(404, "Log event not found")
        return result

    @router.get("/stats")
    async def stats(
        card_id: UUID | None = None, account_id: UUID | None = None, model: str | None = None
    ) -> ErrorStats:
        return await logs.repository.stats(card_id, account_id, model)

    if settings is not None:

        @router.get("/settings")
        async def get_settings() -> AccountPoolSettingsView:
            return await settings.get()

        @router.get("/settings/history")
        async def settings_history() -> tuple[AccountPoolSettingsHistoryEntry, ...]:
            return await settings.history()

        @router.put("/settings")
        async def update_settings(request: AccountPoolSettingsUpdate) -> AccountPoolSettingsView:
            saved: Final = await settings.save(request)
            if saved is None:
                raise HTTPException(409, "Settings have changed; refresh before retrying")
            if sync_settings is not None:
                failed_card_ids: Final = await sync_settings(saved.values)
                if failed_card_ids:
                    raise HTTPException(status_code=502, detail="settings runtime synchronization failed")
            return saved

        @router.post("/settings/preview")
        async def preview_settings(request: AccountPoolSettingsUpdate) -> AccountPoolSettingsPreview:
            current: Final = await settings.get()
            return settings_preview(current, request.values, tuple(record.id for record in await environments.list()))

        @router.post("/settings/rollback")
        async def rollback_settings(request: SettingsRollbackRequest) -> AccountPoolSettingsView:
            restored: Final = await settings.rollback(request.expected_version, request.target_version)
            if restored is None:
                raise HTTPException(409, "Settings have changed or the target version does not exist")
            return restored

    return router


class SettingsRollbackRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_version: int = Field(ge=0)
    target_version: int = Field(ge=0)


class LogClearResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    deleted: int = Field(ge=0)


def unwrap(result: Result[T]) -> T:
    if isinstance(result, Failure):
        raise HTTPException(409, result.message)
    return result.value


async def _set_policy_runtime_status(
    policies: PolicyRepository,
    policy: PolicyView,
    status: Literal["partial", "synced", "failed"],
    error: str | None = None,
) -> PolicyView:
    setter: Final = getattr(policies, "set_runtime_status", None)
    if not callable(setter):
        return policy
    update: Final = cast(
        Callable[[UUID, int, Literal["partial", "synced", "failed"], str | None], Awaitable[PolicyView | None]],
        setter,
    )
    return (await update(policy.card_id, policy.version, status, error)) or policy.model_copy(
        update={"runtime_status": status, "runtime_error": error}
    )
