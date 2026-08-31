"""集中构造渠道同步服务对调用方暴露的结果对象。"""

from __future__ import annotations

from typing import Literal

from account_pool.sync.contracts import ChannelManagementFailure, ChannelOperationView
from account_pool.sync.models import SyncOperation
from account_pool.sync.repository import SyncOperationPersistenceFailure


def management_failure(code: str, retryable: bool) -> ChannelManagementFailure:
    return ChannelManagementFailure(code=code, retryable=retryable)


def operation_view(
    operation: SyncOperation,
    status: Literal["accepted", "existing"],
) -> ChannelOperationView:
    return ChannelOperationView(
        status=status,
        operation_id=operation.operation_id,
        channel_id=operation.channel_id,
        operation_status=operation.status,
        requires_key=operation.requires_key,
        failure=operation.failure,
    )


def persistence_failure(failure: SyncOperationPersistenceFailure) -> ChannelManagementFailure:
    return management_failure(failure.code.value, failure.retryable)
