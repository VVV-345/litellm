"""本模块将受支持渠道注册为不可变静态白名单。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from account_pool.channels.base import ChannelDefinition, UnsupportedChannelError
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.channels.freebuff2api.placeholder import DEFINITION as FREEBUFF2API
from account_pool.domain import ChannelKind


@dataclass(frozen=True, slots=True)
class ChannelRegistry:
    definitions: Mapping[ChannelKind, ChannelDefinition]

    @classmethod
    def default(cls) -> ChannelRegistry:
        suppliers: Final = SupplierRegistry.default()
        cliproxyapi: Final = ChannelDefinition(
            kind=ChannelKind.CLIPROXYAPI,
            suppliers=tuple(suppliers.definitions),
            supplier_registry=suppliers,
        )
        definitions: Final = MappingProxyType(
            {
                cliproxyapi.kind: cliproxyapi,
                FREEBUFF2API.kind: FREEBUFF2API,
            }
        )
        return cls(definitions=definitions)

    def get(self, kind: ChannelKind) -> ChannelDefinition:
        return self.definitions[kind]


__all__ = ("ChannelRegistry", "UnsupportedChannelError")
