"""声明模型策略和候选人工覆盖的持久化仓储协议。"""

from typing import Protocol
from uuid import UUID

from account_pool.models import Strategy
from account_pool.routing.models import RoutingCandidateMutation, RoutingOrderMutation, RoutingPolicyResult


class RoutingPolicyRepository(Protocol):
    async def load(self, model: str) -> RoutingPolicyResult: ...

    async def update_policy(
        self,
        model: str,
        strategy: Strategy,
        expected_version: int,
    ) -> RoutingPolicyResult: ...

    async def update_candidate(
        self,
        model: str,
        binding_id: UUID,
        mutation: RoutingCandidateMutation,
    ) -> RoutingPolicyResult: ...

    async def update_order(
        self,
        model: str,
        mutation: RoutingOrderMutation,
    ) -> RoutingPolicyResult: ...

    async def delete_candidate(
        self,
        model: str,
        binding_id: UUID,
        expected_version: int,
    ) -> RoutingPolicyResult: ...
