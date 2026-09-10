"""本模块执行持久化批量账号操作，复用环境服务的版本控制和幂等配置流程。"""

from __future__ import annotations

import asyncio
from typing import Final
from uuid import UUID

from account_pool.batch_models import BatchClaim, BatchJob, BatchRequest
from account_pool.batch_repository import BatchRepository
from account_pool.domain import UpdateEnvironmentRequest, configuration_from_record, utc_now
from account_pool.error_logs import ErrorLogService
from account_pool.gateway_repository import CooldownRepository
from account_pool.policies import PolicyRepository, PolicyUpdate, policy_validation_error
from account_pool.ports import EnvironmentRepository
from account_pool.result import Failure, FailureCode, Result, Success
from account_pool.service import EnvironmentService


class BatchService:
    def __init__(
        self,
        repository: BatchRepository,
        environments: EnvironmentRepository,
        service: EnvironmentService,
        policies: PolicyRepository,
        logs: ErrorLogService,
        leases: CooldownRepository | None = None,
    ) -> None:
        self.repository: Final = repository
        self.environments: Final = environments
        self.service: Final = service
        self.policies: Final = policies
        self.logs: Final = logs
        self.leases: Final = leases

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
        succeeded, message = outcome
        await self.repository.finish(claim, succeeded, message)
        record: Final = await self.environments.get(claim.target.account_id)
        if record is not None and not succeeded:
            await self.logs.record(record, "configuration", RuntimeError(message), started_at=started)
        return True

    async def run_until_cancelled(self, stopped: asyncio.Event, interval: float = 1.0) -> None:
        while not stopped.is_set():
            did_work = await self.run_once()
            if not did_work:
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=interval)
                except TimeoutError:
                    pass

    async def _run_claim(self, claim: BatchClaim) -> Result[str]:
        record: Final = await self.environments.get(claim.target.account_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "Account no longer exists")
        if claim.request.action == "refresh":
            if record.version != claim.target.version and claim.attempts == 1:
                return Failure(FailureCode.CONFLICT, "Account changed after the batch was submitted")
            refresh_result: Final = await self.service.refresh_environment(record.id)
            return (
                Success("Account refreshed")
                if isinstance(refresh_result, Success)
                else Failure(FailureCode.UPSTREAM, refresh_result.message)
            )
        if claim.request.action == "policy":
            if record.version != claim.target.version:
                return Failure(FailureCode.CONFLICT, "Account changed after the batch was submitted")
            policy_request: Final = claim.request.policy
            if policy_request is None:
                return Failure(FailureCode.INVALID, "Policy is required")
            current_policy: Final = await self.policies.get(record.id)
            if current_policy.policy == policy_request:
                return Success("Policy already updated")
            validation_error: Final = await policy_validation_error(record, policy_request, self.environments)
            if validation_error is not None:
                return Failure(FailureCode.INVALID, validation_error)
            policy: Final = await self.policies.save(
                record.id, PolicyUpdate(version=claim.target.policy_version, policy=policy_request)
            )
            return (
                Success("Policy updated")
                if policy is not None
                else Failure(FailureCode.CONFLICT, "Policy changed after submission")
            )
        configuration: Final = configuration_from_record(record)
        operation_id: Final = f"batch:{claim.request.job_id}:{record.id}"
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
        return Success("Account configuration updated")

    async def _run_safely(self, claim: BatchClaim) -> tuple[bool, str]:
        try:
            run_result: Final = await self._run_claim(claim)
        except Exception as error:
            return False, error.__class__.__name__
        return isinstance(run_result, Success), run_result.value if isinstance(
            run_result, Success
        ) else run_result.message
