"""编排卡片插件操作，复用供应商校验、仓库和错误日志。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from account_pool.application.environments.contracts import LogEventOperation
from account_pool.application.plugin_validation import (
    _plugin_store_approves as _plugin_store_approves,
)
from account_pool.domain import (
    ChannelKind,
    EnvironmentRecord,
)
from account_pool.ports import (
    CLIProxyClient,
    EnvironmentRepository,
)
from account_pool.shared.result import Failure, FailureCode, Result, Success


@dataclass(frozen=True, slots=True)
class EnvironmentPlugins:
    _cli_proxy: CLIProxyClient
    _log_event: LogEventOperation
    _repository: EnvironmentRepository

    async def list_card_plugins(self, environment_id: UUID) -> Result[Mapping[str, object]]:
        return await self.plugin_call(environment_id, lambda record: self._cli_proxy.list_plugins(record))

    async def list_card_plugin_store(self, environment_id: UUID) -> Result[Mapping[str, object]]:
        return await self.plugin_call(environment_id, lambda record: self._cli_proxy.list_plugin_store(record))

    async def install_card_plugin(
        self, environment_id: UUID, plugin_id: str, version: str, source: str | None
    ) -> Result[Mapping[str, object]]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        if record.channel is not ChannelKind.CLIPROXYAPI:
            return Failure(FailureCode.INVALID, "plugins are supported by CLIProxyAPI cards only")
        try:
            store: Final = await self._cli_proxy.list_plugin_store(record)
            if not _plugin_store_approves(store, plugin_id, version, source):
                return Failure(
                    FailureCode.INVALID, "plugin id, version, or source is not approved by the card plugin store"
                )
            return Success(await self._cli_proxy.install_plugin(record, plugin_id, version, source))
        except Exception as error:
            await self._log_event(record, "configuration", error)
            return Failure(FailureCode.UPSTREAM, "plugin runtime operation failed")

    async def set_card_plugin_enabled(
        self, environment_id: UUID, plugin_id: str, enabled: bool
    ) -> Result[Mapping[str, object]]:
        return await self.plugin_call(
            environment_id, lambda record: self._cli_proxy.set_plugin_enabled(record, plugin_id, enabled)
        )

    async def uninstall_card_plugin(self, environment_id: UUID, plugin_id: str) -> Result[Mapping[str, object]]:
        return await self.plugin_call(
            environment_id, lambda record: self._cli_proxy.uninstall_plugin(record, plugin_id)
        )

    async def get_card_plugin_config(self, environment_id: UUID, plugin_id: str) -> Result[Mapping[str, object]]:
        return await self.plugin_call(
            environment_id, lambda record: self._cli_proxy.get_plugin_config(record, plugin_id)
        )

    async def put_card_plugin_config(
        self, environment_id: UUID, plugin_id: str, config: Mapping[str, object]
    ) -> Result[Mapping[str, object]]:
        return await self.plugin_call(
            environment_id, lambda record: self._cli_proxy.put_plugin_config(record, plugin_id, config)
        )

    async def plugin_call(
        self, environment_id: UUID, operation: Callable[[EnvironmentRecord], Awaitable[Mapping[str, object]]]
    ) -> Result[Mapping[str, object]]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        if record.channel is not ChannelKind.CLIPROXYAPI:
            return Failure(FailureCode.INVALID, "plugins are supported by CLIProxyAPI cards only")
        try:
            return Success(await operation(record))
        except Exception as error:
            await self._log_event(record, "configuration", error)
            return Failure(FailureCode.UPSTREAM, "plugin runtime operation failed")
