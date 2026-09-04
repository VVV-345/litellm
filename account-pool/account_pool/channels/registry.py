"""本模块将受支持渠道注册为不可变静态白名单。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from account_pool.channels.base import ChannelDefinition, UnsupportedChannelError
from account_pool.channels.cliproxyapi.channel import CLIProxyAPIChannel
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.channels.freebuff2api.placeholder import DEFINITION as FREEBUFF2API
from account_pool.config import Settings
from account_pool.domain import ChannelKind
from account_pool.secrets import EnvironmentSecretDeriver


@dataclass(frozen=True, slots=True)
class ChannelRegistry:
    definitions: Mapping[ChannelKind, ChannelDefinition]
    implementations: Mapping[ChannelKind, CLIProxyAPIChannel] = MappingProxyType({})

    @classmethod
    def default(
        cls,
        settings: Settings | None = None,
        secrets: EnvironmentSecretDeriver | None = None,
    ) -> ChannelRegistry:
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
        implementations: Final = (
            MappingProxyType(
                {
                    ChannelKind.CLIPROXYAPI: CLIProxyAPIChannel(
                        settings,
                        secrets,
                        suppliers=suppliers,
                    )
                }
            )
            if settings is not None and secrets is not None
            else MappingProxyType({})
        )
        return cls(definitions=definitions, implementations=implementations)

    def get(self, kind: ChannelKind) -> ChannelDefinition:
        try:
            return self.definitions[kind]
        except KeyError as error:
            raise UnsupportedChannelError(f"unknown channel: {kind.value}") from error

    def channel(self, kind: ChannelKind) -> CLIProxyAPIChannel:
        definition: Final = self.get(kind)
        if definition.unavailable_reason is not None:
            raise UnsupportedChannelError(definition.unavailable_reason)
        try:
            return self.implementations[kind]
        except KeyError as error:
            raise UnsupportedChannelError(f"{kind.value} channel is not configured") from error


__all__ = ("ChannelRegistry", "UnsupportedChannelError")
