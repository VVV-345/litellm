"""本模块代理卡片凭据、策略和日志接口，复用统一传输及管理员权限检查。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Final, Literal, TypeVar
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import TypeAdapter

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management_models import (
    BatchJob,
    BatchRequest,
    CardKeyChange,
    CardKeyIssue,
    CardKeyStatus,
    ErrorLogDetail,
    ErrorLogPage,
    ErrorLogQuery,
    ErrorStats,
    PolicyUpdate,
    PolicyView,
)

ManagementRequest = Callable[[Literal["GET", "POST", "PUT", "DELETE"], str, bytes | None], Awaitable[httpx.Response]]


def create_management_router(
    request_manager: ManagementRequest, require_admin: Callable[[UserAPIKeyAuth], None],
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> None:
        require_admin(user)

    router: Final = APIRouter(dependencies=[Depends(authorize)])

    async def call(method: Literal["GET", "POST", "PUT", "DELETE"], path: str, body: bytes | None = None) -> bytes:
        response: Final = await request_manager(method, path, body)
        if response.is_error:
            raise HTTPException(response.status_code, "Account pool operation failed; refresh and retry")
        return response.content

    @router.get("/cards/{card_id}/key/status")
    async def key_status(card_id: UUID, response: Response) -> CardKeyStatus | None:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(await call("GET", f"/api/cards/{card_id}/key/status"), TypeAdapter(CardKeyStatus | None))

    @router.post("/cards/{card_id}/key")
    async def create_key(card_id: UUID, response: Response) -> CardKeyIssue:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(await call("POST", f"/api/cards/{card_id}/key"), TypeAdapter(CardKeyIssue))

    @router.post("/cards/{card_id}/key/rotate")
    async def rotate_key(card_id: UUID, request: CardKeyChange, response: Response) -> CardKeyIssue:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(
            await call("POST", f"/api/cards/{card_id}/key/rotate", request.model_dump_json().encode()), TypeAdapter(CardKeyIssue)
        )

    @router.delete("/cards/{card_id}/key", status_code=204)
    async def revoke_key(card_id: UUID, request: CardKeyChange) -> None:
        await call("DELETE", f"/api/cards/{card_id}/key", request.model_dump_json().encode())

    @router.get("/logs")
    async def logs(query: Annotated[ErrorLogQuery, Query()]) -> ErrorLogPage:
        params: Final = httpx.QueryParams(query.model_dump(mode="json", exclude_none=True))
        return parse_response(await call("GET", f"/api/logs?{params}"), TypeAdapter(ErrorLogPage))

    @router.get("/logs/{event_id}")
    async def log_detail(event_id: UUID) -> ErrorLogDetail:
        return parse_response(await call("GET", f"/api/logs/{event_id}"), TypeAdapter(ErrorLogDetail))

    @router.get("/environments/{card_id}/policy")
    async def get_policy(card_id: UUID) -> PolicyView:
        return parse_response(await call("GET", f"/api/environments/{card_id}/policy"), TypeAdapter(PolicyView))

    @router.get("/policies")
    async def list_policies() -> tuple[PolicyView, ...]:
        return parse_response(await call("GET", "/api/policies"), TypeAdapter(tuple[PolicyView, ...]))

    @router.put("/environments/{card_id}/policy")
    async def save_policy(card_id: UUID, request: PolicyUpdate) -> PolicyView:
        return parse_response(
            await call("PUT", f"/api/environments/{card_id}/policy", request.model_dump_json().encode()), TypeAdapter(PolicyView)
        )

    @router.post("/batches", response_model=BatchJob, status_code=202)
    async def submit_batch(request: BatchRequest) -> BatchJob:
        return parse_response(
            await call("POST", "/api/batches", request.model_dump_json().encode()), TypeAdapter(BatchJob)
        )

    @router.get("/batches", response_model=tuple[BatchJob, ...])
    async def list_batches() -> tuple[BatchJob, ...]:
        return parse_response(await call("GET", "/api/batches"), TypeAdapter(tuple[BatchJob, ...]))

    @router.get("/batches/{job_id}", response_model=BatchJob)
    async def get_batch(job_id: UUID) -> BatchJob:
        return parse_response(await call("GET", f"/api/batches/{job_id}"), TypeAdapter(BatchJob))

    @router.get("/stats", response_model=ErrorStats)
    async def stats(
        card_id: UUID | None = None,
        account_id: UUID | None = None,
        model: str | None = None,
    ) -> ErrorStats:
        params: Final = httpx.QueryParams(
            {
                key: str(value)
                for key, value in (("card_id", card_id), ("account_id", account_id), ("model", model))
                if value is not None
            }
        )
        path: Final = "/api/stats" if not params else f"/api/stats?{params}"
        return parse_response(await call("GET", path), TypeAdapter(ErrorStats))

    return router


T = TypeVar("T")


def parse_response(payload: bytes, adapter: TypeAdapter[T]) -> T:
    try:
        return adapter.validate_json(payload)
    except ValueError as error:
        raise HTTPException(502, "Account Pool Manager returned an invalid response") from error
