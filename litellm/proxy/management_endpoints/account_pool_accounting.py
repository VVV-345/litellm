"""本模块复用模型目录计价，并以幂等事务同步号池请求到标准 Usage，不保存对话正文。"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import Final, Protocol, runtime_checkable
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

import litellm
from litellm.litellm_core_utils.llm_cost_calc.utils import generic_cost_per_token
from litellm.proxy.db.daily_spend_bulk_upsert import DAILY_SPEND_TABLES, build_bulk_upsert, merge_by_conflict_key
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import FinishRequest, Lease
from litellm.types.utils import ModelInfo, Usage


@runtime_checkable
class AccountingDatabase(Protocol):
    async def execute_raw(self, query: str, *args: object) -> int: ...
    def tx(self) -> AbstractAsyncContextManager[AccountingDatabase]: ...


@runtime_checkable
class ModelLookup(Protocol):
    def get_model_info(self, id: str) -> Mapping[str, object] | None: ...


@runtime_checkable
class PriceCatalog(Protocol):
    def get(self, key: str) -> object: ...


class PriceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: str
    model_id: str
    source: str = "unknown"
    rates: dict[str, float] = Field(default_factory=dict)
    tiered_pricing: list[dict[str, JsonValue]] = Field(default_factory=list)


def price_snapshot(account_id: str, model: str, lookup: ModelLookup | None = None) -> PriceSnapshot:
    model_id: Final = str(uuid5(NAMESPACE_URL, f"litellm-account-pool:{account_id.replace('-', '')}:{model}"))
    runtime: Final = sys.modules.get("litellm.proxy.proxy_server")
    router: Final[object] = lookup or getattr(runtime, "llm_router", None)
    try:
        deployment: Final = router.get_model_info(id=model_id) if isinstance(router, ModelLookup) else None
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return PriceSnapshot(model=model, model_id=model_id)
    info: Final = TypeAdapter(dict[str, object]).validate_python((deployment or {}).get("model_info") or {})
    params: Final = TypeAdapter(dict[str, object]).validate_python((deployment or {}).get("litellm_params") or {})
    configured: Final = {
        key: value
        for section in (info, params)
        for key, value in section.items()
        if "cost" in key and isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    catalog_map: Final[object] = vars(litellm).get("model_cost")
    if not isinstance(catalog_map, PriceCatalog):
        return PriceSnapshot(model=model, model_id=model_id)
    mapped: Final = catalog_map.get(model_id)
    catalog_model: Final = model.removeprefix("openai/")
    catalog: Final = TypeAdapter(dict[str, JsonValue]).validate_python(
        mapped or catalog_map.get(model) or catalog_map.get(catalog_model) or catalog_map.get(f"openai/{model}") or {}
    )
    rates: Final = {
        key: float(value)
        for key, value in {**(catalog or {}), **configured}.items()
        if "cost" in key and isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    return PriceSnapshot(
        model=model_id if mapped and not configured else model,
        model_id=model_id,
        source="configured" if configured or mapped else "catalog" if catalog else "unknown",
        rates=rates,
        tiered_pricing=TypeAdapter(list[dict[str, JsonValue]]).validate_python(
            (catalog or {}).get("tiered_pricing") or []
        ),
    )


def estimate_cost(
    result: FinishRequest, price: PriceSnapshot, usage: dict[str, JsonValue] | None, service_tier: str | None = None
) -> FinishRequest:
    details: Final = {
        "model_id": price.model_id,
        "rates": price.rates,
        "tiered_pricing": price.tiered_pricing,
        "service_tier": service_tier,
        "currency": "USD",
    }
    if result.cost_usd is not None:
        return result.model_copy(update={"cost_source": "upstream", "cost_details": details})
    if price.source == "unknown" or usage is None or result.input_tokens is None or result.output_tokens is None:
        return result.model_copy(update={"cost_source": "unknown", "cost_details": details})
    try:
        if "input_cost_per_token" not in price.rates or "output_cost_per_token" not in price.rates:
            return result.model_copy(update={"cost_source": "unknown", "cost_details": details})
        native: Final = result.endpoint.endswith("/messages")
        tokens: Final = result.input_tokens + (
            (result.cache_read_input_tokens or 0) + (result.cache_creation_input_tokens or 0) if native else 0
        )
        if (result.cache_read_input_tokens or 0) + (result.cache_creation_input_tokens or 0) > tokens:
            return result.model_copy(update={"cost_source": "unknown", "cost_details": details})
        raw_details: Final = usage.get("prompt_tokens_details", usage.get("input_tokens_details"))
        normalized: Final = Usage.model_validate(
            {
                **usage,
                "prompt_tokens": tokens,
                "completion_tokens": result.output_tokens,
                "total_tokens": tokens + result.output_tokens,
                "completion_tokens_details": usage.get("completion_tokens_details", usage.get("output_tokens_details")),
                "prompt_tokens_details": {
                    **(raw_details if isinstance(raw_details, dict) else {}),
                    "cached_tokens": result.cache_read_input_tokens or 0,
                    "cache_write_tokens": result.cache_creation_input_tokens or 0,
                    "cache_creation_token_details": usage.get("cache_creation"),
                },
            }
        )
        input_cost, output_cost = generic_cost_per_token(
            model=price.model,
            usage=normalized,
            custom_llm_provider="anthropic" if native else "openai",
            service_tier=service_tier,
            model_info=TypeAdapter(ModelInfo).validate_python(
                {
                    "key": price.model,
                    "max_tokens": None,
                    "max_input_tokens": None,
                    "max_output_tokens": None,
                    "litellm_provider": "anthropic" if native else "openai",
                    "mode": "chat",
                    "supported_openai_params": [],
                    **price.rates,
                    "tiered_pricing": price.tiered_pricing,
                }
            ),
        )
        total: Final = input_cost + output_cost
        if not math.isfinite(total) or total < 0:
            return result.model_copy(update={"cost_source": "unknown", "cost_details": details})
        return result.model_copy(update={"cost_usd": total, "cost_source": price.source, "cost_details": details})
    except (ArithmeticError, AssertionError, AttributeError, KeyError, TypeError, ValueError):
        return result.model_copy(update={"cost_source": "unknown", "cost_details": details})


async def sync_spend(
    lease: Lease,
    result: FinishRequest,
    requested_model: str,
    key: str,
    database: AccountingDatabase | None = None,
) -> str:
    runtime: Final = sys.modules.get("litellm.proxy.proxy_server")
    prisma: Final = getattr(runtime, "prisma_client", None) if runtime else None
    db: Final[object] = database or getattr(prisma, "db", None)
    if not isinstance(db, AccountingDatabase):
        return "unavailable"
    now: Final = datetime.now(timezone.utc)
    key_hash: Final = hashlib.sha256(key.encode()).hexdigest()
    identifier: Final = f"account-pool:{lease.lease_id}"
    input_total: Final = (
        (result.input_tokens or 0) + (result.cache_read_input_tokens or 0) + (result.cache_creation_input_tokens or 0)
        if result.endpoint.endswith("/messages")
        else result.input_tokens or 0
    )
    output: Final = result.output_tokens or 0
    owner: Final = f"account-pool:{lease.key_id}"
    metadata: Final = json.dumps(
        {
            "source": "account_pool",
            "account_pool_request_id": str(lease.request_id),
            "account_pool_card_id": str(lease.card_id),
            "account_pool_account_id": str(lease.account_id),
            "account_pool_key_id": str(lease.key_id),
            "attempt": lease.attempt,
            "proxy_endpoint": result.proxy_endpoint,
            "cost_source": result.cost_source,
            "cost_known": result.cost_usd is not None,
            "status": "success" if result.http_status < 400 else "failure",
            "cost_details": result.cost_details,
            "account_pool_event_id": str(lease.lease_id),
            "usage_object": {
                "prompt_tokens": input_total,
                "completion_tokens": output,
                "cache_read_input_tokens": result.cache_read_input_tokens,
                "cache_creation_input_tokens": result.cache_creation_input_tokens,
            },
        }
    )
    daily: Final = {
        "user_id": owner,
        "date": lease.started_at.astimezone(timezone.utc).strftime("%Y-%m-%d"),
        "api_key": key_hash,
        "model": lease.model,
        "model_group": requested_model,
        "custom_llm_provider": "account_pool",
        "endpoint": result.endpoint,
        "prompt_tokens": input_total,
        "completion_tokens": output,
        "api_requests": 1,
        "successful_requests": int(result.http_status < 400),
        "failed_requests": int(result.http_status >= 400),
        "cache_read_input_tokens": result.cache_read_input_tokens or 0,
        "cache_creation_input_tokens": result.cache_creation_input_tokens or 0,
        "spend": result.cost_usd or 0,
    }
    async with db.tx() as transaction:
        inserted: Final = await transaction.execute_raw(
            'INSERT INTO "LiteLLM_SpendLogs" (request_id, call_type, api_key, spend, total_tokens, prompt_tokens, '
            'completion_tokens, "startTime", "endTime", model, model_id, model_group, custom_llm_provider, '
            '"user", metadata, session_id, status, request_tags, updated_at) '
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::timestamp, $9::timestamp, $10, $11, $12, $13, $14, "
            "$15::jsonb, $16, $17, '[\"account_pool\"]'::jsonb, NOW()) ON CONFLICT (request_id) DO NOTHING",
            identifier,
            "aresponses" if result.endpoint.endswith("/responses") else "acompletion",
            key_hash,
            result.cost_usd or 0,
            input_total + output,
            input_total,
            output,
            lease.started_at.astimezone(timezone.utc).replace(tzinfo=None).isoformat(),
            now.replace(tzinfo=None).isoformat(),
            lease.model,
            result.cost_details.get("model_id", ""),
            requested_model,
            "account_pool",
            owner,
            metadata,
            result.session_id,
            "success" if result.http_status < 400 else "failure",
        )
        if inserted:
            table: Final = DAILY_SPEND_TABLES["user"]
            sql, args = build_bulk_upsert(table, merge_by_conflict_key(table, (daily,)))
            await transaction.execute_raw(sql, *args)
    return "synced"
