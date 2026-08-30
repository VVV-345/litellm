"""验证添加渠道时的上游厂商模型发现与解析器流程相互独立。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

import httpx
import pytest
from account_pool.provider_services.safe_http import ValidatedAddressClientPolicy
from account_pool.upstream_providers.catalog import UPSTREAM_PROVIDER_DEFINITIONS, UpstreamProviderDefinition
from account_pool.upstream_providers.client import HostResolver
from account_pool.upstream_providers.models import (
    UpstreamModelDiscoveryFailureCode,
    UpstreamModelDiscoveryRequest,
)
from account_pool.upstream_providers.registry import UpstreamProviderRegistry
from pydantic import SecretStr

_PUBLIC_ADDRESS: Final = ("8.8.8.8",)


async def _resolve_public_host(_: str) -> tuple[str, ...]:
    return _PUBLIC_ADDRESS


def _definition(provider_id: str) -> UpstreamProviderDefinition:
    return next(definition for definition in UPSTREAM_PROVIDER_DEFINITIONS if definition.manifest.provider_id == provider_id)


def _registry(
    provider_id: str,
    upstream: Callable[[httpx.Request], httpx.Response],
    resolve_host: HostResolver = _resolve_public_host,
) -> UpstreamProviderRegistry:
    def client_factory(
        _: tuple[str, ...],
        timeout: httpx.Timeout,
        __: ValidatedAddressClientPolicy | None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(upstream), timeout=timeout)

    return UpstreamProviderRegistry(
        (_definition(provider_id),),
        resolve_host=resolve_host,
        discovery_client_factory=client_factory,
    )


@pytest.mark.asyncio
async def test_openai_vendor_uses_models_endpoint_and_returns_raw_upstream_model_ids() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "gpt-5.6"}]})

    result: Final = await _registry("openai", upstream).discover(
        UpstreamModelDiscoveryRequest(
            provider_id="openai",
            api_base="https://models.example/v1",
            api_key=SecretStr("one-time-key"),
        )
    )

    assert result.ok
    assert result.models == ("gpt-5.6",)
    assert str(requests[0].url) == "https://models.example/v1/models"
    assert requests[0].headers["authorization"] == "Bearer one-time-key"
    assert "one-time-key" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_anthropic_vendor_uses_its_own_model_listing_protocol() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "claude-opus-4-6"}], "has_more": False})

    result: Final = await _registry("anthropic", upstream).discover(
        UpstreamModelDiscoveryRequest(
            provider_id="anthropic",
            api_base="https://models.example/v1",
            api_key=SecretStr("anthropic-key"),
        )
    )

    assert result.ok
    assert result.models == ("claude-opus-4-6",)
    assert requests[0].headers["x-api-key"] == "anthropic-key"
    assert requests[0].headers["anthropic-version"] == "2023-06-01"
    assert requests[0].url.params["limit"] == "1000"


@pytest.mark.asyncio
async def test_gemini_vendor_uses_its_own_model_listing_protocol() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"models": [{"name": "models/gemini-2.5-pro"}]})

    result: Final = await _registry("gemini", upstream).discover(
        UpstreamModelDiscoveryRequest(
            provider_id="gemini",
            api_base="https://models.example/v1beta",
            api_key=SecretStr("gemini-key"),
        )
    )

    assert result.ok
    assert result.models == ("gemini-2.5-pro",)
    assert requests[0].url.params["key"] == "gemini-key"


@pytest.mark.asyncio
async def test_unknown_vendor_does_not_attempt_a_parser_or_network_request() -> None:
    registry: Final = UpstreamProviderRegistry(())

    result: Final = await registry.discover(
        UpstreamModelDiscoveryRequest(
            provider_id="missing",
            api_base="https://models.example/v1",
            api_key=SecretStr("one-time-key"),
        )
    )

    assert not result.ok
    assert result.failure_code == UpstreamModelDiscoveryFailureCode.UNSUPPORTED_PROVIDER


@pytest.mark.asyncio
async def test_model_discovery_rejects_private_upstream_addresses() -> None:
    async def resolve_private_host(_: str) -> tuple[str, ...]:
        return ("127.0.0.1",)

    def upstream(_: httpx.Request) -> httpx.Response:
        raise AssertionError("private upstream must not be requested")

    registry: Final = _registry("openai", upstream, resolve_private_host)

    result: Final = await registry.discover(
        UpstreamModelDiscoveryRequest(
            provider_id="openai",
            api_base="https://models.example/v1",
            api_key=SecretStr("one-time-key"),
        )
    )

    assert result.failure_code == UpstreamModelDiscoveryFailureCode.INVALID_CONFIGURATION
