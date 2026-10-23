"""封装 Cockpit 服务间管理契约，不负责选卡、重试或自动激活配置。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Final, Literal
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, TypeAdapter, ValidationError, field_validator

_ETAG: Final = r'^"sha256-[0-9a-f]{64}"$'
_JSON: Final = TypeAdapter(dict[str, JsonValue])


def _valid_auth_name(name: str) -> bool:
    return (
        bool(name)
        and name == name.strip()
        and not name.startswith(".")
        and name.lower().endswith(".json")
        and not any(char in '/\\:*?"<>|' or ord(char) < 32 for char in name)
    )


class CockpitAdminConfig(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    url: str = Field(repr=False)
    token: SecretStr = Field(min_length=32)

    @field_validator("url")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        try:
            parsed: Final = urlsplit(value)
            port: Final = parsed.port
        except ValueError:
            raise ValueError("Cockpit URL must be a valid HTTP(S) origin") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or "\\" in value
            or any(ord(char) <= 32 for char in value)
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("Cockpit URL must be a credential-free HTTP(S) origin")
        return value.rstrip("/")

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if any(ord(char) <= 32 or ord(char) >= 127 for char in value.get_secret_value()):
            raise ValueError("Cockpit token must contain only printable non-space ASCII")
        return value


@dataclass(frozen=True, slots=True)
class CockpitFailure:
    category: Literal["transport", "http", "precondition", "invalid_input", "invalid_response"]
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class ManifestSnapshot:
    data: dict[str, JsonValue] = field(repr=False)
    revision: str
    account_count: int
    enabled_key_count: int


@dataclass(frozen=True, slots=True)
class AdminReceipt:
    status: Literal["persisted", "deleted", "restart_scheduled"]
    revision: str | None = None


class AuthFileMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    id: str = Field(repr=False)
    etag: str = Field(pattern=_ETAG)
    size: int = Field(ge=0)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _valid_auth_name(value):
            raise ValueError("Invalid Cockpit credential name")
        return value


class _ClientKey(BaseModel):
    model_config = ConfigDict(strict=True)
    enabled: bool = False


class _ManifestCounts(BaseModel):
    accounts: tuple[dict[str, JsonValue], ...] | None = None
    apiKeys: tuple[_ClientKey, ...] | None = None


class _AuthFiles(BaseModel):
    files: tuple[AuthFileMetadata, ...]


class CockpitAdminClient:
    def __init__(self, config: CockpitAdminConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    async def _request(
        self,
        method: str,
        path: str,
        expected: int,
        *,
        content: bytes | None = None,
        condition: tuple[str, str] | None = None,
    ) -> httpx.Response | CockpitFailure:
        try:
            response: Final = await self._client.request(
                method,
                self._config.url + "/admin/v1/" + path,
                headers={
                    "Authorization": "Bearer " + self._config.token.get_secret_value(),
                    "Content-Type": "application/json",
                    **(dict((condition,)) if condition else {}),
                },
                content=content,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            return CockpitFailure("transport")
        if response.status_code != expected:
            return CockpitFailure(
                "precondition" if response.status_code in {412, 428} else "http", response.status_code
            )
        return response

    async def read_manifest(self) -> ManifestSnapshot | CockpitFailure:
        result: Final = await self._request("GET", "manifest", 200)
        if isinstance(result, CockpitFailure):
            return result
        revision: Final = next((value for key, value in result.headers.items() if key.lower() == "etag"), "")
        if not re.fullmatch(_ETAG, revision):
            return CockpitFailure("invalid_response", result.status_code)
        try:
            data: Final = _JSON.validate_json(result.content)
            counts: Final = _ManifestCounts.model_validate(data)
        except ValidationError:
            return CockpitFailure("invalid_response", result.status_code)
        return ManifestSnapshot(
            data, revision, len(counts.accounts or ()), sum(k.enabled for k in counts.apiKeys or ())
        )

    async def write_manifest(self, data: dict[str, JsonValue], revision: str) -> AdminReceipt | CockpitFailure:
        if not re.fullmatch(_ETAG, revision):
            return CockpitFailure("invalid_input")
        return await self._mutation(
            "PUT", "manifest", "persisted", content=_JSON.dump_json(data), condition=("If-Match", revision)
        )

    async def list_auth_files(self) -> tuple[AuthFileMetadata, ...] | CockpitFailure:
        result: Final = await self._request("GET", "auth-files", 200)
        if isinstance(result, CockpitFailure):
            return result
        try:
            return _AuthFiles.model_validate_json(result.content).files
        except ValidationError:
            return CockpitFailure("invalid_response", result.status_code)

    async def put_auth_file(
        self,
        name: str,
        content: bytes,
        revision: str | None = None,
    ) -> AdminReceipt | CockpitFailure:
        if not _valid_auth_name(name) or (revision is not None and not re.fullmatch(_ETAG, revision)):
            return CockpitFailure("invalid_input")
        return await self._mutation(
            "PUT",
            "auth-files/" + quote(name, safe=""),
            "persisted",
            content=content,
            condition=("If-Match", revision) if revision is not None else ("If-None-Match", "*"),
        )

    async def delete_auth_file(self, name: str, revision: str) -> AdminReceipt | CockpitFailure:
        if not _valid_auth_name(name) or not re.fullmatch(_ETAG, revision):
            return CockpitFailure("invalid_input")
        return await self._mutation(
            "DELETE", "auth-files/" + quote(name, safe=""), "deleted", condition=("If-Match", revision)
        )

    async def restart(self) -> AdminReceipt | CockpitFailure:
        return await self._mutation("POST", "restart", "restart_scheduled")

    async def _mutation(
        self,
        method: str,
        path: str,
        status: Literal["persisted", "deleted", "restart_scheduled"],
        *,
        content: bytes | None = None,
        condition: tuple[str, str] | None = None,
    ) -> AdminReceipt | CockpitFailure:
        result: Final = await self._request(method, path, 202, content=content, condition=condition)
        if isinstance(result, CockpitFailure):
            return result
        try:
            body: Final = _JSON.validate_json(result.content)
        except ValidationError:
            return CockpitFailure("invalid_response", result.status_code)
        revision: Final = result.headers["ETag"] if status == "persisted" and "ETag" in result.headers else None
        if (
            body.get("status") != status
            or (status != "restart_scheduled" and body.get("restartRequired") is not True)
            or (status == "persisted" and (revision is None or not re.fullmatch(_ETAG, revision)))
        ):
            return CockpitFailure("invalid_response", result.status_code)
        return AdminReceipt(status, revision)


class CockpitStatus(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    configured: bool
    account_count: int = Field(default=0, ge=0)
    enabled_key_count: int = Field(default=0, ge=0)
    credential_count: int = Field(default=0, ge=0)
    persisted_manifest_revision: Annotated[str, Field(pattern=_ETAG)] | None = None
    runtime_activation: Literal["restart_required_or_unverified"] = "restart_required_or_unverified"


async def cockpit_status(client: CockpitAdminClient | None) -> CockpitStatus | CockpitFailure:
    if client is None:
        return CockpitStatus(configured=False)
    manifest: Final = await client.read_manifest()
    if isinstance(manifest, CockpitFailure):
        return manifest
    files: Final = await client.list_auth_files()
    if isinstance(files, CockpitFailure):
        return files
    return CockpitStatus(
        configured=True,
        account_count=manifest.account_count,
        enabled_key_count=manifest.enabled_key_count,
        credential_count=len(files),
        persisted_manifest_revision=manifest.revision,
    )
