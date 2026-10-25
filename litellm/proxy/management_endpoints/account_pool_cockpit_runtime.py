"""读取 Cockpit 实际策略版本并显式应用，不保存策略、重启服务或切换流量。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self

from litellm.proxy.management_endpoints.account_pool_cockpit_admin import (
    CockpitAdminClient,
    CockpitFailure,
    RuntimeSnapshot,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_failure import (
    invalid_payload,
    precondition,
)


class CockpitPolicyApply(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: str = Field(pattern=r'^"sha256-[0-9a-f]{64}"$')


class CockpitPolicyRuntime(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    persisted_revision: str = Field(pattern=r'^"sha256-[0-9a-f]{64}"$')
    active_revision: Annotated[str, Field(pattern=r'^"sha256-[0-9a-f]{64}"$')] | None
    apply_supported: bool
    blocked_reason: Literal["non_policy_change", "unsupported_policy", "runtime_unavailable"] | None

    @model_validator(mode="after")
    def consistent_activation(self) -> Self:
        if self.apply_supported != (self.blocked_reason is None):
            raise ValueError("Inconsistent runtime capability")
        if (self.active_revision is None) != (self.blocked_reason == "runtime_unavailable"):
            raise ValueError("Unknown runtime must remain unavailable")
        return self


@dataclass(frozen=True, slots=True)
class RuntimeFailure:
    status: int
    code: Literal[
        "unavailable",
        "invalid_response",
        "revision_conflict",
        "non_policy_change",
        "unsupported_policy",
        "apply_outcome_unknown",
    ]


def _runtime_result(
    result: RuntimeSnapshot | CockpitFailure, *, applying: bool
) -> CockpitPolicyRuntime | RuntimeFailure:
    if isinstance(result, CockpitFailure):
        if precondition(result):
            return RuntimeFailure(409, "revision_conflict")
        if result.code is not None:
            return RuntimeFailure(422, result.code)
        if applying:
            return RuntimeFailure(503, "apply_outcome_unknown")
        if invalid_payload(result):
            return RuntimeFailure(502, "invalid_response")
        return RuntimeFailure(503, "unavailable")
    return CockpitPolicyRuntime(
        persisted_revision=result.persisted_revision,
        active_revision=result.active_revision,
        apply_supported=result.apply_supported,
        blocked_reason=result.blocked_reason,
    )


async def read_cockpit_runtime(client: CockpitAdminClient | None) -> CockpitPolicyRuntime | RuntimeFailure:
    if client is None:
        return RuntimeFailure(503, "unavailable")
    return _runtime_result(await client.read_policy_runtime(), applying=False)


async def apply_cockpit_policy(
    client: CockpitAdminClient | None,
    request: CockpitPolicyApply,
) -> CockpitPolicyRuntime | RuntimeFailure:
    if client is None:
        return RuntimeFailure(503, "unavailable")
    return _runtime_result(await client.apply_policy(request.revision), applying=True)
