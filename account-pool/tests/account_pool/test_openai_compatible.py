"""本文件验证 OpenAI 兼容渠道的地址安全、模型前缀和密钥隔离。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final
from uuid import uuid4

import httpx
import pytest
from account_pool.channels.openai_compatible import OpenAICompatibleChannel
from account_pool.domain import (
    ChannelKind,
    EnvironmentRecord,
    EnvironmentStatus,
    OpenAICompatibleConfiguration,
    OpenAICompatibleCreateRequest,
    OpenAICompatibleCredential,
    Provider,
    ProxyMode,
    QuotaSnapshot,
    SupplierKind,
)
from account_pool.secrets import EnvironmentSecretDeriver, StateCipher


async def public_resolver(_: str, __: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


async def private_resolver(_: str, __: int) -> tuple[str, ...]:
    return ("10.0.0.8",)


def test_openai_compatible_request_rejects_local_and_metadata_targets() -> None:
    for base_url in (
        "http://127.0.0.1/v1",
        "http://[::1]/v1",
        "http://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/v1",
        "https://user:password@example.com/v1",
    ):
        with pytest.raises(ValueError):
            OpenAICompatibleCreateRequest(
                base_url=base_url,
                test_model="upstream-model",
                api_keys=({"api_key": "secret"},),
            )


def test_openai_compatible_request_rejects_protected_headers() -> None:
    with pytest.raises(ValueError, match="protected transport header"):
        OpenAICompatibleCreateRequest(
            base_url="https://api.example.com/v1",
            test_model="upstream-model",
            api_keys=({"api_key": "secret"},),
            headers=(("Authorization", "should-not-be-accepted"),),
        )


@pytest.mark.asyncio
async def test_channel_rejects_hostname_that_resolves_to_private_network() -> None:
    secret_seed: Final = "s" * 32
    environment_id: Final = uuid4()
    cipher: Final = StateCipher(EnvironmentSecretDeriver(secret_seed))
    now: Final = datetime.now(timezone.utc)
    record: Final = EnvironmentRecord(
        id=environment_id,
        name="Blocked card",
        provider=Provider.OPENAI,
        channel=ChannelKind.OPENAI_COMPATIBLE,
        supplier=SupplierKind.OPENAI_COMPATIBLE,
        openai_compatible=OpenAICompatibleConfiguration(
            base_url="https://api.example.com/v1",
            test_model="chat-model",
            credentials=(
                OpenAICompatibleCredential(api_key_ciphertext=cipher.seal(environment_id, "secret-key")),
            ),
        ),
        status=EnvironmentStatus.VALIDATING,
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
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )
    channel: Final = OpenAICompatibleChannel(EnvironmentSecretDeriver(secret_seed), resolver=private_resolver)
    try:
        with pytest.raises(ValueError, match="blocked address"):
            await channel.read_account(record)
    finally:
        await channel.close()


@pytest.mark.asyncio
async def test_channel_discovers_prefixed_models_and_keeps_keys_out_of_record_repr() -> None:
    secret_seed: Final = "s" * 32
    environment_id: Final = uuid4()
    cipher: Final = StateCipher(EnvironmentSecretDeriver(secret_seed))
    configuration: Final = OpenAICompatibleConfiguration(
        base_url="https://api.example.com/v1",
        prefix="vendor/",
        test_model="chat-model",
        credentials=(
            OpenAICompatibleCredential(api_key_ciphertext=cipher.seal(environment_id, "secret-key"), weight=2),
            OpenAICompatibleCredential(api_key_ciphertext=cipher.seal(environment_id, "second-key"), weight=1),
        ),
        custom_models=(),
    )
    now: Final = datetime.now(timezone.utc)
    record: Final = EnvironmentRecord(
        id=environment_id,
        name="Compatible card",
        provider=Provider.OPENAI,
        channel=ChannelKind.OPENAI_COMPATIBLE,
        supplier=SupplierKind.OPENAI_COMPATIBLE,
        openai_compatible=configuration,
        status=EnvironmentStatus.VALIDATING,
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
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        if request.headers["authorization"] == "Bearer secret-key":
            return httpx.Response(200, json={"data": [{"id": "chat-model"}]}, request=request)
        assert request.headers["authorization"] == "Bearer second-key"
        return httpx.Response(200, json={"data": [{"id": "embedding-model"}]}, request=request)

    channel: Final = OpenAICompatibleChannel(
        EnvironmentSecretDeriver(secret_seed),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=public_resolver,
    )
    try:
        observed: Final = await channel.read_account(record)
        gateway: Final = channel.gateway(observed)
    finally:
        await channel.close()

    assert observed.available_models == ("vendor/chat-model", "vendor/embedding-model")
    assert observed.enabled_models == observed.available_models
    assert gateway.credentials[0].api_key == "secret-key"
    assert "secret-key" not in repr(record)


@pytest.mark.asyncio
async def test_custom_models_receive_the_prefix_once() -> None:
    secret_seed: Final = "s" * 32
    environment_id: Final = uuid4()
    cipher: Final = StateCipher(EnvironmentSecretDeriver(secret_seed))
    now: Final = datetime.now(timezone.utc)
    record: Final = EnvironmentRecord(
        id=environment_id,
        name="Custom model card",
        provider=Provider.OPENAI,
        channel=ChannelKind.OPENAI_COMPATIBLE,
        supplier=SupplierKind.OPENAI_COMPATIBLE,
        openai_compatible=OpenAICompatibleConfiguration(
            base_url="https://api.example.com/v1",
            prefix="vendor/",
            test_model="chat-model",
            credentials=(
                OpenAICompatibleCredential(api_key_ciphertext=cipher.seal(environment_id, "secret-key")),
            ),
            custom_models=("chat-model",),
        ),
        status=EnvironmentStatus.VALIDATING,
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
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    client: Final = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": []}, request=request))
    )
    channel: Final = OpenAICompatibleChannel(
        EnvironmentSecretDeriver(secret_seed), client=client, resolver=public_resolver
    )
    try:
        observed: Final = await channel.read_account(record)
    finally:
        await channel.close()

    assert observed.available_models == ("vendor/chat-model",)
