"""本文件验证插件清单校验、幂等安装和启停状态转换。"""

from __future__ import annotations

from typing import Final

import pytest
from account_pool.plugins import PluginManifest, PluginRecord, PluginService, parse_plugin_registry


class MemoryPlugins:
    def __init__(self) -> None:
        self.records: dict[str, PluginRecord] = {}

    async def initialize(self) -> None:
        return None

    async def list(self) -> tuple[PluginRecord, ...]:
        return tuple(self.records.values())

    async def get(self, plugin_id: str) -> PluginRecord | None:
        return self.records.get(plugin_id)

    async def save(self, record: PluginRecord) -> PluginRecord:
        self.records[record.manifest.plugin_id] = record
        return record

    async def delete(self, plugin_id: str) -> None:
        self.records.pop(plugin_id, None)


def manifest(runtime: str = "sidecar") -> PluginManifest:
    return PluginManifest(
        plugin_id="quota-viewer",
        display_name="Quota viewer",
        version="1.0.0",
        runtime=runtime,
        entrypoint="/opt/plugins/quota-viewer",
        sha256="a" * 64,
    )


@pytest.mark.asyncio
async def test_plugin_install_toggle_and_uninstall_are_idempotent() -> None:
    repository: Final = MemoryPlugins()
    service: Final = PluginService(repository, (manifest(),))

    installed: Final = await service.install(manifest())
    assert installed.state == "installed"
    enabled: Final = await service.set_enabled("quota-viewer", True)
    assert enabled is not None and enabled.state == "enabled"
    disabled: Final = await service.set_enabled("quota-viewer", False)
    assert disabled is not None and disabled.state == "disabled"
    assert await service.uninstall("quota-viewer") is True
    assert await service.uninstall("quota-viewer") is False


def test_plugin_registry_rejects_invalid_hash_and_accepts_manifest() -> None:
    parsed: Final = parse_plugin_registry("[{\"plugin_id\": \"quota-viewer\", \"display_name\": \"Quota viewer\", \"version\": \"1\", \"entrypoint\": \"sidecar\", \"sha256\": \"" + "a" * 64 + "\"}]")
    assert parsed[0].plugin_id == "quota-viewer"
    with pytest.raises(ValueError):
        PluginManifest(
            plugin_id="quota-viewer",
            display_name="Quota viewer",
            version="1",
            entrypoint="sidecar",
            sha256="bad",
        )


@pytest.mark.asyncio
async def test_unknown_plugin_cannot_be_enabled() -> None:
    repository: Final = MemoryPlugins()
    service: Final = PluginService(repository)
    assert await service.set_enabled("missing", True) is None
