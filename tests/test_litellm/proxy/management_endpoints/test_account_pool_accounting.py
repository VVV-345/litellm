"""本文件验证模型价格快照、缓存计价和 Spend 与每日用量的原子去重。"""

from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Final
from uuid import NAMESPACE_URL, uuid4, uuid5

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


@pytest.mark.parametrize(
    "overrides,service_tier,prompt_tokens,expected",
    [
        ({}, None, 100, 0.208),
        ({"input_cost_per_token": 0.002}, None, 100, 0.228),
        ({"output_cost_per_token": 0.004}, None, 100, 0.048),
        ({"cache_read_input_token_cost": 0}, None, 100, 0.2),
        ({"input_cost_per_token": 0, "output_cost_per_token": 0, "cache_read_input_token_cost": 0}, None, 100, 0),
        ({}, "priority", 100, 0.416),
        ({}, None, 300000, 600.832),
        (
            {
                "tiered_pricing": [
                    {
                        "range": [0, 1000000],
                        "input_cost_per_token": 0.003,
                        "output_cost_per_token": 0.01,
                        "cache_read_input_token_cost": 0.0002,
                    }
                ]
            },
            None,
            100,
            0.126,
        ),
    ],
)
def test_model_table_native_spend_and_full_log_share_deployment_prices(
    monkeypatch, overrides, service_tier, prompt_tokens, expected
) -> None:
    import litellm
    from litellm import Router
    from litellm.proxy.proxy_server import _enrich_model_info_with_litellm_data
    from litellm.utils import _invalidate_model_cost_lowercase_map

    account: Final = uuid4()
    identifier: Final = str(uuid5(NAMESPACE_URL, f"litellm-account-pool:{account.hex}:billing-alias"))
    published: Final = {
        "input_cost_per_token": 0.001,
        "output_cost_per_token": 0.036,
        "cache_read_input_token_cost": 0.0001,
        "input_cost_per_token_priority": 0.002,
        "output_cost_per_token_priority": 0.072,
        "cache_read_input_token_cost_priority": 0.0002,
        "input_cost_per_token_above_272k_tokens": 0.002,
        "output_cost_per_token_above_272k_tokens": 0.192,
        "cache_read_input_token_cost_above_272k_tokens": 0.0004,
        "litellm_provider": "openai",
        "mode": "chat",
    }
    monkeypatch.setattr(litellm, "model_cost", litellm.model_cost.copy())
    litellm.register_model(
        {
            "openai/pool-upstream-alias": {**published, "cache_read_input_token_cost": 0},
            "openai/pool-catalog-model": published,
            "openai/pool-response-model": {**published, "input_cost_per_token": 1e-9},
        },
        persist_across_reloads=False,
    )
    router: Final = Router(
        model_list=[
            {
                "model_name": "billing-alias",
                "litellm_params": {"model": "openai/pool-upstream-alias", "api_key": "test", **overrides},
                "model_info": {
                    "id": identifier,
                    "managed_by": "account_pool",
                    "account_pool_environment_id": str(account),
                    "base_model": "openai/pool-catalog-model",
                },
            }
        ]
    )
    try:
        response: Final = litellm.ModelResponse(
            model="pool-response-model",
            usage=litellm.Usage(
                prompt_tokens=prompt_tokens, completion_tokens=5, prompt_tokens_details={"cached_tokens": 80}
            ),
        )
        displayed: Final = _enrich_model_info_with_litellm_data(deepcopy(router.model_list[0]))["model_info"]
        snapshot: Final = price_snapshot(str(account), "billing-alias", router)
        for key in ("input_cost_per_token", "output_cost_per_token", "cache_read_input_token_cost"):
            assert snapshot.rates[key] == displayed[key]
        cost: Final = litellm.completion_cost(
            completion_response=response,
            model="openai/pool-upstream-alias",
            custom_llm_provider="openai",
            router_model_id=identifier,
            service_tier=service_tier,
        )
        assert cost == pytest.approx(expected)
        assert response.model == "pool-response-model"
        full_log: Final = estimate_cost(
            FinishRequest(
                lease_id=uuid4(),
                http_status=200,
                message="done",
                endpoint="/v1/responses",
                input_tokens=prompt_tokens,
                output_tokens=5,
                cache_read_input_tokens=80,
            ),
            snapshot,
            {"input_tokens": prompt_tokens, "output_tokens": 5},
            service_tier,
        )
        assert full_log.cost_usd == pytest.approx(cost)

        litellm.model_cost = {"openai/pool-catalog-model": {**published, "output_cost_per_token": 0.072}}
        _invalidate_model_cost_lowercase_map()
        router._replay_model_cost_registrations()
        refreshed: Final = price_snapshot(str(account), "billing-alias", router)
        assert refreshed.rates["output_cost_per_token"] == overrides.get("output_cost_per_token", 0.072)
        assert snapshot.rates["output_cost_per_token"] == overrides.get("output_cost_per_token", 0.036)
    finally:
        router.discard()


def test_unpriced_pool_does_not_fall_back_to_a_cheaper_response_model(monkeypatch) -> None:
    import litellm
    from litellm import Router

    monkeypatch.setattr(litellm, "model_cost", litellm.model_cost.copy())
    litellm.register_model(
        {
            "openai/pool-cheap-test": {
                "input_cost_per_token": 1e-9,
                "output_cost_per_token": 1e-9,
                "litellm_provider": "openai",
                "mode": "chat",
            }
        },
        persist_across_reloads=False,
    )
    router: Final = Router(
        model_list=[
            {
                "model_name": "unpriced-pool",
                "litellm_params": {"model": "openai/unpriced-pool", "api_key": "test"},
                "model_info": {"id": "unpriced-pool-deployment", "managed_by": "account_pool"},
            }
        ]
    )
    response: Final = litellm.ModelResponse(
        model="pool-cheap-test",
        usage=litellm.Usage(prompt_tokens=100, completion_tokens=5),
    )
    try:
        with pytest.raises(ValueError, match="incomplete token pricing"):
            litellm.completion_cost(
                completion_response=response,
                model="openai/unpriced-pool",
                router_model_id="unpriced-pool-deployment",
                custom_llm_provider="openai",
            )
        assert litellm.completion_cost(
            completion_response=response, model="openai/unpriced-pool", custom_llm_provider="openai"
        ) == pytest.approx(105e-9)
    finally:
        router.discard()


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
