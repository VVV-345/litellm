"""删除环境并持久化逐步清理进度，复用原有锁和失败重试流程。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID, uuid4

from account_pool.application.environments.contracts import (
    ChannelOperation,
    LockForOperation,
    LogEventOperation,
    PersistCleanupProgressOperation,
)
from account_pool.cleanup import compose_removed, directory_removed, routes_removed
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import (
    ChannelKind,
    CleanupProgress,
    EnvironmentRecord,
    EnvironmentStatus,
    utc_now,
)
from account_pool.ports import (
    EnvironmentRepository,
)
from account_pool.shared.error_safety import safe_error as _safe_error
from account_pool.shared.result import Failure, FailureCode, Result, Success


@dataclass(frozen=True, slots=True)
class EnvironmentDeletion:
    _channel: ChannelOperation
    _lock_for: LockForOperation
    _log_event: LogEventOperation
    _ownership: CredentialOwnership
    _persist_cleanup_progress: PersistCleanupProgressOperation
    _repository: EnvironmentRepository

    async def delete_environment(self, environment_id: UUID, operation_id: str | None = None) -> Result[None]:
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
            record: Final = await self._repository.get(environment_id)
            if record is None:
                await self._ownership.retain(environment_id)
                return Success(None)
            requested_operation: Final = operation_id or record.operation_id or str(uuid4())
            if record.status is EnvironmentStatus.DELETING and operation_id is not None:
                if record.operation_id not in (None, operation_id):
                    return Failure(FailureCode.CONFLICT, "environment deletion is owned by another operation")
            deleting: EnvironmentRecord | None = (
                record
                if record.status is EnvironmentStatus.DELETING
                else await self._repository.save_if_version(
                    record.model_copy(
                        update={
                            "version": record.version + 1,
                            "status": EnvironmentStatus.DELETING,
                            "desired_state": EnvironmentStatus.DELETING,
                            "enabled": False,
                            "operation_id": requested_operation,
                            "cleanup_progress": CleanupProgress(),
                            "updated_at": utc_now(),
                        }
                    ),
                    record.version,
                )
            )
            if deleting is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")

            progress: CleanupProgress = deleting.cleanup_progress
            deleting_with_routes: Final = (
                deleting
                if progress.routes_removed
                else await self._persist_cleanup_progress(
                    deleting,
                    routes_removed(progress),
                )
            )
            if deleting_with_routes is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            try:
                deleting_with_compose: Final = await self.remove_compose_step(deleting_with_routes)
            except Exception as error:
                failed_compose: Final = deleting_with_routes.model_copy(
                    update={
                        "last_error": _safe_error(error),
                        "configuration_last_error": _safe_error(error),
                        "updated_at": utc_now(),
                    }
                )
                await self._repository.save_if_version(failed_compose, deleting_with_routes.version)
                await self._log_event(failed_compose, "cleanup", error, retryable=True)
                return Failure(FailureCode.UPSTREAM, "environment cleanup failed")
            if deleting_with_compose is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            try:
                deleting_with_directory: Final = await self.remove_directory_step(deleting_with_compose)
            except Exception as error:
                failed_directory: Final = deleting_with_compose.model_copy(
                    update={
                        "last_error": _safe_error(error),
                        "configuration_last_error": _safe_error(error),
                        "updated_at": utc_now(),
                    }
                )
                await self._repository.save_if_version(failed_directory, deleting_with_compose.version)
                await self._log_event(failed_directory, "cleanup", error, retryable=True)
                return Failure(FailureCode.UPSTREAM, "environment cleanup failed")
            if deleting_with_directory is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            try:
                await self._repository.delete(environment_id)
                await self._ownership.retain(environment_id)
            except Exception as error:
                failed_delete: Final = deleting_with_directory.model_copy(
                    update={"last_error": _safe_error(error), "updated_at": utc_now()}
                )
                await self._repository.save_if_version(failed_delete, deleting_with_directory.version)
                await self._log_event(failed_delete, "cleanup", error, retryable=True)
                return Failure(FailureCode.UPSTREAM, "environment metadata cleanup failed")
            await self._log_event(deleting_with_directory, "cleanup", None)
            return Success(None)

    async def remove_compose_step(self, record: EnvironmentRecord) -> EnvironmentRecord | None:
        if record.cleanup_progress.compose_removed:
            return record
        if record.channel is ChannelKind.OPENAI_COMPATIBLE:
            return await self._persist_cleanup_progress(record, compose_removed(record.cleanup_progress))
        channel: Final = self._channel(record)
        await channel.remove_compose(record)
        return await self._persist_cleanup_progress(record, compose_removed(record.cleanup_progress))

    async def remove_directory_step(self, record: EnvironmentRecord) -> EnvironmentRecord | None:
        if record.cleanup_progress.directory_removed:
            return record
        if record.channel is ChannelKind.OPENAI_COMPATIBLE:
            return await self._persist_cleanup_progress(record, directory_removed())
        channel: Final = self._channel(record)
        await channel.remove_directory(record.id)
        return await self._persist_cleanup_progress(record, directory_removed())
