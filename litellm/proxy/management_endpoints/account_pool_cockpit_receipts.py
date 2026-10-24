"""定义 Cockpit 回执的代理管理员路由和 LiteLLM 内部主体读取路由。"""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Final, Literal, Self
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, model_validator

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_cockpit_subject import NativeSubjectResult

_HASH: Final = r"^[0-9a-f]{64}$"
ManagerPost = Callable[[str, bytes], Awaitable[httpx.Response]]
ProxyAdminCheck = Callable[[UserAPIKeyAuth], None]
NativeSubjectReader = Callable[[str], Awaitable[NativeSubjectResult]]


class CockpitNativeSubjectRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    subject_hash: str = Field(pattern=_HASH, repr=False)


class CockpitNativeSubjectScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    subject_hash: str = Field(pattern=_HASH, repr=False)
    card_id: UUID
    model_scope: Literal["unrestricted", "restricted"]
    models: tuple[StrictStr, ...]


class CockpitNativeSubjectResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    subject_hash: str = Field(pattern=_HASH, repr=False)
    scope: CockpitNativeSubjectScope | None


class CockpitCredentialReceiptRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    key_hash: str = Field(pattern=_HASH, repr=False)


class CockpitCredentialReceiptRevalidateRequest(CockpitCredentialReceiptRequest):
    receipt: str = Field(min_length=1, max_length=65536, repr=False)


class CockpitCredentialReceiptResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    key_hash: str = Field(pattern=_HASH, repr=False)
    status: Literal["owned_artifact_correspondence", "rejected"]
    ready_to_migrate: Literal[False] = False
    receipt: str | None = Field(default=None, max_length=65536, repr=False)
    code: Literal[
        "invalid_subject", "subject_unverified", "invalid_clock", "read_unavailable", "state_changed",
        "source_missing", "source_unavailable", "source_credential_missing", "source_auth_unsupported",
        "source_identity_mismatch", "source_ownership_unverified", "source_credential_collision",
        "target_account_missing", "target_account_collision", "target_credential_missing",
        "target_credential_collision", "target_auth_unsupported", "target_content_mismatch",
        "invalid_receipt", "receipt_time_invalid", "stale_receipt",
    ] | None = None

    @model_validator(mode="after")
    def result_shape(self) -> Self:
        if (self.status == "owned_artifact_correspondence") != (self.receipt is not None):
            raise ValueError("Invalid credential receipt result")
        if (self.status == "rejected") != (self.code is not None):
            raise ValueError("Invalid credential receipt result")
        return self


class _InternalNativeSubjectRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original: Final = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response: Final = await original(request)
            except RequestValidationError:
                return Response(
                    content=b'{"detail":"Invalid native subject request"}', status_code=422,
                    media_type="application/json", headers={"Cache-Control": "no-store"},
                )
            except HTTPException as error:
                status_code: Final = error.status_code if error.status_code in (401, 503) else 503
                return Response(
                    content=b'{"detail":"Native subject unavailable"}', status_code=status_code,
                    media_type="application/json", headers={"Cache-Control": "no-store"},
                )
            except Exception:  # noqa: BLE001  # The internal boundary must sanitize unexpected reader failures.
                return Response(
                    content=b'{"detail":"Native subject unavailable"}', status_code=503,
                    media_type="application/json", headers={"Cache-Control": "no-store"},
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle


class _CredentialReceiptRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original: Final = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response: Final = await original(request)
            except RequestValidationError:
                return Response(
                    content=b'{"detail":"Invalid credential receipt request"}', status_code=422,
                    media_type="application/json", headers={"Cache-Control": "no-store"},
                )
            except HTTPException as error:
                status_code: Final = error.status_code if error.status_code in (401, 403, 502, 503) else 503
                return Response(
                    content=b'{"detail":"Credential receipt unavailable"}', status_code=status_code,
                    media_type="application/json", headers={"Cache-Control": "no-store"},
                )
            except Exception:  # noqa: BLE001  # The proxy boundary must sanitize transport failures.
                return Response(
                    content=b'{"detail":"Credential receipt unavailable"}', status_code=503,
                    media_type="application/json", headers={"Cache-Control": "no-store"},
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle


def create_internal_native_subject_router(native_subject_reader: NativeSubjectReader) -> APIRouter:
    router: Final = APIRouter(route_class=_InternalNativeSubjectRoute)

    @router.post("/internal/cockpit/native-subject", response_model=CockpitNativeSubjectResponse)
    async def read_internal_native_subject(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers this by decorator.
        request: CockpitNativeSubjectRequest,
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> CockpitNativeSubjectResponse:
        response.headers["Cache-Control"] = "no-store"
        token: Final = os.getenv("ACCOUNT_POOL_MANAGER_TOKEN", "")
        supplied: Final = authorization.removeprefix("Bearer ") if authorization is not None else ""
        if (
            len(token) < 32
            or not supplied.isascii()
            or not token.isascii()
            or not hmac.compare_digest(supplied, token)
        ):
            raise HTTPException(401, "invalid internal authorization", headers={"Cache-Control": "no-store"})
        try:
            subject: Final = await native_subject_reader(request.subject_hash)
        except Exception:  # noqa: BLE001  # The injected native reader can fail with driver errors.
            raise HTTPException(503, "native subject unavailable", headers={"Cache-Control": "no-store"}) from None
        scope: CockpitNativeSubjectScope | None = None
        if subject.status == "verified" and subject.card_id is not None and subject.model_scope is not None:
            scope = CockpitNativeSubjectScope(
                subject_hash=request.subject_hash,
                card_id=subject.card_id,
                model_scope=subject.model_scope.kind,
                models=subject.model_scope.models,
            )
        return CockpitNativeSubjectResponse(subject_hash=request.subject_hash, scope=scope)

    return router


def create_cockpit_credential_receipt_router(
    manager_post: ManagerPost,
    require_proxy_admin: ProxyAdminCheck,
) -> APIRouter:
    router: Final = APIRouter(route_class=_CredentialReceiptRoute)

    async def request_receipt(
        path: str, body: bytes, expected_key_hash: str, response: Response,
    ) -> CockpitCredentialReceiptResponse:
        response.headers["Cache-Control"] = "no-store"
        try:
            upstream: Final = await manager_post(path, body)
        except HTTPException:
            raise HTTPException(503, "Credential receipt unavailable", headers={"Cache-Control": "no-store"}) from None
        if upstream.status_code != 200:
            raise HTTPException(
                503 if upstream.status_code == 503 else 502,
                "Credential receipt unavailable", headers={"Cache-Control": "no-store"},
            )
        try:
            result: Final = CockpitCredentialReceiptResponse.model_validate_json(upstream.content)
        except ValidationError:
            raise HTTPException(502, "Invalid credential receipt response", headers={"Cache-Control": "no-store"}) from None
        if result.key_hash != expected_key_hash:
            raise HTTPException(502, "Invalid credential receipt response", headers={"Cache-Control": "no-store"})
        return result

    @router.post("/cockpit/credential-receipts", response_model=CockpitCredentialReceiptResponse)
    async def issue_credential_receipt(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers this by decorator.
        request: CockpitCredentialReceiptRequest,
        response: Response,
        user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
    ) -> CockpitCredentialReceiptResponse:
        require_proxy_admin(user_api_key_dict)
        return await request_receipt(
            "/api/cockpit/credential-receipts", request.model_dump_json().encode(), request.key_hash, response,
        )

    @router.post("/cockpit/credential-receipts/revalidate", response_model=CockpitCredentialReceiptResponse)
    async def revalidate_credential_receipt(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers this by decorator.
        request: CockpitCredentialReceiptRevalidateRequest,
        response: Response,
        user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
    ) -> CockpitCredentialReceiptResponse:
        require_proxy_admin(user_api_key_dict)
        return await request_receipt(
            "/api/cockpit/credential-receipts/revalidate", request.model_dump_json().encode(),
            request.key_hash, response,
        )

    return router
