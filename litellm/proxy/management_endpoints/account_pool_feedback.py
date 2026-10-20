"""本模块代理反馈接口，管理员与机器凭证使用独立鉴权，不开放任意 Manager 转发。"""

import json
from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Final, Literal, TypeVar
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_feedback_models import (
    MAX_FEEDBACK_ATTACHMENT_SIZE,
    FeedbackCreate,
    FeedbackEdit,
    FeedbackIssue,
    FeedbackMutation,
    FeedbackPage,
    FeedbackQuery,
    FeedbackTokenCreate,
    FeedbackTokenIssue,
    FeedbackTokenView,
)
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest

T = TypeVar("T")
MultipartRequest = Callable[[str, dict[str, str], str, bytes, str | None], Awaitable[httpx.Response]]


class _ErrorDetail(BaseModel):
    code: str = Field(pattern=r"^feedback_[a-z_]+$", max_length=100)
    message: str = Field(max_length=2048)


class _ErrorResponse(BaseModel):
    detail: _ErrorDetail | str


def _check(response: httpx.Response) -> None:
    if response.is_error:
        try:
            detail: Final = _ErrorResponse.model_validate_json(response.content).detail
        except ValidationError:
            raise HTTPException(response.status_code, "Feedback operation failed; retry") from None
        raise HTTPException(response.status_code, detail.model_dump() if isinstance(detail, _ErrorDetail) else detail)


def _parse(response: httpx.Response, adapter: TypeAdapter[T]) -> T:
    _check(response)
    try:
        return adapter.validate_json(response.content)
    except ValidationError:
        raise HTTPException(502, "Invalid feedback service response") from None


class _FeedbackRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler: Final = super().get_route_handler()

        async def safe(request: Request) -> Response:
            try:
                response: Final = await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "Invalid feedback request; check fields") from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return safe


def create_feedback_router(
    request_manager: ManagementRequest,
    require_admin: Callable[[UserAPIKeyAuth], None],
    request_multipart: MultipartRequest,
) -> APIRouter:
    router: Final = APIRouter(prefix="/feedback", route_class=_FeedbackRoute)

    def admin(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> str:
        require_admin(user)
        return "admin:" + str(user.user_id or "administrator")[:240]

    def token(x_feedback_token: Annotated[str | None, Header(max_length=256)] = None) -> str:
        if not x_feedback_token:
            raise HTTPException(401, "Feedback machine credential required")
        return x_feedback_token

    @router.get("/issues", response_model=FeedbackPage)
    async def page(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        query: Annotated[FeedbackQuery, Query()],
        _identity: Annotated[str, Depends(admin)],
    ) -> FeedbackPage:
        return _parse(
            await request_manager("GET", "/api/feedback/issues?" + urlencode(query.model_dump()), None),
            TypeAdapter(FeedbackPage),
        )

    @router.post("/issues", response_model=FeedbackIssue)
    async def create(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        request: FeedbackCreate,
        identity: Annotated[str, Depends(admin)],
    ) -> FeedbackIssue:
        return _parse(
            await request_manager(
                "POST", "/api/feedback/issues?" + urlencode({"_actor": identity}), request.model_dump_json().encode()
            ),
            TypeAdapter(FeedbackIssue),
        )

    @router.get("/issues/{issue_id}", response_model=FeedbackIssue)
    async def get(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        _identity: Annotated[str, Depends(admin)],
    ) -> FeedbackIssue:
        return _parse(
            await request_manager("GET", f"/api/feedback/issues/{issue_id}", None), TypeAdapter(FeedbackIssue)
        )

    @router.put("/issues/{issue_id}", response_model=FeedbackIssue)
    async def edit(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        request: FeedbackEdit,
        identity: Annotated[str, Depends(admin)],
    ) -> FeedbackIssue:
        return _parse(
            await request_manager(
                "PUT",
                f"/api/feedback/issues/{issue_id}?" + urlencode({"_actor": identity}),
                request.model_dump_json().encode(),
            ),
            TypeAdapter(FeedbackIssue),
        )

    @router.delete("/issues/{issue_id}", status_code=204)
    async def delete(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        revision: Annotated[int, Query(ge=1)],
        identity: Annotated[str, Depends(admin)],
    ) -> Response:
        _check(
            await request_manager(
                "DELETE",
                f"/api/feedback/issues/{issue_id}?" + urlencode({"revision": revision, "_actor": identity}),
                None,
            )
        )
        return Response(status_code=204)

    @router.post("/issues/{issue_id}/actions", response_model=FeedbackIssue)
    async def act(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        request: FeedbackMutation,
        identity: Annotated[str, Depends(admin)],
    ) -> FeedbackIssue:
        return _parse(
            await request_manager(
                "POST",
                f"/api/feedback/issues/{issue_id}/actions?" + urlencode({"_actor": identity}),
                request.model_dump_json().encode(),
            ),
            TypeAdapter(FeedbackIssue),
        )

    @router.post("/issues/{issue_id}/attachments", response_model=FeedbackIssue)
    async def upload_attachment(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        expected_revision: Annotated[int, Form(ge=1)],
        file: Annotated[UploadFile, File()],
        user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
    ) -> FeedbackIssue:
        identity: Final = admin(user)
        content: Final = await file.read(MAX_FEEDBACK_ATTACHMENT_SIZE + 1)
        await file.close()
        response: Final = await request_multipart(
            f"/api/feedback/issues/{issue_id}/attachments?" + urlencode({"_actor": identity}),
            {"expected_revision": str(expected_revision)},
            file.filename or "",
            content,
            file.content_type,
        )
        return _parse(response, TypeAdapter(FeedbackIssue))

    @router.get("/issues/{issue_id}/attachments/{attachment_id}")
    async def download_attachment(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        attachment_id: UUID,
        user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
    ) -> Response:
        _identity: Final = admin(user)
        response: Final = await request_manager(
            "GET",
            f"/api/feedback/issues/{issue_id}/attachments/{attachment_id}",
            None,
        )
        _check(response)
        return Response(
            content=response.content,
            media_type=TypeAdapter(str).validate_python(
                response.headers.get("content-type", "application/octet-stream")
            ),
            headers={
                "Content-Disposition": TypeAdapter(str).validate_python(
                    response.headers.get("content-disposition", "attachment")
                ),
                "X-Content-Type-Options": TypeAdapter(str).validate_python(
                    response.headers.get("x-content-type-options", "nosniff")
                ),
            },
        )

    @router.get("/tokens", response_model=tuple[FeedbackTokenView, ...])
    async def tokens(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        identity: Annotated[str, Depends(admin)],
    ) -> tuple[FeedbackTokenView, ...]:
        return _parse(
            await request_manager("GET", "/api/feedback/tokens", None), TypeAdapter(tuple[FeedbackTokenView, ...])
        )

    @router.post("/tokens", response_model=FeedbackTokenIssue)
    async def issue_token(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        request: FeedbackTokenCreate,
        identity: Annotated[str, Depends(admin)],
    ) -> FeedbackTokenIssue:
        return _parse(
            await request_manager("POST", "/api/feedback/tokens", request.model_dump_json().encode()),
            TypeAdapter(FeedbackTokenIssue),
        )

    @router.delete("/tokens/{token_id}", status_code=204)
    async def revoke_token(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        token_id: UUID,
        identity: Annotated[str, Depends(admin)],
    ) -> Response:
        _check(await request_manager("DELETE", f"/api/feedback/tokens/{token_id}", None))
        return Response(status_code=204)

    async def machine(
        operation: Literal["list", "get", "mutate"],
        credential: str,
        issue_id: UUID | None = None,
        query: FeedbackQuery | None = None,
        mutation: FeedbackMutation | None = None,
    ) -> httpx.Response:
        body: Final = json.dumps(
            {
                "token": credential,
                "operation": operation,
                "issue_id": str(issue_id) if issue_id is not None else None,
                "query": query.model_dump() if query is not None else {},
                "mutation": mutation.model_dump() if mutation is not None else None,
            }
        ).encode()
        return await request_manager("POST", "/api/feedback/agent", body)

    @router.get("/agent/issues", response_model=FeedbackPage)
    async def agent_page(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        query: Annotated[FeedbackQuery, Query()],  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        credential: Annotated[str, Depends(token)],
    ) -> FeedbackPage:
        return _parse(await machine("list", credential, query=query), TypeAdapter(FeedbackPage))

    @router.get("/agent/issues/{issue_id}", response_model=FeedbackIssue)
    async def agent_get(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        credential: Annotated[str, Depends(token)],
    ) -> FeedbackIssue:
        return _parse(await machine("get", credential, issue_id), TypeAdapter(FeedbackIssue))

    @router.post("/agent/issues/{issue_id}/actions", response_model=FeedbackIssue)
    async def agent_act(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        request: FeedbackMutation,
        credential: Annotated[str, Depends(token)],
    ) -> FeedbackIssue:
        return _parse(await machine("mutate", credential, issue_id, mutation=request), TypeAdapter(FeedbackIssue))

    @router.get("/agent/issues/{issue_id}/attachments/{attachment_id}")
    async def agent_attachment(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        attachment_id: UUID,
        credential: Annotated[str, Depends(token)],
    ) -> Response:
        response: Final = await request_manager(
            "POST",
            f"/api/feedback/agent/attachments/{issue_id}/{attachment_id}",
            json.dumps({"token": credential}).encode(),
        )
        _check(response)
        return Response(
            content=response.content,
            media_type=TypeAdapter(str).validate_python(
                response.headers.get("content-type", "application/octet-stream")
            ),
            headers={
                "Content-Disposition": TypeAdapter(str).validate_python(
                    response.headers.get("content-disposition", "attachment")
                ),
                "X-Content-Type-Options": TypeAdapter(str).validate_python(
                    response.headers.get("x-content-type-options", "nosniff")
                ),
            },
        )

    return router
