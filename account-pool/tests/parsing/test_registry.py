"""验证通用解析器的唯一选择路径和人工回退。"""

from typing import Final

import pytest
from account_pool.parsing.registry import ParserSelectionRequest, ParserSelectionSource
from account_pool.provider_services.generic.parser import parse_generic_result
from account_pool.provider_services.parser_registry import build_parser_registry
from pydantic import ValidationError


def test_explicit_generic_parser_has_highest_priority() -> None:
    selection: Final = build_parser_registry().select(
        ParserSelectionRequest(
            provider_id="other",
            api_base="https://gateway.example.com/v1",
            explicit_parser_id="generic",
        )
    )

    assert selection.parser_id == "generic"
    assert selection.source == ParserSelectionSource.EXPLICIT


def test_generic_provider_selects_the_generic_parser() -> None:
    registry: Final = build_parser_registry()
    selection: Final = registry.select(
        ParserSelectionRequest(provider_id="generic", api_base="https://gateway.example.com/v1")
    )

    assert selection.parser_id == "generic"
    assert selection.source == ParserSelectionSource.PROVIDER_ONLY
    assert registry.resolve("generic") is parse_generic_result


def test_other_provider_falls_back_to_manual_even_when_marked_compatible() -> None:
    selection: Final = build_parser_registry().select(
        ParserSelectionRequest(
            provider_id="other",
            api_base="https://gateway.example.com/v1",
            openai_compatible=True,
        )
    )

    assert selection.parser_id is None
    assert selection.source == ParserSelectionSource.MANUAL


def test_unknown_explicit_parser_does_not_silently_fallback() -> None:
    selection: Final = build_parser_registry().select(
        ParserSelectionRequest(
            provider_id="generic",
            api_base="https://gateway.example.com/v1",
            explicit_parser_id="missing-parser",
        )
    )

    assert selection.parser_id is None
    assert selection.source == ParserSelectionSource.MANUAL


def test_selection_request_rejects_credentials() -> None:
    with pytest.raises(ValidationError):
        ParserSelectionRequest.model_validate(
            {
                "provider_id": "generic",
                "api_base": "https://gateway.example.com/v1",
                "api_key": "must-not-be-accepted",
            }
        )
