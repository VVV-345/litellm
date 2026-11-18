"""PostgreSQL 人民币台账持久化，校验真实账号并保留删除账号的历史费用。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Protocol
from uuid import UUID

from psycopg.types.json import Jsonb

from account_pool.cost_models import PoolCostCreate, PoolCostRecord
from account_pool.repository import database_connection

_COST_SCHEMA: Final = (
    """CREATE TABLE IF NOT EXISTS account_pool_cost_records (
        id uuid PRIMARY KEY,
        payload jsonb NOT NULL
    )""",
)


@dataclass(frozen=True, slots=True)
class CostFailure:
    status: int
    message: str


class CostRepository(Protocol):
    async def list(self) -> tuple[PoolCostRecord, ...]: ...
    async def create(self, request: PoolCostCreate, actor: str) -> PoolCostRecord | CostFailure: ...
    async def void(self, record_id: UUID, reason: str, actor: str) -> PoolCostRecord | CostFailure: ...


def replay(record: PoolCostRecord, request: PoolCostCreate) -> PoolCostRecord | CostFailure:
    return (
        record
        if PoolCostCreate.model_validate(record.model_dump(include=set(PoolCostCreate.model_fields))) == request
        else CostFailure(409, "该录入编号已用于其他费用，请刷新后重试")
    )


class PostgresCostRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
            for statement in _COST_SCHEMA:
                await connection.execute(statement)

    async def list(self) -> tuple[PoolCostRecord, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT payload FROM account_pool_cost_records ORDER BY id")
            rows: Final = await cursor.fetchall()
            return tuple(
                sorted(
                    (PoolCostRecord.model_validate(row["payload"]) for row in rows),
                    key=lambda r: (r.paid_on, r.created_at),
                    reverse=True,
                )
            )

    async def create(self, request: PoolCostCreate, actor: str) -> PoolCostRecord | CostFailure:
        async with database_connection(self._database_url) as connection:
            previous: Final = await connection.execute(
                "SELECT payload FROM account_pool_cost_records WHERE id = %s", (request.id,)
            )
            previous_row: Final = await previous.fetchone()
            if previous_row is not None:
                return replay(PoolCostRecord.model_validate(previous_row["payload"]), request)
            account: Final = await connection.execute(
                "SELECT payload->>'name' AS name FROM account_pool_environments WHERE id = %s "
                "AND payload->>'status' <> 'deleting' FOR SHARE",
                (request.account_id,),
            )
            account_row: Final = await account.fetchone()
            if request.account_id is not None and account_row is None:
                return CostFailure(404, "账号不存在或正在删除，无法录入订阅费用")
            record: Final = PoolCostRecord(
                id=request.id,
                category=request.category,
                account_id=request.account_id,
                name=request.name,
                amount_cents=request.amount_cents,
                currency=request.currency,
                paid_on=request.paid_on,
                starts_on=request.starts_on,
                ends_on=request.ends_on,
                note=request.note,
                account_name=str(account_row["name"]) if account_row else None,
                created_at=datetime.now(timezone.utc),
                created_by=actor,
            )
            await connection.execute(
                "INSERT INTO account_pool_cost_records (id, payload) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                (record.id, Jsonb(record.model_dump(mode="json"))),
            )
            saved: Final = await connection.execute(
                "SELECT payload FROM account_pool_cost_records WHERE id = %s", (record.id,)
            )
            row: Final = await saved.fetchone()
            if row is None:
                return CostFailure(503, "费用保存失败，请重试")
            return replay(PoolCostRecord.model_validate(row["payload"]), request)

    async def void(self, record_id: UUID, reason: str, actor: str) -> PoolCostRecord | CostFailure:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_cost_records WHERE id = %s FOR UPDATE", (record_id,)
            )
            row: Final = await cursor.fetchone()
            if row is None:
                return CostFailure(404, "费用记录不存在")
            record: Final = PoolCostRecord.model_validate(row["payload"])
            if record.voided_at is not None:
                return record
            updated: Final = record.model_copy(
                update={"voided_at": datetime.now(timezone.utc), "voided_by": actor, "void_reason": reason}
            )
            await connection.execute(
                "UPDATE account_pool_cost_records SET payload = %s WHERE id = %s",
                (Jsonb(updated.model_dump(mode="json")), record_id),
            )
            return updated
