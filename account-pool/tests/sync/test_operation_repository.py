"""验证同步操作仓储契约、幂等内容比较和持久化行解码。"""

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import uuid4

import pytest
from account_pool.catalog.models import AdministrativeState
from account_pool.models import ChannelPriority, QuotaConfig
from account_pool.sync.models import (
    ChannelDesiredState,
    SafeSyncFailure,
    SyncAction,
    SyncOperation,
    SyncStatus,
)
from account_pool.sync.postgres import decode_operation_row
from account_pool.sync.repository import (
    SyncOperationPersistenceFailure,
    SyncOperationPersistenceFailureCode,
    SyncOperationWriteSuccess,
    same_operation_request,
)
from pydantic import ValidationError

_NOW: Final = datetime(2026, 8, 19, 6, 0, tzinfo=UTC)


def _operation(*, display_name: str = "Primary", idempotency_key: str = "create-primary") -> SyncOperation:
    channel_id: Final = uuid4()
    desired: Final = ChannelDesiredState(
        channel_id=channel_id,
        display_name=display_name,
        provider="openai",
        base_url_display="https://api.example.com/v1",
        administrative_state=AdministrativeState.ENABLED,
        max_concurrency=2,
        priority=ChannelPriority.MEDIUM,
        weight=1,
        quotas=QuotaConfig(),
        bindings=(),
    )
    return SyncOperation(
        operation_id=uuid4(),
        idempotency_key=idempotency_key,
        channel_id=channel_id,
        action=SyncAction.CREATE_CHANNEL,
        status=SyncStatus.PENDING_CREATE,
        desired=desired,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _row(operation: SyncOperation) -> dict[str, object]:
    return {
        "operation_id": operation.operation_id,
        "idempotency_key": operation.idempotency_key,
        "channel_id": operation.channel_id,
        "action": operation.action.value,
        "status": operation.status.value,
        "delete_mode": None,
        "desired_schema_version": 1,
        "desired_payload": operation.desired.model_dump(mode="json"),
        "attempt_count": operation.attempt_count,
        "requires_key": operation.requires_key,
        "failure_code": None,
        "failure_message": None,
        "created_at": operation.created_at,
        "updated_at": operation.updated_at,
        "applied_at": None,
    }


def test_same_idempotency_request_ignores_generated_identity_and_runtime_state() -> None:
    first: Final = _operation()
    repeated: Final = first.model_copy(
        update={
            "operation_id": uuid4(),
            "status": SyncStatus.FAILED,
            "requires_key": True,
            "failure": SafeSyncFailure(code="missing_key", message="fresh credential required"),
            "attempt_count": 2,
            "created_at": _NOW + timedelta(minutes=1),
            "updated_at": _NOW + timedelta(minutes=1),
        }
    )

    assert same_operation_request(first, repeated) is True
    assert same_operation_request(first, _operation(display_name="Different")) is False


def test_repository_results_are_typed_and_immutable() -> None:
    operation: Final = _operation()
    success: Final = SyncOperationWriteSuccess(status="created", operation=operation)
    failure: Final = SyncOperationPersistenceFailure(
        code=SyncOperationPersistenceFailureCode.DATABASE_UNAVAILABLE,
        retryable=True,
    )

    assert success.operation == operation
    assert failure.status == "failed"
    with pytest.raises(ValidationError):
        success.status = "updated"


def test_decode_operation_row_validates_and_reconstructs_operation() -> None:
    operation: Final = _operation()

    decoded: Final = decode_operation_row(_row(operation))

    assert decoded == operation


def test_decode_operation_row_rejects_secret_fields_and_inconsistent_failure_columns() -> None:
    operation: Final = _operation()
    secret_payload: Final = {
        **_row(operation),
        "desired_payload": {
            **operation.desired.model_dump(mode="json"),
            "api_key": "must-not-be-stored",
        },
    }
    inconsistent_failure: Final = {
        **_row(operation),
        "failure_code": "upstream_unavailable",
    }

    with pytest.raises(ValidationError):
        decode_operation_row(secret_payload)
    with pytest.raises(ValidationError):
        decode_operation_row(inconsistent_failure)


def test_safe_failure_rejects_authorization_material() -> None:
    with pytest.raises(ValidationError, match="authorization"):
        SafeSyncFailure(code="upstream", message="Authorization: Bearer must-not-be-stored")
