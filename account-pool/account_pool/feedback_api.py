"""本模块提供私网反馈 API，机器操作再次鉴权且不回显凭证或数据库异常。"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Final, TypeVar
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from psycopg import Error as DatabaseError

from account_pool.feedback import FeedbackActor, FeedbackFailure, FeedbackService
from account_pool.feedback_models import (
    MAX_FEEDBACK_ATTACHMENT_SIZE,
    FeedbackAgentAttachmentRead,
    FeedbackAgentCommand,
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

T = TypeVar("T")


def _expose(result: T | FeedbackFailure) -> T:
    if isinstance(result, FeedbackFailure):
        raise HTTPException(result.status, {"code": result.code, "message": result.message})
    return result


def _attachment_response(content: bytes, filename: str, content_type: str) -> Response:
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )


class FeedbackRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler: Final = super().get_route_handler()

        async def safe(request: Request) -> Response:
            try:
                response: Final = await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "Invalid feedback request; check fields") from None
            except DatabaseError:
                raise HTTPException(503, "Feedback storage unavailable; retry") from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return safe


def create_feedback_router(service: FeedbackService, authorize: Callable[..., None]) -> APIRouter:
    router: Final = APIRouter(
        prefix="/api/feedback",
        dependencies=[Depends(authorize)],
        route_class=FeedbackRoute,
    )

    def actor(
        x_feedback_actor: Annotated[str, Header(max_length=256)] = "administrator",
        trusted_actor: Annotated[str, Query(alias="_actor", max_length=256)] = "",
    ) -> FeedbackActor:
        return FeedbackActor(trusted_actor or x_feedback_actor, True)

    @router.get("/issues", response_model=FeedbackPage)
    async def page(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        query: Annotated[FeedbackQuery, Query()],
    ) -> FeedbackPage:
        return await service.page(query)

    @router.get("/issues/{issue_id}", response_model=FeedbackIssue)
    async def get(issue_id: UUID) -> FeedbackIssue:  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        return _expose(await service.get(issue_id))

    @router.post("/issues", response_model=FeedbackIssue)
    async def create(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        request: FeedbackCreate,
        identity: Annotated[FeedbackActor, Depends(actor)],
    ) -> FeedbackIssue:
        return _expose(await service.create(request, identity))

    @router.put("/issues/{issue_id}", response_model=FeedbackIssue)
    async def edit(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        request: FeedbackEdit,
        identity: Annotated[FeedbackActor, Depends(actor)],
    ) -> FeedbackIssue:
        return _expose(await service.edit(issue_id, request, identity))

    @router.delete("/issues/{issue_id}", status_code=204)
    async def delete(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        revision: Annotated[int, Query(ge=1)],
        identity: Annotated[FeedbackActor, Depends(actor)],
    ) -> Response:
        _expose(await service.delete(issue_id, revision, identity))
        return Response(status_code=204)

    @router.post("/issues/{issue_id}/actions", response_model=FeedbackIssue)
    async def act(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        request: FeedbackMutation,
        identity: Annotated[FeedbackActor, Depends(actor)],
    ) -> FeedbackIssue:
        return _expose(await service.act(issue_id, request, identity))

    @router.post("/issues/{issue_id}/attachments", response_model=FeedbackIssue)
    async def upload_attachment(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        expected_revision: Annotated[int, Form(ge=1)],
        file: Annotated[UploadFile, File()],
        identity: Annotated[FeedbackActor, Depends(actor)],
    ) -> FeedbackIssue:
        content: Final = await file.read(MAX_FEEDBACK_ATTACHMENT_SIZE + 1)
        await file.close()
        return _expose(
            await service.add_attachment(issue_id, expected_revision, file.filename or "", content, identity)
        )

    @router.get("/issues/{issue_id}/attachments/{attachment_id}")
    async def download_attachment(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        attachment_id: UUID,
    ) -> Response:
        attachment: Final = _expose(await service.get_attachment(issue_id, attachment_id))
        return _attachment_response(attachment.content, attachment.filename, attachment.content_type)

    @router.get("/tokens", response_model=tuple[FeedbackTokenView, ...])
    async def tokens() -> tuple[FeedbackTokenView, ...]:  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        return await service.list_tokens()

    @router.post("/tokens", response_model=FeedbackTokenIssue)
    async def issue_token(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        request: FeedbackTokenCreate,
    ) -> FeedbackTokenIssue:
        return await service.issue_token(request)

    @router.delete("/tokens/{token_id}", status_code=204)
    async def revoke_token(token_id: UUID) -> Response:  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        _expose(await service.revoke_token(token_id))
        return Response(status_code=204)

    @router.post("/agent", response_model=FeedbackPage | FeedbackIssue)
    async def agent(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        command: FeedbackAgentCommand,
    ) -> FeedbackPage | FeedbackIssue:
        identity: Final = _expose(await service.authenticate(command.token))
        if command.operation == "list":
            return await service.page(command.query)
        if command.issue_id is None:
            raise HTTPException(422, "Feedback issue_id is required")
        if command.operation == "get":
            return _expose(await service.get(command.issue_id))
        if command.mutation is None:
            raise HTTPException(422, "Feedback mutation is required")
        return _expose(await service.act(command.issue_id, command.mutation, identity))

    @router.post("/agent/attachments/{issue_id}/{attachment_id}")
    async def agent_attachment(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        issue_id: UUID,
        attachment_id: UUID,
        request: FeedbackAgentAttachmentRead,
    ) -> Response:
        _expose(await service.authenticate(request.token))
        attachment: Final = _expose(await service.get_attachment(issue_id, attachment_id))
        return _attachment_response(attachment.content, attachment.filename, attachment.content_type)

    return router
