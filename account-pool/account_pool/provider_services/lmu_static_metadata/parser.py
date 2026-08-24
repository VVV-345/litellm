"""将 LMU 公开静态模型发现转换为安全的人工补充解析结果。"""

from typing import Final
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.domain.provider_source import ProviderValidationResult
from account_pool.parsing.model_discovery import ModelDiscoveryParserSpec, parse_model_discovery_result
from account_pool.parsing.models import ParserRun
from account_pool.parsing.registry import ParserRegistration

PARSER_ID: Final = "lmu-static-metadata"
PARSER_VERSION: Final = "1.0.0"
LMU_STATIC_METADATA_PARSER_REGISTRATION: Final = ParserRegistration(
    parser_id=PARSER_ID,
    provider_ids=("lmu_static_metadata",),
    match_provider_only=True,
)
_SPEC: Final = ModelDiscoveryParserSpec(
    parser_id=PARSER_ID,
    parser_version=PARSER_VERSION,
    unresolved_reason="公开静态页面不能证明当前账户的套餐、可见模型或实际价格",
    warning="公开静态模型信息仅供人工核对，需补充账户实际价格",
    next_action="核对当前账户可见模型后，在管理界面补充分组倍率与价格",
    evidence_summary="公开静态页面的结构化模型清单不代表账户权限或实际计费",
)


def parse_lmu_static_metadata_result(
    channel_id: UUID,
    parser_run_id: UUID,
    parsed_at: AwareDatetime,
    validation: ProviderValidationResult,
) -> ParserRun:
    return parse_model_discovery_result(
        channel_id=channel_id,
        parser_run_id=parser_run_id,
        parsed_at=parsed_at,
        validation=validation,
        spec=_SPEC,
    )
