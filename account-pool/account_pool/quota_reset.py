"""编排单卡额度兑换，发送前持久化不明确状态，重试只能复用原兑换身份。"""

from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID, uuid4

import httpx

from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import ChannelKind, EnvironmentRecord, SupplierKind
from account_pool.quota_reset_models import ResetObservation, ResetOperation, ResetReply


class ResetStore(Protocol):
    async def latest(self, environment_id: UUID) -> ResetOperation | None: ...
    async def get(self, environment_id: UUID, request_id: UUID) -> ResetOperation | None: ...
    async def save(self, operation: ResetOperation) -> None: ...


class ResetEnvironments(Protocol):
    async def get(self, environment_id: UUID) -> EnvironmentRecord | None: ...


class ResetUpstream(Protocol):
    async def observe(self, record: EnvironmentRecord) -> ResetObservation: ...
    async def consume(self, record: EnvironmentRecord, operation: ResetOperation) -> ResetReply: ...


@dataclass(frozen=True, slots=True)
class QuotaResetService:
    store: ResetStore
    environments: ResetEnvironments
    upstream: ResetUpstream
    ownership: CredentialOwnership

    async def record(self, environment_id: UUID) -> EnvironmentRecord:
        record: Final = await self.environments.get(environment_id)
        if record is None:
            raise ValueError("卡片不存在")
        if record.channel is not ChannelKind.CLIPROXYAPI or record.supplier is not SupplierKind.OPENAI_CODEX:
            raise ValueError("此卡片不支持 Codex 官方额度重置")
        if not record.auth_file_name or not record.auth_index or not record.credential_fingerprints:
            raise ValueError("卡片缺少已确认归属的认证文件")
        return record

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
                observed: Final = completed.model_copy(update={"after": after})
            except (httpx.HTTPError, TimeoutError, ValueError):
                observed = completed.model_copy(update={"message": "兑换业务结果已记录，额度复查失败"})
            await self.store.save(observed)
            return observed
