"""对外暴露 Cockpit 授权范围发布与内部路由状态读取路由。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials

from account_pool.cockpit_scope import (
    CockpitScopePublishRequest,
    CockpitScopeRevokeRequest,
    CockpitScopeService,
    CockpitSubjectRouteState,
    CockpitSubjectScopeView,
    ScopeFailure,
    valid_subject_hash,
)


def create_cockpit_scope_router(
    cockpit_scope: CockpitScopeService | None,
    require_manager: Callable[[HTTPAuthorizationCredentials | None], None],
) -> APIRouter:
    router: Final = APIRouter()

    def scope_result(result: CockpitSubjectScopeView | ScopeFailure) -> CockpitSubjectScopeView:
        if isinstance(result, ScopeFailure):
            raise HTTPException(result.status, result.code, headers={"Cache-Control": "no-store"})
        return result

    def scope_service() -> CockpitScopeService:
        if cockpit_scope is None:
            raise HTTPException(503, "Cockpit scope unavailable", headers={"Cache-Control": "no-store"})
        return cockpit_scope

    @router.get("/api/cockpit/scope/{subject_hash}", dependencies=[Depends(require_manager)])
    async def get_cockpit_scope(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers by decorator.
        subject_hash: str,
        response: Response,
    ) -> CockpitSubjectScopeView:
        response.headers["Cache-Control"] = "no-store"
        if not valid_subject_hash(subject_hash):
            raise HTTPException(422, "invalid_receipt", headers={"Cache-Control": "no-store"})
        return scope_result(await scope_service().read(subject_hash))

    @router.post("/api/cockpit/scope", dependencies=[Depends(require_manager)])
    async def publish_cockpit_scope(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers by decorator.
        request: CockpitScopePublishRequest,
        response: Response,
    ) -> CockpitSubjectScopeView:
        response.headers["Cache-Control"] = "no-store"
        return scope_result(await scope_service().publish(request))

    @router.delete("/api/cockpit/scope", dependencies=[Depends(require_manager)])
    async def revoke_cockpit_scope(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers by decorator.
        request: CockpitScopeRevokeRequest,
        response: Response,
    ) -> CockpitSubjectScopeView:
        response.headers["Cache-Control"] = "no-store"
        return scope_result(await scope_service().revoke(request))

    @router.get(
        "/internal/cockpit/route/{subject_hash}",
        dependencies=[Depends(require_manager)],
        include_in_schema=False,
    )
    async def get_cockpit_route(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers by decorator.
        subject_hash: str,
        response: Response,
    ) -> CockpitSubjectRouteState:
        response.headers["Cache-Control"] = "no-store"
        if not valid_subject_hash(subject_hash):
            raise HTTPException(422, "invalid_receipt", headers={"Cache-Control": "no-store"})
        result: Final = await scope_service().route_state(subject_hash)
        if isinstance(result, ScopeFailure):
            raise HTTPException(result.status, result.code, headers={"Cache-Control": "no-store"})
        return result

    return router
