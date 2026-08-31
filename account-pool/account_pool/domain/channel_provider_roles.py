"""定义渠道配置中转发、模型发现和价格解析三类独立角色。"""

from __future__ import annotations

from pydantic import Field

from account_pool.models import FrozenModel


class ChannelProviderRoles(FrozenModel):
    """渠道配置的语义视图，不改变现有 API 或数据库字段。"""

    forwarding_provider: str = Field(min_length=1)
    model_discovery_provider_id: str | None = Field(default=None, min_length=1)
    pricing_parser_provider_id: str | None = Field(default=None, min_length=1)
