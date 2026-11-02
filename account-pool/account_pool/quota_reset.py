"""编排单卡额度兑换，发送前持久化不明确状态，重试只能复用原兑换身份。"""

from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID, uuid4

import httpx

from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import (
    ChannelKind,
    EnvironmentRecord,
    EnvironmentStatus,
    ModelQuotaSnapshot,
    QuotaSnapshot,
    SupplierKind,
    utc_now,
)
from account_pool.quota_reset_models import ResetObservation, ResetOperation, ResetReply, ResetVerification


class ResetStore(Protocol):
    async def latest(self, environment_id: UUID) -> ResetOperation | None: ...
    async def get(self, environment_id: UUID, request_id: UUID) -> ResetOperation | None: ...
    async def save(self, operation: ResetOperation) -> None: ...


class ResetEnvironments(Protocol):
    async def get(self, environment_id: UUID) -> EnvironmentRecord | None: ...
    async def restore_verified(
        self,
        record: EnvironmentRecord,
        model: str,
        quota: QuotaSnapshot,
        model_quotas: tuple[ModelQuotaSnapshot, ...],
    ) -> bool: ...


class ResetUpstream(Protocol):
    async def observe(self, record: EnvironmentRecord) -> ResetObservation: ...
    async def consume(self, record: EnvironmentRecord, operation: ResetOperation) -> ResetReply: ...
    async def verify(self, record: EnvironmentRecord, model: str, recover: bool) -> ResetVerification: ...


def recovered_record(
    record: EnvironmentRecord,
    model: str,
    quota: QuotaSnapshot | None = None,
    model_quotas: tuple[ModelQuotaSnapshot, ...] = (),
) -> EnvironmentRecord | None:
    if (
        not record.enabled
        or record.manual_cooldown
        or record.auth_file_disabled
        or record.status not in (EnvironmentStatus.READY, EnvironmentStatus.COOLING_DOWN)
        or record.configuration_pending
        or (record.cooldown_until is not None and record.cooldown_until > utc_now())
        or record.desired_configuration_version != record.observed_configuration_version
        or record.last_error not in (None, "上游认证失效，请重新认证")
        or any(
            item.model == model and item.reason != "rate_limit" and item.retry_at > utc_now()
            for item in record.model_cooldowns
        )
    ):
        return None
    return record.model_copy(
        update={
            "status": EnvironmentStatus.READY,
            "automatic_cooldown": False,
            "cooldown_until": None,
            "last_error": None,
            "version": record.version + 1,
            "updated_at": utc_now(),
            "quota": record.quota
            if quota is None
            else record.quota.model_copy(
                update={
                    "windows": quota.windows,
                    "observed_at": quota.observed_at,
                    "source": quota.source,
                    "refresh_attempted_at": quota.refresh_attempted_at,
                    "refresh_status": quota.refresh_status,
                    "refresh_error": quota.refresh_error,
                    "refresh_failures": quota.refresh_failures,
                    "reset_credits_available": quota.reset_credits_available,
                }
            ),
            "model_cooldowns": tuple(
                item for item in record.model_cooldowns if item.model != model or item.reason != "rate_limit"
            ),
            # 成功探针只证伪当前模型的旧零额度，其他模型的未知/有效限制必须保留。
            "model_quotas": tuple(
                item for item in record.model_quotas
                if (quota is None or item.model != model) and not any(fresh.model == item.model for fresh in model_quotas)
            ) + model_quotas + (
                (ModelQuotaSnapshot(model=model, quota=QuotaSnapshot(observed_at=utc_now(), source="provider_api")),)
                if quota is not None and not any(item.model == model for item in model_quotas) else ()
            ),
        }
    )


@dataclass(frozen=True, slots=True)
class QuotaResetService:
    store: ResetStore
    environments: ResetEnvironments
    upstream: ResetUpstream
    ownership: CredentialOwnership

    async def recover_due(self, environment_id: UUID) -> bool:
        from account_pool.quota_recovery_scheduler import due_models

        record = await self.record(environment_id)
        models = due_models(record, utc_now())
        if not models:
            return True
        observation = await self.upstream.observe(record)
        if not observation.quota.windows or any(w.remaining_percent <= 0 for w in observation.quota.windows):
            return False
        succeeded = True
        for model in models:
            if any(w.remaining_percent <= 0 for item in observation.model_quotas if item.model == model for w in item.quota.windows):
                succeeded = False
                continue
            result = await self.verify(environment_id, model, True)
            succeeded = succeeded and result.restored
        return succeeded

    async def record(self, environment_id: UUID) -> EnvironmentRecord:
        record: Final = await self.environments.get(environment_id)
        if record is None:
            raise ValueError("卡片不存在")
        if record.channel is not ChannelKind.CLIPROXYAPI or record.supplier is not SupplierKind.OPENAI_CODEX:
            raise ValueError("此卡片不支持 Codex 官方额度重置")
        if not record.auth_file_name or not record.auth_index or not record.credential_fingerprints:
            raise ValueError("卡片缺少已确认归属的认证文件")
        return record

    async def verify(self, environment_id: UUID, model: str, recover: bool) -> ResetVerification:
        async with self.ownership.operation(environment_id):
            record: Final = await self.record(environment_id)
            if model not in record.enabled_models:
                raise ValueError("请选择本卡已启用的模型")
            if recovered_record(record, model) is None:
                return ResetVerification(passed=False, restored=False, reason="independent_block")
            result: Final = await self.upstream.verify(record, model, recover)
            if not result.passed or not result.restored or not recover:
                return result.model_copy(update={"restored": False})
            try:
                observation: Final = await self.upstream.observe(record)
            except (ValueError, httpx.HTTPError, TimeoutError):
                return ResetVerification(passed=True, restored=False, reason="quota_check_failed")
            if any(
                window.remaining_percent <= 0
                for snapshot in (observation.quota, *(item.quota for item in observation.model_quotas if item.model == model))
                for window in snapshot.windows
            ):
                return ResetVerification(passed=True, restored=False, reason="quota_not_recovered")
            # CPA 成功不等于号池恢复；数据库必须仍为验证前快照，且没有独立运行时阻断。
            restored: Final = await self.environments.restore_verified(
                record, model, observation.quota, observation.model_quotas
            )
            return ResetVerification(passed=True, restored=restored, reason="restored" if restored else "state_changed")

    async def prepare(self, environment_id: UUID, previous_request_id: UUID | None) -> ResetOperation:
        async with self.ownership.operation(environment_id):
            record: Final = await self.record(environment_id)
            latest: Final = await self.store.latest(environment_id)
            if latest is not None and (
                latest.status in ("prepared", "uncertain") or latest.request_id != previous_request_id
            ):
                return latest
            observation: Final = await self.upstream.observe(record)
            if not observation.eligible or observation.available == 0 or observation.credit_id is None:
                raise ValueError("没有适用的重置次数或当前无须重置")
            operation: Final = ResetOperation(
                request_id=uuid4(),
                environment_id=environment_id,
                credit_id=observation.credit_id,
                credential_fingerprints=record.credential_fingerprints,
                before=observation,
            )
            await self.store.save(operation)
            return operation

    async def consume(self, environment_id: UUID, request_id: UUID) -> ResetOperation:
        async with self.ownership.operation(environment_id):
            record: Final = await self.record(environment_id)
            operation: Final = await self.store.get(environment_id, request_id)
            if operation is None:
                raise ValueError("请先查询并确认本卡的兑换操作")
            if operation.credential_fingerprints != record.credential_fingerprints:
                raise ValueError("凭据已变更，禁止将原兑换身份用于其他凭据")
            if operation.status not in ("prepared", "uncertain"):
                return operation
            # 单独提交日志后才发送请求；进程退出或取消也不能生成新兑换身份。
            pending: Final = operation.model_copy(update={"status": "uncertain", "message": "兑换结果待核对"})
            await self.store.save(pending)
            try:
                reply: Final = await self.upstream.consume(record, pending)
            except (httpx.HTTPError, TimeoutError, ValueError):
                return pending
            completed: Final = pending.model_copy(
                update={
                    "status": reply.code,
                    "windows_reset": reply.windows_reset,
                    "message": None,
                }
            )
            await self.store.save(completed)
            try:
                after: Final = await self.upstream.observe(record)
            except (httpx.HTTPError, TimeoutError, ValueError):
                unobserved: Final = completed.model_copy(update={"message": "兑换业务结果已记录，额度复查失败"})
                await self.store.save(unobserved)
                return unobserved
            observed: Final = completed.model_copy(update={"after": after})
            await self.store.save(observed)
            return observed
