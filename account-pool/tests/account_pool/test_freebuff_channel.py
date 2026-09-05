"""验证 FreeBuff2API 渠道的 codebuff 授权契约、Compose 渲染边界和渠道行为。"""

from __future__ import annotations

import json
from typing import Final
from uuid import uuid4

import httpx
import pytest
import yaml

from account_pool.channels.freebuff2api.channel import (
    FreeBuff2APIChannel,
    freebuff_supplier,
    pack_authorization_state,
    unpack_authorization_state,
)
from account_pool.channels.freebuff2api.client import HttpCodebuffClient
from account_pool.compose_renderer import render_freebuff_compose
from account_pool.config import Settings
from account_pool.domain import (
    AuthorizationFlow,
    ChannelKind,
    EnvironmentRecord,
    EnvironmentStatus,
    Provider,
    ProxyMode,
    QuotaSnapshot,
    SupplierKind,
    utc_now,
)
from account_pool.secrets import EnvironmentSecretDeriver

_FINGERPRINT_HASH: Final = "fp-hash-for-test"


def _settings(tmp_path: object) -> Settings:
    return Settings(
        database_url="postgresql://unused",
        data_root=tmp_path,  # type: ignore[arg-type]  # tests pass a tmp path
        manager_token="m" * 32,
        secret_seed="s" * 32,
        ssh_host="example.com",
        ssh_user="operator",
    )


def _record() -> EnvironmentRecord:
    now: Final = utc_now()
    return EnvironmentRecord(
        id=uuid4(),
        name="FreeBuff environment",
        provider=Provider.OPENAI,
        channel=ChannelKind.FREEBUFF2API,
        supplier=SupplierKind.FREEBUFF,
        status=EnvironmentStatus.PROVISIONING,
        enabled=True,
        manual_cooldown=False,
        concurrency_limit=1,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=(),
        enabled_models=(),
        auth_file_name=None,
        auth_index=None,
        quota=QuotaSnapshot(),
        model_quotas=(),
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


def test_freebuff_supplier_contract_matches_codebuff_cli_flow() -> None:
    definition: Final = freebuff_supplier()

    assert definition.kind is SupplierKind.FREEBUFF
    assert definition.authorization_flow is AuthorizationFlow.DEVICE_CODE
    assert definition.authorization_path == "/api/auth/cli/code"
    assert definition.callback_port is None
    assert definition.callback_path is None


def test_render_freebuff_compose_pins_entrypoint_and_never_binds_host_ports(tmp_path: object) -> None:
    record: Final = _record()
    settings: Final = _settings(tmp_path)
    renamed: Final = record.model_copy(update={"name": "Renamed"})

    rendered: Final = yaml.safe_load(render_freebuff_compose(record, settings, "gateway-key-test"))
    rerendered: Final = yaml.safe_load(render_freebuff_compose(renamed, settings, "gateway-key-test"))
    service: Final = rendered["services"]["freebuff2api"]

    assert rendered == rerendered
    assert "ports" not in service
    assert rendered["name"] == f"account-pool-{record.id.hex}"
    assert service["entrypoint"] == ["node", "/app/server.js"]
    assert service["environment"] == [
        "PORT=8787",
        "HOST=0.0.0.0",
        "FREEBUFF_API_KEY=gateway-key-test",
        "FREEBUFF_DEBUG=false",
    ]
    assert service["networks"] == {"environment": {"aliases": [f"freebuff-{record.id.hex}"]}}
    assert rendered["volumes"] == {"freebuff-data": {"name": f"account-pool-{record.id.hex}-data"}}
    assert rendered["networks"]["environment"]["internal"] is False
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["volumes"] == ["freebuff-data:/app/credentials:ro"]


def test_authorization_state_round_trip_preserves_operation_fields() -> None:
    from account_pool.channels.freebuff2api.client import CodeAuthorizationOperation

    operation: Final = CodeAuthorizationOperation(
        authorization_url="https://www.codebuff.com/oauth/login?auth_code=secret",
        fingerprint_id="codebuff-cli-litellm-abc",
        fingerprint_hash=_FINGERPRINT_HASH,
        expires_at="2026-09-05T08:00:00Z",
    )

    packed: Final = pack_authorization_state(operation)
    unpacked: Final = unpack_authorization_state(packed)

    assert packed.startswith("freebuff:")
    assert unpacked == operation


@pytest.mark.asyncio
async def test_codebuff_client_starts_authorization_with_official_contract() -> None:
    requests: Final[list[httpx.Request]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/cli/code":
            return httpx.Response(
                200,
                json={
                    "loginUrl": "https://www.codebuff.com/oauth/login?auth_code=one-time",
                    "fingerprintHash": _FINGERPRINT_HASH,
                    "expiresAt": "2026-09-05T08:05:00Z",
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    client: Final = HttpCodebuffClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        operation: Final = await client.start_authorization("codebuff-cli-litellm-abc")
    finally:
        await client.close()

    assert operation.authorization_url == "https://www.codebuff.com/oauth/login?auth_code=one-time"
    assert operation.fingerprint_hash == _FINGERPRINT_HASH
    assert operation.expires_at == "2026-09-05T08:05:00Z"
    assert requests[0].method == "POST"
    assert json.loads(requests[0].content) == {"fingerprintId": "codebuff-cli-litellm-abc"}


@pytest.mark.asyncio
async def test_codebuff_client_returns_none_while_pending() -> None:
    from account_pool.channels.freebuff2api.client import CodeAuthorizationOperation

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"user": None}, request=request)

    client: Final = HttpCodebuffClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    operation: Final = CodeAuthorizationOperation(
        authorization_url="https://www.codebuff.com/oauth/login",
        fingerprint_id="fp",
        fingerprint_hash=_FINGERPRINT_HASH,
        expires_at="",
    )
    try:
        token: Final = await client.authorization_token(operation)
    finally:
        await client.close()

    assert token is None


@pytest.mark.asyncio
async def test_codebuff_client_extracts_token_from_status_user() -> None:
    from account_pool.channels.freebuff2api.client import CodeAuthorizationOperation

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"user": {"email": "user@example.com", "authToken": "token-secret"}},
            request=request,
        )

    client: Final = HttpCodebuffClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    operation: Final = CodeAuthorizationOperation(
        authorization_url="https://www.codebuff.com/oauth/login",
        fingerprint_id="fp",
        fingerprint_hash=_FINGERPRINT_HASH,
        expires_at="",
    )
    try:
        token: Final = await client.authorization_token(operation)
    finally:
        await client.close()

    assert token == "token-secret"


@pytest.mark.asyncio
async def test_channel_authorization_status_waits_until_user_authorizes() -> None:
    from account_pool.channels.freebuff2api.client import CodeAuthorizationOperation

    class StubCodebuffClient:
        async def close(self) -> None:
            return None

        async def start_authorization(self, fingerprint_id: str) -> CodeAuthorizationOperation:
            return CodeAuthorizationOperation(
                authorization_url="https://www.codebuff.com/oauth/login",
                fingerprint_id=fingerprint_id,
                fingerprint_hash=_FINGERPRINT_HASH,
                expires_at="",
            )

        async def authorization_token(self, operation: CodeAuthorizationOperation) -> str | None:
            return None

    channel: Final = FreeBuff2APIChannel(
        _settings("/tmp"),
        EnvironmentSecretDeriver("s" * 32),
        client=StubCodebuffClient(),  # type: ignore[arg-type]  # stub satisfies the used surface
    )
    try:
        status: Final = await channel.authorization_status(_record(), pack_authorization_state(
            CodeAuthorizationOperation(
                authorization_url="https://www.codebuff.com/oauth/login",
                fingerprint_id="fp",
                fingerprint_hash=_FINGERPRINT_HASH,
                expires_at="",
            )
        ))
    finally:
        await channel.close()

    assert status == "wait"


@pytest.mark.asyncio
async def test_channel_gateway_exposes_openai_compatible_freebuff_endpoint() -> None:
    channel: Final = FreeBuff2APIChannel(_settings("/tmp"), EnvironmentSecretDeriver("s" * 32))
    try:
        record: Final = _record().model_copy(
            update={
                "status": EnvironmentStatus.READY,
                "available_models": ("deepseek/deepseek-v4-flash",),
                "enabled_models": ("deepseek/deepseek-v4-flash",),
            }
        )

        gateway: Final = channel.gateway(record)

        assert gateway.api_base == f"http://freebuff-{record.id.hex}:8787"
        assert gateway.custom_llm_provider == "openai"
        assert gateway.routable is True
        assert gateway.enabled_models == ("deepseek/deepseek-v4-flash",)
    finally:
        await channel.close()
