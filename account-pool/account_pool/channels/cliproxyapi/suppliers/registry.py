"""本模块将已验证的 CLIProxyAPI 供应商契约注册为只读白名单。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from account_pool.channels.cliproxyapi.suppliers.anthropic_claude import DEFINITION as ANTHROPIC_CLAUDE
from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.cliproxyapi.suppliers.gemini import DEFINITION as GEMINI
from account_pool.channels.cliproxyapi.suppliers.gemini_interactions import DEFINITION as GEMINI_INTERACTIONS
from account_pool.channels.cliproxyapi.suppliers.google_antigravity import DEFINITION as GOOGLE_ANTIGRAVITY
from account_pool.channels.cliproxyapi.suppliers.kimi import DEFINITION as KIMI
from account_pool.channels.cliproxyapi.suppliers.openai_codex import DEFINITION as OPENAI_CODEX
from account_pool.channels.cliproxyapi.suppliers.vertex import DEFINITION as VERTEX
from account_pool.channels.cliproxyapi.suppliers.xai import DEFINITION as XAI
from account_pool.domain import SupplierKind


@dataclass(frozen=True, slots=True)
class SupplierRegistry:
    definitions: Mapping[SupplierKind, SupplierDefinition]

    @classmethod
    def default(cls) -> SupplierRegistry:
        definitions: Final = MappingProxyType(
            {
                definition.kind: definition
                for definition in (
                    OPENAI_CODEX,
                    ANTHROPIC_CLAUDE,
                    GOOGLE_ANTIGRAVITY,
                    KIMI,
                    XAI,
                    GEMINI,
                    GEMINI_INTERACTIONS,
                    VERTEX,
                )
            }
        )
        return cls(definitions=definitions)

    def get(self, kind: SupplierKind) -> SupplierDefinition:
        return self.definitions[kind]
