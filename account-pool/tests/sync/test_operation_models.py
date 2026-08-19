"""验证渠道同步操作的数据约束和序列化安全性。"""

from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

import pytest
from account_pool.catalog.models import AdministrativeState, BindingOwnership
from account_pool.models import QuotaConfig
from account_pool.sync.models import (
    ChannelDesiredState,
    DeleteMode,
    DesiredBinding,
    ExternalDeploymentDelete,
    SyncAction,
    SyncOperation,
    SyncStatus,
)
from pydantic import ValidationError


def _desired_channel() -> ChannelDesiredState:
    channel_id: Final = uuid4()
    return ChannelDesiredState(
        schema_version=1,
        channel_id=channel_id,
        legacy_account_id="primary",
        display_name="Primary",
        provider="openai",
        base_url_display="https://api.example.com/v1",
        administrative_state=AdministrativeState.ENABLED,
        max_concurrency=2,
        priority=0,
        weight=1,
        quotas=QuotaConfig(),
        bindings=(
            DesiredBinding(
                binding_id=uuid4(),
                channel_id=channel_id,
                public_model="public-model",
                provider_model="openai/provider-model",
                litellm_deployment_id="deployment-id",
                ownership=BindingOwnership.POOL_MANAGED,
            ),
        ),
    )


def test_operation_is_immutable_versioned_and_secret_free() -> None:
    desired: Final = _desired_channel()
    created_at: Final = datetime(2026, 8, 19, 6, 0, tzinfo=UTC)
    operation: Final = SyncOperation(
        operation_id=uuid4(),
        idempotency_key="create-primary-1",
        channel_id=desired.channel_id,
        action=SyncAction.CREATE_CHANNEL,
        status=SyncStatus.PENDING_CREATE,
        desired=desired,
        created_at=created_at,
        updated_at=created_at,
    )

    dumped: Final = operation.model_dump_json()

    assert operation.desired.schema_version == 1
    assert operation.requires_key is False
    assert "api_key" not in dumped
    assert "authorization" not in dumped.lower()


@pytest.mark.parametrize(
    ("action", "status"),
    [
        (SyncAction.CREATE_CHANNEL, SyncStatus.PENDING_UPDATE),
        (SyncAction.UPDATE_CHANNEL, SyncStatus.PENDING_CREATE),
        (SyncAction.DETACH_CHANNEL, SyncStatus.PENDING_UPDATE),
        (SyncAction.DELETE_CHANNEL, SyncStatus.PENDING_CREATE),
    ],
)
def test_operation_rejects_invalid_pending_status(action: SyncAction, status: SyncStatus) -> None:
    desired: Final = _desired_channel()
    timestamp: Final = datetime(2026, 8, 19, 6, 0, tzinfo=UTC)

    with pytest.raises(ValidationError, match="pending status"):
        SyncOperation(
            operation_id=uuid4(),
            idempotency_key="invalid-status",
            channel_id=desired.channel_id,
            action=action,
            status=status,
            desired=desired,
            created_at=timestamp,
            updated_at=timestamp,
        )


def test_operation_rejects_naive_timestamps_and_secret_fields() -> None:
    desired: Final = _desired_channel()
    payload: Final = {
        "operation_id": uuid4(),
        "idempotency_key": "invalid-operation",
        "channel_id": desired.channel_id,
        "action": SyncAction.CREATE_CHANNEL,
        "status": SyncStatus.PENDING_CREATE,
        "desired": desired.model_dump(),
        "created_at": datetime(2026, 8, 19, 6, 0),
        "updated_at": datetime(2026, 8, 19, 6, 0),
        "api_key": "must-not-be-accepted",
    }

    with pytest.raises(ValidationError):
        SyncOperation.model_validate(payload)


def test_channel_delete_requires_explicit_mode() -> None:
    desired: Final = _desired_channel()
    timestamp: Final = datetime(2026, 8, 19, 6, 0, tzinfo=UTC)

    with pytest.raises(ValidationError, match="delete_mode"):
        SyncOperation(
            operation_id=uuid4(),
            idempotency_key="delete-primary",
            channel_id=desired.channel_id,
            action=SyncAction.DELETE_CHANNEL,
            status=SyncStatus.PENDING_DELETE,
            desired=desired,
            created_at=timestamp,
            updated_at=timestamp,
        )

    operation: Final = SyncOperation(
        operation_id=uuid4(),
        idempotency_key="delete-primary-managed",
        channel_id=desired.channel_id,
        action=SyncAction.DELETE_CHANNEL,
        status=SyncStatus.PENDING_DELETE,
        delete_mode=DeleteMode.DELETE_MANAGED_DEPLOYMENT,
        desired=desired,
        created_at=timestamp,
        updated_at=timestamp,
    )
    assert operation.delete_mode == DeleteMode.DELETE_MANAGED_DEPLOYMENT


def test_external_deployment_delete_requires_external_binding_and_confirmation() -> None:
    external: Final = ExternalDeploymentDelete(
        channel_id=uuid4(),
        binding_id=uuid4(),
        litellm_deployment_id="external-deployment",
        ownership=BindingOwnership.EXTERNALLY_MANAGED,
        confirmed=True,
    )
    assert external.confirmed is True

    with pytest.raises(ValidationError):
        ExternalDeploymentDelete.model_validate(
            {
                **external.model_dump(),
                "confirmed": False,
            }
        )

    with pytest.raises(ValidationError):
        ExternalDeploymentDelete.model_validate(
            {
                **external.model_dump(),
                "ownership": BindingOwnership.POOL_MANAGED,
            }
        )
