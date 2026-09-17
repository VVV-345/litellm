"""本文件验证模型价格快照、缓存计价和 Spend 与每日用量的原子去重。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Final
from uuid import uuid4

import pytest

from litellm.proxy.management_endpoints.account_pool_accounting import (
    PriceSnapshot,
    estimate_cost,
    price_snapshot,
    sync_spend,
)
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import FinishRequest, Lease


@pytest.mark.parametrize("native,input_count", [(False, 100), (True, 10)])
def test_cached_input_is_not_charged_twice(native: bool, input_count: int) -> None:
    result: Final = FinishRequest(
        lease_id=uuid4(),
        http_status=200,
        message="done",
        endpoint="/v1/messages" if native else "/v1/responses",
        input_tokens=input_count,
        output_tokens=5,
        cache_read_input_tokens=80,
        cache_creation_input_tokens=10,
    )
    price: Final = PriceSnapshot(
        model="model-a",
        model_id="deployment-1",
        source="configured",
        rates={
            "input_cost_per_token": 0.001,
            "output_cost_per_token": 0.004,
            "cache_read_input_token_cost": 0.0001,
            "cache_creation_input_token_cost": 0.002,
        },
    )
    priced: Final = estimate_cost(result, price, {"input_tokens": input_count, "output_tokens": 5})
    assert priced.cost_usd == pytest.approx(0.01 + 0.008 + 0.02 + 0.02)
    assert priced.cost_details["rates"] == price.rates


def test_unknown_model_and_missing_usage_do_not_become_free() -> None:
    result: Final = FinishRequest(lease_id=uuid4(), http_status=200, message="done", endpoint="/v1/responses")
    priced: Final = estimate_cost(result, PriceSnapshot(model="unknown", model_id="unknown"), None)
    assert priced.cost_usd is None
    assert priced.cost_source == "unknown"


def test_price_lookup_is_bound_to_actual_deployment_and_freezes_rates() -> None:
    class Lookup:
        def __init__(self) -> None:
            self.model = {
                "model_info": {"input_cost_per_token": 0.002, "output_cost_per_token": 0.007},
                "litellm_params": {"api_key": "never-store-this"},
            }

        def get_model_info(self, id: str) -> Mapping[str, object]:
            assert id
            return self.model

    lookup: Final = Lookup()
    snapshot: Final = price_snapshot(str(uuid4()), "unknown-test-model", lookup)
    lookup.model["model_info"]["input_cost_per_token"] = 0.1
    assert snapshot.rates["input_cost_per_token"] == 0.002
    assert "never-store-this" not in snapshot.model_dump_json()


def test_partial_deployment_catalog_retains_builtin_token_rates(monkeypatch) -> None:
    from uuid import NAMESPACE_URL, uuid5

    import litellm

    account: Final = uuid4()
    model: Final = "audit-priced-model"
    model_id: Final = str(uuid5(NAMESPACE_URL, f"litellm-account-pool:{account.hex}:{model}"))
    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            model_id: {"litellm_provider": "openai", "cache_read_input_token_cost": 0},
            model: {"input_cost_per_token": 0.002, "output_cost_per_token": 0.003},
        },
    )
    price: Final = price_snapshot(str(account), model)
    assert price.rates["input_cost_per_token"] == 0.002
    assert price.rates["output_cost_per_token"] == 0.003
    assert price.rates["cache_read_input_token_cost"] == 0


class Database:
    def __init__(self) -> None:
        self.ids: set[object] = set()
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail_rollup = False

    @asynccontextmanager
    async def tx(self) -> AsyncGenerator[Database]:
        original: Final = self.ids.copy()
        try:
            yield self
        except Exception:
            self.ids = original
            raise

    async def execute_raw(self, query: str, *args: object) -> int:
        self.calls.append((query, args))
        if 'INSERT INTO "LiteLLM_SpendLogs"' in query:
            if args[0] in self.ids:
                return 0
            self.ids.add(args[0])
            return 1
        if self.fail_rollup:
            raise RuntimeError("rollup failed")
        return 1


@pytest.mark.asyncio
async def test_duplicate_finish_and_retry_attempts_do_not_double_count_spend() -> None:
    db: Final = Database()
    lease: Final = Lease(
        lease_id=uuid4(),
        request_id=uuid4(),
        card_id=uuid4(),
        account_id=uuid4(),
        key_id=uuid4(),
        channel="cliproxyapi",
        supplier="openai_codex",
        model="model-a",
        started_at=datetime.now(timezone.utc),
    )
    result: Final = FinishRequest(
        lease_id=lease.lease_id,
        http_status=200,
        message="done",
        endpoint="/v1/responses",
        input_tokens=100,
        output_tokens=5,
        cost_usd=0.123,
        cost_source="configured",
    )
    assert await sync_spend(lease, result, "alias", "private-key", db) == "synced"
    await sync_spend(lease, result, "alias", "private-key", db)
    assert len([query for query, _ in db.calls if 'INSERT INTO "LiteLLM_DailyUserSpend"' in query]) == 1
    assert "private-key" not in str(db.calls)
    assert json.loads(str(db.calls[0][1][14]))["status"] == "success"
    retry: Final = lease.model_copy(update={"lease_id": uuid4(), "attempt": 2})
    await sync_spend(retry, result, "alias", "private-key", db)
    assert len(db.ids) == 2
    db.fail_rollup = True
    new_lease: Final = lease.model_copy(update={"lease_id": uuid4()})
    with pytest.raises(RuntimeError):
        await sync_spend(new_lease, result, "alias", "private-key", db)
    assert len(db.ids) == 2
