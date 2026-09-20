"""定义供应商额度观测、刷新结果和错误契约，不依赖解析器或服务编排。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from account_pool.domain import ModelQuotaSnapshot, ProviderEndpointFailure, QuotaSnapshot


class QuotaObservation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    observed_at: datetime | None = None
    signals: Mapping[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderQuotaRefresh:
    quota: QuotaSnapshot
    model_quotas: tuple[ModelQuotaSnapshot, ...] = ()


class ProviderQuotaError(RuntimeError):
    def __init__(self, failures: tuple[ProviderEndpointFailure, ...], message: str) -> None:
        self.failures: Final = failures
        primary: Final = failures[0] if failures else None
        self.endpoint: Final = None if primary is None else primary.endpoint
        self.method: Final = None if primary is None else primary.method
        self.status_code: Final = None if primary is None else primary.status_code
        self.request_id: Final = None if primary is None else primary.request_id
        self.upstream_code: Final = None if primary is None else primary.upstream_code
        self.retryable: Final = any(failure.retryable for failure in failures)
        detail: Final = "; ".join(failure.summary() for failure in failures)
        super().__init__(message if not detail else f"{message}: {detail}")
