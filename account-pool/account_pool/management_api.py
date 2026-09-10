"""本模块提供卡片 Key 和日志管理接口，所有操作沿用 Manager 管理鉴权。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Final, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from account_pool.card_keys import CardKeyChange, CardKeyIssue, CardKeyService, CardKeyStatus
from account_pool.domain import EnvironmentRecord
from account_pool.error_logs import ErrorLogDetail, ErrorLogPage, ErrorLogQuery, ErrorLogService
from account_pool.ports import EnvironmentRepository
from account_pool.policies import PolicyRepository, PolicyUpdate, PolicyView
from account_pool.result import Failure, Result

T = TypeVar("T")


def create_management_router(
    keys: CardKeyService, logs: ErrorLogService, environments: EnvironmentRepository,
    authorize: Callable[..., None], policies: PolicyRepository,
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

    @router.get("/environments/{card_id}/policy")
    async def get_policy(card_id: UUID) -> PolicyView:
        await card(card_id)
        return await policies.get(card_id)

    @router.get("/policies")
    async def list_policies() -> tuple[PolicyView, ...]:
        return await policies.list()

    @router.put("/environments/{card_id}/policy")
    async def update_policy(card_id: UUID, request: PolicyUpdate) -> PolicyView:
        record: Final = await card(card_id)
        if request.policy.codex is not None and record.supplier.value != "openai_codex":
            raise HTTPException(422, "Codex settings apply only to Codex accounts")
        unknown: Final = tuple(item.target for item in request.policy.model_aliases
                               if item.target not in record.available_models)
        if unknown:
            raise HTTPException(422, "Model aliases contain unavailable targets")
        saved: Final = await policies.save(card_id, request)
        if saved is None:
            raise HTTPException(409, "Card is unavailable or policy has changed; refresh before retrying")
        return saved

    @router.get("/logs/{event_id}")
    async def log_detail(event_id: UUID) -> ErrorLogDetail:
        result: Final = await logs.repository.detail(event_id)
        if result is None:
            raise HTTPException(404, "Log event not found")
        return result

    return router


def unwrap(result: Result[T]) -> T:
    if isinstance(result, Failure):
        raise HTTPException(409, result.message)
    return result.value
