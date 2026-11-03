"""持久化逐卡发布断点，在环境行锁内安装门禁，不在排空期间持有环境行锁。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from account_pool.domain import ChannelKind, EnvironmentRecord, EnvironmentStatus
from account_pool.repository import database_connection

RUNTIME_ROLLOUT_SCHEMA: Final = """CREATE TABLE IF NOT EXISTS account_pool_runtime_rollouts (
    environment_id uuid PRIMARY KEY REFERENCES account_pool_environments(id) ON DELETE CASCADE,
    phase text NOT NULL CHECK (phase IN ('draining', 'backup', 'switching', 'verified', 'failed')),
    payload jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
)"""


@dataclass(frozen=True, slots=True)
class RuntimeCheckpoint:
    environment_id: UUID
    release_id: str
    target_image: str
    phase: Literal["draining", "backup", "switching", "verified", "failed"] = "draining"
    image_verified: bool = False
    credential_state: Literal["not_checked", "preserved", "absent"] = "not_checked"
    model_inference: Literal["not_run"] = "not_run"
    failed_phase: str | None = None


CHECKPOINT: Final = TypeAdapter(RuntimeCheckpoint)


def require_rollout_ready(record: EnvironmentRecord) -> None:
    if (record.channel is not ChannelKind.CLIPROXYAPI
            or record.status in (EnvironmentStatus.PROVISIONING, EnvironmentStatus.DELETING)
            or record.configuration_pending
            or record.desired_configuration_version != record.observed_configuration_version):
        raise ValueError("runtime rollout requires an existing CLIProxyAPI card with applied configuration")


class PostgresRuntimeRolloutRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def get(self, environment_id: UUID) -> RuntimeCheckpoint | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_runtime_rollouts WHERE environment_id = %s", (environment_id,)
            )
            row: Final = await cursor.fetchone()
        return None if row is None else CHECKPOINT.validate_python(row["payload"])

    async def begin(self, environment_id: UUID, release_id: str, image: str) -> RuntimeCheckpoint:
        async with database_connection(self._database_url) as connection:
            # 与租约准入使用相同行锁，提交门禁后才能等待既有租约结算。
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_environments WHERE id = %s FOR UPDATE", (environment_id,)
            )
            row: Final = await cursor.fetchone()
            if row is None:
                raise ValueError("card not found")
            previous_cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_runtime_rollouts WHERE environment_id = %s", (environment_id,)
            )
            previous_row: Final = await previous_cursor.fetchone()
            previous: Final = None if previous_row is None else CHECKPOINT.validate_python(previous_row["payload"])
            if previous is not None:
                if (previous.release_id, previous.target_image) == (release_id, image):
                    return previous
                if previous.phase != "verified":
                    raise ValueError("unfinished rollout belongs to another release")
            require_rollout_ready(EnvironmentRecord.model_validate(row["payload"]))
            checkpoint: Final = RuntimeCheckpoint(environment_id, release_id, image)
            await connection.execute(
                "INSERT INTO account_pool_runtime_rollouts (environment_id, phase, payload) VALUES (%s, %s, %s) "
                "ON CONFLICT (environment_id) DO UPDATE SET phase = EXCLUDED.phase, "
                "payload = EXCLUDED.payload, updated_at = now()",
                (environment_id, checkpoint.phase, Jsonb(CHECKPOINT.dump_python(checkpoint, mode="json"))),
            )
        return checkpoint

    async def save(self, checkpoint: RuntimeCheckpoint) -> RuntimeCheckpoint:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "UPDATE account_pool_runtime_rollouts SET phase = %s, payload = %s, updated_at = now() "
                "WHERE environment_id = %s AND payload->>'release_id' = %s "
                "AND payload->>'target_image' = %s RETURNING environment_id",
                (checkpoint.phase, Jsonb(CHECKPOINT.dump_python(checkpoint, mode="json")),
                 checkpoint.environment_id, checkpoint.release_id, checkpoint.target_image),
            )
            if await cursor.fetchone() is None:
                raise ValueError("runtime rollout identity changed")
        return checkpoint

    async def has_leases(self, environment_id: UUID) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT 1 FROM account_pool_leases WHERE account_id = %s AND expires_at > now() LIMIT 1", (environment_id,)
            )
            return await cursor.fetchone() is not None
