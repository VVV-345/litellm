"""组装当前启用的通用解析器注册表。"""

from account_pool.parsing.registry import ParserRegistry, RegisteredParser
from account_pool.provider_services.generic.parser import (
    GENERIC_PARSER_REGISTRATION,
    parse_generic_result,
)


def build_parser_registry() -> ParserRegistry:
    return ParserRegistry(
        (
            RegisteredParser(
                registration=GENERIC_PARSER_REGISTRATION,
                parse=parse_generic_result,
            ),
        )
    )
