"""本模块定义渠道注册表的静态数据结构和拒绝语义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from account_pool.domain import ChannelKind, SupplierKind


class UnsupportedChannelError(ValueError):
    pass


class SupplierResolver(Protocol):
    def get(self, kind: SupplierKind) -> object: ...


@dataclass(frozen=True, slots=True)
class ChannelDefinition:
    kind: ChannelKind
    suppliers: tuple[SupplierKind, ...]
    supplier_registry: SupplierResolver | None
    unavailable_reason: str | None = None

    def supplier(self, kind: SupplierKind) -> object:
        if self.unavailable_reason is not None:
            raise UnsupportedChannelError(self.unavailable_reason)
        if self.supplier_registry is None or kind not in self.suppliers:
            raise UnsupportedChannelError(f"{self.kind.value} does not support {kind.value}")
        return self.supplier_registry.get(kind)
