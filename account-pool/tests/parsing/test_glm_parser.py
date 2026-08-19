from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from account_pool.domain.provider_source import (
    ModelOffer,
    ProviderValidationFailureCode,
    ProviderValidationResult,
)
from account_pool.parsing.models import ParserFailureCategory, ParserRunStatus
from account_pool.provider_services.glm.manifest import GLM_OFFICIAL_MANIFEST
from account_pool.provider_services.glm.parser import parse_glm_official_result


def _validation_result(
    ok: bool,
    failure_code: ProviderValidationFailureCode | None = None,
    models: tuple[ModelOffer, ...] = (),
) -> ProviderValidationResult:
    return ProviderValidationResult(
        ok=ok,
        provider_id="glm_official",
        normalized_api_base="https://open.bigmodel.cn/api/paas/v4",
        group=None,
        key_fingerprint="fingerprint",
        message="不会复制到解析结果的渠道校验文案",
        failure_code=failure_code,
        capabilities=GLM_OFFICIAL_MANIFEST.capabilities,
        models=models,
    )


def test_glm_model_discovery_uses_unified_partial_contract() -> None:
    run: Final = parse_glm_official_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 19, 8, 0, tzinfo=UTC),
        validation=_validation_result(
            ok=True,
            models=(ModelOffer(model="glm-5"), ModelOffer(model="glm-4.7")),
        ),
    )

    assert run.parser_id == "glm-official"
    assert run.status == ParserRunStatus.PARTIAL
    assert run.discovered_models == ("glm-4.7", "glm-5")
    assert run.result.subscription is None
    assert run.result.metered is None
    assert run.result.billing_routes == ()
    assert tuple(field.path for field in run.result.unresolved_fields) == ("subscription", "metered")
    serialized: Final = run.model_dump_json()
    assert "open.bigmodel.cn" not in serialized
    assert "fingerprint" not in serialized
    assert "渠道校验文案" not in serialized


def test_glm_validation_failure_uses_typed_code() -> None:
    run: Final = parse_glm_official_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 19, 8, 0, tzinfo=UTC),
        validation=_validation_result(
            ok=False,
            failure_code=ProviderValidationFailureCode.AUTHENTICATION,
        ),
    )

    assert run.status == ParserRunStatus.AUTHENTICATION_FAILED
    assert run.issues[0].category == ParserFailureCategory.AUTHENTICATION
    assert run.issues[0].retryable is False
