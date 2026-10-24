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
    StrictFloat,
    StrictStr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from litellm.proxy._types import SpecialModelNames
from litellm.proxy.management_endpoints.account_pool_integration import CardScope
from litellm.repositories.verification_token_repository import TokenQueryDatabase

NativeSubjectStatus = Literal[
    "verified", "missing", "revoked", "blocked", "expired", "legacy_subject",
    "card_scope_unverified", "model_scope_unverified", "invalid_record", "unavailable",
]
_KEY_HASH: Final = re.compile(r"[0-9a-f]{64}")
_JSON_OBJECT: Final = TypeAdapter(dict[str, JsonValue])
_MODEL_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
_MODEL_SENTINELS: Final = frozenset(item.value for item in SpecialModelNames)


def _literal_models(models: tuple[str, ...]) -> bool:
    return all(_MODEL_NAME.fullmatch(model) is not None and model not in _MODEL_SENTINELS for model in models)


class NativeModelScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["unrestricted", "restricted"]
    models: tuple[StrictStr, ...]

    @model_validator(mode="after")
    def explicit_scope(self) -> Self:
        if (self.kind == "restricted") != bool(self.models) or not _literal_models(self.models):
            raise ValueError("Model scope must be unrestricted or a nonempty literal allowlist")
        return self


class NativeSubjectResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: NativeSubjectStatus
    card_id: UUID | None = None
    model_scope: NativeModelScope | None = None
    checked_at: AwareDatetime

    @model_validator(mode="after")
    def verified_card_only(self) -> Self:
        if (self.status == "verified") != (self.card_id is not None):
            raise ValueError("Only verified subjects have a card scope")
        if (self.status == "verified") != (self.model_scope is not None):
            raise ValueError("Only verified subjects have an explicit model scope")
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


class _OwnerScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject_id: StrictStr
    models: tuple[StrictStr, ...]
    blocked: StrictBool
    permissions_unrestricted: StrictBool
    team_id: StrictStr | None


class _MembershipScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: StrictStr
    team_id: StrictStr
    budget_id: StrictStr | None


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
    max_budget: StrictFloat | None
    budget_duration: StrictStr | None
    budget_reset_at: datetime | None
    user_scope: Annotated[_OwnerScope | None, BeforeValidator(_decode_object)]
    team_scope: Annotated[_OwnerScope | None, BeforeValidator(_decode_object)]
    project_scope: Annotated[_OwnerScope | None, BeforeValidator(_decode_object)]
    membership_scope: Annotated[_MembershipScope | None, BeforeValidator(_decode_object)]

    def supported(self) -> bool:
        return self.key_type in (None, "default") and not any(
            (
                self.aliases, self.config, self.permissions, self.router_settings,
                self.access_group_ids, self.policies, self.allowed_routes, self.allowed_cache_controls,
                self.model_max_budget, self.budget_fallbacks, self.budget_limits,
            )
        ) and all(
            owner is None for owner in (
                self.organization_id, self.agent_id, self.object_permission_id, self.budget_id,
                self.max_budget, self.budget_duration, self.budget_reset_at,
            )
        )

    def model_scope(self) -> NativeModelScope | None:
        owners: Final = ((self.user_id, self.user_scope), (self.team_id, self.team_scope),
                         (self.project_id, self.project_scope))
        for owner_id, owner in owners:
            if owner_id is None:
                if owner is not None:
                    return None
                continue
            if not owner_id or owner is None or owner.subject_id != owner_id:
                return None
            if owner.blocked or not owner.permissions_unrestricted or not _literal_models(owner.models):
                return None
            if owner.team_id is not None and owner.team_id != self.team_id:
                return None
        if self.project_scope is not None and self.project_scope.team_id != self.team_id:
            return None
        if self.user_id is not None and self.team_id is not None:
            if (self.membership_scope is None or self.membership_scope.user_id != self.user_id
                    or self.membership_scope.team_id != self.team_id or self.membership_scope.budget_id is not None):
                return None
        elif self.membership_scope is not None:
            return None
        if not _literal_models(self.models):
            return None
        # 原生 common_checks 仅对个人 Key 应用用户 models；团队 Key 使用团队和成员限制。
        applicable: Final = (self.models,) + tuple(
            owner.models for owner in (
                self.user_scope if self.team_id is None else None, self.team_scope, self.project_scope,
            ) if owner is not None
        )
        restricted: Final = tuple(frozenset(models) for models in applicable if models)
        if not restricted:
            return NativeModelScope(kind="unrestricted", models=())
        intersection: Final = restricted[0].intersection(*restricted[1:])
        return NativeModelScope(kind="restricted", models=tuple(sorted(intersection))) if intersection else None


_LIVE_QUERY: Final = """SELECT k.token, k.blocked, k.expires, k.metadata, k.models, k.aliases, k.config, k.permissions,
    k.router_settings, k.access_group_ids, k.policies, k.allowed_routes, k.allowed_cache_controls,
    k.key_type, k.model_max_budget, k.budget_fallbacks, k.budget_limits, k.user_id, k.team_id,
    k.project_id, k.organization_id, k.agent_id, k.object_permission_id, k.budget_id,
    k.max_budget, k.budget_duration, k.budget_reset_at,
    CASE WHEN u.user_id IS NULL THEN NULL ELSE json_build_object(
        'subject_id', u.user_id, 'models', u.models, 'blocked', false, 'team_id', u.team_id,
        'permissions_unrestricted', u.organization_id IS NULL AND u.object_permission_id IS NULL
            AND u.max_budget IS NULL AND u.budget_duration IS NULL AND u.budget_reset_at IS NULL
            AND u.max_parallel_requests IS NULL AND u.tpm_limit IS NULL AND u.rpm_limit IS NULL
            AND u.metadata = '{}'::jsonb AND u.model_max_budget = '{}'::jsonb
            AND cardinality(u.policies) = 0 AND cardinality(u.allowed_cache_controls) = 0
    ) END AS user_scope,
    CASE WHEN t.team_id IS NULL THEN NULL ELSE json_build_object(
        'subject_id', t.team_id, 'models', t.models, 'blocked', t.blocked, 'team_id', NULL,
        'permissions_unrestricted', t.organization_id IS NULL AND t.object_permission_id IS NULL
            AND t.max_budget IS NULL AND t.soft_budget IS NULL
            AND t.budget_duration IS NULL AND t.budget_reset_at IS NULL
            AND t.max_parallel_requests IS NULL AND t.tpm_limit IS NULL AND t.rpm_limit IS NULL
            AND t.metadata = '{}'::jsonb AND t.model_max_budget = '{}'::jsonb
            AND (t.router_settings IS NULL OR t.router_settings = '{}'::jsonb)
            AND t.budget_limits IS NULL AND t.model_id IS NULL AND NOT t.allow_team_guardrail_config
            AND cardinality(t.policies) = 0 AND cardinality(t.access_group_ids) = 0
            AND cardinality(t.team_member_permissions) = 0 AND cardinality(t.default_team_member_models) = 0
    ) END AS team_scope,
    CASE WHEN p.project_id IS NULL THEN NULL ELSE json_build_object(
        'subject_id', p.project_id, 'models', p.models, 'blocked', p.blocked, 'team_id', p.team_id,
        'permissions_unrestricted', p.budget_id IS NULL AND p.object_permission_id IS NULL
            AND p.metadata = '{}'::jsonb AND p.model_rpm_limit = '{}'::jsonb AND p.model_tpm_limit = '{}'::jsonb
    ) END AS project_scope,
    CASE WHEN m.user_id IS NULL THEN NULL ELSE json_build_object(
        'user_id', m.user_id, 'team_id', m.team_id, 'budget_id', m.budget_id
    ) END AS membership_scope
FROM "LiteLLM_VerificationToken" AS k
LEFT JOIN "LiteLLM_UserTable" AS u ON u.user_id = k.user_id
LEFT JOIN "LiteLLM_TeamTable" AS t ON t.team_id = k.team_id
LEFT JOIN "LiteLLM_ProjectTable" AS p ON p.project_id = k.project_id
LEFT JOIN "LiteLLM_TeamMembership" AS m ON m.user_id = k.user_id AND m.team_id = k.team_id
WHERE k.token = $1
AND NOT EXISTS (SELECT 1 FROM "LiteLLM_DeletedVerificationToken" WHERE token = $1) LIMIT 1"""


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
        rows: Final = await self.database.query_raw(_LIVE_QUERY, key_hash)
        return rows[0] if rows else None


async def inspect_native_subject_with_repository(
    key_hash: str, repository: NativeSubjectRepository, *, now: datetime
) -> NativeSubjectResult:
    checked_at: Final = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)

    def result(
        status: NativeSubjectStatus, card_id: UUID | None = None, model_scope: NativeModelScope | None = None,
    ) -> NativeSubjectResult:
        return NativeSubjectResult(status=status, card_id=card_id, model_scope=model_scope, checked_at=checked_at)

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
    if not permission.supported() or any(
        field not in ("account_pool_card_id", "account_pool_binding_id") for field in state.metadata
    ):
        return result("model_scope_unverified")
    model_scope: Final = permission.model_scope()
    if model_scope is None:
        return result("model_scope_unverified")
    return result("verified", scope.card_id, model_scope)


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
