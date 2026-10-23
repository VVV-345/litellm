"""从主库读取原生 Key 的静态单卡上界；不注册密钥、不缓存，也不替代运行时鉴权。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Final, Literal, Protocol, Self, cast
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    JsonValue,
    StrictBool,
    StrictStr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from litellm.proxy.management_endpoints.account_pool_integration import CardScope
from litellm.repositories.verification_token_repository import TokenQueryDatabase

NativeSubjectStatus = Literal[
    "verified", "missing", "revoked", "blocked", "expired", "legacy_subject",
    "card_scope_unverified", "model_scope_unverified", "invalid_record", "unavailable",
]
_KEY_HASH: Final = re.compile(r"[0-9a-f]{64}")
_JSON_OBJECT: Final = TypeAdapter(dict[str, JsonValue])


class NativeSubjectResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: NativeSubjectStatus
    card_id: UUID | None = None
    checked_at: AwareDatetime

    @model_validator(mode="after")
    def verified_card_only(self) -> Self:
        if (self.status == "verified") != (self.card_id is not None):
            raise ValueError("Only verified subjects have a card scope")
        return self


def _decode_object(value: object) -> object:
    return _JSON_OBJECT.validate_json(value) if isinstance(value, str) else value


JsonObject = Annotated[Mapping[str, JsonValue], BeforeValidator(_decode_object)]


class _LiveState(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    token: StrictStr
    blocked: StrictBool | None
    expires: datetime | None
    metadata: JsonObject

    @field_validator("expires", mode="before")
    @classmethod
    def validate_expiry(cls, value: object) -> object:
        if value is not None and not isinstance(value, (datetime, str)):
            raise ValueError("Expiry must be a timestamp")
        return value


class _PermissionScope(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    models: tuple[StrictStr, ...]
    aliases: JsonObject
    config: JsonObject
    permissions: JsonObject
    router_settings: JsonObject | None
    access_group_ids: tuple[StrictStr, ...]
    policies: tuple[StrictStr, ...]
    allowed_routes: tuple[StrictStr, ...]
    allowed_cache_controls: tuple[StrictStr, ...]
    key_type: StrictStr | None
    model_max_budget: JsonObject
    budget_fallbacks: JsonObject
    budget_limits: JsonValue
    user_id: StrictStr | None
    team_id: StrictStr | None
    project_id: StrictStr | None
    organization_id: StrictStr | None
    agent_id: StrictStr | None
    object_permission_id: StrictStr | None
    budget_id: StrictStr | None

    def unrestricted(self) -> bool:
        return self.key_type in (None, "default") and not any(
            (
                self.models, self.aliases, self.config, self.permissions, self.router_settings,
                self.access_group_ids, self.policies, self.allowed_routes, self.allowed_cache_controls,
                self.model_max_budget, self.budget_fallbacks, self.budget_limits,
            )
        ) and all(
            owner is None for owner in (
                self.user_id, self.team_id, self.project_id, self.organization_id,
                self.agent_id, self.object_permission_id, self.budget_id,
            )
        )


class NativeSubjectRepository(Protocol):
    async def find_deleted(self, key_hash: str) -> object | None: ...

    async def find_live(self, key_hash: str) -> object | None: ...


@dataclass(frozen=True, slots=True)
class WriterNativeSubjectRepository:
    database: TokenQueryDatabase

    async def find_deleted(self, key_hash: str) -> object | None:
        rows: Final = await self.database.query_raw(
            'SELECT token FROM "LiteLLM_DeletedVerificationToken" WHERE token = $1 LIMIT 1', key_hash
        )
        return rows[0] if rows else None

    async def find_live(self, key_hash: str) -> object | None:
        rows: Final = await self.database.query_raw(
            'SELECT * FROM "LiteLLM_VerificationToken" WHERE token = $1 '
            'AND NOT EXISTS (SELECT 1 FROM "LiteLLM_DeletedVerificationToken" WHERE token = $1) LIMIT 1', key_hash
        )
        return rows[0] if rows else None


async def inspect_native_subject_with_repository(
    key_hash: str, repository: NativeSubjectRepository, *, now: datetime
) -> NativeSubjectResult:
    checked_at: Final = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)

    def result(status: NativeSubjectStatus, card_id: UUID | None = None) -> NativeSubjectResult:
        return NativeSubjectResult(status=status, card_id=card_id, checked_at=checked_at)

    if _KEY_HASH.fullmatch(key_hash) is None:
        return result("invalid_record")
    try:
        if await repository.find_deleted(key_hash) is not None:
            return result("revoked")
        row: Final = await repository.find_live(key_hash)
    except Exception:
        return result("unavailable")
    if row is None:
        return result("missing")
    try:
        state: Final = _LiveState.model_validate(row)
        scope: Final = CardScope.model_validate(state.metadata)
    except ValidationError:
        return result("invalid_record")
    if state.token != key_hash:
        return result("invalid_record")
    if state.blocked is True:
        return result("blocked")
    expiry: Final = (
        state.expires.replace(tzinfo=timezone.utc)
        if state.expires is not None and state.expires.tzinfo is None
        else state.expires
    )
    if expiry is not None and expiry <= checked_at:
        return result("expired")
    if scope.binding_id is not None:
        return result("legacy_subject")
    if scope.card_id is None:
        return result("card_scope_unverified")
    try:
        permission: Final = _PermissionScope.model_validate(row)
    except ValidationError:
        return result("model_scope_unverified")
    if not permission.unrestricted() or any(
        field not in ("account_pool_card_id", "account_pool_binding_id") for field in state.metadata
    ):
        return result("model_scope_unverified")
    return result("verified", scope.card_id)


async def inspect_native_subject(key_hash: str) -> NativeSubjectResult:
    checked_at: Final = datetime.now(timezone.utc)
    if _KEY_HASH.fullmatch(key_hash) is None:
        return NativeSubjectResult(status="invalid_record", checked_at=checked_at)
    try:
        from litellm.proxy.proxy_server import prisma_client

        if prisma_client is None:
            return NativeSubjectResult(status="unavailable", checked_at=checked_at)
        database: Final = cast(
            TokenQueryDatabase, prisma_client.writer_db
        )  # cast-ok: PrismaWrapper 动态代理 query_raw，撤销检查只能使用主库
        repository: Final = WriterNativeSubjectRepository(database)
        return await inspect_native_subject_with_repository(key_hash, repository, now=checked_at)
    except Exception:
        return NativeSubjectResult(status="unavailable", checked_at=checked_at)
