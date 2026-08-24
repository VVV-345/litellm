"""验证 LMU 公开静态模型发现的解析器输出。"""

from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from account_pool.domain.provider_source import ModelOffer, ProviderValidationResult
from account_pool.parsing.models import ParserRunStatus
from account_pool.provider_services.lmu_static_metadata.manifest import LMU_STATIC_METADATA_MANIFEST
from account_pool.provider_services.lmu_static_metadata.parser import parse_lmu_static_metadata_result


def test_lmu_static_metadata_parser_keeps_billing_unresolved() -> None:
    run: Final = parse_lmu_static_metadata_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        validation=ProviderValidationResult(
            ok=True,
            provider_id="lmu_static_metadata",
            normalized_api_base="https://api.lmuai.com",
            group=None,
            key_fingerprint=None,
            message="must not reach parser output",
            capabilities=LMU_STATIC_METADATA_MANIFEST.capabilities,
            models=(ModelOffer(model="model-a"),),
        ),
    )

    assert run.status == ParserRunStatus.PARTIAL
    assert run.discovered_models == ("model-a",)
    assert run.result.metered is None
    assert tuple(field.path for field in run.result.unresolved_fields) == ("subscription", "metered")
    assert "价格" in run.issues[0].next_action
    assert "must not reach parser output" not in run.model_dump_json()
