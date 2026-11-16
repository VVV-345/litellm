"""本文件验证 OpenAI 兼容渠道的地址安全、模型前缀和密钥隔离。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Final
from uuid import uuid4

import httpx
import pytest
from account_pool.api import create_router
from account_pool.channels.openai_compatible import OpenAICompatibleChannel
from account_pool.channels.registry import ChannelRegistry
from account_pool.domain import (
    ChannelKind,
    CreateDirectCredentialEnvironmentRequest,
    DirectAPIKeyCredentialRequest,
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
from account_pool.service import EnvironmentService
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_account_pool import EmptyProfiles, FakeCLIProxy, FakeRuntime, MemoryRepository, _record, _settings


async def public_resolver(_: str, __: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


async def private_resolver(_: str, __: int) -> tuple[str, ...]:
    return ("10.0.0.8",)


@pytest.mark.parametrize(
    "base",
    (
        "https://open.bigmodel.cn/api/paas/v4",
        "https://opencode.ai/inference/openai/v1",
        "https://opencode.ai/inference/go/openai/v1",
    ),
)
def test_manager_model_preview_and_provider_presets_use_existing_configuration(tmp_path: Path, base: str) -> None:
    console: Final = "https://opencode.ai/inference/openai/v1"
    go: Final = "https://opencode.ai/inference/go/openai/v1"
    secret: Final = EnvironmentSecretDeriver("s" * 32)
    settings: Final = _settings(tmp_path)
    records: Final = tuple(
        _record(status=EnvironmentStatus.READY).model_copy(update={
            "channel": ChannelKind.OPENAI_COMPATIBLE,
            "supplier": SupplierKind.OPENAI_COMPATIBLE,
            "openai_compatible": OpenAICompatibleConfiguration(
                base_url=url, test_model="fixture-model",
                credentials=(OpenAICompatibleCredential(api_key_ciphertext="saved-fixture"),),
            ),
        })
        for url in (
            "https://open.bigmodel.cn/api/paas/v4",
            "https://api.z.ai/api/paas/v4/",
            console,
            f"{go}/",
            "https://custom.example/v1",
        )
    )
    repository: Final = MemoryRepository(records[0])
    repository.records.update({record.id: record for record in records[1:]})
    requests: Final[list[httpx.Request]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "fixture-model"}]})

    channel: Final = OpenAICompatibleChannel(
        secret, httpx.AsyncClient(transport=httpx.MockTransport(upstream)), public_resolver,
    )
    registry: Final = replace(
        ChannelRegistry.default(), implementations=MappingProxyType({ChannelKind.OPENAI_COMPATIBLE: channel}),
    )
    service: Final = EnvironmentService(
        settings, repository, FakeRuntime(), FakeCLIProxy(), EmptyProfiles(), secret, channels=registry,
    )
    app: Final = FastAPI()
    app.include_router(create_router(service, "fixture-manager"))
    path: Final = "/api/openai-compatible/models"
    payload: Final = {"base_url": base, "credential": {"api_key": "preview-key"}, "headers": [["X-Fixture", "yes"]]}
    with TestClient(app) as client:
        assert client.post(path, json=payload).status_code == 401
        client.headers["Authorization"] = "Bearer fixture-manager"
        rejected: Final = client.post(path, json={**payload, "credential": {"api_key": ["preview-key"]}})
        assert rejected.status_code == 422 and "preview-key" not in rejected.text
        assert not requests
        preview: Final = client.post(path, json=payload)
        assert preview.status_code == 200 and preview.json() == ["fixture-model"]
        assert [(request.method, str(request.url)) for request in requests] == [("GET", f"{base}/models")]
        assert requests[0].headers["Authorization"] == "Bearer preview-key"
        assert requests[0].headers["X-Fixture"] == "yes"
        catalog: Final = client.get("/api/provider-families")
        assert catalog.status_code == 200
    entries: Final = {family["kind"]: family for family in catalog.json()}
    assert entries["glm"]["card_count"] == 2
    assert entries["glm"]["supplier"] == SupplierKind.OPENAI_COMPATIBLE
    assert entries["opencode"]["card_count"] == 2
    assert entries["opencode"]["supplier"] == SupplierKind.OPENAI_COMPATIBLE
    assert entries["openai_compatible"]["card_count"] == 1
    assert tuple(repository.records.values()) == records


@pytest.mark.asyncio
async def test_preview_models_uses_credentials_and_headers_without_creating_a_card() -> None:
    requests: Final[list[httpx.Request]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}, {"id": "model-a"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        channel: Final = OpenAICompatibleChannel(
            client=client, secrets=EnvironmentSecretDeriver("s" * 32), resolver=public_resolver,
        )
        result: Final = await channel.discover_models_from_source(
            "https://api.example.com/v1", "preview-secret", (("x-feature", "enabled"),), None,
        )
    assert result == ("model-a", "model-b")
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.example.com/v1/models"
    assert requests[0].headers["authorization"] == "Bearer preview-secret"
    assert requests[0].headers["x-feature"] == "enabled"


@pytest.mark.asyncio
async def test_preview_models_rejects_private_dns_before_sending_credentials() -> None:
    def upstream(_: httpx.Request) -> httpx.Response:
        pytest.fail("Private destination must never receive credentials")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        channel: Final = OpenAICompatibleChannel(
            client=client, secrets=EnvironmentSecretDeriver("s" * 32), resolver=private_resolver,
        )
        with pytest.raises(ValueError, match="blocked address"):
            await channel.discover_models_from_source("https://api.example.com/v1", "preview-secret", (), None)


def test_openai_compatible_request_rejects_multiple_keys() -> None:
    with pytest.raises(ValueError, match="at most 1 item"):
        OpenAICompatibleCreateRequest(
            base_url="https://api.example.com/v1",
            test_model="upstream-model",
            api_keys=({"api_key": "first-key"}, {"api_key": "second-key"}),
        )


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


@pytest.mark.parametrize(
    "base_url",
    (
        "http://generativelanguage.googleapis.com/v1beta",
        "https://127.0.0.1/v1beta",
        "https://metadata.google.internal/v1beta",
    ),
)
def test_direct_api_key_base_url_rejects_unsafe_targets(base_url: str) -> None:
    with pytest.raises(ValueError):
        DirectAPIKeyCredentialRequest(api_key="secret", base_url=base_url)


def test_gemini_direct_api_key_requires_official_endpoint() -> None:
    with pytest.raises(ValueError):
        CreateDirectCredentialEnvironmentRequest(
            name="Gemini",
            supplier=SupplierKind.GEMINI,
            credential=DirectAPIKeyCredentialRequest(api_key="secret", base_url="https://example.com/v1beta"),
        )


def test_xai_direct_api_key_accepts_safe_public_https_endpoint() -> None:
    request: Final = CreateDirectCredentialEnvironmentRequest(
        name="xAI",
        supplier=SupplierKind.XAI,
        credential=DirectAPIKeyCredentialRequest(api_key="secret", base_url="https://api.example.com/v1"),
    )
    assert request.credential.base_url is not None


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
            credentials=(OpenAICompatibleCredential(api_key_ciphertext=cipher.seal(environment_id, "secret-key")),),
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
        base_url="http://api.example.com/v1",
        prefix="vendor/",
        test_model="chat-model",
        credentials=(
            OpenAICompatibleCredential(api_key_ciphertext=cipher.seal(environment_id, "secret-key"), weight=2),
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
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "api.example.com"
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

    assert observed.available_models == ("vendor/chat-model",)
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
            credentials=(OpenAICompatibleCredential(api_key_ciphertext=cipher.seal(environment_id, "secret-key")),),
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
