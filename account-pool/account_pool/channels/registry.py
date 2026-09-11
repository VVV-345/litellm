"""本模块将受支持渠道注册为不可变静态白名单。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from account_pool.channels.base import ChannelDefinition, UnsupportedChannelError
from account_pool.channels.cliproxyapi.channel import CLIProxyAPIChannel
from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition, parse_empty_quota
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.channels.freebuff2api.channel import FreeBuff2APIChannel
from account_pool.channels.freebuff2api.suppliers import FREEBUFF_SUPPLIERS
from account_pool.channels.openai_compatible import OpenAICompatibleChannel
from account_pool.config import Settings
from account_pool.domain import AuthorizationFlow, ChannelKind, SupplierKind
from account_pool.secrets import EnvironmentSecretDeriver


@dataclass(frozen=True, slots=True)
class ChannelRegistry:
    definitions: Mapping[ChannelKind, ChannelDefinition]
    implementations: Mapping[ChannelKind, CLIProxyAPIChannel | FreeBuff2APIChannel | OpenAICompatibleChannel] = MappingProxyType({})

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
        freebuff2api: Final = ChannelDefinition(
            kind=ChannelKind.FREEBUFF2API,
            suppliers=tuple(FREEBUFF_SUPPLIERS),
            supplier_registry=FreeBuffSupplierResolver(),
        )
        openai_compatible: Final = ChannelDefinition(
            kind=ChannelKind.OPENAI_COMPATIBLE,
            suppliers=(SupplierKind.OPENAI_COMPATIBLE,),
            supplier_registry=OpenAICompatibleSupplierResolver(),
        )
        definitions: Final = MappingProxyType(
            {
                openai_compatible.kind: openai_compatible,
                cliproxyapi.kind: cliproxyapi,
                freebuff2api.kind: freebuff2api,
            }
        )
        implementations: Final = (
            MappingProxyType(
                {
                    ChannelKind.OPENAI_COMPATIBLE: OpenAICompatibleChannel(secrets),
                    ChannelKind.CLIPROXYAPI: CLIProxyAPIChannel(
                        settings,
                        secrets,
                        suppliers=suppliers,
                    ),
                    ChannelKind.FREEBUFF2API: FreeBuff2APIChannel(settings, secrets),
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

    def channel(self, kind: ChannelKind) -> CLIProxyAPIChannel | FreeBuff2APIChannel | OpenAICompatibleChannel:
        definition: Final = self.get(kind)
        if definition.unavailable_reason is not None:
            raise UnsupportedChannelError(definition.unavailable_reason)
        try:
            return self.implementations[kind]
        except KeyError as error:
            raise UnsupportedChannelError(f"{kind.value} channel is not configured") from error


class FreeBuffSupplierResolver:
    def get(self, kind: object) -> object:
        try:
            return FREEBUFF_SUPPLIERS[kind]  # type: ignore[index]  # resolver contract is keyed by SupplierKind
        except (KeyError, TypeError) as error:
            raise KeyError(str(kind)) from error


class OpenAICompatibleSupplierResolver:
    def get(self, kind: SupplierKind) -> SupplierDefinition:
        if kind is not SupplierKind.OPENAI_COMPATIBLE:
            raise KeyError(kind.value)
        return _OPENAI_COMPATIBLE_SUPPLIER


_OPENAI_COMPATIBLE_SUPPLIER: Final = SupplierDefinition(
    kind=SupplierKind.OPENAI_COMPATIBLE,
    authorization_flow=AuthorizationFlow.BROWSER_OAUTH,
    authorization_path="",
    callback_provider_key="",
    auth_file_provider_key="",
    excluded_models_key="",
    callback_port=None,
    callback_path=None,
    quota_parser=parse_empty_quota,
)


__all__ = ("ChannelRegistry", "UnsupportedChannelError")
