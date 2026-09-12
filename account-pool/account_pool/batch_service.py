"""本模块执行持久化批量账号操作，复用环境服务的版本控制和幂等配置流程。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, Literal, cast
from uuid import UUID

from account_pool.batch_models import BatchAuthorization, BatchClaim, BatchJob, BatchRequest
from account_pool.batch_repository import BatchRepository
from account_pool.domain import (
    EnvironmentRecord,
    EnvironmentStatus,
    UpdateEnvironmentRequest,
    configuration_from_record,
    utc_now,
)
from account_pool.error_logs import ErrorLogService, LogStage
from account_pool.gateway_repository import CooldownRepository
from account_pool.policies import AccountPolicy, PolicyRepository, PolicyUpdate, PolicyView, policy_validation_error
from account_pool.ports import EnvironmentRepository
from account_pool.result import Failure, FailureCode, Result, Success
from account_pool.service import EnvironmentService


@dataclass(frozen=True, slots=True)
class BatchExecution:
    message: str
    authorization: BatchAuthorization | None = None


class BatchService:
    def __init__(
        self,
        repository: BatchRepository,
        environments: EnvironmentRepository,
        service: EnvironmentService,
        policies: PolicyRepository,
        logs: ErrorLogService,
        leases: CooldownRepository | None = None,
        sync_policy: Callable[[EnvironmentRecord, AccountPolicy], Awaitable[None]] | None = None,
    ) -> None:
        self.repository: Final = repository
        self.environments: Final = environments
        self.service: Final = service
        self.policies: Final = policies
        self.logs: Final = logs
        self.leases: Final = leases
        self.sync_policy: Final = sync_policy

    async def submit(self, request: BatchRequest) -> bool:
        records: Final = await asyncio.gather(*(self.environments.get(target.account_id) for target in request.targets))
        if any(record is None for record in records):
            return False
        return await self.repository.submit(request)

    async def get(self, job_id: UUID) -> BatchJob | None:
        return await self.repository.get(job_id)

    async def list(self) -> tuple[BatchJob, ...]:
        return await self.repository.list()

    async def run_once(self) -> bool:
        claim: Final = await self.repository.claim()
        if claim is None:
            return False
        started: Final = utc_now()
        outcome: Final = await self._run_safely(claim)
        succeeded, message, authorization = outcome
        await self.repository.finish(claim, succeeded, message, authorization)
        record: Final = await self.environments.get(claim.target.account_id)
        if record is not None and not succeeded:
            stage: Final[LogStage] = "authorization" if claim.request.action == "authorize" else "configuration"
            await self.logs.record(record, stage, RuntimeError(message), started_at=started)
        return True

    async def run_until_cancelled(self, stopped: asyncio.Event, interval: float = 1.0) -> None:
        while not stopped.is_set():
            did_work = await self.run_once()
            if not did_work:
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=interval)
                except TimeoutError:
                    pass

    async def _run_claim(self, claim: BatchClaim) -> Result[BatchExecution]:
        record: Final = await self.environments.get(claim.target.account_id)
        if record is None:
            return (
                Success(BatchExecution("Account already deleted"))
                if claim.request.action == "delete"
                else Failure(FailureCode.NOT_FOUND, "Account no longer exists")
            )
        if record.status is EnvironmentStatus.MIGRATION_REQUIRED and claim.request.action != "delete":
            return Failure(FailureCode.INVALID, "Retired cards are read-only and can only be exported or deleted")
        operation_id: Final = f"batch:{claim.request.job_id}:{record.id}"
        if claim.request.action == "delete":
            owns_cleanup: Final = record.status is EnvironmentStatus.DELETING and record.operation_id == operation_id
            if record.version != claim.target.version and not owns_cleanup:
                return Failure(FailureCode.CONFLICT, "Account changed after the batch was submitted")
            # 删除失败后环境保留原操作 ID，新任务沿用它才能继续清理而不会与自己冲突。
            deletion_operation_id: Final = (
                record.operation_id
                if record.status is EnvironmentStatus.DELETING and record.operation_id
                else operation_id
            )
            delete_result: Final = await self.service.delete_environment(record.id, deletion_operation_id)
            return (
                Success(BatchExecution("Account deleted"))
                if isinstance(delete_result, Success)
                else Failure(delete_result.code, delete_result.message)
            )
        if claim.request.action == "refresh":
            if record.version != claim.target.version and claim.attempts == 1:
                return Failure(FailureCode.CONFLICT, "Account changed after the batch was submitted")
            refresh_result: Final = await self.service.refresh_environment(record.id)
            return (
                Success(BatchExecution("Account refreshed"))
                if isinstance(refresh_result, Success)
                else Failure(FailureCode.UPSTREAM, refresh_result.message)
            )
        if claim.request.action == "authorize":
            if record.version != claim.target.version and record.operation_id != operation_id:
                return Failure(FailureCode.CONFLICT, "Account changed after the batch was submitted")
            if record.operation_id == operation_id and record.status in (
                EnvironmentStatus.VALIDATING,
                EnvironmentStatus.READY,
                EnvironmentStatus.COOLING_DOWN,
                EnvironmentStatus.DISABLED,
            ):
                return Success(BatchExecution("Authorization already submitted"))
            authorization_result: Final = await self.service.authorize_environment(record.id, operation_id)
            if not isinstance(authorization_result, Success):
                return Failure(authorization_result.code, authorization_result.message)
            authorization: Final = authorization_result.value
            return Success(
                BatchExecution(
                    "Authorization details generated",
                    BatchAuthorization(
                        flow=authorization.flow,
                        authorization_url=authorization.authorization_url,
                        ssh_command=authorization.ssh_command,
                        user_code=authorization.user_code,
                        expires_at=authorization.expires_at,
                    ),
                )
            )
        if claim.request.action == "policy":
            if record.version != claim.target.version:
                return Failure(FailureCode.CONFLICT, "Account changed after the batch was submitted")
            policy_request: Final = claim.request.policy
            if policy_request is None:
                return Failure(FailureCode.INVALID, "Policy is required")
            current_policy: Final = await self.policies.get(record.id)
            if current_policy.policy == policy_request:
                if self.sync_policy is not None:
                    try:
                        await self.sync_policy(record, policy_request)
                    except Exception:
                        await _set_policy_runtime_status(
                            self.policies, current_policy, "failed", "policy runtime synchronization failed"
                        )
                        return Failure(FailureCode.UPSTREAM, "policy runtime synchronization failed")
                    await _set_policy_runtime_status(self.policies, current_policy, "synced")
                return Success(BatchExecution("Policy already updated"))
            validation_error: Final = await policy_validation_error(record, policy_request, self.environments)
            if validation_error is not None:
                return Failure(FailureCode.INVALID, validation_error)
            policy: Final = await self.policies.save(
                record.id, PolicyUpdate(version=claim.target.policy_version, policy=policy_request)
            )
            if policy is not None and self.sync_policy is not None:
                try:
                    await self.sync_policy(record, policy.policy)
                except Exception:
                    await _set_policy_runtime_status(
                        self.policies, policy, "failed", "policy runtime synchronization failed"
                    )
                    return Failure(FailureCode.UPSTREAM, "policy runtime synchronization failed")
                await _set_policy_runtime_status(self.policies, policy, "synced")
            return (
                Success(BatchExecution("Policy updated"))
                if policy is not None
                else Failure(FailureCode.CONFLICT, "Policy changed after submission")
            )
        configuration: Final = configuration_from_record(record)
        if record.version != claim.target.version and record.operation_id != operation_id:
            return Failure(FailureCode.CONFLICT, "Account changed after the batch was submitted")
        update: Final = UpdateEnvironmentRequest(
            version=claim.target.version,
            operation_id=operation_id,
            name=configuration.name,
            concurrency_limit=configuration.concurrency_limit,
            enabled=record.enabled
            if claim.request.action in ("cooldown", "release")
            else claim.request.action == "enable",
            manual_cooldown=claim.request.action == "cooldown",
            proxy_mode=configuration.proxy_mode,
            proxy_profile_id=configuration.proxy_profile_id,
            enabled_models=configuration.enabled_models,
        )
        update_result: Final = await self.service.update_environment(record.id, update)
        if not isinstance(update_result, Success):
            return Failure(FailureCode.CONFLICT, update_result.message)
        if claim.request.action == "release" and self.leases is not None:
            await self.leases.clear_cooldown(record.id)
        return Success(BatchExecution("Account configuration updated"))

    async def _run_safely(self, claim: BatchClaim) -> tuple[bool, str, BatchAuthorization | None]:
        try:
            run_result: Final = await self._run_claim(claim)
        except Exception as error:
            return False, error.__class__.__name__, None
        if isinstance(run_result, Success):
            return True, run_result.value.message, run_result.value.authorization
        return False, run_result.message, None


async def _set_policy_runtime_status(
    policies: PolicyRepository,
    policy: PolicyView,
    status: Literal["partial", "synced", "failed"],
    error: str | None = None,
) -> PolicyView:
    setter: Final = getattr(policies, "set_runtime_status", None)
    if not callable(setter):
        return policy
    update: Final = cast(
        Callable[[UUID, int, Literal["partial", "synced", "failed"], str | None], Awaitable[PolicyView | None]],
        setter,
    )
    return (await update(policy.card_id, policy.version, status, error)) or policy.model_copy(
        update={"runtime_status": status, "runtime_error": error}
    )
