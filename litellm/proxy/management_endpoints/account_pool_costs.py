"""成本台账管理员代理，复用现有传输与鉴权，不开放任意 Manager 请求。"""

from collections.abc import Callable
from typing import Annotated, Final, TypeVar
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import TypeAdapter, ValidationError

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_cost_models import (
    PoolCostCreate,
    PoolCostQuery,
    PoolCostRecord,
    PoolCostReport,
    PoolCostVoid,
)
from litellm.proxy.management_endpoints.account_pool_management import (
    ManagementRequest,
    _operator_detail,  # pyright: ignore[reportPrivateUsage]  # Reuse the existing sanitized Manager error boundary.
)

T = TypeVar("T")  # rebind-ok: TypeVar is a type declaration on supported Python 3.10 runtimes.


def parse(response: httpx.Response, adapter: TypeAdapter[T]) -> T:
    if response.is_error:
        raise HTTPException(response.status_code, _operator_detail(response.content))
    try:
        return adapter.validate_json(response.content)
    except ValidationError:
        raise HTTPException(502, "成本服务返回无效，请重试") from None


def create_cost_router(
    request_manager: ManagementRequest, require_admin: Callable[[UserAPIKeyAuth], None]
) -> APIRouter:
    router: Final = APIRouter(prefix="/costs")

    def admin(response: Response, user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> str:
        require_admin(user)
        response.headers["Cache-Control"] = "no-store"  # rebind-ok: FastAPI injects the outgoing response headers.
        return "admin:" + str(user.user_id or "administrator")[:240]

    async def report(
        query: Annotated[PoolCostQuery, Query()], identity: Annotated[str, Depends(admin)]
    ) -> PoolCostReport:
        return parse(
            await request_manager("GET", "/api/costs?" + urlencode(query.model_dump(mode="json")), None),
            TypeAdapter(PoolCostReport),
        )

    async def create(request: PoolCostCreate, identity: Annotated[str, Depends(admin)]) -> PoolCostRecord:
        return parse(
            await request_manager(
                "POST", "/api/costs?" + urlencode((("actor", identity),)), request.model_dump_json().encode()
            ),
            TypeAdapter(PoolCostRecord),
        )

    async def void(record_id: UUID, request: PoolCostVoid, identity: Annotated[str, Depends(admin)]) -> PoolCostRecord:
        return parse(
            await request_manager(
                "POST",
                f"/api/costs/{record_id}/void?" + urlencode((("actor", identity),)),
                request.model_dump_json().encode(),
            ),
            TypeAdapter(PoolCostRecord),
        )

    router.get("", response_model=PoolCostReport)(report)
    router.post("", response_model=PoolCostRecord)(create)
    router.post("/{record_id}/void", response_model=PoolCostRecord)(void)
    return router
