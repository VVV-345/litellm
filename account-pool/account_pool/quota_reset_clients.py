"""分派 Codex 与 GLM 的手动额度操作，复用同一个兑换状态机及持久化存储。"""

from dataclasses import dataclass

from account_pool.domain import EnvironmentRecord
from account_pool.provider_families import provider_family_kind
from account_pool.quota_reset import ResetUpstream
from account_pool.quota_reset_models import ResetObservation, ResetOperation, ResetReply, ResetVerification


@dataclass(frozen=True, slots=True)
class ProviderResetClient:
    codex: ResetUpstream
    glm: ResetUpstream

    def _client(self, record: EnvironmentRecord) -> ResetUpstream:
        return self.glm if provider_family_kind(record) == "glm" else self.codex

    async def observe(self, record: EnvironmentRecord) -> ResetObservation:
        return await self._client(record).observe(record)

    async def consume(self, record: EnvironmentRecord, operation: ResetOperation) -> ResetReply:
        return await self._client(record).consume(record, operation)

    async def verify(self, record: EnvironmentRecord, model: str, recover: bool) -> ResetVerification:
        return await self._client(record).verify(record, model, recover)
