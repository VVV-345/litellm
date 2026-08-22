"""把类型化渠道校验结果转换为统一的模型发现解析运行。"""

from typing import Final, assert_never
from uuid import UUID

from pydantic import AwareDatetime, Field

from account_pool.domain.provider_source import (
    ProviderCapability,
    ProviderValidationFailureCode,
    ProviderValidationResult,
)
from account_pool.models import FrozenModel
from account_pool.parsing.models import (
    MeteredData,
    ParsedChannelData,
    ParserFailureCategory,
    ParserIssue,
    ParserRun,
    ParserRunStatus,
    UnresolvedField,
)

_BILLING_FIELDS: Final = ("subscription", "metered")


class ModelDiscoveryParserSpec(FrozenModel):
    parser_id: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    unresolved_reason: str = Field(min_length=1)
    warning: str = Field(min_length=1)
    next_action: str = Field(min_length=1)
    evidence_summary: str = Field(min_length=1)


def parse_model_discovery_result(
    channel_id: UUID,
    parser_run_id: UUID,
    parsed_at: AwareDatetime,
    validation: ProviderValidationResult,
    spec: ModelDiscoveryParserSpec,
    *,
    metered: MeteredData | None = None,
) -> ParserRun:
    if validation.ok and validation.models:
        return _partial_result(
            channel_id=channel_id,
            parser_run_id=parser_run_id,
            parsed_at=parsed_at,
            validation=validation,
            spec=spec,
            metered=metered,
        )
    failure_code: Final = validation.failure_code or ProviderValidationFailureCode.UPSTREAM_RESPONSE
    status, category, retryable, next_action, evidence = _failure_details(failure_code)
    return ParserRun(
        parser_run_id=parser_run_id,
        channel_id=channel_id,
        parser_id=spec.parser_id,
        parser_version=spec.parser_version,
        parsed_at=parsed_at,
        status=status,
        issues=(
            _issue(
                parsed_at=parsed_at,
                spec=spec,
                stage="model_discovery",
                category=category,
                field_paths=(),
                retryable=retryable,
                next_action=next_action,
                evidence_summary=evidence,
            ),
        ),
    )


def _partial_result(
    channel_id: UUID,
    parser_run_id: UUID,
    parsed_at: AwareDatetime,
    validation: ProviderValidationResult,
    spec: ModelDiscoveryParserSpec,
    metered: MeteredData | None,
) -> ParserRun:
    discovered_models: Final = tuple(sorted(frozenset(offer.model for offer in validation.models)))
    unresolved_paths: Final = tuple(path for path in _BILLING_FIELDS if path != "metered" or metered is None)
    capabilities: Final = (
        (ProviderCapability.MODEL_DISCOVERY, ProviderCapability.MODEL_PRICING)
        if metered is not None
        else (ProviderCapability.MODEL_DISCOVERY,)
    )
    result: Final = ParsedChannelData(
        capabilities=capabilities,
        metered=metered,
        unresolved_fields=tuple(
            UnresolvedField(path=path, reason=spec.unresolved_reason, retryable=False) for path in unresolved_paths
        ),
        warnings=(spec.warning,),
    )
    return ParserRun(
        parser_run_id=parser_run_id,
        channel_id=channel_id,
        parser_id=spec.parser_id,
        parser_version=spec.parser_version,
        parsed_at=parsed_at,
        status=ParserRunStatus.PARTIAL,
        result=result,
        discovered_models=discovered_models,
        issues=(
            _issue(
                parsed_at=parsed_at,
                spec=spec,
                stage="billing_discovery",
                category=ParserFailureCategory.UNSUPPORTED,
                field_paths=unresolved_paths,
                retryable=False,
                next_action=spec.next_action,
                evidence_summary=spec.evidence_summary,
            ),
        ),
    )


def _failure_details(
    code: ProviderValidationFailureCode,
) -> tuple[ParserRunStatus, ParserFailureCategory, bool, str, str]:
    match code:
        case ProviderValidationFailureCode.AUTHENTICATION:
            return (
                ParserRunStatus.AUTHENTICATION_FAILED,
                ParserFailureCategory.AUTHENTICATION,
                False,
                "检查渠道凭证及模型列表读取权限",
                "上游拒绝了模型列表认证",
            )
        case ProviderValidationFailureCode.TRANSPORT:
            return (
                ParserRunStatus.TRANSPORT_FAILED,
                ParserFailureCategory.TRANSPORT,
                True,
                "确认上游服务可达后重试",
                "模型列表请求未能到达上游或未及时返回",
            )
        case ProviderValidationFailureCode.UPSTREAM_RESPONSE:
            return (
                ParserRunStatus.INVALID_RESPONSE,
                ParserFailureCategory.INVALID_RESPONSE,
                False,
                "确认上游模型列表接口及响应格式",
                "模型列表响应未通过状态或结构校验",
            )
        case ProviderValidationFailureCode.INVALID_CONFIGURATION:
            return (
                ParserRunStatus.MANUAL_REQUIRED,
                ParserFailureCategory.MANUAL_REQUIRED,
                False,
                "检查渠道 URL 与解析器配置",
                "渠道配置未通过解析器安全校验",
            )
        case ProviderValidationFailureCode.NO_MODELS:
            return (
                ParserRunStatus.MANUAL_REQUIRED,
                ParserFailureCategory.INCOMPLETE,
                False,
                "确认该凭证至少可以访问一个模型，或人工补充模型",
                "上游没有返回当前凭证可见模型",
            )
        case ProviderValidationFailureCode.UNSUPPORTED_PROVIDER:
            return (
                ParserRunStatus.UNSUPPORTED,
                ParserFailureCategory.UNSUPPORTED,
                False,
                "选择已注册解析器或使用人工模板",
                "渠道没有已注册的自动解析器",
            )
    assert_never(code)


def _issue(
    parsed_at: AwareDatetime,
    spec: ModelDiscoveryParserSpec,
    stage: str,
    category: ParserFailureCategory,
    field_paths: tuple[str, ...],
    retryable: bool,
    next_action: str,
    evidence_summary: str,
) -> ParserIssue:
    return ParserIssue(
        parser_id=spec.parser_id,
        parser_version=spec.parser_version,
        stage=stage,
        category=category,
        field_paths=field_paths,
        retryable=retryable,
        next_action=next_action,
        evidence_summary=evidence_summary,
        first_seen_at=parsed_at,
        latest_seen_at=parsed_at,
    )
