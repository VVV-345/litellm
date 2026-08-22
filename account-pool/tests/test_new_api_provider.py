"""验证 New API 网关的模型发现、倍率价格提取和密钥安全边界。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Final

import httpx
import pytest
from account_pool.domain.provider_source import (
    CapabilityState,
    ProviderCapability,
    ProviderValidationFailureCode,
    ProviderValidationRequest,
)
from account_pool.provider_services.new_api import NewApiProviderService
from pydantic import SecretStr

HostResolver = Callable[[str], Awaitable[tuple[str, ...]]]


async def public_resolver(_: str) -> tuple[str, ...]:
    return ("93.184.216.34",)


async def private_resolver(_: str) -> tuple[str, ...]:
    return ("127.0.0.1", "10.0.0.8")


async def test_new_api_discovers_models_and_pricing_without_leaking_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"object": "list", "data": [{"id": "gpt-4o"}, {"id": "claude-3.5-sonnet"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(
                status_code=200,
                json={
                    "success": True,
                    "message": "",
                    "data": {
                        "gpt-4o": {"model_ratio": 2.0, "completion_ratio": 4.0, "group_ratio": 1.5},
                        "claude-3.5-sonnet": {"model_ratio": 3.0, "completion_ratio": 5.0, "group_ratio": 1.5},
                    },
                },
            )
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await NewApiProviderService(client, public_resolver).validate(
            ProviderValidationRequest(
                provider_id="new_api",
                api_base="https://gateway.example.com/v1/",
                api_key=SecretStr("provider-secret"),
                group="premium",
            )
        )

    assert result.ok
    assert tuple(offer.model for offer in result.models) == ("claude-3.5-sonnet", "gpt-4o")
    assert len(result.pricing) == 2
    assert str(requests[0].url) == "https://gateway.example.com/v1/models"
    assert str(requests[1].url) == "https://gateway.example.com/api/pricing"
    assert "provider-secret" not in result.model_dump_json()

    by_model: Final = {offer.provider_model_id: offer for offer in result.pricing}
    gpt: Final = by_model["gpt-4o"]
    assert gpt.input_price == Decimal("2.0")
    assert gpt.output_price == Decimal("4.0")
    assert gpt.group_multiplier == Decimal("1.5")
    assert gpt.unit == "multiplier"
    assert gpt.currency == "RATIO"
    assert gpt.group_name == "premium"


async def test_new_api_preserves_multiplier_semantics_when_model_price_is_positive() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "ratio-model"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(
                status_code=200,
                json={
                    "success": True,
                    "data": {
                        "ratio-model": {
                            "model_price": 0.002,
                            "model_ratio": 2.0,
                            "completion_ratio": 3.0,
                            "group_ratio": 1.5,
                        }
                    },
                },
            )
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await NewApiProviderService(client, public_resolver).validate(
            ProviderValidationRequest(provider_id="new_api", api_base="https://gateway.example.com/v1", api_key=SecretStr("k"))
        )

    assert result.ok
    assert result.pricing[0].unit == "multiplier"
    assert result.pricing[0].currency == "RATIO"
    assert result.pricing[0].input_price == Decimal("2.0")
    assert result.pricing[0].output_price == Decimal("3.0")
    assert result.pricing[0].group_multiplier == Decimal("1.5")


async def test_new_api_pricing_only_contains_models_visible_to_the_key() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "visible-model"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(
                status_code=200,
                json={
                    "success": True,
                    "data": {
                        "visible-model": {"model_ratio": 2.0, "completion_ratio": 3.0},
                        "unavailable-model": {"model_ratio": 4.0, "completion_ratio": 5.0},
                    },
                },
            )
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await NewApiProviderService(client, public_resolver).validate(
            ProviderValidationRequest(provider_id="new_api", api_base="https://gateway.example.com/v1", api_key=SecretStr("k"))
        )

    assert result.ok
    assert tuple(offer.provider_model_id for offer in result.pricing) == ("visible-model",)


async def test_new_api_rejects_private_resolution_before_sending_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await NewApiProviderService(client, private_resolver).validate(
            ProviderValidationRequest(provider_id="new_api", api_base="https://gateway.example.com/v1", api_key=SecretStr("must-not-leak"))
        )

    assert not result.ok
    assert "公网" in result.message
    assert requests == []


async def test_new_api_rejects_unsafe_url_before_sending_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await NewApiProviderService(client, public_resolver).validate(
            ProviderValidationRequest(
                provider_id="new_api",
                api_base="https://user@gateway.example.com/v1",
                api_key=SecretStr("must-not-leak"),
            )
        )

    assert not result.ok
    assert requests == []


async def test_new_api_reports_pricing_shape_failure() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(status_code=200, json={"success": True, "data": {"model-a": "wrong-shape"}})
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await NewApiProviderService(client, public_resolver).validate(
            ProviderValidationRequest(provider_id="new_api", api_base="https://gateway.example.com/v1", api_key=SecretStr("k"))
        )

    assert not result.ok
    assert result.failure_code == ProviderValidationFailureCode.UPSTREAM_RESPONSE


async def test_new_api_pricing_authentication_rejection_is_typed() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(status_code=401)
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await NewApiProviderService(client, public_resolver).validate(
            ProviderValidationRequest(provider_id="new_api", api_base="https://gateway.example.com/v1", api_key=SecretStr("k"))
        )

    assert not result.ok
    assert result.failure_code == ProviderValidationFailureCode.AUTHENTICATION


async def test_new_api_rejects_pricing_redirect_without_following_it() -> None:
    requested_urls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(status_code=302, headers={"location": "https://elsewhere.example/pricing"})
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await NewApiProviderService(client, public_resolver).validate(
            ProviderValidationRequest(provider_id="new_api", api_base="https://gateway.example.com/v1", api_key=SecretStr("k"))
        )

    assert not result.ok
    assert result.failure_code == ProviderValidationFailureCode.UPSTREAM_RESPONSE
    assert requested_urls == ["https://gateway.example.com/v1/models", "https://gateway.example.com/api/pricing"]


def test_new_api_manifest_declares_pricing_supported() -> None:
    states: Final = {item.capability: item.state for item in NewApiProviderService(httpx.AsyncClient(), public_resolver).manifest.capabilities}

    assert states[ProviderCapability.CONNECTION] == CapabilityState.SUPPORTED
    assert states[ProviderCapability.MODEL_DISCOVERY] == CapabilityState.SUPPORTED
    assert states[ProviderCapability.MODEL_PRICING] == CapabilityState.SUPPORTED
    assert states[ProviderCapability.ACCOUNT_BALANCE] == CapabilityState.UNSUPPORTED
    assert states[ProviderCapability.SUBSCRIPTIONS] == CapabilityState.UNSUPPORTED
    assert states[ProviderCapability.PERIODIC_LIMITS] == CapabilityState.UNSUPPORTED
