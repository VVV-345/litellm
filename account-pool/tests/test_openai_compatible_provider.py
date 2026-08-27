"""验证 OpenAI 兼容渠道的模型发现、能力声明和密钥安全边界。"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Final, TypeAlias

import httpcore
import httpx
import pytest
from account_pool.domain.provider_source import (
    CapabilityState,
    ProviderCapability,
    ProviderValidationFailureCode,
    ProviderValidationRequest,
)
from account_pool.provider_services.openai_compatible import OpenAICompatibleProviderService
from account_pool.provider_services.safe_http import (
    ValidatedAddressClientPolicy,
    ValidatedAddressTransport,
    validated_address_client_policy,
)
from httpcore._backends.base import SOCKET_OPTION
from pydantic import SecretStr

HostResolver: TypeAlias = Callable[[str], Awaitable[tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class CapturedRequest:
    url: str
    authorization: str | None


class CapturingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        captured: list[CapturedRequest],  # mutable-ok: test transport records requests
    ) -> None:
        self._captured: Final = captured
        self.closed: bool = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._captured.append(
            CapturedRequest(
                url=str(request.url),
                authorization=request.headers["authorization"] if "authorization" in request.headers else None,
            )
        )
        return httpx.Response(
            status_code=200,
            json={"object": "list", "data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-b"}]},
        )

    async def aclose(self) -> None:
        self.closed = True


@dataclass(frozen=True, slots=True)
class CapturedTlsConnection:
    address: str
    server_hostname: str | None


@dataclass(frozen=True, slots=True)
class CapturedHttpRequest:
    host_header: bytes | None


@dataclass(frozen=True, slots=True)
class CapturedTcpConnection:
    address: str
    timeout: float | None


class CapturingHttpStream(httpcore.AsyncNetworkStream):
    def __init__(
        self,
        address: str,
        tls_connections: list[CapturedTlsConnection],  # mutable-ok: test backend records TLS connections
        http_requests: list[CapturedHttpRequest],  # mutable-ok: test stream records HTTP requests
    ) -> None:
        self._address: Final = address
        self._tls_connections: Final = tls_connections
        self._http_requests: Final = http_requests
        self._response_sent: bool = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._response_sent:
            return b""
        self._response_sent = True
        return (
            b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 25\r\n\r\n{"data":[{"id":"model"}]}'
        )

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        host_header: Final = next(
            (line.split(b":", 1)[1].strip() for line in buffer.split(b"\r\n") if line.lower().startswith(b"host:")),
            None,
        )
        if host_header is not None:
            self._http_requests.append(CapturedHttpRequest(host_header=host_header))

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self._tls_connections.append(
            CapturedTlsConnection(
                address=self._address,
                server_hostname=server_hostname,
            )
        )
        return self

    def get_extra_info(self, info: str) -> object | None:
        return None


class CapturingHttpBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        tls_connections: list[CapturedTlsConnection],  # mutable-ok: test backend records TLS connections
        http_requests: list[CapturedHttpRequest],  # mutable-ok: test stream records HTTP requests
        tcp_connections: list[CapturedTcpConnection] | None = None,  # mutable-ok: test backend records TCP connections
    ) -> None:
        self._tls_connections: Final = tls_connections
        self._http_requests: Final = http_requests
        self._tcp_connections: Final = tcp_connections

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if self._tcp_connections is not None:
            assert self._tcp_connections is not None
            self._tcp_connections.append(CapturedTcpConnection(address=host, timeout=timeout))
        return CapturingHttpStream(host, self._tls_connections, self._http_requests)

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("validated HTTPS discovery must not use Unix sockets")

    async def sleep(self, seconds: float) -> None:
        raise AssertionError("validated HTTPS discovery must not sleep")


class FailingHttpBackend(httpcore.AsyncNetworkBackend):
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("unavailable")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("validated HTTPS discovery must not use Unix sockets")

    async def sleep(self, seconds: float) -> None:
        raise AssertionError("validated HTTPS discovery must not sleep")


class ClosingValidatedAddressTransport(ValidatedAddressTransport):
    def __init__(self, addresses: tuple[str, ...]) -> None:
        super().__init__(addresses, FailingHttpBackend())
        self.closed: bool = False

    async def aclose(self) -> None:
        await super().aclose()
        self.closed = True


class NonAsyncResponseStreamPool:
    async def handle_async_request(self, request: httpcore.Request) -> httpcore.Response:
        return httpcore.Response(status=200, content=(b"not", b"async"))

    async def aclose(self) -> None:
        return None


class ClosingProtocolErrorTransport(ValidatedAddressTransport):
    def __init__(self, addresses: tuple[str, ...]) -> None:
        super().__init__(addresses)
        object.__setattr__(self, "_pool", NonAsyncResponseStreamPool())
        self.closed: bool = False

    async def aclose(self) -> None:
        self.closed = True


def mocked_client_factory(
    upstream: Callable[[httpx.Request], httpx.Response],
) -> Callable[[tuple[str, ...], httpx.Timeout, ValidatedAddressClientPolicy | None], httpx.AsyncClient]:
    return lambda _, __, ___: httpx.AsyncClient(transport=httpx.MockTransport(upstream))


async def public_resolver(_: str) -> tuple[str, ...]:
    return ("93.184.216.34",)


async def private_resolver(_: str) -> tuple[str, ...]:
    return ("127.0.0.1", "10.0.0.8")


async def failing_resolver(_: str) -> tuple[str, ...]:
    raise OSError("DNS unavailable")


async def test_validated_address_transport_pins_tcp_and_preserves_host_and_tls_sni() -> None:
    tls_connections: Final[list[CapturedTlsConnection]] = []  # mutable-ok: captures transport observations
    http_requests: Final[list[CapturedHttpRequest]] = []  # mutable-ok: captures transport observations
    backend: Final = CapturingHttpBackend(tls_connections, http_requests)
    transport: Final = ValidatedAddressTransport(("93.184.216.34",), backend)
    async with httpx.AsyncClient(transport=transport) as client:
        response: Final = await client.get("https://gateway.example.com/v1/models")

    assert response.json() == {"data": [{"id": "model"}]}
    assert tls_connections == [
        CapturedTlsConnection(
            address="93.184.216.34",
            server_hostname="gateway.example.com",
        )
    ]
    assert http_requests == [CapturedHttpRequest(host_header=b"gateway.example.com")]


async def test_validated_address_transport_uses_configured_ssl_context() -> None:
    tls_connections: Final[list[CapturedTlsConnection]] = []  # mutable-ok: captures transport observations
    http_requests: Final[list[CapturedHttpRequest]] = []  # mutable-ok: captures transport observations
    configured_ssl_context: Final = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    class CapturingSslStream(CapturingHttpStream):
        async def start_tls(
            self,
            ssl_context: ssl.SSLContext,
            server_hostname: str | None = None,
            timeout: float | None = None,
        ) -> httpcore.AsyncNetworkStream:
            assert ssl_context is configured_ssl_context
            return await super().start_tls(ssl_context, server_hostname, timeout)

    class CapturingSslBackend(CapturingHttpBackend):
        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: Iterable[SOCKET_OPTION] | None = None,
        ) -> httpcore.AsyncNetworkStream:
            return CapturingSslStream(host, self._tls_connections, self._http_requests)

    backend: Final = CapturingSslBackend(tls_connections, http_requests)
    transport: Final = ValidatedAddressTransport(("93.184.216.34",), backend, configured_ssl_context)
    async with httpx.AsyncClient(transport=transport) as client:
        response: Final = await client.get("https://gateway.example.com/v1/models")

    assert response.status_code == 200
    assert tuple(connection.server_hostname for connection in tls_connections) == ("gateway.example.com",)


async def test_validated_address_transport_falls_back_to_next_pinned_address() -> None:
    tls_connections: Final[list[CapturedTlsConnection]] = []  # mutable-ok: captures transport observations
    http_requests: Final[list[CapturedHttpRequest]] = []  # mutable-ok: captures transport observations

    class FirstAddressFailsBackend(CapturingHttpBackend):
        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: Iterable[SOCKET_OPTION] | None = None,
        ) -> httpcore.AsyncNetworkStream:
            if host == "93.184.216.34":
                raise httpcore.ConnectError("first address unavailable")
            return await super().connect_tcp(host, port, timeout, local_address, socket_options)

    backend: Final = FirstAddressFailsBackend(tls_connections, http_requests)
    transport: Final = ValidatedAddressTransport(("93.184.216.34", "93.184.216.35"), backend)
    async with httpx.AsyncClient(transport=transport) as client:
        response: Final = await client.get("https://gateway.example.com/v1/models")

    assert response.status_code == 200
    assert tuple(connection.address for connection in tls_connections) == ("93.184.216.35",)


async def test_validated_address_transport_maps_httpcore_errors_to_httpx() -> None:
    transport: Final = ValidatedAddressTransport(("93.184.216.34",), FailingHttpBackend())
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://gateway.example.com/v1/models")


async def test_validated_address_transport_bounds_each_address_connect_timeout() -> None:
    tls_connections: Final[list[CapturedTlsConnection]] = []  # mutable-ok: captures transport observations
    http_requests: Final[list[CapturedHttpRequest]] = []  # mutable-ok: captures transport observations
    tcp_connections: Final[list[CapturedTcpConnection]] = []  # mutable-ok: captures TCP timeouts

    class EveryAddressFailsBackend(CapturingHttpBackend):
        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: Iterable[SOCKET_OPTION] | None = None,
        ) -> httpcore.AsyncNetworkStream:
            assert self._tcp_connections is not None
            self._tcp_connections.append(CapturedTcpConnection(address=host, timeout=timeout))
            raise httpcore.ConnectTimeout("unavailable")

    backend: Final = EveryAddressFailsBackend(tls_connections, http_requests, tcp_connections)
    transport: Final = ValidatedAddressTransport(
        ("93.184.216.34", "93.184.216.35"),
        backend,
    )
    async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(120, connect=5)) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await client.get("https://gateway.example.com/v1/models")

    assert tuple(connection.address for connection in tcp_connections) == ("93.184.216.34", "93.184.216.35")
    assert all(connection.timeout is not None and 0 < connection.timeout <= 5 for connection in tcp_connections)


async def test_validated_address_transport_uses_remaining_total_connect_budget() -> None:
    tls_connections: Final[list[CapturedTlsConnection]] = []  # mutable-ok: captures transport observations
    http_requests: Final[list[CapturedHttpRequest]] = []  # mutable-ok: captures transport observations
    tcp_connections: Final[list[CapturedTcpConnection]] = []  # mutable-ok: captures TCP timeouts

    class FirstAddressConsumesBudgetBackend(CapturingHttpBackend):
        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: Iterable[SOCKET_OPTION] | None = None,
        ) -> httpcore.AsyncNetworkStream:
            assert self._tcp_connections is not None
            self._tcp_connections.append(CapturedTcpConnection(address=host, timeout=timeout))
            if host == "93.184.216.34":
                await asyncio.sleep(0.05)
                raise httpcore.ConnectTimeout("first address unavailable")
            return await super().connect_tcp(host, port, timeout, local_address, socket_options)

    backend: Final = FirstAddressConsumesBudgetBackend(tls_connections, http_requests, tcp_connections)
    transport: Final = ValidatedAddressTransport(
        ("93.184.216.34", "93.184.216.35"),
        backend,
    )
    async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(120, connect=0.1)) as client:
        response: Final = await client.get("https://gateway.example.com/v1/models")

    assert response.status_code == 200
    assert tcp_connections[0].address == "93.184.216.34"
    assert tcp_connections[0].timeout is not None
    assert 0 < tcp_connections[0].timeout <= 0.1
    assert tcp_connections[1].address == "93.184.216.35"
    assert tcp_connections[1].timeout is not None
    assert 0 < tcp_connections[1].timeout < 0.08


async def test_openai_compatible_uses_validated_addresses_without_replacing_hostname() -> None:
    requests: Final[list[CapturedRequest]] = []  # mutable-ok: captures requests emitted by the provider
    requested_addresses: Final[list[tuple[str, ...]]] = []  # mutable-ok: captures factory inputs
    transports: Final[list[CapturingTransport]] = []  # mutable-ok: captures factory-owned clients

    def discovery_client(
        addresses: tuple[str, ...],
        _: httpx.Timeout,
        __: ValidatedAddressClientPolicy | None,
    ) -> httpx.AsyncClient:
        transport: Final = CapturingTransport(requests)
        requested_addresses.append(addresses)
        transports.append(transport)
        return httpx.AsyncClient(transport=transport)

    service: Final = OpenAICompatibleProviderService(
        resolve_host=public_resolver,
        discovery_client_factory=discovery_client,
    )
    result: Final = await service.validate(
        ProviderValidationRequest(
            provider_id="openai_compatible",
            api_base="https://gateway.example.com/openai/v1/",
            api_key=SecretStr("provider-secret"),
            group="premium",
        )
    )

    assert result.ok
    assert requested_addresses == [("93.184.216.34",)]
    assert result.normalized_api_base == "https://gateway.example.com/openai/v1"
    assert tuple(offer.model for offer in result.models) == ("model-a", "model-b")
    assert result.group == "premium"
    assert result.key_fingerprint is not None and result.key_fingerprint != "provider-secret"
    assert "provider-secret" not in result.model_dump_json()
    assert requests == [
        CapturedRequest(
            url="https://gateway.example.com/openai/v1/models",
            authorization="Bearer provider-secret",
        )
    ]
    assert tuple(transport.closed for transport in transports) == (True,)


async def test_openai_compatible_preserves_injected_tls_policy() -> None:
    tls_connections: Final[list[CapturedTlsConnection]] = []  # mutable-ok: captures transport observations
    http_requests: Final[list[CapturedHttpRequest]] = []  # mutable-ok: captures transport observations
    configured_ssl_context: Final = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    class CapturingSslStream(CapturingHttpStream):
        async def start_tls(
            self,
            ssl_context: ssl.SSLContext,
            server_hostname: str | None = None,
            timeout: float | None = None,
        ) -> httpcore.AsyncNetworkStream:
            assert ssl_context is configured_ssl_context
            return await super().start_tls(ssl_context, server_hostname, timeout)

    class CapturingSslBackend(CapturingHttpBackend):
        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: Iterable[SOCKET_OPTION] | None = None,
        ) -> httpcore.AsyncNetworkStream:
            return CapturingSslStream(host, self._tls_connections, self._http_requests)

    backend: Final = CapturingSslBackend(tls_connections, http_requests)
    original_client: Final = httpx.AsyncClient(
        verify=configured_ssl_context,
        timeout=httpx.Timeout(120, connect=5),
        trust_env=False,
    )
    original_policy: Final = ValidatedAddressClientPolicy(
        ssl_context=configured_ssl_context,
        http1=True,
        http2=False,
        max_connections=10,
        max_keepalive_connections=5,
        keepalive_expiry=5,
        retries=0,
        local_address=None,
        socket_options=None,
    )

    def discovery_client(
        addresses: tuple[str, ...],
        _: httpx.Timeout,
        __: ValidatedAddressClientPolicy | None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=ValidatedAddressTransport(
                addresses,
                backend,
                policy=original_policy,
            )
        )

    try:
        result: Final = await OpenAICompatibleProviderService(
            original_client,
            public_resolver,
            discovery_client,
        ).validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("provider-secret"),
            )
        )
    finally:
        await original_client.aclose()

    assert result.ok
    assert tuple(connection.server_hostname for connection in tls_connections) == ("gateway.example.com",)


async def test_openai_compatible_uses_injected_120_second_total_timeout() -> None:
    original_client: Final = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5), trust_env=False)
    expected_policy: Final = validated_address_client_policy(original_client)
    assert isinstance(expected_policy, ValidatedAddressClientPolicy)
    captured_timeouts: Final[list[httpx.Timeout]] = []  # mutable-ok: captures created client timeout
    captured_policies: Final[
        list[ValidatedAddressClientPolicy | None]
    ] = []  # mutable-ok: captures created client policy

    def discovery_client(
        addresses: tuple[str, ...],
        timeout: httpx.Timeout,
        policy: ValidatedAddressClientPolicy | None,
    ) -> httpx.AsyncClient:
        captured_timeouts.append(timeout)
        captured_policies.append(policy)
        return httpx.AsyncClient(transport=CapturingTransport([]))

    try:
        result: Final = await OpenAICompatibleProviderService(
            original_client,
            public_resolver,
            discovery_client,
        ).validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("provider-secret"),
            )
        )
    finally:
        await original_client.aclose()

    assert result.ok
    assert captured_timeouts == [httpx.Timeout(120, connect=5)]
    assert captured_policies == [expected_policy]


async def test_openai_compatible_uses_custom_injected_timeout() -> None:
    custom_timeout: Final = httpx.Timeout(17, connect=3)
    original_client: Final = httpx.AsyncClient(timeout=custom_timeout, trust_env=False)
    captured_timeouts: Final[list[httpx.Timeout]] = []  # mutable-ok: captures factory arguments

    def discovery_client(
        addresses: tuple[str, ...],
        timeout: httpx.Timeout,
        policy: ValidatedAddressClientPolicy | None,
    ) -> httpx.AsyncClient:
        captured_timeouts.append(timeout)
        return httpx.AsyncClient(transport=CapturingTransport([]))

    try:
        result: Final = await OpenAICompatibleProviderService(
            original_client, public_resolver, discovery_client
        ).validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("provider-secret"),
            )
        )
    finally:
        await original_client.aclose()

    assert result.ok
    assert captured_timeouts == [custom_timeout]


async def test_openai_compatible_rejects_proxy_client_before_sending_key() -> None:
    async with httpx.AsyncClient(proxy="http://proxy.example:8080") as client:
        result: Final = await OpenAICompatibleProviderService(client, public_resolver).validate(
            ProviderValidationRequest(
                provider_id="openai_compatible",
                api_base="https://gateway.example.com/v1",
                api_key=SecretStr("must-not-leak"),
            )
        )

    assert not result.ok
    assert result.failure_code == ProviderValidationFailureCode.INVALID_CONFIGURATION
    assert "DNS-pinned" in result.message
    assert "must-not-leak" not in result.model_dump_json()


async def test_openai_compatible_maps_pinned_protocol_failure_and_closes_client() -> None:
    transports: Final[list[ClosingProtocolErrorTransport]] = []  # mutable-ok: captures factory-owned clients

    def failing_client(
        addresses: tuple[str, ...],
        _: httpx.Timeout,
        __: ValidatedAddressClientPolicy | None,
    ) -> httpx.AsyncClient:
        transport: Final = ClosingProtocolErrorTransport(addresses)
        transports.append(transport)
        return httpx.AsyncClient(transport=transport)

    result: Final = await OpenAICompatibleProviderService(
        resolve_host=public_resolver,
        discovery_client_factory=failing_client,
    ).validate(
        ProviderValidationRequest(
            provider_id="openai_compatible",
            api_base="https://gateway.example.com/v1",
            api_key=SecretStr("must-not-leak"),
        )
    )

    assert not result.ok
    assert result.failure_code == ProviderValidationFailureCode.TRANSPORT
    assert "must-not-leak" not in result.model_dump_json()
    assert tuple(transport.closed for transport in transports) == (True,)


async def test_openai_compatible_maps_pinned_transport_failure_and_closes_client() -> None:
    transports: Final[list[ClosingValidatedAddressTransport]] = []  # mutable-ok: captures factory-owned clients

    def failing_client(
        addresses: tuple[str, ...],
        _: httpx.Timeout,
        __: ValidatedAddressClientPolicy | None,
    ) -> httpx.AsyncClient:
        transport: Final = ClosingValidatedAddressTransport(addresses)
        transports.append(transport)
        return httpx.AsyncClient(transport=transport)

    result: Final = await OpenAICompatibleProviderService(
        resolve_host=public_resolver, discovery_client_factory=failing_client
    ).validate(
        ProviderValidationRequest(
            provider_id="openai_compatible",
            api_base="https://gateway.example.com/v1",
            api_key=SecretStr("must-not-leak"),
        )
    )

    assert not result.ok
    assert result.failure_code == ProviderValidationFailureCode.TRANSPORT
    assert "must-not-leak" not in result.model_dump_json()
    assert tuple(transport.closed for transport in transports) == (True,)


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
    requests: Final[list[httpx.Request]] = []  # mutable-ok: captures requests emitted by the provider

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    result: Final = await OpenAICompatibleProviderService(
        resolve_host=public_resolver, discovery_client_factory=mocked_client_factory(upstream)
    ).validate(
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
    requests: Final[list[httpx.Request]] = []  # mutable-ok: captures requests emitted by the provider

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    result: Final = await OpenAICompatibleProviderService(
        resolve_host=private_resolver, discovery_client_factory=mocked_client_factory(upstream)
    ).validate(
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
    requests: Final[list[httpx.Request]] = []  # mutable-ok: captures requests emitted by the provider

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    result: Final = await OpenAICompatibleProviderService(
        resolve_host=private_resolver, discovery_client_factory=mocked_client_factory(upstream)
    ).validate(
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
    requests: Final[list[httpx.Request]] = []  # mutable-ok: captures requests emitted by the provider

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    result: Final = await OpenAICompatibleProviderService(
        resolve_host=failing_resolver, discovery_client_factory=mocked_client_factory(upstream)
    ).validate(
        ProviderValidationRequest(
            provider_id="openai_compatible",
            api_base="https://gateway.example.com/v1",
            api_key=SecretStr("must-not-leak"),
        )
    )

    assert not result.ok
    assert requests == []


async def test_openai_compatible_does_not_follow_redirects() -> None:
    requests: Final[list[httpx.Request]] = []  # mutable-ok: captures requests emitted by the provider

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=307, headers={"location": "https://other.example.com/v1/models"})

    result: Final = await OpenAICompatibleProviderService(
        resolve_host=public_resolver, discovery_client_factory=mocked_client_factory(upstream)
    ).validate(
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

    service: Final = OpenAICompatibleProviderService(
        resolve_host=public_resolver, discovery_client_factory=mocked_client_factory(upstream)
    )
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

    result: Final = await OpenAICompatibleProviderService(
        resolve_host=public_resolver, discovery_client_factory=mocked_client_factory(upstream)
    ).validate(
        ProviderValidationRequest(
            provider_id="openai_compatible",
            api_base="https://gateway.example.com/v1",
            api_key=SecretStr("provider-secret"),
        )
    )

    assert not result.ok
    assert "1 MiB" in result.message


def test_openai_compatible_manifest_is_honest_about_generic_capabilities() -> None:
    service: Final = OpenAICompatibleProviderService(resolve_host=public_resolver)
    states: Final = {item.capability: item.state for item in service.manifest.capabilities}

    assert states[ProviderCapability.CONNECTION] == CapabilityState.SUPPORTED
    assert states[ProviderCapability.MODEL_DISCOVERY] == CapabilityState.SUPPORTED
    assert states[ProviderCapability.ACCOUNT_BALANCE] == CapabilityState.UNSUPPORTED
    assert states[ProviderCapability.SUBSCRIPTIONS] == CapabilityState.UNSUPPORTED
    assert states[ProviderCapability.PERIODIC_LIMITS] == CapabilityState.UNSUPPORTED
    assert states[ProviderCapability.MODEL_PRICING] == CapabilityState.UNSUPPORTED
