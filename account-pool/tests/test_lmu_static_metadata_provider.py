"""验证 LMU 静态公开模型元数据的安全提取边界。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

import httpx
from account_pool.domain.provider_source import ProviderValidationRequest
from account_pool.provider_services.lmu_static_metadata.service import LmuStaticMetadataProviderService
from pydantic import SecretStr

HostResolver = Callable[[str], Awaitable[tuple[str, ...]]]


async def public_resolver(_: str) -> tuple[str, ...]:
    return ("93.184.216.34",)


def client_factory(
    upstream: Callable[[httpx.Request], httpx.Response],
) -> Callable[[tuple[str, ...]], httpx.AsyncClient]:
    return lambda _: httpx.AsyncClient(transport=httpx.MockTransport(upstream))


async def test_lmu_static_metadata_discovers_only_schema_validated_json_ld_models() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"""<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"@type":"Product","identifier":"model-a"},{"@type":"Product","identifier":"model-b"}]}</script>""",
        )

    result: Final = await LmuStaticMetadataProviderService(
        public_resolver,
        client_factory(upstream),
    ).validate(
        ProviderValidationRequest(
            provider_id="lmu_static_metadata",
            api_base="https://api.lmuai.com/",
            api_key=SecretStr("must-not-send"),
        )
    )

    assert result.ok
    assert tuple(model.model for model in result.models) == ("model-a", "model-b")
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.lmuai.com/models"
    assert requests[0].method == "GET"
    assert "authorization" not in requests[0].headers
    assert "must-not-send" not in result.model_dump_json()


async def test_lmu_static_metadata_rejects_generic_json_ld_without_guessing_models() -> None:
    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/html"},
            content=b"""<script type="application/ld+json">{"@type":"Organization","name":"LMU"}</script>""",
        )

    result: Final = await LmuStaticMetadataProviderService(
        public_resolver,
        client_factory(upstream),
    ).validate(
        ProviderValidationRequest(
            provider_id="lmu_static_metadata",
            api_base="https://api.lmuai.com/",
            api_key=SecretStr("must-not-send"),
        )
    )

    assert not result.ok
    assert result.models == ()
    assert "页面" in result.message


async def test_lmu_static_metadata_rejects_origin_mismatch_without_request() -> None:
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code=200)

    result: Final = await LmuStaticMetadataProviderService(
        public_resolver,
        client_factory(upstream),
    ).validate(
        ProviderValidationRequest(
            provider_id="lmu_static_metadata",
            api_base="https://elsewhere.example/",
            api_key=SecretStr("must-not-send"),
        )
    )

    assert not result.ok
    assert requests == []
