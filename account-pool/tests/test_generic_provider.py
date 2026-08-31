"""验证 通用解析器 网关的模型发现、倍率价格提取和密钥安全边界。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Final

import httpcore
import httpx
from account_pool.domain.provider_source import (
    CapabilityState,
    PricingDiscoveryFailureCode,
    ProviderCapability,
    ProviderValidationRequest,
)
from account_pool.provider_services.generic import GenericProviderService
from account_pool.provider_services.generic.client import _ResolvedAddressTransport
from pydantic import SecretStr, ValidationError

HostResolver = Callable[[str], Awaitable[tuple[str, ...]]]


async def public_resolver(_: str) -> tuple[str, ...]:
    return ("93.184.216.34",)


def mocked_client_factory(
    upstream: Callable[[httpx.Request], httpx.Response],
) -> Callable[[tuple[str, ...]], httpx.AsyncClient]:
    return lambda _: httpx.AsyncClient(transport=httpx.MockTransport(upstream))


async def private_resolver(_: str) -> tuple[str, ...]:
    return ("127.0.0.1", "10.0.0.8")


async def test_generic_discovers_models_and_pricing_without_leaking_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(
                status_code=200, json={"object": "list", "data": [{"id": "gpt-4o"}, {"id": "claude-3.5-sonnet"}]}
            )
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
        result: Final = await GenericProviderService(client, public_resolver, mocked_client_factory(upstream)).validate(
            ProviderValidationRequest(
                provider_id="generic",
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


async def test_generic_uses_multiplier_pricing_when_fixed_model_price_is_also_present() -> None:
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
        result: Final = await GenericProviderService(client, public_resolver, mocked_client_factory(upstream)).validate(
            ProviderValidationRequest(
                provider_id="generic", api_base="https://gateway.example.com/v1", api_key=SecretStr("k")
            )
        )

    assert result.ok
    assert result.pricing[0].unit == "multiplier"
    assert result.pricing[0].currency == "RATIO"
    assert result.pricing[0].input_price == Decimal("2.0")
    assert result.pricing[0].output_price == Decimal("3.0")
    assert result.pricing[0].group_multiplier == Decimal("1.5")


async def test_generic_reads_new_api_array_pricing_and_ignores_non_token_fixed_prices() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "text-model"}, {"id": "image-model"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(
                status_code=200,
                json={
                    "success": True,
                    "group_ratio": {"premium": 1.5},
                    "data": [
                        {
                            "model_name": "text-model",
                            "quota_type": 0,
                            "model_ratio": 2.0,
                            "completion_ratio": 3.0,
                            "cache_ratio": 0.5,
                            "create_cache_ratio": 1.25,
                            "owner_by": "platform",
                        },
                        {"model_name": "image-model", "quota_type": 1, "model_price": 0.02},
                    ],
                },
            )
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GenericProviderService(client, public_resolver, mocked_client_factory(upstream)).validate(
            ProviderValidationRequest(
                provider_id="generic",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("k"),
                group="premium",
            )
        )

    assert result.ok
    assert tuple(offer.provider_model_id for offer in result.pricing) == ("text-model",)
    offer: Final = result.pricing[0]
    assert (offer.input_price, offer.output_price, offer.cache_read_price, offer.cache_write_price) == (
        Decimal("2.0"),
        Decimal("3.0"),
        Decimal("0.5"),
        Decimal("1.25"),
    )
    assert offer.group_multiplier == Decimal("1.5")


async def test_generic_pricing_only_contains_models_visible_to_the_key() -> None:
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
        result: Final = await GenericProviderService(client, public_resolver, mocked_client_factory(upstream)).validate(
            ProviderValidationRequest(
                provider_id="generic", api_base="https://gateway.example.com/v1", api_key=SecretStr("k")
            )
        )

    assert result.ok
    assert tuple(offer.provider_model_id for offer in result.pricing) == ("visible-model",)


async def test_generic_client_uses_validated_address_without_changing_tls_hostname() -> None:
    seen_hosts: list[str] = []

    class CapturingBackend:
        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: tuple[tuple[int, int, int], ...] | None = None,
        ) -> httpcore.AsyncNetworkStream:
            seen_hosts.append(host)
            raise httpcore.ConnectError("test connection")

        async def connect_unix_socket(
            self,
            path: str,
            timeout: float | None = None,
            socket_options: tuple[tuple[int, int, int], ...] | None = None,
        ) -> httpcore.AsyncNetworkStream:
            raise AssertionError("通用解析器 discovery must not use Unix sockets")

        async def sleep(self, seconds: float) -> None:
            raise AssertionError("通用解析器 discovery must not sleep")

    backend: Final = CapturingBackend()
    transport: Final = _ResolvedAddressTransport(("93.184.216.34",), backend)
    async with httpx.AsyncClient(transport=transport) as client:
        try:
            await client.get("https://gateway.example.com/v1/models")
        except httpcore.ConnectError:
            pass
        else:
            raise AssertionError("the capturing backend must reject the connection")

    assert seen_hosts == ["93.184.216.34"]


async def test_generic_uses_validated_address_for_each_request() -> None:
    requests: list[httpx.Request] = []
    requested_addresses: list[tuple[str, ...]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(status_code=200, json={"success": True, "data": {}})
        return httpx.Response(status_code=404)

    def discovery_client(addresses: tuple[str, ...]) -> httpx.AsyncClient:
        requested_addresses.append(addresses)
        return httpx.AsyncClient(transport=httpx.MockTransport(upstream))

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GenericProviderService(client, public_resolver, discovery_client).validate(
            ProviderValidationRequest(
                provider_id="generic", api_base="https://gateway.example.com/v1", api_key=SecretStr("k")
            )
        )

    assert result.ok
    assert requested_addresses == [("93.184.216.34",)]
    assert [request.url.path for request in requests] == ["/v1/models", "/api/pricing"]


async def test_generic_rejects_private_resolution_before_sending_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GenericProviderService(client, private_resolver).validate(
            ProviderValidationRequest(
                provider_id="generic", api_base="https://gateway.example.com/v1", api_key=SecretStr("must-not-leak")
            )
        )

    assert not result.ok
    assert "公网" in result.message
    assert requests == []


async def test_generic_rejects_unsafe_url_before_sending_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GenericProviderService(client, public_resolver, mocked_client_factory(upstream)).validate(
            ProviderValidationRequest(
                provider_id="generic",
                api_base="https://user@gateway.example.com/v1",
                api_key=SecretStr("must-not-leak"),
            )
        )

    assert not result.ok
    assert requests == []


async def test_generic_degrades_to_models_only_when_pricing_shape_is_unreadable() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(status_code=200, json={"success": True, "data": {"model-a": "wrong-shape"}})
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GenericProviderService(client, public_resolver, mocked_client_factory(upstream)).validate(
            ProviderValidationRequest(
                provider_id="generic", api_base="https://gateway.example.com/v1", api_key=SecretStr("k")
            )
        )

    assert result.ok
    assert tuple(offer.model for offer in result.models) == ("model-a",)
    assert result.pricing == ()
    assert result.pricing_failure_code is not None


async def test_generic_degrades_to_models_only_when_pricing_unauthorized() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(status_code=401)
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GenericProviderService(client, public_resolver, mocked_client_factory(upstream)).validate(
            ProviderValidationRequest(
                provider_id="generic", api_base="https://gateway.example.com/v1", api_key=SecretStr("k")
            )
        )

    assert result.ok
    assert tuple(offer.model for offer in result.models) == ("model-a",)
    assert result.pricing == ()
    assert result.pricing_failure_code is not None


async def test_generic_degrades_to_models_only_but_never_follows_pricing_redirect() -> None:
    requested_urls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(status_code=302, headers={"location": "https://elsewhere.example/pricing"})
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GenericProviderService(client, public_resolver, mocked_client_factory(upstream)).validate(
            ProviderValidationRequest(
                provider_id="generic", api_base="https://gateway.example.com/v1", api_key=SecretStr("k")
            )
        )

    assert result.ok
    assert result.pricing == ()
    assert requested_urls == ["https://gateway.example.com/v1/models", "https://gateway.example.com/api/pricing"]


async def test_generic_marks_pricing_service_unavailable_as_transient() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/pricing":
            return httpx.Response(status_code=503)
        return httpx.Response(status_code=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GenericProviderService(client, public_resolver, mocked_client_factory(upstream)).validate(
            ProviderValidationRequest(
                provider_id="generic", api_base="https://gateway.example.com/v1", api_key=SecretStr("k")
            )
        )

    assert result.ok
    assert tuple(offer.model for offer in result.models) == ("model-a",)
    assert result.pricing == ()
    assert result.pricing_failure_code == PricingDiscoveryFailureCode.TRANSPORT


async def test_generic_fetches_pricing_via_admin_login_when_credentials_provided() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/user/login":
            return httpx.Response(
                status_code=200,
                headers={"set-cookie": "session=sess-abc; Path=/"},
                json={"success": True, "data": {"id": 7}},
            )
        if request.url.path == "/api/pricing":
            if request.headers.get("cookie") != "session=sess-abc" or request.headers.get("new-api-user") != "7":
                return httpx.Response(status_code=401)
            return httpx.Response(
                status_code=200,
                json={"success": True, "data": {"model-a": {"model_ratio": 2.0, "completion_ratio": 3.0}}},
            )
        return httpx.Response(status_code=404)

    def discovery_client(_: tuple[str, ...]) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(upstream))

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GenericProviderService(client, public_resolver, discovery_client).validate(
            ProviderValidationRequest(
                provider_id="generic",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("k"),
                username=SecretStr("admin"),
                password=SecretStr("admin-pass"),
            )
        )

    assert result.ok
    assert tuple(offer.provider_model_id for offer in result.pricing) == ("model-a",)
    assert [request.url.path for request in requests] == ["/v1/models", "/api/pricing", "/api/user/login", "/api/pricing"]
    login_request: Final = next(request for request in requests if request.url.path == "/api/user/login")
    assert b"admin-pass" in login_request.content
    dumped: Final = result.model_dump_json()
    assert "admin" not in dumped
    assert "admin-pass" not in dumped


async def test_generic_degrades_to_models_only_when_login_fails() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/user/login":
            return httpx.Response(status_code=200, json={"success": False, "message": "bad"})
        if request.url.path == "/api/pricing":
            return httpx.Response(status_code=401)
        return httpx.Response(status_code=404)

    def discovery_client(_: tuple[str, ...]) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(upstream))

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GenericProviderService(client, public_resolver, discovery_client).validate(
            ProviderValidationRequest(
                provider_id="generic",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("k"),
                username=SecretStr("admin"),
                password=SecretStr("admin-pass"),
            )
        )

    assert result.ok
    assert tuple(offer.model for offer in result.models) == ("model-a",)
    assert result.pricing == ()
    assert result.pricing_failure_code is not None
    assert [request.url.path for request in requests] == ["/v1/models", "/api/pricing", "/api/user/login"]


async def test_generic_login_cookie_does_not_leak_through_shared_client() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(status_code=200, json={"data": [{"id": "model-a"}]})
        if request.url.path == "/api/user/login":
            return httpx.Response(
                status_code=302,
                headers={"location": "https://elsewhere.example/login", "set-cookie": "session=leaked; Path=/"},
            )
        if request.url.path == "/api/pricing":
            return httpx.Response(status_code=200, json={"success": True, "data": {}})
        return httpx.Response(status_code=404)

    def discovery_client(_: tuple[str, ...]) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(upstream))

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        await GenericProviderService(client, public_resolver, discovery_client).validate(
            ProviderValidationRequest(
                provider_id="generic",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("first-key"),
                username=SecretStr("admin"),
                password=SecretStr("admin-pass"),
            )
        )
        await GenericProviderService(client, public_resolver, mocked_client_factory(upstream)).validate(
            ProviderValidationRequest(
                provider_id="generic",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("second-key"),
            )
        )

    second_models_request: Final = [
        request
        for request in requests
        if request.url.path == "/v1/models" and request.headers["authorization"] == "Bearer second-key"
    ][0]
    assert "session=leaked" not in second_models_request.headers.get("cookie", "")


async def test_generic_rejects_partial_admin_credentials() -> None:
    try:
        ProviderValidationRequest(
            provider_id="generic",
            api_base="https://gateway.example.com/v1",
            api_key=SecretStr("k"),
            username=SecretStr("admin"),
        )
    except ValidationError as error:
        assert "username and password must be provided together" in str(error)
        return
    raise AssertionError("partial administrator credentials must be rejected")


def test_generic_manifest_declares_pricing_supported() -> None:
    states: Final = {
        item.capability: item.state
        for item in GenericProviderService(httpx.AsyncClient(), public_resolver).manifest.capabilities
    }

    assert states[ProviderCapability.CONNECTION] == CapabilityState.SUPPORTED
    assert states[ProviderCapability.MODEL_DISCOVERY] == CapabilityState.SUPPORTED
    assert states[ProviderCapability.MODEL_PRICING] == CapabilityState.SUPPORTED
    assert set(states) == {
        ProviderCapability.CONNECTION,
        ProviderCapability.MODEL_DISCOVERY,
        ProviderCapability.MODEL_PRICING,
    }
