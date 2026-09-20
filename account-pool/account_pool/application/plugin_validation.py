"""验证卡片插件来源及版本，不执行插件安装操作。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError

_PLUGIN_VERSION_PATTERN: Final = re.compile(r"^[vV]?[0-9][0-9A-Za-z.+-]{0,63}$")


class _PluginStoreEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    version: str
    source_id: str


class _PluginStoreResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    plugins: tuple[_PluginStoreEntry, ...]


def _plugin_store_approves(store: Mapping[str, object], plugin_id: str, version: str, source: str | None) -> bool:
    if _PLUGIN_VERSION_PATTERN.fullmatch(version) is None:
        return False
    try:
        response: Final = _PluginStoreResponse.model_validate(store)
    except ValidationError:
        return False
    candidates: Final = tuple(entry for entry in response.plugins if entry.id == plugin_id)
    if source is None:
        return len(candidates) == 1
    matches: Final = tuple(entry for entry in candidates if entry.source_id == source)
    return len(matches) == 1
