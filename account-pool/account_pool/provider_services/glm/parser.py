"""将 GLM 官方模型发现结果转换为统一解析器输出。"""

from typing import Final
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.domain.provider_source import ProviderValidationResult
from account_pool.parsing.model_discovery import ModelDiscoveryParserSpec, parse_model_discovery_result
from account_pool.parsing.models import ParserRun
from account_pool.parsing.registry import ParserRegistration

PARSER_ID: Final = "glm-official"
PARSER_VERSION: Final = "1.0.0"
GLM_OFFICIAL_PARSER_SPEC: Final = ModelDiscoveryParserSpec(
    parser_id=PARSER_ID,
    parser_version=PARSER_VERSION,
    unresolved_reason="GLM 官方推理 API 未提供稳定的账户套餐与实际价格查询接口",
    warning="模型发现已完成，套餐与按量数据需要人工补充",
    next_action="在管理界面补充当前账户的套餐、额度与实际价格",
    evidence_summary="官方模型列表接口只证明模型可见性，不能证明账户权益或成交价格",
)
GLM_OFFICIAL_PARSER_REGISTRATION: Final = ParserRegistration(
    parser_id=PARSER_ID,
    provider_ids=("glm_official", "zai"),
    exact_origins=("https://open.bigmodel.cn",),
)


def parse_glm_official_result(
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
        spec=GLM_OFFICIAL_PARSER_SPEC,
    )
