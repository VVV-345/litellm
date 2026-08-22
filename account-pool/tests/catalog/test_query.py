"""验证渠道目录公开查询只返回 Dashboard 所需的脱敏聚合字段。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.lifecycle import CatalogApplyResult, CatalogLifecycleCommand, CatalogPendingDeleteResult
from account_pool.catalog.models import CatalogImport, CatalogSnapshot, ImportResult
from account_pool.catalog.query import ChannelCatalogQueryService
from tests.catalog.test_importer import legacy_config


class FakeCatalogRepository:
    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self._snapshot: Final = snapshot

    async def load_snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    async def import_once(self, command: CatalogImport) -> ImportResult:
        raise AssertionError(f"query service must not import {len(command.channels)} channels")

    async def apply_lifecycle(self, command: CatalogLifecycleCommand) -> CatalogApplyResult:
        raise AssertionError(f"query service must not apply lifecycle action {command.action}")

    async def mark_pending_delete(self, operation_id: UUID, channel_id: UUID) -> CatalogPendingDeleteResult:
        raise AssertionError(f"query service must not mark {channel_id} pending for {operation_id}")


async def test_channel_list_aggregates_enabled_models_without_internal_credentials() -> None:
    imported_at: Final = datetime(2026, 8, 19, 5, 0, tzinfo=UTC)
    imported: Final = catalog_import_from_pool_config(legacy_config(), imported_at)
    sensitive_channel: Final = imported.channels[0].model_copy(
        update={
            "credential_ref": "provider-secret-reference",
            "key_mask": "sk-***main",
            "key_fingerprint": "secret-fingerprint",
        }
    )
    service: Final = ChannelCatalogQueryService(
        FakeCatalogRepository(
            CatalogSnapshot(
                channels=(sensitive_channel, *imported.channels[1:]),
                bindings=imported.bindings,
                policies=imported.policies,
            )
        )
    )

    result: Final = await service.list_channels()
    rendered: Final = result.model_dump_json().casefold()

    assert tuple(channel.display_name for channel in result.channels) == ("Zulu", "Alpha")
    assert result.channels[0].binding_count == 2
    assert result.channels[0].enabled_binding_count == 1
    assert result.channels[0].models == ("z-model",)
    assert result.channels[0].key_mask == "sk-***main"
    assert "credential_ref" not in rendered
    assert "key_fingerprint" not in rendered
    assert "provider-secret-reference" not in rendered
    assert "secret-fingerprint" not in rendered


async def test_channel_lookup_returns_the_saved_url_without_credential_reference() -> None:
    imported_at: Final = datetime(2026, 8, 19, 5, 0, tzinfo=UTC)
    imported: Final = catalog_import_from_pool_config(legacy_config(), imported_at)
    channel: Final = imported.channels[0].model_copy(update={"credential_ref": "provider-secret-reference"})
    service: Final = ChannelCatalogQueryService(
        FakeCatalogRepository(CatalogSnapshot(channels=(channel,), bindings=imported.bindings))
    )

    found: Final = await service.get_channel(channel.channel_id)

    assert found is not None
    assert found.channel_id == channel.channel_id
    assert found.base_url_display == channel.base_url_display
    assert "credential_ref" not in found.model_dump_json()
