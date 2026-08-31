"""安全同步 Account Pool 管理的 LiteLLM Deployment。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, NotRequired, TypedDict
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from account_pool.catalog.models import BindingOwnership
from account_pool.models import FrozenModel
from account_pool.provider_services.http_response import read_limited_response
from account_pool.sync.models import (
    DesiredBinding,
    ExternalDeploymentDelete,
    SafeSyncFailure,
    SyncAction,
    SyncOperation,
)

_DEFAULT_MAX_RESPONSE_BYTES: Final = 65_536
_MANAGED_BY: Final = "account_pool"


class LiteLLMSyncAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    DELETE_EXTERNAL = "delete_external"
    DISCOVER = "discover"


class LiteLLMSyncSuccess(FrozenModel):
    action: LiteLLMSyncAction
    litellm_deployment_id: str = Field(min_length=1)


class LiteLLMSyncFailure(FrozenModel):
    action: LiteLLMSyncAction
    failure: SafeSyncFailure
    retryable: bool


LiteLLMSyncResult = LiteLLMSyncSuccess | LiteLLMSyncFailure


class ManagedDeploymentMarker(FrozenModel):
    litellm_deployment_id: str = Field(min_length=1)
    channel_id: UUID
    binding_id: UUID
    operation_id: UUID


class ManagedDeploymentListSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    deployments: tuple[ManagedDeploymentMarker, ...]


ManagedDeploymentListResult = ManagedDeploymentListSuccess | LiteLLMSyncFailure


class _ModelInfoPayload(TypedDict):
    id: str
    channel_id: str
    binding_id: str
    operation_id: str
    managed_by: str


class _LiteLLMParamsPayload(TypedDict):
    model: NotRequired[str]
    api_base: str
    api_key: NotRequired[str]


class _DeploymentPayload(TypedDict):
    model_name: str
    litellm_params: _LiteLLMParamsPayload
    model_info: _ModelInfoPayload


class _DeletePayload(TypedDict):
    id: str


class _ResponseModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class _CreatedModelInfo(_ResponseModel):
    id: str = Field(min_length=1)


class _CreateResponse(_ResponseModel):
    model_id: str | None = None
    model_info: _CreatedModelInfo | None = None


class _UpdateResponse(_ResponseModel):
    model_id: str = Field(min_length=1)


class _DeleteResponse(_ResponseModel):
    message: str = Field(min_length=1)


class _LiteLLMError(_ResponseModel):
    message: str = Field(min_length=1)


class _LiteLLMErrorResponse(_ResponseModel):
    error: _LiteLLMError


class _ManagedModelInfo(_ResponseModel):
    id: str = Field(min_length=1)
    managed_by: str | None = None
    channel_id: UUID | None = None
    binding_id: UUID | None = None
    operation_id: UUID | None = None


class _ModelInfoEntry(_ResponseModel):
    model_info: _ManagedModelInfo | None = None


class _ModelInfoResponse(_ResponseModel):
    data: tuple[_ModelInfoEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class _ResponseContent:
    content: bytes


@dataclass(frozen=True, slots=True)
class _MissingDeployment:
    pass


class LiteLLMDeploymentSyncAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        admin_endpoint: str,
        admin_key: SecretStr,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._client = client
        self._admin_endpoint = admin_endpoint.rstrip("/")
        self._admin_key = admin_key
        self._max_response_bytes = max_response_bytes

    async def create_deployment(
        self,
        operation: SyncOperation,
        binding: DesiredBinding,
        api_base: str,
        api_key: SecretStr,
    ) -> LiteLLMSyncResult:
        action: Final = LiteLLMSyncAction.CREATE
        rejected: Final = _reject_managed_operation(
            action=action,
            operation=operation,
            binding=binding,
            expected_sync_actions=frozenset((SyncAction.CREATE_CHANNEL, SyncAction.UPDATE_CHANNEL)),
        )
        if rejected is not None:
            return rejected
        if binding.provider_model is None:
            return _failure(action, "missing_provider_model", "Provider model is required", retryable=False)
        payload: Final[_DeploymentPayload] = _deployment_payload(
            operation=operation,
            binding=binding,
            api_base=api_base,
            api_key=api_key,
        )
        response: Final = await self._request(action=action, method="POST", path="/model/new", payload=payload)
        if isinstance(response, LiteLLMSyncFailure):
            return response
        if isinstance(response, _MissingDeployment):
            return _invalid_response(action)
        try:
            parsed: Final = _CreateResponse.model_validate_json(response.content)
        except ValidationError:
            return _invalid_response(action)
        returned_id: Final = parsed.model_id or (parsed.model_info.id if parsed.model_info is not None else None)
        if returned_id is None:
            return _invalid_response(action)
        if returned_id != binding.litellm_deployment_id:
            return _failure(
                action,
                "deployment_id_mismatch",
                "LiteLLM returned a different deployment ID",
                retryable=False,
            )
        return LiteLLMSyncSuccess(action=action, litellm_deployment_id=binding.litellm_deployment_id)

    async def update_deployment(
        self,
        operation: SyncOperation,
        binding: DesiredBinding,
        api_base: str,
        api_key: SecretStr | None = None,
    ) -> LiteLLMSyncResult:
        action: Final = LiteLLMSyncAction.UPDATE
        rejected: Final = _reject_managed_operation(
            action=action,
            operation=operation,
            binding=binding,
            expected_sync_actions=frozenset((SyncAction.UPDATE_CHANNEL,)),
        )
        if rejected is not None:
            return rejected
        payload: Final[_DeploymentPayload] = _deployment_payload(
            operation=operation,
            binding=binding,
            api_base=api_base,
            api_key=api_key,
        )
        response: Final = await self._request(
            action=action,
            method="PATCH",
            path=f"/model/{binding.litellm_deployment_id}/update",
            payload=payload,
        )
        if isinstance(response, LiteLLMSyncFailure):
            return response
        if isinstance(response, _MissingDeployment):
            return _invalid_response(action)
        try:
            parsed: Final = _UpdateResponse.model_validate_json(response.content)
        except ValidationError:
            return _invalid_response(action)
        if parsed.model_id != binding.litellm_deployment_id:
            return _failure(
                action,
                "deployment_id_mismatch",
                "LiteLLM returned a different deployment ID",
                retryable=False,
            )
        return LiteLLMSyncSuccess(action=action, litellm_deployment_id=binding.litellm_deployment_id)

    async def delete_managed_deployment(self, binding: DesiredBinding) -> LiteLLMSyncResult:
        action: Final = LiteLLMSyncAction.DELETE
        if binding.ownership != BindingOwnership.POOL_MANAGED:
            return _ownership_failure(action)
        return await self._delete(action=action, deployment_id=binding.litellm_deployment_id)

    async def delete_external_deployment(self, deletion: ExternalDeploymentDelete) -> LiteLLMSyncResult:
        return await self._delete(
            action=LiteLLMSyncAction.DELETE_EXTERNAL,
            deployment_id=deletion.litellm_deployment_id,
        )

    async def list_managed_deployments(self) -> ManagedDeploymentListResult:
        action: Final = LiteLLMSyncAction.DISCOVER
        response: Final = await self._request(action=action, method="GET", path="/model/info", payload=None)
        if isinstance(response, LiteLLMSyncFailure):
            return response
        if isinstance(response, _MissingDeployment):
            return _invalid_response(action)
        try:
            parsed: Final = _ModelInfoResponse.model_validate_json(response.content)
        except ValidationError:
            return _invalid_response(action)
        markers: Final = tuple(
            ManagedDeploymentMarker(
                litellm_deployment_id=entry.model_info.id,
                channel_id=entry.model_info.channel_id,
                binding_id=entry.model_info.binding_id,
                operation_id=entry.model_info.operation_id,
            )
            for entry in parsed.data
            if entry.model_info is not None
            and entry.model_info.managed_by == _MANAGED_BY
            and entry.model_info.channel_id is not None
            and entry.model_info.binding_id is not None
            and entry.model_info.operation_id is not None
        )
        return ManagedDeploymentListSuccess(deployments=markers)

    async def _delete(self, action: LiteLLMSyncAction, deployment_id: str) -> LiteLLMSyncResult:
        payload: Final[_DeletePayload] = {"id": deployment_id}
        response: Final = await self._request(
            action=action,
            method="POST",
            path="/model/delete",
            payload=payload,
            missing_deployment_id=deployment_id,
        )
        if isinstance(response, LiteLLMSyncFailure):
            return response
        if isinstance(response, _MissingDeployment):
            return LiteLLMSyncSuccess(action=action, litellm_deployment_id=deployment_id)
        try:
            _DeleteResponse.model_validate_json(response.content)
        except ValidationError:
            return _invalid_response(action)
        return LiteLLMSyncSuccess(action=action, litellm_deployment_id=deployment_id)

    async def _request(
        self,
        action: LiteLLMSyncAction,
        method: str,
        path: str,
        payload: _DeploymentPayload | _DeletePayload | None,
        missing_deployment_id: str | None = None,
    ) -> _ResponseContent | _MissingDeployment | LiteLLMSyncFailure:
        try:
            async with self._client.stream(
                method=method,
                url=f"{self._admin_endpoint}{path}",
                headers={"authorization": f"Bearer {self._admin_key.get_secret_value()}"},
                json=payload,
                follow_redirects=False,
            ) as response:
                status_code: Final = response.status_code
                if 300 <= status_code < 400:
                    return _failure(action, "redirect_rejected", "LiteLLM redirect was rejected", retryable=False)
                if status_code >= 400:
                    if status_code == 400 and missing_deployment_id is not None:
                        missing_response_content: Final = await read_limited_response(
                            response, self._max_response_bytes
                        )
                        if missing_response_content is not None and _is_missing_deployment_response(
                            missing_response_content, missing_deployment_id
                        ):
                            return _MissingDeployment()
                    return _failure(
                        action,
                        "upstream_status",
                        f"LiteLLM management request returned HTTP {status_code}",
                        retryable=status_code in {408, 409, 429} or status_code >= 500,
                    )
                response_content: Final = await read_limited_response(response, self._max_response_bytes)
        except httpx.HTTPError:
            return _failure(action, "transport_failed", "LiteLLM management request failed", retryable=True)
        if response_content is None:
            return _failure(action, "response_too_large", "LiteLLM response exceeded the size limit", retryable=False)
        return _ResponseContent(content=response_content)


def _is_missing_deployment_response(content: bytes, deployment_id: str) -> bool:
    try:
        response: Final = _LiteLLMErrorResponse.model_validate_json(content)
    except ValidationError:
        return False
    expected_message: Final = f"{{'error': 'Model with id={deployment_id} not found in db'}}"
    return response.error.message == expected_message


def _deployment_payload(
    operation: SyncOperation,
    binding: DesiredBinding,
    api_base: str,
    api_key: SecretStr | None,
) -> _DeploymentPayload:
    litellm_params: Final[_LiteLLMParamsPayload] = {"api_base": api_base}
    if binding.provider_model is not None:
        litellm_params["model"] = binding.provider_model
    if api_key is not None:
        litellm_params["api_key"] = api_key.get_secret_value()
    model_info: Final[_ModelInfoPayload] = {
        "id": binding.litellm_deployment_id,
        "channel_id": str(binding.channel_id),
        "binding_id": str(binding.binding_id),
        "operation_id": str(operation.operation_id),
        "managed_by": _MANAGED_BY,
    }
    return {
        "model_name": binding.public_model,
        "litellm_params": litellm_params,
        "model_info": model_info,
    }


def _reject_managed_operation(
    action: LiteLLMSyncAction,
    operation: SyncOperation,
    binding: DesiredBinding,
    expected_sync_actions: frozenset[SyncAction],
) -> LiteLLMSyncFailure | None:
    if binding.ownership != BindingOwnership.POOL_MANAGED:
        return _ownership_failure(action)
    if operation.action not in expected_sync_actions:
        return _operation_failure(action)
    desired_binding: Final = next(
        (candidate for candidate in operation.desired.bindings if candidate.binding_id == binding.binding_id),
        None,
    )
    if operation.channel_id != binding.channel_id or desired_binding != binding:
        return _operation_failure(action)
    return None


def _ownership_failure(action: LiteLLMSyncAction) -> LiteLLMSyncFailure:
    return _failure(
        action,
        "ownership_rejected",
        "Only pool-managed deployments are allowed",
        retryable=False,
    )


def _operation_failure(action: LiteLLMSyncAction) -> LiteLLMSyncFailure:
    return _failure(
        action,
        "operation_mismatch",
        "Sync operation does not match the deployment binding",
        retryable=False,
    )


def _invalid_response(action: LiteLLMSyncAction) -> LiteLLMSyncFailure:
    return _failure(action, "invalid_response", "LiteLLM returned an invalid response", retryable=False)


def _failure(
    action: LiteLLMSyncAction,
    code: str,
    message: str,
    retryable: bool,
) -> LiteLLMSyncFailure:
    return LiteLLMSyncFailure(
        action=action,
        failure=SafeSyncFailure(code=code, message=message),
        retryable=retryable,
    )
