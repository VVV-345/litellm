from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.common_utils.resource_ownership import is_proxy_admin
from litellm.proxy.management_endpoints.account_pool_cockpit_native import (
    NativeCockpitAccountView,
    NativeCockpitError,
    NativeCockpitServices,
    NativeScopeStatus,
    native_cockpit_enabled,
    native_cockpit_services,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_policy import (
    CockpitPolicyUpdate,
    CockpitPolicyView,
    PolicyFailure,
    read_cockpit_policy,
    save_cockpit_policy,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_receipt_contracts import (
    CredentialReceiptRejection,
    NativeCredentialReceipt,
    ReceiptRejectionCode,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_runtime import (
    CockpitPolicyApply,
    CockpitPolicyRuntime,
    RuntimeFailure,
    apply_cockpit_policy,
    read_cockpit_runtime,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_scope_service import (
    CockpitScopeActivationRequest,
    CockpitScopePublishRequest,
    CockpitScopeRevokeRequest,
    ScopeFailure,
)


class NativeSubjectRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    key_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class NativeReceiptRequest(NativeSubjectRequest):
    receipt: str = Field(min_length=1, max_length=16384, repr=False)


class NativeReceiptResponse(BaseModel):
    key_hash: str
    status: Literal["native_scope_verified", "rejected"]
    receipt: str | None = Field(default=None, repr=False)
    code: ReceiptRejectionCode | None = None
    ready_to_migrate: Literal[False] = False


class NativePreflightResponse(BaseModel):
    key_hash: str
    status: Literal["eligible", "blocked"]
    code: ReceiptRejectionCode | None = None
    revision: str | None = None
    models: tuple[str, ...] = ()


class NativeBackendResponse(BaseModel):
    backend: Literal["manager", "cockpit"]


class _NativeRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original: Final = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response: Final = await original(request)
            except RequestValidationError:
                return JSONResponse(
                    {"detail": "invalid_native_request"}, status_code=422, headers={"Cache-Control": "no-store"}
                )
            except NativeCockpitError as error:
                return JSONResponse(
                    {"detail": error.code}, status_code=error.status, headers={"Cache-Control": "no-store"}
                )
            except HTTPException as error:
                return JSONResponse(
                    {"detail": "native_access_denied"},
                    status_code=error.status_code,
                    headers={"Cache-Control": "no-store"},
                )
            except Exception:  # noqa: BLE001  # Never return credential-bearing driver or transport errors to the browser.
                return JSONResponse({"detail": "unavailable"}, status_code=503, headers={"Cache-Control": "no-store"})
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle


def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> None:
    if not is_proxy_admin(user):
        raise HTTPException(403, "Proxy admin required")


class _NativeHandlers:
    def __init__(self, provider: Callable[[], NativeCockpitServices]) -> None:
        self._provider: Final = provider

    async def configuration(self) -> NativeBackendResponse:
        return NativeBackendResponse(backend="cockpit" if native_cockpit_enabled() else "manager")

    async def accounts(self) -> tuple[NativeCockpitAccountView, ...]:
        return await self._provider().accounts()

    async def preflight(self, request: NativeSubjectRequest) -> NativePreflightResponse:
        observed: Final = await self._provider().observations.read(request.key_hash)
        if isinstance(observed, CredentialReceiptRejection):
            return NativePreflightResponse(key_hash=request.key_hash, status="blocked", code=observed.code)
        return NativePreflightResponse(
            key_hash=request.key_hash,
            status="eligible",
            revision=observed.manifest_revision,
            models=observed.scope.models,
        )

    def receipt_response(
        self, key_hash: str, receipt: NativeCredentialReceipt | CredentialReceiptRejection
    ) -> NativeReceiptResponse:
        if isinstance(receipt, CredentialReceiptRejection):
            return NativeReceiptResponse(key_hash=key_hash, status="rejected", code=receipt.code)
        return NativeReceiptResponse(
            key_hash=key_hash, status="native_scope_verified", receipt=receipt.model_dump_json()
        )

    async def issue(self, request: NativeSubjectRequest) -> NativeReceiptResponse:
        receipt: Final = await self._provider().receipts.issue(request.key_hash)
        return self.receipt_response(
            request.key_hash,
            receipt
            if isinstance(receipt, (NativeCredentialReceipt, CredentialReceiptRejection))
            else CredentialReceiptRejection("invalid_receipt"),
        )

    async def revalidate(self, request: NativeReceiptRequest) -> NativeReceiptResponse:
        receipt: Final = await self._provider().receipts.revalidate(request.key_hash, request.receipt)
        return self.receipt_response(
            request.key_hash,
            receipt
            if isinstance(receipt, (NativeCredentialReceipt, CredentialReceiptRejection))
            else CredentialReceiptRejection("invalid_receipt"),
        )

    async def view(self, subject_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]) -> NativeScopeStatus:
        return await self._provider().view(subject_hash)

    async def publish(self, request: CockpitScopePublishRequest) -> NativeScopeStatus:
        services: Final = self._provider()
        result: Final = await services.scope.publish(request)
        if isinstance(result, ScopeFailure):
            raise NativeCockpitError(result.status, result.code)
        return await services.view(request.subject_hash)

    async def revoke(self, request: CockpitScopeRevokeRequest) -> NativeScopeStatus:
        services: Final = self._provider()
        result: Final = await services.scope.revoke(request)
        if isinstance(result, ScopeFailure):
            raise NativeCockpitError(result.status, result.code)
        return await services.view(request.subject_hash)

    async def activate(self, request: CockpitScopeActivationRequest) -> NativeScopeStatus:
        services: Final = self._provider()
        result: Final = await services.scope.activate(request)
        if isinstance(result, ScopeFailure):
            raise NativeCockpitError(result.status, result.code)
        return await services.view(request.subject_hash)

    async def deactivate(self, request: CockpitScopeActivationRequest) -> NativeScopeStatus:
        services: Final = self._provider()
        result: Final = await services.scope.deactivate(request)
        if isinstance(result, ScopeFailure):
            raise NativeCockpitError(result.status, result.code)
        return await services.view(request.subject_hash)

    async def policy(self) -> CockpitPolicyView:
        result: Final = await read_cockpit_policy(self._provider().target)
        if isinstance(result, PolicyFailure):
            raise NativeCockpitError(result.status, result.code)
        return result

    async def save_policy(self, request: CockpitPolicyUpdate) -> CockpitPolicyView:
        result: Final = await save_cockpit_policy(self._provider().target, request)
        if isinstance(result, PolicyFailure):
            raise NativeCockpitError(result.status, result.code)
        return result

    async def runtime(self) -> CockpitPolicyRuntime:
        result: Final = await read_cockpit_runtime(self._provider().target)
        if isinstance(result, RuntimeFailure):
            raise NativeCockpitError(result.status, result.code)
        return result

    async def apply_policy(self, request: CockpitPolicyApply) -> CockpitPolicyRuntime:
        result: Final = await apply_cockpit_policy(self._provider().target, request)
        if isinstance(result, RuntimeFailure):
            raise NativeCockpitError(result.status, result.code)
        return result


def create_native_cockpit_router(provider: Callable[[], NativeCockpitServices] = native_cockpit_services) -> APIRouter:
    handlers: Final = _NativeHandlers(provider)
    router: Final = APIRouter(
        prefix="/account_pool/cockpit/native",
        tags=["Account Pool"],
        route_class=_NativeRoute,
        dependencies=[Depends(authorize)],
    )

    router.add_api_route("/policy/runtime", handlers.runtime, methods=["GET"])
    router.add_api_route("/policy/apply", handlers.apply_policy, methods=["POST"])
    router.add_api_route("/configuration", handlers.configuration, methods=["GET"])
    router.add_api_route("/accounts", handlers.accounts, methods=["GET"])
    router.add_api_route("/preflight", handlers.preflight, methods=["POST"])
    router.add_api_route("/credential-receipts", handlers.issue, methods=["POST"])
    router.add_api_route("/credential-receipts/revalidate", handlers.revalidate, methods=["POST"])
    router.add_api_route("/scope/{subject_hash}", handlers.view, methods=["GET"])
    router.add_api_route("/scope", handlers.publish, methods=["POST"])
    router.add_api_route("/scope", handlers.revoke, methods=["DELETE"])
    router.add_api_route("/scope/activate", handlers.activate, methods=["POST"])
    router.add_api_route("/scope/deactivate", handlers.deactivate, methods=["POST"])
    router.add_api_route("/policy", handlers.policy, methods=["GET"])
    router.add_api_route("/policy", handlers.save_policy, methods=["PUT"])
    return router
