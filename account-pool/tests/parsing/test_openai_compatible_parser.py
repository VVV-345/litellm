from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from account_pool.domain.provider_source import (
    ModelOffer,
    ProviderValidationFailureCode,
    ProviderValidationResult,
)
from account_pool.parsing.models import ParserFailureCategory, ParserRunStatus
from account_pool.provider_services.openai_compatible.manifest import OPENAI_COMPATIBLE_MANIFEST
from account_pool.provider_services.openai_compatible.parser import parse_openai_compatible_result


def _validation_result(
    ok: bool,
    message: str,
    models: tuple[ModelOffer, ...] = (),
    failure_code: ProviderValidationFailureCode | None = None,
) -> ProviderValidationResult:
    return ProviderValidationResult(
        ok=ok,
        provider_id="openai_compatible",
        normalized_api_base="https://gateway.example.com/v1",
        group="premium",
        key_fingerprint="fingerprint",
        message=message,
        failure_code=failure_code,
        capabilities=OPENAI_COMPATIBLE_MANIFEST.capabilities,
        models=models,
    )


def test_successful_model_discovery_becomes_partial_billing_result() -> None:
    parsed_at: Final = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
    run: Final = parse_openai_compatible_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=parsed_at,
        validation=_validation_result(
            ok=True,
            message="discovered",
            models=(ModelOffer(model="model-b"), ModelOffer(model="model-a")),
        ),
    )

    assert run.status == ParserRunStatus.PARTIAL
    assert run.result.subscription is None
    assert run.result.metered is None
    assert run.result.billing_routes == ()
    assert tuple(field.path for field in run.result.unresolved_fields) == ("subscription", "metered")
    assert run.discovered_models == ("model-a", "model-b")
    assert len(run.issues) == 1
    assert run.issues[0].field_paths == ("subscription", "metered")
    assert "gateway.example.com" not in run.model_dump_json()
    assert "fingerprint" not in run.model_dump_json()


def test_authentication_failure_is_structured_and_secret_free() -> None:
    run: Final = parse_openai_compatible_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 19, 8, 0, tzinfo=UTC),
        validation=_validation_result(
            ok=False,
            message="文案不会参与分类",
            failure_code=ProviderValidationFailureCode.AUTHENTICATION,
        ),
    )

    assert run.status == ParserRunStatus.AUTHENTICATION_FAILED
    assert run.result == run.result.__class__()
    assert run.issues[0].category == ParserFailureCategory.AUTHENTICATION
    assert run.issues[0].retryable is False
    assert "API Key" not in run.model_dump_json()


def test_transport_and_invalid_response_failures_are_classified() -> None:
    timestamp: Final = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
    transport: Final = parse_openai_compatible_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=timestamp,
        validation=_validation_result(
            ok=False,
            message="相同文案",
            failure_code=ProviderValidationFailureCode.TRANSPORT,
        ),
    )
    invalid: Final = parse_openai_compatible_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=timestamp,
        validation=_validation_result(
            ok=False,
            message="相同文案",
            failure_code=ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        ),
    )

    assert transport.status == ParserRunStatus.TRANSPORT_FAILED
    assert transport.issues[0].retryable is True
    assert invalid.status == ParserRunStatus.INVALID_RESPONSE
    assert invalid.issues[0].category == ParserFailureCategory.INVALID_RESPONSE


def test_empty_success_is_invalid_and_upstream_message_is_not_copied() -> None:
    run: Final = parse_openai_compatible_result(
        channel_id=uuid4(),
        parser_run_id=uuid4(),
        parsed_at=datetime(2026, 8, 19, 8, 0, tzinfo=UTC),
        validation=_validation_result(ok=True, message="private upstream detail"),
    )

    assert run.status == ParserRunStatus.INVALID_RESPONSE
    assert "private upstream detail" not in run.model_dump_json()
