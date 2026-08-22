"""组装当前可用的厂商解析器注册表。"""

from account_pool.parsing.registry import ParserRegistry, RegisteredParser
from account_pool.provider_services.glm.parser import (
    GLM_OFFICIAL_PARSER_REGISTRATION,
    parse_glm_official_result,
)
from account_pool.provider_services.new_api.parser import (
    NEW_API_PARSER_REGISTRATION,
    parse_new_api_result,
)
from account_pool.provider_services.openai_compatible.parser import (
    OPENAI_COMPATIBLE_PARSER_REGISTRATION,
    parse_openai_compatible_result,
)


def build_parser_registry() -> ParserRegistry:
    return ParserRegistry(
        (
            RegisteredParser(
                registration=GLM_OFFICIAL_PARSER_REGISTRATION,
                parse=parse_glm_official_result,
            ),
            RegisteredParser(
                registration=OPENAI_COMPATIBLE_PARSER_REGISTRATION,
                parse=parse_openai_compatible_result,
            ),
            RegisteredParser(
                registration=NEW_API_PARSER_REGISTRATION,
                parse=parse_new_api_result,
            ),
        )
    )
