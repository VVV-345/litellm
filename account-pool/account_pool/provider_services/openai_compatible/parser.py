"""将 OpenAI 兼容模型发现结果转换为统一解析器输出。"""

from typing import Final
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.domain.provider_source import ProviderCapability, ProviderValidationResult
from account_pool.parsing.models import (
    ParsedChannelData,
    ParserFailureCategory,
    ParserIssue,
    ParserRun,
    ParserRunStatus,
    UnresolvedField,
)

PARSER_ID: Final = "openai-compatible"
PARSER_VERSION: Final = "1.0.0"
_BILLING_FIELDS: Final = ("subscription", "metered")


def parse_openai_compatible_result(
    channel_id: UUID,
    parser_run_id: UUID,
    parsed_at: AwareDatetime,
    validation: ProviderValidationResult,
) -> ParserRun:
    if validation.ok and validation.models:
        return _partial_model_discovery(channel_id, parser_run_id, parsed_at, validation)
    classified_message: Final = validation.message if not validation.ok else "模型列表响应格式无法识别"
    status, category, retryable, next_action, evidence = _classify_failure(classified_message)
    return ParserRun(
        parser_run_id=parser_run_id,
        channel_id=channel_id,
        parser_id=PARSER_ID,
        parser_version=PARSER_VERSION,
        parsed_at=parsed_at,
        status=status,
        issues=(
            _issue(
                parsed_at=parsed_at,
                stage="model_discovery",
                category=category,
                field_paths=(),
                retryable=retryable,
                next_action=next_action,
                evidence_summary=evidence,
            ),
        ),
    )


def _partial_model_discovery(
    channel_id: UUID,
    parser_run_id: UUID,
    parsed_at: AwareDatetime,
    validation: ProviderValidationResult,
) -> ParserRun:
    discovered_models: Final = tuple(sorted(frozenset(offer.model for offer in validation.models)))
    result: Final = ParsedChannelData(
        capabilities=(ProviderCapability.MODEL_DISCOVERY,),
        unresolved_fields=tuple(
            UnresolvedField(
                path=path,
                reason="OpenAI 兼容协议未定义统一的账户计费接口",
                retryable=False,
            )
            for path in _BILLING_FIELDS
        ),
        warnings=("模型发现已完成，套餐与按量数据需要专用解析器或人工补充",),
    )
    return ParserRun(
        parser_run_id=parser_run_id,
        channel_id=channel_id,
        parser_id=PARSER_ID,
        parser_version=PARSER_VERSION,
        parsed_at=parsed_at,
        status=ParserRunStatus.PARTIAL,
        result=result,
        discovered_models=discovered_models,
        issues=(
            _issue(
                parsed_at=parsed_at,
                stage="billing_discovery",
                category=ParserFailureCategory.UNSUPPORTED,
                field_paths=_BILLING_FIELDS,
                retryable=False,
                next_action="选择厂商专用解析器，或在管理界面补充套餐与价格数据",
                evidence_summary="上游仅提供标准模型列表，未提供可验证的统一计费信息",
            ),
        ),
    )


def _classify_failure(
    message: str,
) -> tuple[ParserRunStatus, ParserFailureCategory, bool, str, str]:
    if "API Key" in message or "权限" in message:
        return (
            ParserRunStatus.AUTHENTICATION_FAILED,
            ParserFailureCategory.AUTHENTICATION,
            False,
            "检查渠道凭证及模型列表读取权限",
            "上游拒绝了模型列表认证",
        )
    if "无法连接" in message or "超时" in message:
        return (
            ParserRunStatus.TRANSPORT_FAILED,
            ParserFailureCategory.TRANSPORT,
            True,
            "确认上游服务可达后重试",
            "模型列表请求未能到达上游或未及时返回",
        )
    if "格式无法识别" in message:
        return (
            ParserRunStatus.INVALID_RESPONSE,
            ParserFailureCategory.INVALID_RESPONSE,
            False,
            "确认该接口兼容 OpenAI GET /models 响应格式",
            "模型列表响应未通过结构校验",
        )
    return (
        ParserRunStatus.MANUAL_REQUIRED,
        ParserFailureCategory.MANUAL_REQUIRED,
        False,
        "查看渠道配置并选择专用解析器或人工录入",
        "通用解析器无法安全分类该失败",
    )


def _issue(
    parsed_at: AwareDatetime,
    stage: str,
    category: ParserFailureCategory,
    field_paths: tuple[str, ...],
    retryable: bool,
    next_action: str,
    evidence_summary: str,
) -> ParserIssue:
    return ParserIssue(
        parser_id=PARSER_ID,
        parser_version=PARSER_VERSION,
        stage=stage,
        category=category,
        field_paths=field_paths,
        retryable=retryable,
        next_action=next_action,
        evidence_summary=evidence_summary,
        first_seen_at=parsed_at,
        latest_seen_at=parsed_at,
    )
