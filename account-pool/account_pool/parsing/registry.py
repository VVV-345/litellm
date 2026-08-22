"""按固定优先级选择解析器，选择阶段不接收或使用渠道凭证。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Self
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from account_pool.domain.provider_source import ProviderValidationResult
from account_pool.models import FrozenModel
from account_pool.parsing.models import ParserRun


class ParserSelectionSource(StrEnum):
    EXPLICIT = "explicit"
    PROVIDER_ORIGIN = "provider_origin"
    PROVIDER_ONLY = "provider_only"
    OPENAI_COMPATIBLE = "openai_compatible"
    MANUAL = "manual"


class ParserRegistration(FrozenModel):
    parser_id: str = Field(min_length=1)
    provider_ids: tuple[str, ...] = Field(min_length=1)
    exact_origins: tuple[str, ...] = ()
    openai_compatible_fallback: bool = False
    match_provider_only: bool = False

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        if len(self.provider_ids) != len(frozenset(self.provider_ids)):
            raise ValueError("parser provider IDs must be unique")
        if len(self.exact_origins) != len(frozenset(self.exact_origins)):
            raise ValueError("parser origins must be unique")
        if any(normalize_provider_origin(origin) != origin for origin in self.exact_origins):
            raise ValueError("parser origins must be normalized HTTPS origins")
        if self.match_provider_only and self.openai_compatible_fallback:
            raise ValueError("a parser cannot be both provider-only and OpenAI-compatible fallback")
        return self


ParserCallable = Callable[[UUID, UUID, AwareDatetime, ProviderValidationResult], ParserRun]


@dataclass(frozen=True, slots=True)
class RegisteredParser:
    registration: ParserRegistration
    parse: ParserCallable


class ParserSelectionRequest(FrozenModel):
    provider_id: str = Field(min_length=1)
    api_base: str = Field(min_length=1)
    explicit_parser_id: str | None = None
    openai_compatible: bool = False


class ParserSelection(FrozenModel):
    parser_id: str | None
    source: ParserSelectionSource
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manual_selection(self) -> Self:
        if self.source == ParserSelectionSource.MANUAL and self.parser_id is not None:
            raise ValueError("manual fallback cannot select an automatic parser")
        if self.source != ParserSelectionSource.MANUAL and self.parser_id is None:
            raise ValueError("automatic selection requires a parser ID")
        return self


def normalize_provider_origin(api_base: str) -> str | None:
    candidate: Final = api_base.strip()
    try:
        parsed: Final = urlsplit(candidate)
        port: Final = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname is None:
        return None
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        return None
    host: Final = parsed.hostname.casefold()
    formatted_host: Final = f"[{host}]" if ":" in host else host
    authority: Final = formatted_host if port in (None, 443) else f"{formatted_host}:{port}"
    return urlunsplit(("https", authority, "", "", ""))


class ParserRegistry:
    def __init__(self, parsers: Iterable[RegisteredParser]) -> None:
        resolved: Final = tuple(parsers)
        by_id: Final = {parser.registration.parser_id: parser for parser in resolved}
        if len(by_id) != len(resolved):
            raise ValueError("parser IDs must be unique")
        exact_entries: Final = tuple(
            ((provider_id, origin), parser)
            for parser in resolved
            for provider_id in parser.registration.provider_ids
            for origin in parser.registration.exact_origins
        )
        exact_map: Final = {key: parser for key, parser in exact_entries}
        if len(exact_map) != len(exact_entries):
            raise ValueError("provider and origin pairs must select exactly one parser")
        provider_only_entries: Final = tuple(
            (provider_id, parser)
            for parser in resolved
            for provider_id in parser.registration.provider_ids
            if parser.registration.match_provider_only
        )
        provider_only_map: Final = {provider_id: parser for provider_id, parser in provider_only_entries}
        if len(provider_only_map) != len(provider_only_entries):
            raise ValueError("provider-only parsers must map to unique provider IDs")
        fallbacks: Final = tuple(parser for parser in resolved if parser.registration.openai_compatible_fallback)
        if len(fallbacks) > 1:
            raise ValueError("only one OpenAI-compatible fallback parser can be registered")
        self._by_id = MappingProxyType(by_id)
        self._exact = MappingProxyType(exact_map)
        self._provider_only = MappingProxyType(provider_only_map)
        self._openai_compatible = fallbacks[0] if fallbacks else None

    def select(self, request: ParserSelectionRequest) -> ParserSelection:
        if request.explicit_parser_id is not None:
            explicit: Final = self._by_id.get(request.explicit_parser_id)
            if explicit is None:
                return ParserSelection(
                    parser_id=None,
                    source=ParserSelectionSource.MANUAL,
                    reason="显式指定的解析器未注册，需要管理员修正或人工录入",
                )
            return ParserSelection(
                parser_id=explicit.registration.parser_id,
                source=ParserSelectionSource.EXPLICIT,
                reason="使用渠道显式指定的解析器",
            )
        origin: Final = normalize_provider_origin(request.api_base)
        exact: Final = None if origin is None else self._exact.get((request.provider_id, origin))
        if exact is not None:
            return ParserSelection(
                parser_id=exact.registration.parser_id,
                source=ParserSelectionSource.PROVIDER_ORIGIN,
                reason="Provider 与标准化 origin 精确匹配专用解析器",
            )
        provider_only: Final = self._provider_only.get(request.provider_id)
        if provider_only is not None:
            return ParserSelection(
                parser_id=provider_only.registration.parser_id,
                source=ParserSelectionSource.PROVIDER_ONLY,
                reason="Provider 专用解析器（自托管无固定 origin）",
            )
        if request.openai_compatible and self._openai_compatible is not None:
            return ParserSelection(
                parser_id=self._openai_compatible.registration.parser_id,
                source=ParserSelectionSource.OPENAI_COMPATIBLE,
                reason="使用已声明兼容的 OpenAI 通用解析器",
            )
        return ParserSelection(
            parser_id=None,
            source=ParserSelectionSource.MANUAL,
            reason="没有安全匹配的自动解析器，使用静态模板或人工录入",
        )

    def resolve(self, parser_id: str) -> ParserCallable | None:
        parser: Final = self._by_id.get(parser_id)
        return None if parser is None else parser.parse
