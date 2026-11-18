"""私网成本管理 API，复用 Manager 鉴权，只提供台账录入、报表和作废。"""

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from psycopg import Error as DatabaseError

from account_pool.cost_models import PoolCostCreate, PoolCostQuery, PoolCostRecord, PoolCostReport, PoolCostVoid
from account_pool.cost_repository import CostFailure, CostRepository
from account_pool.costs import cost_report
from account_pool.ports import EnvironmentRepository


class CostRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler: Final = super().get_route_handler()

        async def safe(request: Request) -> Response:
            try:
                response: Final = await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "请检查金额、账号和日期，结束日期不能早于开始日期") from None
            except DatabaseError:
                raise HTTPException(503, "成本存储暂不可用，请重试") from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return safe


def expose(result: PoolCostRecord | CostFailure) -> PoolCostRecord:
    if isinstance(result, CostFailure):
        raise HTTPException(result.status, result.message)
    return result


def create_cost_router(
    repository: CostRepository, environments: EnvironmentRepository, authorize: Callable[..., None]
) -> APIRouter:
    router: Final = APIRouter(prefix="/api/costs", dependencies=[Depends(authorize)], route_class=CostRoute)

    async def report(query: Annotated[PoolCostQuery, Query()]) -> PoolCostReport:
        accounts: Final = await environments.list()
        return cost_report(
            await repository.list(),
            {a.id: a.name for a in accounts},
            query,
            datetime.now(ZoneInfo("Asia/Shanghai")).date(),
        )

    async def create(
        request: PoolCostCreate, actor: Annotated[str, Query(max_length=256)] = "administrator"
    ) -> PoolCostRecord:
        if request.paid_on > datetime.now(ZoneInfo("Asia/Shanghai")).date():
            raise HTTPException(422, "付款日期不能晚于今天，未付款续费不能记为实际支出")
        return expose(await repository.create(request, actor))

    async def void(
        record_id: UUID, request: PoolCostVoid, actor: Annotated[str, Query(max_length=256)] = "administrator"
    ) -> PoolCostRecord:
        return expose(await repository.void(record_id, request.reason, actor))

    async def stop_renewal(record_id: UUID) -> PoolCostRecord:
        return expose(await repository.stop_renewal(record_id))

    router.post("/{record_id}/stop-renewal", response_model=PoolCostRecord)(stop_renewal)
    router.get("", response_model=PoolCostReport)(report)
    router.post("", response_model=PoolCostRecord)(create)
    router.post("/{record_id}/void", response_model=PoolCostRecord)(void)
    return router
