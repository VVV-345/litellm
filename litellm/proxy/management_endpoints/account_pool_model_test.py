"""管理员卡片连接测试，固定部署并验证完整响应，不返回凭据或上游原始错误。"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Annotated, Final, Literal, Protocol
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from litellm.llms.anthropic.experimental_pass_through.messages.streaming_iterator import aclose_if_supported
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest
from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator
from litellm.types.router import RetryPolicy, UpdateRouterConfig


class AccountPoolModelTestRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model: str = Field(min_length=1, max_length=256)
    mode: Literal["chat", "responses"] = "chat"
    stream: bool = False


class AccountPoolModelTestResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    success: bool
    card_id: UUID
    model: str
    mode: Literal["chat", "responses"]
    stream: bool
    elapsed_ms: int
    message: str
    error_code: str | None = None


class _Card(BaseModel):
    enabled: bool
    enabled_models: tuple[str, ...]


class _ModelInfo(BaseModel):
    id: str
    account_pool_environment_id: str | None = None


class _Deployment(BaseModel):
    model_name: str
    model_info: _ModelInfo


class _Message(BaseModel):
    content: str | None = None


class _Choice(BaseModel):
    message: _Message | None = None
    delta: _Message | None = None
    finish_reason: str | None = None


class _Text(BaseModel):
    type: str
    text: str = ""


class _Output(BaseModel):
    type: str
    content: tuple[_Text, ...] = ()


class _Completion(BaseModel):
    choices: tuple[_Choice, ...] = ()
    status: str | None = None
    output: tuple[_Output, ...] = ()
    error: object | None = None


class _Event(_Completion):
    type: str = ""
    response: _Completion | None = None


class ModelTestRouter(Protocol):
    def get_model_list(self, model_name: str | None = None) -> Sequence[Mapping[str, object]] | None: ...
    async def acompletion(self, **kwargs: object) -> object: ...
    async def aresponses(self, **kwargs: object) -> object: ...


def configured_router() -> ModelTestRouter | None:
    from litellm.proxy.proxy_server import llm_router

    return llm_router


def _complete(response: _Completion, mode: Literal["chat", "responses"]) -> bool:
    if response.error is not None:
        return False
    if mode == "chat":
        return any(
            choice.finish_reason == "stop" and choice.message is not None and bool(choice.message.content)
            for choice in response.choices
        )
    return response.status == "completed" and any(
        text.text.strip()
        for item in response.output
        if item.type == "message"
        for text in item.content
        if text.type == "output_text"
    )


async def _completed(result: object, request: AccountPoolModelTestRequest) -> bool:
    if not request.stream:
        return _complete(_Completion.model_validate(result, from_attributes=True), request.mode)
    if not isinstance(result, AsyncIterator):
        return False
    has_text = False  # rebind-ok: bounded streaming validation state
    completed = False  # rebind-ok: terminal event is required even after text
    try:
        async for chunk in result:
            event: Final = _Event.model_validate(chunk, from_attributes=True)
            if event.error is not None or event.type in ("error", "response.failed", "response.incomplete"):
                return False
            if request.mode == "responses":
                if event.type == "response.completed" and event.response is not None:
                    completed = _complete(event.response, request.mode)
                continue
            has_text = has_text or any(choice.delta and choice.delta.content for choice in event.choices)
            if any(choice.finish_reason not in (None, "stop") for choice in event.choices):
                return False
            completed = completed or any(choice.finish_reason == "stop" for choice in event.choices)
        return bool(completed and (request.mode == "responses" or has_text))
    finally:
        await aclose_if_supported(result)
        if isinstance(result, BaseResponsesAPIStreamingIterator):
            await result.response.aclose()


def create_model_test_router(
    request_manager: ManagementRequest,
    require_admin: Callable[[UserAPIKeyAuth], None],
    router_provider: Callable[[], ModelTestRouter | None] = configured_router,
) -> APIRouter:
    router: Final = APIRouter()

    @router.post("/environments/{card_id}/test")
    async def test_model(
        card_id: UUID,
        request: AccountPoolModelTestRequest,
        user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
    ) -> AccountPoolModelTestResult:
        require_admin(user)
        response: Final = await request_manager("GET", f"/api/environments/{card_id}", None)
        if response.is_error:
            raise HTTPException(response.status_code, "Account is unavailable")
        try:
            card: Final = _Card.model_validate_json(response.content)
        except ValueError:
            raise HTTPException(502, "Account configuration is unavailable") from None
        if not card.enabled or request.model not in card.enabled_models:
            raise HTTPException(400, "Select an enabled model on this account")
        llm_router: Final = router_provider()
        if llm_router is None:
            raise HTTPException(503, "Model routing is not ready")
        deployments: Final = tuple(
            _Deployment.model_validate(item) for item in (llm_router.get_model_list(model_name=request.model) or ())
        )
        selected: Final = next(
            (item for item in deployments if item.model_name == request.model and item.model_info.account_pool_environment_id == str(card_id)),
            None,
        )
        if selected is None:
            raise HTTPException(409, "This account's model is not synchronized with the router")
        started: Final = time.perf_counter()
        identity: Final = PoolIdentity(
            key_hash=user.api_key or "master", request_id=uuid4(), card_id=card_id, model_test=True,
            retry_deadline=time.time() + 30,
            router_settings=UpdateRouterConfig(
                num_retries=0, timeout=30, retry_after=0, model_group_retry_policy={},
                retry_policy=RetryPolicy(RateLimitErrorRetries=0, TimeoutErrorRetries=0, InternalServerErrorRetries=0),
            ),
        )
        reset: Final = pool_identity.set(identity)
        parameters: Final = {
            "model": selected.model_info.id, "specific_deployment": True, "stream": request.stream,
            "num_retries": 0, "max_retries": 0, "disable_fallbacks": True, "timeout": 30,
            "caching": False, "cache": {"no-cache": True, "no-store": True},
            "metadata": {"user_api_key": user.api_key, "account_pool_model_test": True},
        }
        try:
            async with asyncio.timeout(30):
                result: Final = (
                    await llm_router.acompletion(
                        **parameters, messages=[{"role": "user", "content": "Hello. Reply briefly with Hello."}],
                        max_completion_tokens=256,
                    )
                    if request.mode == "chat"
                    else await llm_router.aresponses(
                        **parameters, input="Hello. Reply briefly with Hello.", max_output_tokens=256, store=False,
                    )
                )
                success: Final = await _completed(result, request)
            return AccountPoolModelTestResult(
                **request.model_dump(), card_id=card_id, success=success,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                message="Model returned a complete response" if success else "Model did not return a complete text response",
                error_code=None if success else "incomplete_response",
            )
        except Exception as error:
            timeout: Final = isinstance(error, (TimeoutError, httpx.TimeoutException)) or getattr(error, "status_code", None) == 408
            status: Final = getattr(error, "status_code", None)
            return AccountPoolModelTestResult(
                **request.model_dump(), card_id=card_id, success=False,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                message="Connection test timed out" if timeout else "Connection test failed; inspect this account's request logs",
                error_code="timeout" if timeout else f"http_{status}" if isinstance(status, int) else "connection_test_failed",
            )
        finally:
            pool_identity.reset(reset)

    return router
