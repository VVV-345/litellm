"""验证 LiteLLM Deployment 同步适配器的安全边界和所有权约束。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, cast
from uuid import UUID, uuid4

import httpx
import pytest
from account_pool.catalog.models import AdministrativeState, BindingOwnership
from account_pool.models import ChannelPriority, QuotaConfig
from account_pool.sync.litellm import (
    LiteLLMDeploymentSyncAdapter,
    LiteLLMSyncAction,
    LiteLLMSyncFailure,
    LiteLLMSyncSuccess,
    ManagedDeploymentListSuccess,
    ManagedDeploymentMarker,
)
from account_pool.sync.models import (
    ChannelDesiredState,
    DesiredBinding,
    ExternalDeploymentDelete,
    SyncAction,
    SyncOperation,
    SyncStatus,
)
from pydantic import SecretStr, TypeAdapter

_JSON_OBJECT: Final = TypeAdapter(dict[str, object])
_PROVIDER_KEY: Final = "provider-secret-must-not-leak"
_ADMIN_KEY: Final = "admin-secret-must-not-leak"


def _operation(
    ownership: BindingOwnership = BindingOwnership.POOL_MANAGED,
) -> tuple[SyncOperation, DesiredBinding]:
    channel_id: Final = uuid4()
    binding: Final = DesiredBinding(
        binding_id=uuid4(),
        channel_id=channel_id,
        public_model="public-model",
        provider_model="openai/provider-model",
        litellm_deployment_id="stable-deployment-id",
        ownership=ownership,
    )
    desired: Final = ChannelDesiredState(
        channel_id=channel_id,
        display_name="Primary",
        provider="openai",
        base_url_display="https://provider.example/v1",
        administrative_state=AdministrativeState.ENABLED,
        max_concurrency=2,
        priority=ChannelPriority.MEDIUM,
        weight=1,
        quotas=QuotaConfig(),
        bindings=(binding,),
    )
    timestamp: Final = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
    return (
        SyncOperation(
            operation_id=uuid4(),
            idempotency_key=f"sync-{uuid4()}",
            channel_id=channel_id,
            action=SyncAction.CREATE_CHANNEL,
            status=SyncStatus.PENDING_CREATE,
            desired=desired,
            created_at=timestamp,
            updated_at=timestamp,
        ),
        binding,
    )


def _request_payload(request: httpx.Request) -> dict[str, object]:
    return _JSON_OBJECT.validate_json(request.content)


@pytest.mark.asyncio
async def test_create_uses_stable_id_and_account_pool_markers_without_leaking_credentials() -> None:
    operation, binding = _operation()
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"model_id": binding.litellm_deployment_id})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
        )
        result: Final = await adapter.create_deployment(
            operation=operation,
            binding=binding,
            api_base="https://provider.example/v1",
            api_key=SecretStr(_PROVIDER_KEY),
        )

    assert result == LiteLLMSyncSuccess(
        action=LiteLLMSyncAction.CREATE,
        litellm_deployment_id=binding.litellm_deployment_id,
    )
    assert len(requests) == 1
    request: Final = requests[0]
    payload: Final = _request_payload(request)
    litellm_params: Final = cast(dict[str, object], payload["litellm_params"])
    model_info: Final = cast(dict[str, object], payload["model_info"])
    assert request.method == "POST"
    assert request.url.path == "/model/new"
    assert request.headers["authorization"] == f"Bearer {_ADMIN_KEY}"
    assert payload["model_name"] == binding.public_model
    assert litellm_params == {
        "model": binding.provider_model,
        "api_base": "https://provider.example/v1",
        "api_key": _PROVIDER_KEY,
    }
    assert model_info == {
        "id": binding.litellm_deployment_id,
        "channel_id": str(binding.channel_id),
        "binding_id": str(binding.binding_id),
        "operation_id": str(operation.operation_id),
        "managed_by": "account_pool",
    }
    serialized: Final = result.model_dump_json()
    assert _PROVIDER_KEY not in serialized
    assert _ADMIN_KEY not in serialized
    assert "authorization" not in serialized.casefold()


@pytest.mark.asyncio
async def test_update_rejects_external_binding_without_sending_request() -> None:
    operation, binding = _operation(BindingOwnership.EXTERNALLY_MANAGED)

    def upstream(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected request to {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
        )
        result: Final = await adapter.update_deployment(
            operation=operation,
            binding=binding,
            api_base="https://provider.example/v1",
            api_key=SecretStr(_PROVIDER_KEY),
        )

    assert isinstance(result, LiteLLMSyncFailure)
    assert result.failure.code == "ownership_rejected"
    assert result.retryable is False
    assert _PROVIDER_KEY not in result.model_dump_json()
    assert _ADMIN_KEY not in result.model_dump_json()


@pytest.mark.asyncio
async def test_managed_delete_rejects_external_binding_and_deletes_managed_binding() -> None:
    _, external = _operation(BindingOwnership.EXTERNALLY_MANAGED)
    _, managed = _operation()
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"message": "deleted"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
        )
        rejected: Final = await adapter.delete_managed_deployment(external)
        deleted: Final = await adapter.delete_managed_deployment(managed)

    assert isinstance(rejected, LiteLLMSyncFailure)
    assert rejected.failure.code == "ownership_rejected"
    assert deleted == LiteLLMSyncSuccess(
        action=LiteLLMSyncAction.DELETE,
        litellm_deployment_id=managed.litellm_deployment_id,
    )
    assert len(requests) == 1
    assert requests[0].url.path == "/model/delete"
    assert _request_payload(requests[0]) == {"id": managed.litellm_deployment_id}


@pytest.mark.asyncio
async def test_managed_delete_treats_known_missing_deployment_as_success() -> None:
    _, binding = _operation()

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {"message": f"{{'error': 'Model with id={binding.litellm_deployment_id} not found in db'}}"}
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
        )
        result: Final = await adapter.delete_managed_deployment(binding)

    assert result == LiteLLMSyncSuccess(
        action=LiteLLMSyncAction.DELETE,
        litellm_deployment_id=binding.litellm_deployment_id,
    )


@pytest.mark.asyncio
async def test_external_delete_treats_known_missing_deployment_as_success() -> None:
    deletion: Final = ExternalDeploymentDelete(
        channel_id=uuid4(),
        binding_id=uuid4(),
        litellm_deployment_id="external-deployment-id",
        ownership=BindingOwnership.EXTERNALLY_MANAGED,
        confirmed=True,
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {"message": f"{{'error': 'Model with id={deletion.litellm_deployment_id} not found in db'}}"}
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
        )
        result: Final = await adapter.delete_external_deployment(deletion)

    assert result == LiteLLMSyncSuccess(
        action=LiteLLMSyncAction.DELETE_EXTERNAL,
        litellm_deployment_id=deletion.litellm_deployment_id,
    )


@pytest.mark.asyncio
async def test_managed_delete_keeps_unrelated_bad_request_as_failure() -> None:
    _, binding = _operation()

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "invalid request"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
        )
        result: Final = await adapter.delete_managed_deployment(binding)

    assert isinstance(result, LiteLLMSyncFailure)
    assert result.failure.code == "upstream_status"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_external_delete_is_separate_and_requires_confirmed_external_request() -> None:
    channel_id: Final = uuid4()
    binding_id: Final = uuid4()
    deletion: Final = ExternalDeploymentDelete(
        channel_id=channel_id,
        binding_id=binding_id,
        litellm_deployment_id="external-deployment-id",
        ownership=BindingOwnership.EXTERNALLY_MANAGED,
        confirmed=True,
    )
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"message": "deleted"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
        )
        result: Final = await adapter.delete_external_deployment(deletion)

    assert result == LiteLLMSyncSuccess(
        action=LiteLLMSyncAction.DELETE_EXTERNAL,
        litellm_deployment_id=deletion.litellm_deployment_id,
    )
    assert len(requests) == 1
    assert _request_payload(requests[0]) == {"id": deletion.litellm_deployment_id}


@pytest.mark.asyncio
async def test_failure_does_not_return_raw_litellm_response_or_credentials() -> None:
    operation, binding = _operation()
    raw_secret: Final = "raw-response-secret-must-not-leak"

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={
                "error": raw_secret,
                "authorization": request.headers["authorization"],
                "api_key": _PROVIDER_KEY,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
        )
        result: Final = await adapter.create_deployment(
            operation=operation,
            binding=binding,
            api_base="https://provider.example/v1",
            api_key=SecretStr(_PROVIDER_KEY),
        )

    assert isinstance(result, LiteLLMSyncFailure)
    assert result.failure.code == "upstream_status"
    assert result.retryable is True
    serialized: Final = result.model_dump_json()
    assert raw_secret not in serialized
    assert _PROVIDER_KEY not in serialized
    assert _ADMIN_KEY not in serialized
    assert "authorization" not in serialized.casefold()


@pytest.mark.asyncio
async def test_adapter_rejects_redirects_and_oversized_success_responses() -> None:
    operation, binding = _operation()
    responses: Final = (
        httpx.Response(307, headers={"location": "https://other.example/model/new"}),
        httpx.Response(200, content=b"{" + b'"model_id":"' + b"x" * 256 + b'"}'),
    )
    request_count: list[int] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        request_count.append(1)
        return responses[len(request_count) - 1]

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
            max_response_bytes=64,
        )
        redirect: Final = await adapter.create_deployment(
            operation=operation,
            binding=binding,
            api_base="https://provider.example/v1",
            api_key=SecretStr(_PROVIDER_KEY),
        )
        oversized: Final = await adapter.create_deployment(
            operation=operation,
            binding=binding,
            api_base="https://provider.example/v1",
            api_key=SecretStr(_PROVIDER_KEY),
        )

    assert isinstance(redirect, LiteLLMSyncFailure)
    assert redirect.failure.code == "redirect_rejected"
    assert isinstance(oversized, LiteLLMSyncFailure)
    assert oversized.failure.code == "response_too_large"
    assert len(request_count) == 2


@pytest.mark.asyncio
async def test_create_rejects_mismatched_returned_deployment_id() -> None:
    operation, binding = _operation()

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model_id": "unexpected-deployment-id"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
        )
        result: Final = await adapter.create_deployment(
            operation=operation,
            binding=binding,
            api_base="https://provider.example/v1",
            api_key=SecretStr(_PROVIDER_KEY),
        )

    assert isinstance(result, LiteLLMSyncFailure)
    assert result.failure.code == "deployment_id_mismatch"
    assert "unexpected-deployment-id" not in result.model_dump_json()


def test_public_result_models_never_accept_credential_fields() -> None:
    payload: Final[dict[str, object]] = {
        "action": LiteLLMSyncAction.CREATE,
        "litellm_deployment_id": "deployment-id",
        "api_key": _PROVIDER_KEY,
    }

    with pytest.raises(ValueError):
        LiteLLMSyncSuccess.model_validate(payload)


def test_operation_binding_must_match_adapter_input() -> None:
    operation, _ = _operation()
    _, unrelated_binding = _operation()
    assert operation.channel_id != unrelated_binding.channel_id
    assert isinstance(operation.operation_id, UUID)


@pytest.mark.asyncio
async def test_managed_deployment_discovery_returns_only_complete_account_pool_markers() -> None:
    channel_id: Final = uuid4()
    binding_id: Final = uuid4()
    operation_id: Final = uuid4()
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"model_info": {"id": "ordinary-deployment"}},
                    {
                        "model_info": {
                            "id": "managed-deployment",
                            "managed_by": "account_pool",
                            "channel_id": str(channel_id),
                            "binding_id": str(binding_id),
                            "operation_id": str(operation_id),
                        }
                    },
                    {
                        "model_info": {
                            "id": "incomplete-marker",
                            "managed_by": "account_pool",
                            "channel_id": str(channel_id),
                        }
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter: Final = LiteLLMDeploymentSyncAdapter(
            client=client,
            admin_endpoint="https://litellm.example",
            admin_key=SecretStr(_ADMIN_KEY),
        )
        result: Final = await adapter.list_managed_deployments()

    assert result == ManagedDeploymentListSuccess(
        deployments=(
            ManagedDeploymentMarker(
                litellm_deployment_id="managed-deployment",
                channel_id=channel_id,
                binding_id=binding_id,
                operation_id=operation_id,
            ),
        )
    )
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/model/info"
    assert requests[0].headers["authorization"] == f"Bearer {_ADMIN_KEY}"
