"""使用 PostgreSQL 持久化渠道目录，并保证旧配置只导入一次。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from pydantic import AwareDatetime

from account_pool.catalog.lifecycle import (
    ApplyChannelCreate,
    ApplyChannelDelete,
    ApplyChannelDetach,
    ApplyChannelImport,
    ApplyChannelUpdate,
    ApplyExternalBindingDelete,
    CatalogApplyFailure,
    CatalogApplyFailureCode,
    CatalogApplyResult,
    CatalogApplySuccess,
    CatalogLifecycleCommand,
    CatalogPendingDeleteResult,
    CatalogPendingDeleteSuccess,
)
from account_pool.catalog.models import (
    AdministrativeState,
    BindingOwnership,
    CatalogImport,
    CatalogSnapshot,
    ChannelRecord,
    DeploymentBindingRecord,
    ImportConflict,
    ImportResult,
    ModelCandidateOverrideRecord,
    ModelPolicyRecord,
)
from account_pool.models import AccountId, ChannelPriority, FrozenModel, ModelName, QuotaConfig, QuotaUnit, Strategy

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CATALOG_LOCK_KEY: Final = 4_186_343_189_064_001_901

_SELECT_CHANNELS: Final = """
SELECT channel_id, legacy_account_id, account_order, display_name, provider, model_discovery_provider_id,
       parser_provider_id,
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
SELECT model, policy_order, strategy, version, created_at, updated_at
FROM "LiteLLM_AccountPoolModelPolicy"
ORDER BY policy_order
"""
_SELECT_CANDIDATE_OVERRIDES: Final = """
SELECT model, binding_id, manual_order, weight, paused, created_at, updated_at
FROM "LiteLLM_AccountPoolModelCandidateOverride"
ORDER BY model, manual_order NULLS LAST, binding_id
"""
_INSERT_CHANNEL: Final = """
INSERT INTO "LiteLLM_AccountPoolChannel" (
    channel_id, legacy_account_id, account_order, display_name, provider,
    model_discovery_provider_id, parser_provider_id, channel_group, base_url_display, administrative_state,
    max_concurrency,
    priority, weight, quota_unit, quota_total, quota_five_hour, quota_weekly,
    credential_ref, key_mask, key_fingerprint, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
_INSERT_BINDING: Final = """
INSERT INTO "LiteLLM_AccountPoolBinding" (
    binding_id, channel_id, deployment_order, public_model, provider_model,
    litellm_deployment_id, ownership, enabled, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
_INSERT_POLICY: Final = """
INSERT INTO "LiteLLM_AccountPoolModelPolicy" (
    model, policy_order, strategy, version, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s, %s)
"""
_UPDATE_CHANNEL: Final = """
UPDATE "LiteLLM_AccountPoolChannel"
SET legacy_account_id = %s, account_order = %s, display_name = %s, provider = %s,
    model_discovery_provider_id = %s, parser_provider_id = %s, channel_group = %s, base_url_display = %s,
    administrative_state = %s,
    max_concurrency = %s, priority = %s, weight = %s, quota_unit = %s,
    quota_total = %s, quota_five_hour = %s, quota_weekly = %s,
    credential_ref = %s, key_mask = %s, key_fingerprint = %s, updated_at = %s
WHERE channel_id = %s
"""

_SYNC_ACTION_BY_COMMAND: Final = {
    ApplyChannelCreate: "create_channel",
    ApplyChannelUpdate: "update_channel",
    ApplyChannelImport: "import_channel",
    ApplyChannelDetach: "detach_channel",
    ApplyChannelDelete: "delete_channel",
    ApplyExternalBindingDelete: "delete_external_deployment",
}


class _ChannelRow(FrozenModel):
    channel_id: UUID
    legacy_account_id: AccountId | None
    account_order: int
    display_name: str
    provider: str
    model_discovery_provider_id: str | None
    parser_provider_id: str | None
    channel_group: str | None
    base_url_display: str
    administrative_state: AdministrativeState
    max_concurrency: int
    priority: ChannelPriority
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
    version: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class _CandidateOverrideRow(FrozenModel):
    model: ModelName
    binding_id: UUID
    manual_order: int | None
    weight: int | None
    paused: bool
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
            # 事务级咨询锁确保多个服务实例并发启动时，旧配置也只会创建一份目录数据。
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

    async def apply_lifecycle(self, command: CatalogLifecycleCommand) -> CatalogApplyResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                operation_state: Final = await _lock_operation(connection, command)
                if operation_state == "applied":
                    return CatalogApplySuccess(operation_id=command.operation_id)
                if operation_state != "ready":
                    return CatalogApplyFailure(
                        operation_id=command.operation_id,
                        code=CatalogApplyFailureCode.OPERATION_MISMATCH,
                        retryable=False,
                    )
                result: Final = await _apply_lifecycle_command(connection, command)
                if isinstance(result, CatalogApplyFailure):
                    return result
                await connection.execute(
                    """
                    UPDATE "LiteLLM_AccountPoolSyncOperation"
                    SET status = 'applied', requires_key = FALSE, failure_code = NULL,
                        failure_message = NULL, applied_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE operation_id = %s
                    """,
                    (str(command.operation_id),),
                )
                return CatalogApplySuccess(operation_id=command.operation_id)
        except psycopg.Error:
            return CatalogApplyFailure(
                operation_id=command.operation_id,
                code=CatalogApplyFailureCode.DATABASE_UNAVAILABLE,
                retryable=True,
            )

    async def mark_pending_delete(
        self,
        operation_id: UUID,
        channel_id: UUID,
    ) -> CatalogPendingDeleteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                operation_cursor: Final = await connection.execute(
                    """
                    SELECT action, status, channel_id
                    FROM "LiteLLM_AccountPoolSyncOperation"
                    WHERE operation_id = %s
                    FOR UPDATE
                    """,
                    (str(operation_id),),
                )
                operation: Final = await operation_cursor.fetchone()
                if (
                    operation is None
                    or operation.get("action") not in {"detach_channel", "delete_channel"}
                    or operation.get("status") not in {"pending_delete", "failed"}
                    or operation.get("channel_id") != str(channel_id)
                ):
                    return CatalogApplyFailure(
                        operation_id=operation_id,
                        code=CatalogApplyFailureCode.OPERATION_MISMATCH,
                        retryable=False,
                    )
                channel_cursor: Final = await connection.execute(
                    """
                    UPDATE "LiteLLM_AccountPoolChannel"
                    SET administrative_state = 'pending_delete', updated_at = CURRENT_TIMESTAMP
                    WHERE channel_id = %s
                    RETURNING channel_id
                    """,
                    (str(channel_id),),
                )
                if await channel_cursor.fetchone() is None:
                    return CatalogApplyFailure(
                        operation_id=operation_id,
                        code=CatalogApplyFailureCode.CHANNEL_NOT_FOUND,
                        retryable=False,
                    )
                return CatalogPendingDeleteSuccess(operation_id=operation_id)
        except psycopg.Error:
            return CatalogApplyFailure(
                operation_id=operation_id,
                code=CatalogApplyFailureCode.DATABASE_UNAVAILABLE,
                retryable=True,
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
        override_cursor: Final = await connection.execute(_SELECT_CANDIDATE_OVERRIDES)
        channel_rows: Final = tuple(cast(object, row) for row in await channel_cursor.fetchall())
        binding_rows: Final = tuple(cast(object, row) for row in await binding_cursor.fetchall())
        policy_rows: Final = tuple(cast(object, row) for row in await policy_cursor.fetchall())
        override_rows: Final = tuple(cast(object, row) for row in await override_cursor.fetchall())
        return CatalogSnapshot(
            channels=tuple(_decode_channel(row) for row in channel_rows),
            bindings=tuple(_decode_binding(row) for row in binding_rows),
            policies=tuple(_decode_policy(row) for row in policy_rows),
            candidate_overrides=tuple(_decode_candidate_override(row) for row in override_rows),
        )


def _decode_channel(value: object) -> ChannelRecord:
    row: Final = _ChannelRow.model_validate(value)
    return ChannelRecord(
        channel_id=row.channel_id,
        legacy_account_id=row.legacy_account_id,
        account_order=row.account_order,
        display_name=row.display_name,
        provider=row.provider,
        model_discovery_provider_id=row.model_discovery_provider_id,
        parser_provider_id=row.parser_provider_id,
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
        version=row.version,
    )


def _decode_candidate_override(value: object) -> ModelCandidateOverrideRecord:
    row: Final = _CandidateOverrideRow.model_validate(value)
    return ModelCandidateOverrideRecord(
        model=row.model,
        binding_id=row.binding_id,
        manual_order=row.manual_order,
        weight=row.weight,
        paused=row.paused,
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
        and left.model_discovery_provider_id == right.model_discovery_provider_id
        and left.parser_provider_id == right.parser_provider_id
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
        record.model_discovery_provider_id,
        record.parser_provider_id,
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
    return (
        record.model,
        record.policy_order,
        record.strategy.value,
        record.version,
        record.created_at,
        record.updated_at,
    )


async def _lock_operation(
    connection: AsyncConnection[Mapping[str, object]],
    command: CatalogLifecycleCommand,
) -> str:
    cursor: Final = await connection.execute(
        """
        SELECT action, status, channel_id
        FROM "LiteLLM_AccountPoolSyncOperation"
        WHERE operation_id = %s
        FOR UPDATE
        """,
        (str(command.operation_id),),
    )
    row: Final = await cursor.fetchone()
    if row is None:
        return "missing"
    expected_action: Final = _SYNC_ACTION_BY_COMMAND[type(command)]
    if row.get("action") != expected_action or row.get("channel_id") != str(_command_channel_id(command)):
        return "mismatch"
    status: Final = row.get("status")
    if status == "applied":
        return "applied"
    return "ready" if status in _allowed_operation_statuses(expected_action) else "mismatch"


async def _apply_lifecycle_command(
    connection: AsyncConnection[Mapping[str, object]],
    command: CatalogLifecycleCommand,
) -> CatalogApplyResult:
    if isinstance(command, (ApplyChannelCreate, ApplyChannelImport)):
        return await _create_channel(connection, command)
    if isinstance(command, ApplyChannelUpdate):
        return await _update_channel(connection, command)
    if isinstance(command, ApplyExternalBindingDelete):
        cursor: Final = await connection.execute(
            """
            DELETE FROM "LiteLLM_AccountPoolBinding"
            WHERE binding_id = %s AND channel_id = %s AND ownership = 'externally_managed'
            RETURNING binding_id
            """,
            (str(command.binding.binding_id), str(command.channel_id)),
        )
        if await cursor.fetchone() is None:
            return CatalogApplyFailure(
                operation_id=command.operation_id,
                code=CatalogApplyFailureCode.BINDING_NOT_FOUND,
                retryable=False,
            )
        return CatalogApplySuccess(operation_id=command.operation_id)
    delete_cursor: Final = await connection.execute(
        'DELETE FROM "LiteLLM_AccountPoolChannel" WHERE channel_id = %s RETURNING channel_id',
        (str(command.channel_id),),
    )
    if await delete_cursor.fetchone() is None:
        return CatalogApplyFailure(
            operation_id=command.operation_id,
            code=CatalogApplyFailureCode.CHANNEL_NOT_FOUND,
            retryable=False,
        )
    return CatalogApplySuccess(operation_id=command.operation_id)


async def _create_channel(
    connection: AsyncConnection[Mapping[str, object]],
    command: ApplyChannelCreate | ApplyChannelImport,
) -> CatalogApplyResult:
    existing: Final = await connection.execute(
        'SELECT 1 FROM "LiteLLM_AccountPoolChannel" WHERE channel_id = %s',
        (str(command.channel.channel_id),),
    )
    if await existing.fetchone() is not None:
        return CatalogApplyFailure(
            operation_id=command.operation_id,
            code=CatalogApplyFailureCode.STATE_CONFLICT,
            retryable=False,
        )
    await connection.execute(_INSERT_CHANNEL, _channel_parameters(command.channel))
    await _insert_bindings(connection, command.bindings)
    return CatalogApplySuccess(operation_id=command.operation_id)


async def _update_channel(
    connection: AsyncConnection[Mapping[str, object]],
    command: ApplyChannelUpdate,
) -> CatalogApplyResult:
    cursor: Final = await connection.execute(_UPDATE_CHANNEL, _channel_update_parameters(command.channel))
    if cursor.rowcount != 1:
        return CatalogApplyFailure(
            operation_id=command.operation_id,
            code=CatalogApplyFailureCode.CHANNEL_NOT_FOUND,
            retryable=False,
        )
    await connection.execute(
        'DELETE FROM "LiteLLM_AccountPoolBinding" WHERE channel_id = %s',
        (str(command.channel.channel_id),),
    )
    await _insert_bindings(connection, command.bindings)
    return CatalogApplySuccess(operation_id=command.operation_id)


def _channel_update_parameters(record: ChannelRecord) -> tuple[object, ...]:
    values: Final = _channel_parameters(record)
    return (*values[1:20], record.updated_at, str(record.channel_id))


def _command_channel_id(command: CatalogLifecycleCommand) -> UUID:
    if isinstance(command, (ApplyChannelCreate, ApplyChannelUpdate, ApplyChannelImport)):
        return command.channel.channel_id
    return command.channel_id


def _allowed_operation_statuses(action: str) -> frozenset[str]:
    pending: Final = {
        "create_channel": "pending_create",
        "import_channel": "pending_create",
        "update_channel": "pending_update",
        "detach_channel": "pending_delete",
        "delete_channel": "pending_delete",
        "delete_external_deployment": "pending_delete",
    }[action]
    return frozenset((pending, "failed"))
