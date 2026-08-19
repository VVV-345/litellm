from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

import httpx
import pytest
from account_pool.domain.provider_source import (
    CapabilityState,
    ProviderCapability,
    ProviderValidationFailureCode,
    ProviderValidationRequest,
)
from account_pool.provider_services.openai_compatible import OpenAICompatibleProviderService
from pydantic import SecretStr

HostResolver = Callable[[str], Awaitable[tuple[str, ...]]]


async def public_resolver(_: str) -> tuple[str, ...]:
    return ("93.184.216.34",)


async def private_resolver(_: str) -> tuple[str, ...]:
    return ("127.0.0.1", "10.0.0.8")


async def failing_resolver(_: str) -> tuple[str, ...]:
    raise OSError("DNS unavailable")


async def test_openai_compatible_discovers_models_without_returning_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status_code=200,
            json={"object": "list", "data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-b"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        service: Final = OpenAICompatibleProviderService(client=client, resolve_host=public_resolver)
        result: Final = await service.validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base="https://gateway.example.com/openai/v1/",
                api_key=SecretStr("provider-secret"),
                group="premium",
            )
        )

    assert result.ok
    assert result.normalized_api_base == "https://gateway.example.com/openai/v1"
    assert tuple(offer.model for offer in result.models) == ("model-a", "model-b")
    assert result.group == "premium"
    assert result.key_fingerprint is not None and result.key_fingerprint != "provider-secret"
    assert "provider-secret" not in result.model_dump_json()
    assert str(requests[0].url) == "https://gateway.example.com/openai/v1/models"
    assert requests[0].headers["authorization"] == "Bearer provider-secret"


@pytest.mark.parametrize(
    "api_base",
    (
        "http://gateway.example.com/v1",
        "https://user@gateway.example.com/v1",
        "https://gateway.example.com/v1?tenant=secret",
        "https://gateway.example.com/v1#fragment",
        "https://gateway.example.com:99999/v1",
    ),
)
async def test_openai_compatible_rejects_unsafe_url_before_sending_key(api_base: str) -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await OpenAICompatibleProviderService(client, public_resolver).validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base=api_base,
                api_key=SecretStr("must-not-leak"),
            )
        )

    assert not result.ok
    assert requests == []
    assert "must-not-leak" not in result.model_dump_json()


async def test_openai_compatible_rejects_private_resolution_before_sending_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await OpenAICompatibleProviderService(client, private_resolver).validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("must-not-leak"),
            )
        )

    assert not result.ok
    assert "公网" in result.message
    assert requests == []


async def test_openai_compatible_rejects_private_ipv6_literal_before_sending_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await OpenAICompatibleProviderService(client, private_resolver).validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base="https://[::1]/v1",
                api_key=SecretStr("must-not-leak"),
            )
        )

    assert not result.ok
    assert result.normalized_api_base == "https://[::1]/v1"
    assert requests == []


async def test_openai_compatible_rejects_dns_failure_before_sending_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await OpenAICompatibleProviderService(client, failing_resolver).validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("must-not-leak"),
            )
        )

    assert not result.ok
    assert requests == []


async def test_openai_compatible_does_not_follow_redirects() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=307, headers={"location": "https://other.example.com/v1/models"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await OpenAICompatibleProviderService(client, public_resolver).validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("redirect-secret"),
            )
        )

    assert not result.ok
    assert "重定向" in result.message
    assert len(requests) == 1
    assert requests[0].url.host == "gateway.example.com"


async def test_openai_compatible_reports_auth_and_invalid_payload() -> None:
    responses: Final = iter(
        (
            httpx.Response(status_code=401, json={"error": {"message": "includes upstream secret"}}),
            httpx.Response(status_code=200, json={"models": ["wrong-shape"]}),
        )
    )

    def upstream(_: httpx.Request) -> httpx.Response:
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        service: Final = OpenAICompatibleProviderService(client, public_resolver)
        request: Final = ProviderValidationRequest(
            provider_id="openai_compatible",
            api_base="https://gateway.example.com/v1",
            api_key=SecretStr("invalid-secret"),
        )
        unauthorized: Final = await service.validate(request)
        invalid_payload: Final = await service.validate(request)

    assert not unauthorized.ok
    assert "API Key" in unauthorized.message
    assert unauthorized.failure_code == ProviderValidationFailureCode.AUTHENTICATION
    assert "includes upstream secret" not in unauthorized.message
    assert not invalid_payload.ok
    assert "响应格式" in invalid_payload.message
    assert invalid_payload.failure_code == ProviderValidationFailureCode.UPSTREAM_RESPONSE


async def test_openai_compatible_rejects_oversized_model_response() -> None:
    oversized: Final = b'{"data":[]}' + (b" " * 1_048_576)

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, content=oversized)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await OpenAICompatibleProviderService(client, public_resolver).validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("provider-secret"),
            )
        )

    assert not result.ok
    assert "1 MiB" in result.message


def test_openai_compatible_manifest_is_honest_about_generic_capabilities() -> None:
    service: Final = OpenAICompatibleProviderService(
        client=httpx.AsyncClient(),
        resolve_host=public_resolver,
    )
    states: Final = {item.capability: item.state for item in service.manifest.capabilities}

    assert states[ProviderCapability.CONNECTION] == CapabilityState.SUPPORTED
    assert states[ProviderCapability.MODEL_DISCOVERY] == CapabilityState.SUPPORTED
    assert states[ProviderCapability.ACCOUNT_BALANCE] == CapabilityState.UNSUPPORTED
    assert states[ProviderCapability.SUBSCRIPTIONS] == CapabilityState.UNSUPPORTED
    assert states[ProviderCapability.PERIODIC_LIMITS] == CapabilityState.UNSUPPORTED
    assert states[ProviderCapability.MODEL_PRICING] == CapabilityState.UNSUPPORTED
