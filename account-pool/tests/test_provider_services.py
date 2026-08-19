"""验证渠道服务注册机制和 GLM 官方模型发现行为。"""

from __future__ import annotations

from typing import Final

import httpx
import pytest
from account_pool.domain.provider_source import ProviderValidationFailureCode, ProviderValidationRequest
from account_pool.provider_services.glm import GlmOfficialProviderService
from account_pool.provider_services.registry import ProviderServiceRegistry
from pydantic import SecretStr


@pytest.mark.asyncio
async def test_glm_validation_discovers_models_without_returning_api_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status_code=200,
            json={"object": "list", "data": [{"id": "glm-4.7"}, {"id": "glm-5"}, {"id": "glm-4.7"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        service: Final = GlmOfficialProviderService(client)
        result: Final = await service.validate(
            ProviderValidationRequest(
                provider_id="glm_official",
                api_base="https://open.bigmodel.cn/api/paas/v4/",
                api_key=SecretStr("glm-secret-key"),
                group="生产",
            )
        )

    assert result.ok
    assert tuple(item.model for item in result.models) == ("glm-4.7", "glm-5")
    assert result.normalized_api_base == "https://open.bigmodel.cn/api/paas/v4"
    assert result.group == "生产"
    assert result.key_fingerprint is not None and result.key_fingerprint != "glm-secret-key"
    assert "glm-secret-key" not in result.model_dump_json()
    assert requests[0].headers["authorization"] == "Bearer glm-secret-key"
    assert str(requests[0].url) == "https://open.bigmodel.cn/api/paas/v4/models"


@pytest.mark.asyncio
async def test_glm_validation_rejects_non_official_url_without_sending_key() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        service: Final = GlmOfficialProviderService(client)
        result: Final = await service.validate(
            ProviderValidationRequest(
                provider_id="glm_official",
                api_base="https://example.invalid/api/paas/v4",
                api_key=SecretStr("must-not-leak"),
            )
        )

    assert not result.ok
    assert "仅允许" in result.message
    assert result.failure_code == ProviderValidationFailureCode.INVALID_CONFIGURATION
    assert requests == []


@pytest.mark.parametrize(
    "api_base",
    (
        "https://open.bigmodel.cn:8443/api/paas/v4",
        "https://user@open.bigmodel.cn/api/paas/v4",
    ),
)
@pytest.mark.asyncio
async def test_glm_validation_rejects_modified_official_authority(api_base: str) -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await GlmOfficialProviderService(client).validate(
            ProviderValidationRequest(
                provider_id="glm_official",
                api_base=api_base,
                api_key=SecretStr("must-not-leak"),
            )
        )

    assert not result.ok
    assert requests == []


@pytest.mark.asyncio
async def test_glm_validation_reports_invalid_key() -> None:
    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=401, json={"error": {"message": "unauthorized"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        service: Final = GlmOfficialProviderService(client)
        result: Final = await service.validate(
            ProviderValidationRequest(
                provider_id="glm_official",
                api_base="https://open.bigmodel.cn/api/paas/v4",
                api_key=SecretStr("invalid"),
            )
        )

    assert not result.ok
    assert "API Key 无效" in result.message
    assert result.failure_code == ProviderValidationFailureCode.AUTHENTICATION


@pytest.mark.asyncio
async def test_glm_validation_rejects_redirect_oversized_and_server_failure() -> None:
    oversized: Final = b'{"data":[]}' + (b" " * 1_048_576)
    responses: Final = iter(
        (
            httpx.Response(status_code=307, headers={"location": "https://example.invalid/models"}),
            httpx.Response(status_code=200, content=oversized),
            httpx.Response(status_code=503),
        )
    )

    def upstream(_: httpx.Request) -> httpx.Response:
        return next(responses)

    request: Final = ProviderValidationRequest(
        provider_id="glm_official",
        api_base="https://open.bigmodel.cn/api/paas/v4",
        api_key=SecretStr("test-placeholder"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        service: Final = GlmOfficialProviderService(client)
        redirect: Final = await service.validate(request)
        too_large: Final = await service.validate(request)
        unavailable: Final = await service.validate(request)

    assert redirect.failure_code == ProviderValidationFailureCode.UPSTREAM_RESPONSE
    assert too_large.failure_code == ProviderValidationFailureCode.UPSTREAM_RESPONSE
    assert unavailable.failure_code == ProviderValidationFailureCode.TRANSPORT


@pytest.mark.asyncio
async def test_registry_returns_typed_failure_for_unknown_provider() -> None:
    registry: Final = ProviderServiceRegistry(())
    result: Final = await registry.validate(
        ProviderValidationRequest(
            provider_id="missing",
            api_base="https://provider.example/v1",
            api_key=SecretStr("secret"),
        )
    )

    assert not result.ok
    assert result.capabilities == ()
    assert result.failure_code == ProviderValidationFailureCode.UNSUPPORTED_PROVIDER
