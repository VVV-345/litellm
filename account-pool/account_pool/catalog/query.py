"""将渠道目录快照聚合为不含内部凭证引用的公开查询视图。"""

from typing import Final, Protocol
from uuid import UUID

from account_pool.catalog.models import CatalogSnapshot, ChannelList, ChannelRecord, ChannelSummary
from account_pool.catalog.repository import CatalogRepository


class ChannelCatalogReader(Protocol):
    async def list_channels(self) -> ChannelList: ...

    async def get_channel(self, channel_id: UUID) -> ChannelSummary | None: ...


class ChannelCatalogQueryService:
    def __init__(self, repository: CatalogRepository) -> None:
        self._repository: Final = repository

    async def list_channels(self) -> ChannelList:
        snapshot: Final = await self._repository.load_snapshot()
        return ChannelList(channels=tuple(_channel_summary(channel, snapshot) for channel in snapshot.channels))

    async def get_channel(self, channel_id: UUID) -> ChannelSummary | None:
        snapshot: Final = await self._repository.load_snapshot()
        channel: Final = next((item for item in snapshot.channels if item.channel_id == channel_id), None)
        return None if channel is None else _channel_summary(channel, snapshot)


def _channel_summary(channel: ChannelRecord, snapshot: CatalogSnapshot) -> ChannelSummary:
    bindings: Final = tuple(binding for binding in snapshot.bindings if binding.channel_id == channel.channel_id)
    models: Final = tuple(sorted(frozenset(binding.public_model for binding in bindings if binding.enabled)))
    return ChannelSummary(
        channel_id=channel.channel_id,
        display_name=channel.display_name,
        provider=channel.provider,
        model_discovery_provider_id=channel.model_discovery_provider_id,
        parser_provider_id=channel.parser_provider_id,
        group=channel.group,
        base_url_display=channel.base_url_display,
        administrative_state=channel.administrative_state,
        max_concurrency=channel.max_concurrency,
        priority=channel.priority,
        weight=channel.weight,
        key_mask=channel.key_mask,
        binding_count=len(bindings),
        enabled_binding_count=sum(1 for binding in bindings if binding.enabled),
        models=models,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )
