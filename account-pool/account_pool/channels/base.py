"""本模块定义渠道注册表的静态数据结构和拒绝语义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from account_pool.domain import ChannelKind, SupplierKind

if TYPE_CHECKING:
    from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition


class UnsupportedChannelError(ValueError):
    pass


class SupplierResolver(Protocol):
    def get(self, kind: SupplierKind) -> SupplierDefinition: ...


@dataclass(frozen=True, slots=True)
class ChannelDefinition:
    kind: ChannelKind
    suppliers: tuple[SupplierKind, ...]
    supplier_registry: SupplierResolver | None
    unavailable_reason: str | None = None

    def supplier(self, kind: SupplierKind) -> SupplierDefinition:
        if self.unavailable_reason is not None:
            raise UnsupportedChannelError(self.unavailable_reason)
        if self.supplier_registry is None or kind not in self.suppliers:
            raise UnsupportedChannelError(f"{self.kind.value} does not support {kind.value}")
        try:
            return self.supplier_registry.get(kind)
        except KeyError as error:
            raise UnsupportedChannelError(f"{self.kind.value} does not support {kind.value}") from error
