"""PostgreSQL 人民币台账持久化，校验真实账号并保留删除账号的历史费用。"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Final, Protocol
from uuid import UUID, uuid5

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from account_pool.cost_models import PoolCostCreate, PoolCostRecord
from account_pool.cost_renewal import cycle_end
from account_pool.repository import database_connection

_COST_SCHEMA: Final = (
    """CREATE TABLE IF NOT EXISTS account_pool_cost_records (
        id uuid PRIMARY KEY,
        payload jsonb NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS account_pool_cost_renewals (
        id uuid PRIMARY KEY REFERENCES account_pool_cost_records(id),
        auto_renew boolean NOT NULL DEFAULT false,
        cycle_months integer CHECK (cycle_months IN (1, 3, 12)),
        renewal_of uuid UNIQUE REFERENCES account_pool_cost_records(id)
    )""",
)
_METADATA_FIELDS: Final = {"auto_renew", "cycle_months", "renewal_of"}
_RECORD_QUERY: Final = (
    "SELECT c.payload || jsonb_build_object('auto_renew', COALESCE(r.auto_renew, false), "
    "'cycle_months', r.cycle_months, 'renewal_of', r.renewal_of) AS payload "
    "FROM account_pool_cost_records c LEFT JOIN account_pool_cost_renewals r ON r.id = c.id "
)


@dataclass(frozen=True, slots=True)
class CostFailure:
    status: int
    message: str


class CostRepository(Protocol):
    async def list(self) -> tuple[PoolCostRecord, ...]: ...
    async def create(self, request: PoolCostCreate, actor: str) -> PoolCostRecord | CostFailure: ...
    async def replace(self, record_id: UUID, request: PoolCostCreate, actor: str) -> PoolCostRecord | CostFailure: ...
    async def void(self, record_id: UUID, reason: str, actor: str) -> PoolCostRecord | CostFailure: ...
    async def stop_renewal(self, record_id: UUID) -> PoolCostRecord | CostFailure: ...


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
            cursor: Final = await connection.execute(_RECORD_QUERY + "ORDER BY c.id")
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
            await connection.execute("SELECT pg_advisory_xact_lock(741935802)")
            return await self._create(connection, request, actor)

    async def _create(
        self, connection: AsyncConnection[Mapping[str, object]], request: PoolCostCreate, actor: str
    ) -> PoolCostRecord | CostFailure:
        previous: Final = await connection.execute(_RECORD_QUERY + "WHERE c.id = %s", (request.id,))
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
            auto_renew=request.auto_renew,
            cycle_months=request.cycle_months,
            account_name=str(account_row["name"]) if account_row else None,
            created_at=datetime.now(timezone.utc),
            created_by=actor,
        )
        if request.account_id is not None:
            await connection.execute(
                "UPDATE account_pool_cost_renewals SET auto_renew = false WHERE id IN "
                "(SELECT id FROM account_pool_cost_records WHERE payload->>'account_id' = %s)",
                (str(request.account_id),),
            )
        await connection.execute(
            "INSERT INTO account_pool_cost_records (id, payload) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (record.id, Jsonb(record.model_dump(mode="json", exclude=_METADATA_FIELDS))),
        )
        await connection.execute(
            "INSERT INTO account_pool_cost_renewals (id, auto_renew, cycle_months) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (record.id, record.auto_renew, record.cycle_months),
        )
        saved: Final = await connection.execute(_RECORD_QUERY + "WHERE c.id = %s", (record.id,))
        row: Final = await saved.fetchone()
        if row is None:
            return CostFailure(503, "费用保存失败，请重试")
        return replay(PoolCostRecord.model_validate(row["payload"]), request)

    async def replace(self, record_id: UUID, request: PoolCostCreate, actor: str) -> PoolCostRecord | CostFailure:
        async with database_connection(self._database_url) as connection:
            # 更正、续期及作废共享事务锁，新记录与旧记录作废同时提交，避免费用重复或丢失。
            await connection.execute("SELECT pg_advisory_xact_lock(741935802)")
            cursor: Final = await connection.execute(_RECORD_QUERY + "WHERE c.id = %s FOR UPDATE OF c", (record_id,))
            row: Final = await cursor.fetchone()
            if row is None:
                return CostFailure(404, "费用记录不存在")
            original: Final = PoolCostRecord.model_validate(row["payload"])
            reason: Final = f"重新录入：{request.id}"
            if original.voided_at is not None:
                saved: Final = await connection.execute(_RECORD_QUERY + "WHERE c.id = %s", (request.id,))
                saved_row: Final = await saved.fetchone()
                if original.void_reason == reason and saved_row is not None:
                    return replay(PoolCostRecord.model_validate(saved_row["payload"]), request)
                return CostFailure(409, "原费用已更正或作废，请刷新后重试")
            if request.id == record_id or (request.category, request.account_id) != (
                original.category,
                original.account_id,
            ):
                return CostFailure(422, "重新录入不能更改费用归属或复用原编号")
            duplicate: Final = await connection.execute(
                "SELECT id FROM account_pool_cost_records WHERE id = %s", (request.id,)
            )
            if await duplicate.fetchone() is not None:
                return CostFailure(409, "该录入编号已用于其他费用，请刷新后重试")
            child: Final = await connection.execute(
                "SELECT id FROM account_pool_cost_renewals WHERE renewal_of = %s", (record_id,)
            )
            if await child.fetchone() is not None:
                return CostFailure(409, "该费用已生成下一期，请刷新后重新录入最近一期")
            replacement: Final = await self._create(connection, request, actor)
            if isinstance(replacement, CostFailure):
                return replacement
            await self._void(connection, original, reason, actor)
            return replacement

    async def void(self, record_id: UUID, reason: str, actor: str) -> PoolCostRecord | CostFailure:
        async with database_connection(self._database_url) as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(741935802)")
            cursor: Final = await connection.execute(_RECORD_QUERY + "WHERE c.id = %s FOR UPDATE OF c", (record_id,))
            row: Final = await cursor.fetchone()
            if row is None:
                return CostFailure(404, "费用记录不存在")
            record: Final = PoolCostRecord.model_validate(row["payload"])
            if record.voided_at is not None:
                return record
            return await self._void(connection, record, reason, actor)

    async def _void(
        self, connection: AsyncConnection[Mapping[str, object]], record: PoolCostRecord, reason: str, actor: str
    ) -> PoolCostRecord:
        updated: Final = record.model_copy(
            update={
                "voided_at": datetime.now(timezone.utc),
                "voided_by": actor,
                "void_reason": reason,
                "auto_renew": False,
            }
        )
        await connection.execute(
            "UPDATE account_pool_cost_records SET payload = %s WHERE id = %s",
            (Jsonb(updated.model_dump(mode="json", exclude=_METADATA_FIELDS)), record.id),
        )
        await connection.execute(
            "WITH RECURSIVE chain AS (SELECT id FROM account_pool_cost_renewals WHERE id = %s "
            "UNION ALL SELECT r.id FROM account_pool_cost_renewals r JOIN chain ON r.renewal_of = chain.id) "
            "UPDATE account_pool_cost_renewals SET auto_renew = false WHERE id IN (SELECT id FROM chain)",
            (record.id,),
        )
        return updated

    async def stop_renewal(self, record_id: UUID) -> PoolCostRecord | CostFailure:
        async with database_connection(self._database_url) as connection:
            # 与续期事务使用同一把锁，关闭成功后不会再生成下一期。
            await connection.execute("SELECT pg_advisory_xact_lock(741935802)")
            cursor: Final = await connection.execute(_RECORD_QUERY + "WHERE c.id = %s", (record_id,))
            row: Final = await cursor.fetchone()
            if row is None:
                return CostFailure(404, "费用记录不存在")
            await connection.execute(
                "WITH RECURSIVE chain AS (SELECT id FROM account_pool_cost_renewals WHERE id = %s "
                "UNION ALL SELECT r.id FROM account_pool_cost_renewals r JOIN chain ON r.renewal_of = chain.id) "
                "UPDATE account_pool_cost_renewals SET auto_renew = false WHERE id IN (SELECT id FROM chain)",
                (record_id,),
            )
            return PoolCostRecord.model_validate(row["payload"]).model_copy(update={"auto_renew": False})

    async def renew_due(self, today: date) -> None:
        async with database_connection(self._database_url) as connection:
            # 多进程、重复执行及关闭续期共用事务锁；父记录唯一引用阻止重复入账。
            await connection.execute("SELECT pg_advisory_xact_lock(741935802)")
            for _ in range(1200):
                if not await self._renew_one(connection, today):
                    return

    async def _renew_one(self, connection: AsyncConnection[Mapping[str, object]], today: date) -> bool:
        cursor: Final = await connection.execute(
            _RECORD_QUERY + "JOIN account_pool_environments e ON e.id = (c.payload->>'account_id')::uuid "
            "WHERE r.auto_renew AND c.payload->>'voided_at' IS NULL "
            "AND (c.payload->>'ends_on')::date < %s AND e.payload->>'status' <> 'deleting' "
            "AND NOT EXISTS (SELECT 1 FROM account_pool_cost_renewals child WHERE child.renewal_of = c.id) "
            "ORDER BY c.id LIMIT 1 FOR SHARE OF e FOR UPDATE OF c",
            (today,),
        )
        row: Final = await cursor.fetchone()
        if row is None:
            return False
        previous: Final = PoolCostRecord.model_validate(row["payload"])
        if previous.cycle_months is None:
            return False
        start: Final = previous.ends_on + timedelta(days=1)
        record: Final = previous.model_copy(
            update={
                "id": uuid5(previous.id, "automatic-renewal"),
                "starts_on": start,
                "ends_on": cycle_end(start, previous.cycle_months),
                "paid_on": start,
                "renewal_of": previous.id,
                "created_at": datetime.now(timezone.utc),
                "created_by": "system:auto-renewal",
            }
        )
        await connection.execute(
            "INSERT INTO account_pool_cost_records (id, payload) VALUES (%s, %s)",
            (record.id, Jsonb(record.model_dump(mode="json", exclude=_METADATA_FIELDS))),
        )
        await connection.execute(
            "INSERT INTO account_pool_cost_renewals (id, auto_renew, cycle_months, renewal_of) "
            "VALUES (%s, true, %s, %s)",
            (record.id, record.cycle_months, previous.id),
        )
        return True
