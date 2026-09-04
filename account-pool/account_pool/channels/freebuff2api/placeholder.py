"""本模块声明 FreeBuff2API 尚未实现的静态渠道占位定义。"""

from __future__ import annotations

from typing import Final

from account_pool.channels.base import ChannelDefinition
from account_pool.domain import ChannelKind


DEFINITION: Final = ChannelDefinition(
    kind=ChannelKind.FREEBUFF2API,
    suppliers=(),
    supplier_registry=None,
    unavailable_reason="FreeBuff2API is not implemented",
)
