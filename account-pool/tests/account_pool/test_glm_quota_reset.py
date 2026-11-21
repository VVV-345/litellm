"""只用合成供应商响应验证 GLM 手动重置，不连接真实账号或消耗权益。"""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from typing import Final

import httpx
import pytest

from account_pool.domain import utc_now
from account_pool.providers.glm_quota_reset import GLMResetClient
from account_pool.quota_reset import QuotaResetService
from account_pool.shared.secrets import EnvironmentSecretDeriver
from test_openai_compatible import _compatible_card, public_resolver
from test_quota_reset import setup_service, Environments

SECRETS: Final = EnvironmentSecretDeriver("s" * 32)


def card(base: str = "https://open.bigmodel.cn/api/coding/paas/v4"):
    return _compatible_card(base, SECRETS).model_copy(update={"credential_fingerprints": ("fixture-key",), "enabled_models": ("model-a",)})


def vendor(cards=None, percentage=100, weekly=0):
    expires = (utc_now() + timedelta(days=2)).isoformat()
    inventory = {
        "customerId": 12345678901234567,
        "targetType": "PERSONAL",
        "fiveHourResets": [{"recordId": 123, "grantType": "DIRECT", "expireTime": expires, "available": True}],
        "weekResets": [{"recordId": 456, "grantType": "GLOBAL", "expireTime": expires, "available": True}],
    }
    if cards is not None:
        inventory.update(cards)
    used = []

    def handle(request):
        if request.url.path.endswith("/quota/limit"):
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "success": True,
                    "data": {
                        "limits": [
                            {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": percentage},
                            {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "percentage": weekly},
                            {"type": "TIME_LIMIT", "unit": 5, "number": 1, "percentage": 100},
                        ]
                    },
                },
            )
        if request.url.path.endswith("/list"):
            assert request.method == "GET" and request.url.params["targetType"] == "PERSONAL"
            return httpx.Response(200, json={"code": 200, "success": True, "data": inventory})
        if request.url.path.endswith("/use"):
            body = json.loads(request.content)
            used.append((body, request.headers["Authorization"]))
            return httpx.Response(200, json={"code": 200, "success": True, "data": body["recordId"]})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
        raise AssertionError("Unexpected vendor path")

    return handle, used


@pytest.mark.asyncio
@pytest.mark.parametrize("issuer", ["open.bigmodel.cn", "api.z.ai"])
async def test_glm_lists_safe_cards_and_sends_exact_confirmed_record(issuer):
    handle, used = vendor()
    record = card(f"https://{issuer}/api/coding/paas/v4")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = GLMResetClient(http, SECRETS, public_resolver)
        service, _, store, _ = setup_service()
        service = replace(service, environments=Environments(record), upstream=client)
        observation = await client.observe(record)
        assert observation.available == 2 and len(observation.cards) == 2
        assert observation.credit_id == "123" and len(observation.provider_scope) == 64
        operation = await service.prepare(record.id, None, "123")
        results = await asyncio.gather(*(service.consume(record.id, operation.request_id) for _ in range(5)))
        assert all(result.status == "reset" for result in results)
        assert len(used) == 1
        assert used[0][0] == {
            "targetType": "PERSONAL",
            "resetType": "FIVE_HOUR",
            "recordId": 123,
            "grantType": "DIRECT",
            "requestId": str(operation.request_id),
        }
        assert used[0][1] == ("synthetic-glm-key" if issuer == "open.bigmodel.cn" else "Bearer synthetic-glm-key")
        assert "synthetic-glm-key" not in operation.model_dump_json()


@pytest.mark.asyncio
async def test_weekly_is_selected_without_spending_a_five_hour_card_and_mcp_does_not_block_models():
    handle, used = vendor(percentage=0, weekly=100)
    record = card()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = GLMResetClient(http, SECRETS, public_resolver)
        service, _, _, _ = setup_service()
        service = replace(service, environments=Environments(record), upstream=client)
        observation = await client.observe(record)
        assert observation.credit_id == "456"
        with pytest.raises(ValueError):
            await service.prepare(record.id, None, "123")
        operation = await service.prepare(record.id, None, "456")
        await service.consume(record.id, operation.request_id)
        assert used[0][0]["resetType"] == "WEEK" and used[0][0]["recordId"] == 456


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"recordId": 0},
        {"recordId": None},
        {"grantType": ""},
        {"expireTime": "not a date"},
        {"expireTime": "2000-01-01 00:00:00"},
        {"available": False},
    ],
)
async def test_unsafe_expired_or_unavailable_records_cannot_be_prepared(invalid):
    base = {"recordId": 123, "grantType": "DIRECT", "expireTime": "2099-01-01 00:00:00", "available": True}
    handle, used = vendor({"fiveHourResets": [{**base, **invalid}], "weekResets": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = GLMResetClient(http, SECRETS, public_resolver)
        observation = await client.observe(card())
        assert observation.available == 0 and not observation.eligible
        assert not used


@pytest.mark.asyncio
async def test_one_vendor_account_cannot_redeem_the_same_card_through_two_environment_keys():
    handle, used = vendor()
    service, _, store, _ = setup_service()
    first, second = card(), card()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = GLMResetClient(http, SECRETS, public_resolver)
        a = replace(service, environments=Environments(first), upstream=client)
        b = replace(service, environments=Environments(second), upstream=client)
        oa, ob = await asyncio.gather(a.prepare(first.id, None), b.prepare(second.id, None))
        results = await asyncio.gather(
            a.consume(first.id, oa.request_id), b.consume(second.id, ob.request_id), return_exceptions=True
        )
        assert len(used) == 1
        assert sum(isinstance(result, ValueError) for result in results) == 1
        assert len(store.claims) == 1


@pytest.mark.asyncio
async def test_uncertain_redemption_never_reposts_after_client_restart():
    handle, used = vendor()
    calls = []

    def fail_use(request):
        if request.url.path.endswith("/use"):
            calls.append(request)
            raise httpx.ReadTimeout("PRIVATE")
        return handle(request)

    record = card()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail_use)) as http:
        client = GLMResetClient(http, SECRETS, public_resolver)
        service, _, _, _ = setup_service()
        service = replace(service, environments=Environments(record), upstream=client)
        operation = await service.prepare(record.id, None)
        assert (await service.consume(record.id, operation.request_id)).status == "uncertain"
        restarted = replace(service, upstream=GLMResetClient(http, SECRETS, public_resolver))
        assert (await restarted.consume(record.id, operation.request_id)).status == "uncertain"
        assert len(calls) == 1 and not used


@pytest.mark.asyncio
async def test_glm_verification_uses_same_coding_key_and_does_not_treat_mcp_as_model_quota():
    handle, _ = vendor(percentage=0, weekly=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await GLMResetClient(http, SECRETS, public_resolver).verify(card(), "model-a", True)
        assert result.passed and result.restored


@pytest.mark.asyncio
async def test_glm_verified_recovery_updates_display_quota_without_clearing_other_limits():
    from account_pool.domain import QuotaSnapshot, QuotaWindow

    record = card().model_copy(
        update={
            "display_quota": QuotaSnapshot(
                windows=(QuotaWindow(name="5 hour", window_minutes=300, used_percent=100, remaining_percent=0),)
            )
        }
    )
    handle, _ = vendor(percentage=0, weekly=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        service, _, _, _ = setup_service()
        environments = Environments(record)
        service = replace(service, environments=environments, upstream=GLMResetClient(http, SECRETS, public_resolver))
        result = await service.verify(record.id, "model-a", True)
        assert result.passed and result.restored
        assert environments.record.display_quota.windows[0].remaining_percent == 100
        assert environments.record.quota == record.quota


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", [{"targetType": "TEAM"}, {"customerId": None}, {"customerId": 0}])
async def test_unverified_account_or_team_scope_is_rejected_without_post(mutate):
    handle, used = vendor(mutate)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ValueError):
            await GLMResetClient(http, SECRETS, public_resolver).observe(card())
        assert not used


@pytest.mark.asyncio
async def test_glm_http_flow_exposes_cards_but_requires_explicit_confirmation_before_use():
    from fastapi import FastAPI
    from account_pool.quota_reset_api import create_quota_reset_router

    handle, used = vendor()
    record = card()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream_http:
        service, _, _, _ = setup_service()
        service = replace(service, environments=Environments(record), upstream=GLMResetClient(upstream_http, SECRETS, public_resolver))
        app = FastAPI()
        app.include_router(create_quota_reset_router(service, lambda: None))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture.test") as http:
            root = f"/api/environments/{record.id}/quota-reset"
            observation = await http.get(root)
            assert observation.status_code == 200
            assert len(observation.json()["observation"]["cards"]) == 2
            assert "synthetic-glm-key" not in observation.text and "customerId" not in observation.text
            prepared = await http.post(root + "/prepare", json={"credit_id": "123"})
            assert prepared.status_code == 200 and prepared.json()["credit_id"] == "123"
            request_id = prepared.json()["request_id"]
            assert (await http.post(root + "/consume", json={"request_id": request_id})).status_code == 422
            assert not used
            redeemed = await http.post(root + "/consume", json={"request_id": request_id, "confirmed": True})
            assert redeemed.status_code == 200 and redeemed.json()["status"] == "reset"
            assert len(used) == 1


@pytest.mark.asyncio
async def test_account_change_after_ui_confirmation_cannot_prepare_a_different_account():
    handle, used = vendor()
    record = card()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        service, _, store, _ = setup_service()
        service = replace(service, environments=Environments(record), upstream=GLMResetClient(http, SECRETS, public_resolver))
        with pytest.raises(ValueError, match="账号已变化"):
            await service.prepare(record.id, None, "123", "b" * 64)
        assert not used and not store.items
