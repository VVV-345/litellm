"""从 LiteLLM 标准调用记录生成账号指标，不重复记账或读取供应商额度。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Final, Literal, Protocol, runtime_checkable
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from litellm.proxy.management_endpoints.account_pool_management_models import ErrorStats


class StatsQuery(Protocol):
    async def __call__(self, sql: str, since: datetime) -> object: ...


_STANDARD_STATS_SQL: Final = """
WITH attributed AS (
    SELECT s.*, coalesce(
        s.metadata::jsonb #>> '{spend_logs_metadata,account_pool_account_id}',
        s.metadata::jsonb #>> '{spend_logs_metadata,account_pool_card_id}',
        s.metadata::jsonb ->> 'account_pool_card_id',
        m.model_info::jsonb ->> 'account_pool_environment_id'
    ) AS account_id,
    coalesce(s.metadata::jsonb #>> '{spend_logs_metadata,account_pool_request_id}',
             s.metadata::jsonb ->> 'account_pool_request_id', s.request_id) AS pool_request_id
    FROM "LiteLLM_SpendLogs" s
    LEFT JOIN "LiteLLM_ProxyModelTable" m ON m.model_id = s.model_id
    WHERE s."startTime" >= $1
), final_requests AS (
    SELECT DISTINCT ON (pool_request_id) * FROM attributed
    WHERE account_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ORDER BY pool_request_id, (request_id LIKE 'account-pool:%'), "endTime" DESC
)
SELECT account_id, count(*)::integer AS total_requests,
    count(*) FILTER (WHERE status = 'success')::integer AS succeeded_requests,
    count(*) FILTER (WHERE status = 'failure')::integer AS failed_requests,
    count(*) FILTER (WHERE (metadata::jsonb #>> '{spend_logs_metadata,account_pool_attempt_count}')::integer > 1)::integer
        AS retried_requests,
    coalesce(sum(prompt_tokens), 0)::bigint AS input_tokens,
    coalesce(sum(completion_tokens), 0)::bigint AS output_tokens,
    coalesce(sum((metadata::jsonb #>> '{additional_usage_values,cache_read_input_tokens}')::bigint), 0)::bigint
        AS cache_read_input_tokens,
    coalesce(sum((metadata::jsonb #>> '{additional_usage_values,cache_creation_input_tokens}')::bigint), 0)::bigint
        AS cache_creation_input_tokens,
    coalesce(sum(spend), 0)::double precision AS total_cost_usd,
    count(*) FILTER (WHERE spend IS NOT NULL)::integer AS known_cost_requests,
    avg(coalesce(request_duration_ms,
        extract(epoch FROM ("endTime" - "startTime")) * 1000))::double precision AS average_duration_ms
FROM final_requests GROUP BY GROUPING SETS ((account_id), ())
"""


class StandardAccountStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: UUID | None = None
    total_requests: int = Field(ge=0)
    succeeded_requests: int = Field(ge=0)
    failed_requests: int = Field(ge=0)
    retried_requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(ge=0, allow_inf_nan=False)
    known_cost_requests: int = Field(ge=0)
    average_duration_ms: float | None = None

    def public(self) -> ErrorStats:
        return ErrorStats.model_validate(
            MappingProxyType(
                {
                    **self.model_dump(),
                    "card_id": self.account_id,
                    "statistics_source": "litellm",
                    "cache_rate": min(self.cache_read_input_tokens / self.input_tokens, 1)
                    if self.input_tokens
                    else None,
                }
            )
        )


class AccountPoolDashboardStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: ErrorStats
    cards: tuple[ErrorStats, ...]
    statistics_source: Literal["litellm"] = "litellm"
    occurred_from: datetime


@runtime_checkable
class StatisticsDatabase(Protocol):
    async def query_raw(self, sql: str, since: datetime) -> object: ...


async def query_standard_stats(sql: str, since: datetime) -> object:
    from litellm.proxy.proxy_server import prisma_client

    if prisma_client is None:
        raise HTTPException(503, "LiteLLM statistics database is unavailable")
    database: Final = prisma_client.db
    if not isinstance(database, StatisticsDatabase):
        raise HTTPException(503, "LiteLLM statistics database is unavailable")
    try:
        return await database.query_raw(sql, since)
    except Exception as exc:
        raise HTTPException(503, "LiteLLM statistics database is unavailable") from exc


async def standard_dashboard(
    query: StatsQuery = query_standard_stats, *, now: datetime | None = None
) -> AccountPoolDashboardStats:
    since: Final = (now or datetime.now(timezone.utc)) - timedelta(days=30)
    rows: Final = TypeAdapter(tuple[StandardAccountStats, ...]).validate_python(await query(_STANDARD_STATS_SQL, since))
    return AccountPoolDashboardStats(
        summary=next((row.public() for row in rows if row.account_id is None), ErrorStats(statistics_source="litellm")),
        cards=tuple(row.public() for row in rows if row.account_id is not None),
        occurred_from=since,
    )
