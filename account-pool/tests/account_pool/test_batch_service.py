"""本文件验证批量任务逐账号执行、版本冲突和失败结果持久化。"""

from __future__ import annotations

from datetime import timedelta
from typing import Final
from uuid import UUID, uuid4

import pytest
from account_pool.batch_models import BatchAuthorization, BatchClaim, BatchJob, BatchRequest, BatchTarget
from account_pool.batch_service import BatchService
from account_pool.domain import (
    AuthorizationFlow,
    AuthorizationView,
    EnvironmentRecord,
    EnvironmentStatus,
    SupplierKind,
    UpdateEnvironmentRequest,
    to_view,
    utc_now,
)
from account_pool.error_logs import ErrorLogService
from account_pool.policies import AccountPolicy, CodexPolicy, ModelAlias, PolicyView, RoutingPolicy
from account_pool.result import Success
from pydantic import HttpUrl, TypeAdapter
from test_account_pool import MemoryRepository, _record
from test_management import MemoryLogs, MemoryPolicies


class MemoryBatches:
    def __init__(self, claim: BatchClaim | None = None) -> None:
        self.claim_value = claim
        self.finished: tuple[bool, str, BatchAuthorization | None] | None = None

    async def submit(self, request: BatchRequest) -> bool:
        return True

    async def list(self) -> tuple[BatchJob, ...]:
        return ()

    async def get(self, job_id) -> BatchJob | None:
        return None

    async def claim(self) -> BatchClaim | None:
        claim: Final = self.claim_value
        self.claim_value = None
        return claim

    async def finish(
        self,
        claim: BatchClaim,
        succeeded: bool,
        message: str,
        authorization: BatchAuthorization | None,
    ) -> None:
        self.finished = succeeded, message, authorization


class UpdatingService:
    def __init__(self, environments: MemoryRepository) -> None:
        self.environments: Final = environments
        self.requests: tuple[UpdateEnvironmentRequest, ...] = ()
        self.refreshes: tuple[UUID, ...] = ()
        self.authorizations: tuple[tuple[UUID, str | None], ...] = ()
        self.deletions: tuple[tuple[UUID, str | None], ...] = ()

    async def get_environment(self, environment_id: UUID) -> Success[UUID]:
        return Success(environment_id)

    async def refresh_environment(self, environment_id: UUID) -> Success[UUID]:
        self.refreshes = (*self.refreshes, environment_id)
        return Success(environment_id)

    async def authorize_environment(
        self, environment_id: UUID, operation_id: str | None = None
    ) -> Success[AuthorizationView]:
        self.authorizations = (*self.authorizations, (environment_id, operation_id))
        record: Final = await self.environments.get(environment_id)
        assert record is not None
        return Success(
            AuthorizationView(
                environment=to_view(record),
                flow=AuthorizationFlow.DEVICE_CODE,
                authorization_url=TypeAdapter(HttpUrl).validate_python("https://example.com/device"),
                ssh_command=None,
                user_code="ABCD-1234",
                expires_at=utc_now() + timedelta(minutes=5),
            )
        )

    async def update_environment(
        self,
        environment_id: UUID,
        request: UpdateEnvironmentRequest,
    ) -> Success[UUID]:
        self.requests = (*self.requests, request)
        return Success(environment_id)

    async def delete_environment(self, environment_id: UUID, operation_id: str | None = None) -> Success[None]:
        self.deletions = (*self.deletions, (environment_id, operation_id))
        await self.environments.delete(environment_id)
        return Success(None)


class MemoryCooldowns:
    def __init__(self, account_id: UUID) -> None:
        self.cooled: tuple[UUID, ...] = (account_id,)

    async def clear_cooldown(self, account_id: UUID) -> None:
        self.cooled = tuple(identifier for identifier in self.cooled if identifier != account_id)


@pytest.mark.asyncio
async def test_batch_disable_uses_version_snapshot_and_idempotency_key() -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    request: Final = BatchRequest(
        job_id=uuid4(), action="disable", targets=(BatchTarget(account_id=record.id, version=record.version),)
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4()))
    environments: Final = MemoryRepository(record)
    updater: Final = UpdatingService(environments)
    service: Final = BatchService(batches, environments, updater, MemoryPolicies(), ErrorLogService(MemoryLogs()))

    assert await service.run_once()
    assert batches.finished == (True, "Account configuration updated", None)
    assert updater.requests[0].enabled is False
    assert updater.requests[0].operation_id == f"batch:{request.job_id}:{record.id}"


@pytest.mark.asyncio
async def test_batch_rejects_stale_target_without_mutating_account() -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    request: Final = BatchRequest(
        job_id=uuid4(), action="cooldown", targets=(BatchTarget(account_id=record.id, version=record.version + 1),)
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4()))
    updater: Final = UpdatingService(MemoryRepository(record))
    logs: Final = MemoryLogs()
    service: Final = BatchService(batches, updater.environments, updater, MemoryPolicies(), ErrorLogService(logs))

    assert await service.run_once()
    assert batches.finished == (False, "Account changed after the batch was submitted", None)
    assert updater.requests == ()
    assert logs.events[-1].finished_at is not None and logs.events[-1].finished_at <= utc_now()


@pytest.mark.asyncio
async def test_reclaimed_refresh_retries_after_the_account_version_changes() -> None:
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(update={"version": 2})
    request: Final = BatchRequest(
        job_id=uuid4(), action="refresh", targets=(BatchTarget(account_id=record.id, version=1),)
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4(), attempts=2))
    environments: Final = MemoryRepository(record)
    updater: Final = UpdatingService(environments)
    service: Final = BatchService(batches, environments, updater, MemoryPolicies(), ErrorLogService(MemoryLogs()))

    assert await service.run_once()
    assert batches.finished == (True, "Account refreshed", None)
    assert updater.refreshes == (record.id,)


@pytest.mark.asyncio
async def test_batch_authorize_persists_resumable_authorization_details() -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    request: Final = BatchRequest(
        job_id=uuid4(), action="authorize", targets=(BatchTarget(account_id=record.id, version=record.version),)
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4()))
    environments: Final = MemoryRepository(record)
    updater: Final = UpdatingService(environments)
    service: Final = BatchService(batches, environments, updater, MemoryPolicies(), ErrorLogService(MemoryLogs()))

    assert await service.run_once()
    assert updater.authorizations == ((record.id, f"batch:{request.job_id}:{record.id}"),)
    assert batches.finished is not None
    succeeded, message, authorization = batches.finished
    assert succeeded is True
    assert message == "Authorization details generated"
    assert authorization is not None
    assert authorization.flow is AuthorizationFlow.DEVICE_CODE
    assert str(authorization.authorization_url) == "https://example.com/device"
    assert authorization.user_code == "ABCD-1234"


@pytest.mark.asyncio
async def test_reclaimed_batch_authorize_reuses_active_operation_after_version_changes() -> None:
    base: Final = _record(status=EnvironmentStatus.READY)
    request: Final = BatchRequest(
        job_id=uuid4(), action="authorize", targets=(BatchTarget(account_id=base.id, version=base.version),)
    )
    operation_id: Final = f"batch:{request.job_id}:{base.id}"
    record: Final = base.model_copy(
        update={
            "version": base.version + 1,
            "status": EnvironmentStatus.AWAITING_AUTHORIZATION,
            "operation_id": operation_id,
            "oauth_authorization_url": "https://example.com/device",
            "oauth_expires_at": utc_now() + timedelta(minutes=5),
        }
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4(), attempts=2))
    environments: Final = MemoryRepository(record)
    updater: Final = UpdatingService(environments)
    service: Final = BatchService(batches, environments, updater, MemoryPolicies(), ErrorLogService(MemoryLogs()))

    assert await service.run_once()
    assert batches.finished is not None and batches.finished[0] is True
    assert updater.authorizations == ((record.id, operation_id),)


@pytest.mark.asyncio
async def test_reclaimed_batch_authorize_does_not_restart_completed_authorization() -> None:
    base: Final = _record(status=EnvironmentStatus.READY)
    request: Final = BatchRequest(
        job_id=uuid4(), action="authorize", targets=(BatchTarget(account_id=base.id, version=base.version),)
    )
    operation_id: Final = f"batch:{request.job_id}:{base.id}"
    record: Final = base.model_copy(update={"version": base.version + 2, "operation_id": operation_id})
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4(), attempts=2))
    environments: Final = MemoryRepository(record)
    updater: Final = UpdatingService(environments)
    service: Final = BatchService(batches, environments, updater, MemoryPolicies(), ErrorLogService(MemoryLogs()))

    assert await service.run_once()
    assert batches.finished == (True, "Authorization already submitted", None)
    assert updater.authorizations == ()


@pytest.mark.asyncio
async def test_batch_authorize_logs_version_conflicts_as_authorization_failures() -> None:
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(update={"version": 2})
    request: Final = BatchRequest(
        job_id=uuid4(), action="authorize", targets=(BatchTarget(account_id=record.id, version=1),)
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4()))
    environments: Final = MemoryRepository(record)
    logs: Final = MemoryLogs()
    service: Final = BatchService(
        batches, environments, UpdatingService(environments), MemoryPolicies(), ErrorLogService(logs)
    )

    assert await service.run_once()
    assert batches.finished == (False, "Account changed after the batch was submitted", None)
    assert logs.events[-1].stage == "authorization"


@pytest.mark.asyncio
async def test_batch_delete_uses_version_snapshot_and_idempotency_key() -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    request: Final = BatchRequest(
        job_id=uuid4(), action="delete", targets=(BatchTarget(account_id=record.id, version=record.version),)
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4()))
    environments: Final = MemoryRepository(record)
    updater: Final = UpdatingService(environments)
    service: Final = BatchService(batches, environments, updater, MemoryPolicies(), ErrorLogService(MemoryLogs()))

    assert await service.run_once()
    assert batches.finished == (True, "Account deleted", None)
    assert updater.deletions == ((record.id, f"batch:{request.job_id}:{record.id}"),)
    assert await environments.get(record.id) is None


@pytest.mark.asyncio
async def test_batch_delete_rejects_an_account_changed_after_submission() -> None:
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(update={"version": 2})
    request: Final = BatchRequest(
        job_id=uuid4(), action="delete", targets=(BatchTarget(account_id=record.id, version=1),)
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4()))
    environments: Final = MemoryRepository(record)
    updater: Final = UpdatingService(environments)
    service: Final = BatchService(batches, environments, updater, MemoryPolicies(), ErrorLogService(MemoryLogs()))

    assert await service.run_once()
    assert batches.finished == (False, "Account changed after the batch was submitted", None)
    assert updater.deletions == ()
    assert await environments.get(record.id) == record


@pytest.mark.asyncio
async def test_reclaimed_batch_delete_continues_its_own_cleanup() -> None:
    base: Final = _record(status=EnvironmentStatus.READY)
    request: Final = BatchRequest(
        job_id=uuid4(), action="delete", targets=(BatchTarget(account_id=base.id, version=base.version),)
    )
    operation_id: Final = f"batch:{request.job_id}:{base.id}"
    record: Final = base.model_copy(
        update={"version": base.version + 1, "status": EnvironmentStatus.DELETING, "operation_id": operation_id}
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4(), attempts=2))
    environments: Final = MemoryRepository(record)
    updater: Final = UpdatingService(environments)
    service: Final = BatchService(batches, environments, updater, MemoryPolicies(), ErrorLogService(MemoryLogs()))

    assert await service.run_once()
    assert batches.finished == (True, "Account deleted", None)
    assert updater.deletions == ((record.id, operation_id),)


@pytest.mark.asyncio
async def test_new_batch_delete_continues_an_existing_cleanup_operation() -> None:
    base: Final = _record(status=EnvironmentStatus.READY)
    existing_operation_id: Final = "existing-delete-operation"
    record: Final = base.model_copy(
        update={
            "version": base.version + 1,
            "status": EnvironmentStatus.DELETING,
            "operation_id": existing_operation_id,
        }
    )
    request: Final = BatchRequest(
        job_id=uuid4(), action="delete", targets=(BatchTarget(account_id=record.id, version=record.version),)
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4()))
    environments: Final = MemoryRepository(record)
    updater: Final = UpdatingService(environments)
    service: Final = BatchService(batches, environments, updater, MemoryPolicies(), ErrorLogService(MemoryLogs()))

    assert await service.run_once()
    assert batches.finished == (True, "Account deleted", None)
    assert updater.deletions == ((record.id, existing_operation_id),)


@pytest.mark.asyncio
async def test_reclaimed_batch_delete_succeeds_after_account_was_removed() -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    request: Final = BatchRequest(
        job_id=uuid4(), action="delete", targets=(BatchTarget(account_id=record.id, version=record.version),)
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4(), attempts=2))
    environments: Final = MemoryRepository(record)
    await environments.delete(record.id)
    updater: Final = UpdatingService(environments)
    service: Final = BatchService(batches, environments, updater, MemoryPolicies(), ErrorLogService(MemoryLogs()))

    assert await service.run_once()
    assert batches.finished == (True, "Account already deleted", None)
    assert updater.deletions == ()


@pytest.mark.asyncio
async def test_reclaimed_policy_job_accepts_an_already_applied_policy() -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    policy: Final = AccountPolicy(routing=RoutingPolicy(weight=4))
    request: Final = BatchRequest(
        job_id=uuid4(),
        action="policy",
        policy=policy,
        targets=(BatchTarget(account_id=record.id, version=record.version, policy_version=0),),
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4(), attempts=2))
    environments: Final = MemoryRepository(record)
    policies: Final = MemoryPolicies()
    policies.records[record.id] = PolicyView(card_id=record.id, version=1, policy=policy)
    service: Final = BatchService(
        batches,
        environments,
        UpdatingService(environments),
        policies,
        ErrorLogService(MemoryLogs()),
    )

    assert await service.run_once()
    assert batches.finished == (True, "Policy already updated", None)
    assert policies.records[record.id].version == 1


@pytest.mark.asyncio
async def test_batch_policy_syncs_saved_policy_to_runtime() -> None:
    record: Final = _record(status=EnvironmentStatus.READY)
    policy: Final = AccountPolicy(routing=RoutingPolicy(weight=4))
    request: Final = BatchRequest(
        job_id=uuid4(),
        action="policy",
        policy=policy,
        targets=(BatchTarget(account_id=record.id, version=record.version, policy_version=0),),
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4()))
    environments: Final = MemoryRepository(record)
    policies: Final = MemoryPolicies()
    synchronized: list[tuple[UUID, AccountPolicy]] = []

    async def sync_policy(card: EnvironmentRecord, saved: AccountPolicy) -> None:
        synchronized.append((card.id, saved))

    service: Final = BatchService(
        batches,
        environments,
        UpdatingService(environments),
        policies,
        ErrorLogService(MemoryLogs()),
        sync_policy=sync_policy,
    )

    assert await service.run_once()
    assert batches.finished == (True, "Policy updated", None)
    assert synchronized == [(record.id, policy)]


@pytest.mark.asyncio
async def test_batch_release_clears_persisted_and_gateway_cooldown() -> None:
    record: Final = _record(status=EnvironmentStatus.COOLING_DOWN).model_copy(update={"manual_cooldown": True})
    request: Final = BatchRequest(
        job_id=uuid4(), action="release", targets=(BatchTarget(account_id=record.id, version=record.version),)
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4()))
    environments: Final = MemoryRepository(record)
    updater: Final = UpdatingService(environments)
    cooldowns: Final = MemoryCooldowns(record.id)
    service: Final = BatchService(
        batches,
        environments,
        updater,
        MemoryPolicies(),
        ErrorLogService(MemoryLogs()),
        cooldowns,
    )

    assert await service.run_once()
    assert updater.requests[0].manual_cooldown is False
    assert cooldowns.cooled == ()


@pytest.mark.parametrize("case", ["missing_member", "preferred", "alias", "codex_supplier"])
@pytest.mark.asyncio
async def test_batch_policy_reuses_card_scope_validation(case: str) -> None:
    base: Final = _record(status=EnvironmentStatus.READY)
    record: Final = base.model_copy(update={"supplier": SupplierKind.KIMI}) if case == "codex_supplier" else base
    policy: Final = (
        AccountPolicy(account_ids=(uuid4(),))
        if case == "missing_member"
        else AccountPolicy(routing=RoutingPolicy(preferred_account_ids=(uuid4(),)))
        if case == "preferred"
        else AccountPolicy(model_aliases=(ModelAlias(alias="alias", target="missing"),))
        if case == "alias"
        else AccountPolicy(codex=CodexPolicy())
    )
    request: Final = BatchRequest(
        job_id=uuid4(),
        action="policy",
        policy=policy,
        targets=(BatchTarget(account_id=record.id, version=record.version),),
    )
    batches: Final = MemoryBatches(BatchClaim(request=request, target=request.targets[0], token=uuid4()))
    environments: Final = MemoryRepository(record)
    policies: Final = MemoryPolicies()
    service: Final = BatchService(
        batches,
        environments,
        UpdatingService(environments),
        policies,
        ErrorLogService(MemoryLogs()),
    )

    assert await service.run_once()
    assert batches.finished is not None and batches.finished[0] is False
    assert policies.records == {}
