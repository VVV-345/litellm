"""集中定义 Cockpit manifest 的只读字段视图；写入仍基于原始快照保留未知字段。"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class CockpitAccount(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    authId: str = ""
    authKind: str = ""
    accessTokenOnly: bool = False
    upstreamApiKey: str = Field(default="", repr=False)


class CockpitTargetAccount(CockpitAccount):
    model_config = ConfigDict(frozen=True, strict=True)

    id: str = Field(min_length=1)


class CockpitPolicyAccount(CockpitAccount):
    model_config = ConfigDict(strict=True)

    id: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class CockpitAlias(BaseModel):
    model_config = ConfigDict(strict=True)

    sourceModel: str = ""
    alias: str = ""


class CockpitAccountModelRule(BaseModel):
    model_config = ConfigDict(strict=True)

    accountId: str = ""
    excludedModels: list[str] | None = None


class CockpitPolicyManifest(BaseModel):
    routingStrategy: str = "auto"
    accounts: tuple[CockpitPolicyAccount, ...] | None = None
    customRoutingRules: tuple[dict[str, JsonValue], ...] | None = None


class CockpitAccountManifest(BaseModel):
    accounts: tuple[CockpitTargetAccount, ...] | None = None


class CockpitScopeManifest(BaseModel):
    model_config = ConfigDict(strict=True)

    accounts: list[CockpitAccount] | None = None
    apiKeys: list[dict[str, JsonValue]] | None = None
    modelIds: list[str] | None = None
    modelAliases: list[CockpitAlias] | None = None
    excludedModels: list[str] | None = None
    accountModelRules: list[CockpitAccountModelRule] | None = None


class CockpitKeyCount(BaseModel):
    model_config = ConfigDict(strict=True)

    enabled: bool = False


class CockpitCountManifest(BaseModel):
    accounts: tuple[dict[str, JsonValue], ...] | None = None
    apiKeys: tuple[CockpitKeyCount, ...] | None = None


def auth_aliases(auth_id: str) -> frozenset[str]:
    value: Final = auth_id.strip().lower()
    return frozenset((value, PurePosixPath(value).name)) if value else frozenset()


def wildcard_match(pattern: str, model: str) -> bool:
    normalized: Final = pattern.strip().casefold()
    value: Final = model.strip().casefold()
    if not normalized or not value:
        return False
    if normalized == "*":
        return True
    if "*" not in normalized:
        return normalized == value
    anchored_start: Final = not normalized.startswith("*")
    anchored_end: Final = not normalized.endswith("*")
    parts: Final = tuple(part for part in normalized.split("*") if part)
    remaining: str = value
    for index, part in enumerate(parts):
        found = remaining.find(part)
        if found < 0 or (index == 0 and anchored_start and found != 0):
            return False
        remaining = remaining[found + len(part) :]
    return not anchored_end or bool(parts) and value.endswith(parts[-1])
