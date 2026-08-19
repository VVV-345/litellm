"""验证解析器注册表的匹配优先级和无凭证选择过程。"""

from typing import Final

import pytest
from account_pool.parsing.registry import (
    ParserSelectionRequest,
    ParserSelectionSource,
    normalize_provider_origin,
)
from account_pool.provider_services.glm.parser import parse_glm_official_result
from account_pool.provider_services.parser_registry import build_parser_registry
from pydantic import ValidationError


def test_explicit_parser_has_highest_priority() -> None:
    selection: Final = build_parser_registry().select(
        ParserSelectionRequest(
            provider_id="zai",
            api_base="https://open.bigmodel.cn/api/paas/v4",
            explicit_parser_id="openai-compatible",
            openai_compatible=True,
        )
    )

    assert selection.parser_id == "openai-compatible"
    assert selection.source == ParserSelectionSource.EXPLICIT


def test_glm_provider_and_origin_select_specialized_parser() -> None:
    registry: Final = build_parser_registry()
    selection: Final = registry.select(
        ParserSelectionRequest(
            provider_id="zai",
            api_base="https://OPEN.BIGMODEL.CN:443/api/paas/v4/",
            openai_compatible=True,
        )
    )

    assert selection.parser_id == "glm-official"
    assert selection.source == ParserSelectionSource.PROVIDER_ORIGIN
    assert registry.resolve("glm-official") is parse_glm_official_result


def test_specialized_parser_requires_both_provider_and_origin() -> None:
    selection: Final = build_parser_registry().select(
        ParserSelectionRequest(
            provider_id="unrelated",
            api_base="https://open.bigmodel.cn/api/paas/v4",
        )
    )

    assert selection.parser_id is None
    assert selection.source == ParserSelectionSource.MANUAL


def test_openai_compatible_fallback_requires_explicit_compatibility() -> None:
    registry: Final = build_parser_registry()
    compatible: Final = registry.select(
        ParserSelectionRequest(
            provider_id="custom",
            api_base="https://gateway.example.com/v1",
            openai_compatible=True,
        )
    )
    manual: Final = registry.select(
        ParserSelectionRequest(
            provider_id="custom",
            api_base="https://gateway.example.com/v1",
        )
    )

    assert compatible.parser_id == "openai-compatible"
    assert compatible.source == ParserSelectionSource.OPENAI_COMPATIBLE
    assert manual.parser_id is None
    assert manual.source == ParserSelectionSource.MANUAL


def test_unknown_explicit_parser_does_not_silently_fallback() -> None:
    selection: Final = build_parser_registry().select(
        ParserSelectionRequest(
            provider_id="custom",
            api_base="https://gateway.example.com/v1",
            explicit_parser_id="missing-parser",
            openai_compatible=True,
        )
    )

    assert selection.parser_id is None
    assert selection.source == ParserSelectionSource.MANUAL


@pytest.mark.parametrize(
    "api_base",
    (
        "http://open.bigmodel.cn/api/paas/v4",
        "https://user@open.bigmodel.cn/api/paas/v4",
        "https://open.bigmodel.cn/api/paas/v4?target=other",
    ),
)
def test_unsafe_origin_does_not_match_specialized_parser(api_base: str) -> None:
    selection: Final = build_parser_registry().select(ParserSelectionRequest(provider_id="zai", api_base=api_base))

    assert selection.source == ParserSelectionSource.MANUAL
    assert normalize_provider_origin(api_base) is None


def test_selection_request_rejects_credentials() -> None:
    with pytest.raises(ValidationError):
        ParserSelectionRequest.model_validate(
            {
                "provider_id": "custom",
                "api_base": "https://gateway.example.com/v1",
                "openai_compatible": True,
                "api_key": "must-not-be-accepted",
            }
        )
