"""验证 PostgreSQL 行编解码在无数据库环境下仍能精确还原可空嵌套对象。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from account_pool.parsing.persistence import ParserExportStatus
from account_pool.parsing.postgres.codec import decode_record

_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_GROUP_ROW_ID: Final = UUID("30000000-0000-0000-0000-000000000003")
_PARSED_AT: Final = datetime(2026, 8, 19, 14, 0, tzinfo=UTC)


def _run_row() -> dict[str, object]:
    return {
        "parser_run_id": _RUN_ID,
        "channel_id": _CHANNEL_ID,
        "parser_id": "fixture-parser",
        "parser_version": "1.0.0",
        "parsed_at": _PARSED_AT,
        "status": "success",
        "content_hash": "a" * 64,
        "discovered_models": ["model-a"],
        "capabilities": ["model_discovery"],
        "unresolved_fields": [],
        "evidence": [],
        "warnings": [],
        "issues": [],
        "has_metered": True,
        "export_status": "pending",
        "export_attempt_count": 0,
        "export_last_attempt_at": None,
        "exported_at": None,
        "export_failure_code": None,
        "export_failure_retryable": None,
    }


def _group_row() -> dict[str, object]:
    return {
        "metered_group_row_id": _GROUP_ROW_ID,
        "parser_run_id": _RUN_ID,
        "group_order": 0,
        "group_id": "default",
        "group_name": None,
        "concurrency": None,
    }


def _price_row(has_normalized_prices: bool) -> dict[str, object]:
    return {
        "metered_price_id": UUID("40000000-0000-0000-0000-000000000004"),
        "metered_group_row_id": _GROUP_ROW_ID,
        "price_order": 0,
        "provider_model_id": "model-a",
        "litellm_model_name": None,
        "public_model_name": None,
        "currency": "USD",
        "unit": "million_tokens",
        "input_price": None,
        "output_price": None,
        "cache_read_price": None,
        "cache_write_price": None,
        "group_multiplier": "1",
        "price_calculation": "multiplier",
        "conversion_note": None,
        "effective_input_price": None,
        "effective_output_price": None,
        "effective_cache_read_price": None,
        "effective_cache_write_price": None,
        "normalized_input_price": None,
        "normalized_output_price": None,
        "normalized_cache_read_price": None,
        "normalized_cache_write_price": None,
        "has_normalized_prices": has_normalized_prices,
        "concurrency": None,
    }


@pytest.mark.parametrize("has_normalized_prices", [False, True])
def test_decoder_preserves_empty_normalized_price_object(has_normalized_prices: bool) -> None:
    record: Final = decode_record(
        raw_run=_run_row(),
        subscription_rows=(),
        quota_rows=(),
        group_rows=(_group_row(),),
        price_rows=(_price_row(has_normalized_prices),),
        route_rows=(),
    )

    assert record.export.status == ParserExportStatus.PENDING
    assert record.run.result.metered is not None
    price: Final = record.run.result.metered.groups[0].models[0]
    if has_normalized_prices:
        assert price.normalized_per_million_tokens is not None
        assert price.normalized_per_million_tokens.input_price is None
    else:
        assert price.normalized_per_million_tokens is None
