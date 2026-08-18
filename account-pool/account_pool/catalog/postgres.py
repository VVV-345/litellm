from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from pydantic import AwareDatetime

from account_pool.catalog.models import (
    AdministrativeState,
    BindingOwnership,
    CatalogImport,
    CatalogSnapshot,
    ChannelRecord,
    DeploymentBindingRecord,
    ImportConflict,
    ImportResult,
    ModelPolicyRecord,
)
from account_pool.models import AccountId, FrozenModel, ModelName, QuotaConfig, QuotaUnit, Strategy

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CATALOG_LOCK_KEY: Final = 4_186_343_189_064_001_901

_SELECT_CHANNELS: Final = """
SELECT channel_id, legacy_account_id, account_order, display_name, provider,
       channel_group, base_url_display, administrative_state, max_concurrency,
       priority, weight, quota_unit, quota_total, quota_five_hour, quota_weekly,
       credential_ref, key_mask, key_fingerprint, created_at, updated_at
FROM "LiteLLM_AccountPoolChannel"
ORDER BY account_order
"""
_SELECT_BINDINGS: Final = """
SELECT binding_id, channel_id, deployment_order, public_model, provider_model,
       litellm_deployment_id, ownership, enabled, created_at, updated_at
FROM "LiteLLM_AccountPoolBinding"
ORDER BY channel_id, deployment_order
"""
_SELECT_POLICIES: Final = """
SELECT model, policy_order, strategy, created_at, updated_at
FROM "LiteLLM_AccountPoolModelPolicy"
ORDER BY policy_order
"""
_INSERT_CHANNEL: Final = """
INSERT INTO "LiteLLM_AccountPoolChannel" (
    channel_id, legacy_account_id, account_order, display_name, provider,
    channel_group, base_url_display, administrative_state, max_concurrency,
    priority, weight, quota_unit, quota_total, quota_five_hour, quota_weekly,
    credential_ref, key_mask, key_fingerprint, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
_INSERT_BINDING: Final = """
INSERT INTO "LiteLLM_AccountPoolBinding" (
    binding_id, channel_id, deployment_order, public_model, provider_model,
    litellm_deployment_id, ownership, enabled, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
_INSERT_POLICY: Final = """
INSERT INTO "LiteLLM_AccountPoolModelPolicy" (
    model, policy_order, strategy, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s)
"""


class _ChannelRow(FrozenModel):
    channel_id: UUID
    legacy_account_id: AccountId | None
    account_order: int
    display_name: str
    provider: str
    channel_group: str | None
    base_url_display: str
    administrative_state: AdministrativeState
    max_concurrency: int
    priority: int
    weight: int
    quota_unit: QuotaUnit
    quota_total: float | None
    quota_five_hour: float | None
    quota_weekly: float | None
    credential_ref: str | None
    key_mask: str | None
    key_fingerprint: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class _BindingRow(FrozenModel):
    binding_id: UUID
    channel_id: UUID
    deployment_order: int
    public_model: ModelName
    provider_model: str | None
    litellm_deployment_id: str
    ownership: BindingOwnership
    enabled: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class _PolicyRow(FrozenModel):
    model: ModelName
    policy_order: int
    strategy: Strategy
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PostgresCatalogRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def load_snapshot(self) -> CatalogSnapshot:
        async with await self._connect() as connection, connection.transaction():
            await self._set_search_path(connection)
            return await self._load_snapshot(connection)

    async def import_once(self, command: CatalogImport) -> ImportResult:
        async with await self._connect() as connection, connection.transaction():
            await self._set_search_path(connection)
            await connection.execute("SELECT pg_advisory_xact_lock(%s)", (_CATALOG_LOCK_KEY,))
            snapshot: Final = await self._load_snapshot(connection)
            conflicts: Final = _collect_conflicts(command=command, snapshot=snapshot)
            if conflicts:
                return ImportResult(status="conflict", conflicts=conflicts)

            channels: Final = tuple(
                channel for channel in command.channels if not _matching_channels(channel, snapshot)
            )
            bindings: Final = tuple(
                binding for binding in command.bindings if not _matching_bindings(binding, snapshot)
            )
            policies: Final = tuple(policy for policy in command.policies if not _matching_policies(policy, snapshot))
            await _insert_channels(connection=connection, records=channels)
            await _insert_bindings(connection=connection, records=bindings)
            await _insert_policies(connection=connection, records=policies)
            if not channels and not bindings and not policies:
                return ImportResult(status="unchanged")
            return ImportResult(
                status="created",
                created_channels=len(channels),
                created_bindings=len(bindings),
                created_policies=len(policies),
            )

    async def _connect(self) -> AsyncConnection[Mapping[str, object]]:
        row_factory: Final = cast(AsyncRowFactory[Mapping[str, object]], dict_row)
        return await AsyncConnection[Mapping[str, object]].connect(
            self._database_url,
            row_factory=row_factory,
        )

    async def _set_search_path(self, connection: AsyncConnection[Mapping[str, object]]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))

    @staticmethod
    async def _load_snapshot(connection: AsyncConnection[Mapping[str, object]]) -> CatalogSnapshot:
        channel_cursor: Final = await connection.execute(_SELECT_CHANNELS)
        binding_cursor: Final = await connection.execute(_SELECT_BINDINGS)
        policy_cursor: Final = await connection.execute(_SELECT_POLICIES)
        channel_rows: Final = tuple(cast(object, row) for row in await channel_cursor.fetchall())
        binding_rows: Final = tuple(cast(object, row) for row in await binding_cursor.fetchall())
        policy_rows: Final = tuple(cast(object, row) for row in await policy_cursor.fetchall())
        return CatalogSnapshot(
            channels=tuple(_decode_channel(row) for row in channel_rows),
            bindings=tuple(_decode_binding(row) for row in binding_rows),
            policies=tuple(_decode_policy(row) for row in policy_rows),
        )


def _decode_channel(value: object) -> ChannelRecord:
    row: Final = _ChannelRow.model_validate(value)
    return ChannelRecord(
        channel_id=row.channel_id,
        legacy_account_id=row.legacy_account_id,
        account_order=row.account_order,
        display_name=row.display_name,
        provider=row.provider,
        group=row.channel_group,
        base_url_display=row.base_url_display,
        administrative_state=row.administrative_state,
        max_concurrency=row.max_concurrency,
        priority=row.priority,
        weight=row.weight,
        quotas=QuotaConfig(
            unit=row.quota_unit,
            total=row.quota_total,
            five_hour=row.quota_five_hour,
            weekly=row.quota_weekly,
        ),
        credential_ref=row.credential_ref,
        key_mask=row.key_mask,
        key_fingerprint=row.key_fingerprint,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _decode_binding(value: object) -> DeploymentBindingRecord:
    row: Final = _BindingRow.model_validate(value)
    return DeploymentBindingRecord(
        binding_id=row.binding_id,
        channel_id=row.channel_id,
        deployment_order=row.deployment_order,
        public_model=row.public_model,
        provider_model=row.provider_model,
        litellm_deployment_id=row.litellm_deployment_id,
        ownership=row.ownership,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _decode_policy(value: object) -> ModelPolicyRecord:
    row: Final = _PolicyRow.model_validate(value)
    return ModelPolicyRecord(
        model=row.model,
        policy_order=row.policy_order,
        strategy=row.strategy,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _matching_channels(record: ChannelRecord, snapshot: CatalogSnapshot) -> tuple[ChannelRecord, ...]:
    return tuple(
        existing
        for existing in snapshot.channels
        if existing.channel_id == record.channel_id
        or (record.legacy_account_id is not None and existing.legacy_account_id == record.legacy_account_id)
    )


def _matching_bindings(
    record: DeploymentBindingRecord,
    snapshot: CatalogSnapshot,
) -> tuple[DeploymentBindingRecord, ...]:
    return tuple(
        existing
        for existing in snapshot.bindings
        if existing.binding_id == record.binding_id
        or existing.litellm_deployment_id == record.litellm_deployment_id
    )


def _matching_policies(record: ModelPolicyRecord, snapshot: CatalogSnapshot) -> tuple[ModelPolicyRecord, ...]:
    return tuple(existing for existing in snapshot.policies if existing.model == record.model)


def _collect_conflicts(command: CatalogImport, snapshot: CatalogSnapshot) -> tuple[ImportConflict, ...]:
    channel_conflicts: Final = tuple(
        ImportConflict(entity="channel", identity=str(record.channel_id), reason="persisted business fields differ")
        for record in command.channels
        if not _matches_one_channel(record, _matching_channels(record, snapshot))
    )
    binding_conflicts: Final = tuple(
        ImportConflict(entity="binding", identity=str(record.binding_id), reason="persisted business fields differ")
        for record in command.bindings
        if not _matches_one_binding(record, _matching_bindings(record, snapshot))
    )
    policy_conflicts: Final = tuple(
        ImportConflict(entity="policy", identity=record.model, reason="persisted business fields differ")
        for record in command.policies
        if not _matches_one_policy(record, _matching_policies(record, snapshot))
    )
    return channel_conflicts + binding_conflicts + policy_conflicts


def _matches_one_channel(record: ChannelRecord, matches: tuple[ChannelRecord, ...]) -> bool:
    return not matches or (len(matches) == 1 and _same_channel_business_fields(record, matches[0]))


def _matches_one_binding(record: DeploymentBindingRecord, matches: tuple[DeploymentBindingRecord, ...]) -> bool:
    return not matches or (len(matches) == 1 and _same_binding_business_fields(record, matches[0]))


def _matches_one_policy(record: ModelPolicyRecord, matches: tuple[ModelPolicyRecord, ...]) -> bool:
    return not matches or (len(matches) == 1 and _same_policy_business_fields(record, matches[0]))


def _same_channel_business_fields(left: ChannelRecord, right: ChannelRecord) -> bool:
    return (
        left.channel_id == right.channel_id
        and left.legacy_account_id == right.legacy_account_id
        and left.account_order == right.account_order
        and left.display_name == right.display_name
        and left.provider == right.provider
        and left.group == right.group
        and left.base_url_display == right.base_url_display
        and left.administrative_state == right.administrative_state
        and left.max_concurrency == right.max_concurrency
        and left.priority == right.priority
        and left.weight == right.weight
        and left.quotas == right.quotas
        and left.credential_ref == right.credential_ref
        and left.key_mask == right.key_mask
        and left.key_fingerprint == right.key_fingerprint
    )


def _same_binding_business_fields(left: DeploymentBindingRecord, right: DeploymentBindingRecord) -> bool:
    return (
        left.binding_id == right.binding_id
        and left.channel_id == right.channel_id
        and left.deployment_order == right.deployment_order
        and left.public_model == right.public_model
        and left.provider_model == right.provider_model
        and left.litellm_deployment_id == right.litellm_deployment_id
        and left.ownership == right.ownership
        and left.enabled == right.enabled
    )


def _same_policy_business_fields(left: ModelPolicyRecord, right: ModelPolicyRecord) -> bool:
    return left.model == right.model and left.policy_order == right.policy_order and left.strategy == right.strategy


async def _insert_channels(
    connection: AsyncConnection[Mapping[str, object]],
    records: tuple[ChannelRecord, ...],
) -> None:
    if not records:
        return
    parameters: Final = tuple(_channel_parameters(record) for record in records)
    async with connection.cursor() as cursor:
        await cursor.executemany(_INSERT_CHANNEL, parameters)


async def _insert_bindings(
    connection: AsyncConnection[Mapping[str, object]],
    records: tuple[DeploymentBindingRecord, ...],
) -> None:
    if not records:
        return
    parameters: Final = tuple(_binding_parameters(record) for record in records)
    async with connection.cursor() as cursor:
        await cursor.executemany(_INSERT_BINDING, parameters)


async def _insert_policies(
    connection: AsyncConnection[Mapping[str, object]],
    records: tuple[ModelPolicyRecord, ...],
) -> None:
    if not records:
        return
    parameters: Final = tuple(_policy_parameters(record) for record in records)
    async with connection.cursor() as cursor:
        await cursor.executemany(_INSERT_POLICY, parameters)


def _channel_parameters(record: ChannelRecord) -> tuple[object, ...]:
    return (
        str(record.channel_id),
        record.legacy_account_id,
        record.account_order,
        record.display_name,
        record.provider,
        record.group,
        record.base_url_display,
        record.administrative_state.value,
        record.max_concurrency,
        record.priority,
        record.weight,
        record.quotas.unit.value,
        record.quotas.total,
        record.quotas.five_hour,
        record.quotas.weekly,
        record.credential_ref,
        record.key_mask,
        record.key_fingerprint,
        record.created_at,
        record.updated_at,
    )


def _binding_parameters(record: DeploymentBindingRecord) -> tuple[object, ...]:
    return (
        str(record.binding_id),
        str(record.channel_id),
        record.deployment_order,
        record.public_model,
        record.provider_model,
        record.litellm_deployment_id,
        record.ownership.value,
        record.enabled,
        record.created_at,
        record.updated_at,
    )


def _policy_parameters(record: ModelPolicyRecord) -> tuple[object, ...]:
    return (record.model, record.policy_order, record.strategy.value, record.created_at, record.updated_at)
