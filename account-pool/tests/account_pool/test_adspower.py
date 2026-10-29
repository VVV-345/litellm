"""验证 AdsPower 代理读取、去重导入和凭据脱敏。"""

from time import monotonic
from typing import Final
from uuid import uuid4

import httpx
import pytest
from account_pool.adspower import AdsPowerClient, AdsPowerError, AdsPowerSyncService
from account_pool.proxy_catalog import ISPProxyInput, ISPProxyProfile


class MemoryCatalog:
    def __init__(self) -> None:
        self.profiles: tuple[ISPProxyProfile, ...] = ()

    async def list_isps(self) -> tuple[ISPProxyProfile, ...]:
        return self.profiles

    async def save_isps(self, profiles: tuple[ISPProxyInput, ...]) -> tuple[ISPProxyProfile, ...]:
        records: Final = tuple(ISPProxyProfile(id=uuid4(), **p.model_dump()) for p in profiles)
        self.profiles += records
        return records


def profile(password: str = "private-password", proxy_type: str = "socks5") -> dict[str, object]:
    return {
        "user_id": "profile-1",
        "name": "My browser",
        "user_proxy_config": {
            "proxy_type": proxy_type,
            "proxy_host": "proxy.example",
            "proxy_port": "12324",
            "proxy_user": "private-user",
            "proxy_password": password,
        },
    }


@pytest.mark.asyncio
async def test_profiles_are_paginated_and_never_expose_proxy_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer local-api-key"
        assert request.url.params["page"] == "2"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "list": [
                        profile(),
                        {
                            "user_id": "direct",
                            "name": "Direct",
                            "user_proxy_config": {"proxy_type": "no_proxy"},
                        },
                        {"user_id": "unset", "user_proxy_config": None},
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        page: Final = await AdsPowerClient("http://127.0.0.1:50325", "local-api-key", client=http).list_profiles(2)
    assert page.page == 2
    assert page.profiles[0].protocol == "socks5"
    assert page.profiles[0].port == 12324
    assert page.profiles[0].syncable
    assert not page.profiles[1].syncable
    assert not page.profiles[2].syncable
    assert "private-password" not in page.model_dump_json()
    assert "private-user" not in page.model_dump_json()


@pytest.mark.asyncio
async def test_sync_reuses_same_proxy_but_preserves_old_profile_when_credentials_change() -> None:
    repository: Final = MemoryCatalog()
    responses: Final = iter((profile(), profile(), profile("changed-password")))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["user_id"] == "profile-1"
        return httpx.Response(200, json={"code": 0, "data": {"list": [next(responses)]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service: Final = AdsPowerSyncService(AdsPowerClient("http://local:50325", "key", client=http), repository)
        first: Final = await service.sync(("profile-1",))
        second: Final = await service.sync(("profile-1",))
        changed: Final = await service.sync(("profile-1",))
    assert first.synced[0].created
    assert not second.synced[0].created
    assert first.synced[0].isp_profile_id == second.synced[0].isp_profile_id
    assert changed.synced[0].isp_profile_id != first.synced[0].isp_profile_id
    assert len(repository.profiles) == 2
    assert repository.profiles[0].password == "private-password"
    assert repository.profiles[1].password == "changed-password"
    assert "password" not in changed.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    ({"code": -1, "msg": "private-password"}, {"code": 0, "data": {"list": [profile(proxy_type="no_proxy")]}}),
)
async def test_sync_failure_does_not_import_partial_or_unsupported_profiles(payload: dict[str, object]) -> None:
    repository: Final = MemoryCatalog()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http:
        service: Final = AdsPowerSyncService(AdsPowerClient("http://local:50325", "key", client=http), repository)
        with pytest.raises(AdsPowerError) as caught:
            await service.sync(("profile-1",))
    assert "private-password" not in str(caught.value)
    assert repository.profiles == ()


@pytest.mark.asyncio
async def test_api_failure_does_not_expose_response_or_request_secrets() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="local-api-key"))
    ) as http:
        with pytest.raises(AdsPowerError) as caught:
            await AdsPowerClient("http://local:50325", "local-api-key", client=http).list_profiles(1)
    assert "local-api-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_second_profile_failure_does_not_save_the_first_profile() -> None:
    repository: Final = MemoryCatalog()
    responses: Final = iter(({"code": 0, "data": {"list": [profile()]}}, {"code": -1}))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=next(responses)))
    ) as http:
        service: Final = AdsPowerSyncService(AdsPowerClient("http://local:50325", "key", client=http), repository)
        with pytest.raises(AdsPowerError):
            await service.sync(("profile-1", "profile-2"))
    assert repository.profiles == ()


@pytest.mark.asyncio
async def test_local_api_rate_limit_is_retried_and_requests_are_spaced() -> None:
    responses: Final = iter((
        {"code": -1, "msg": "Too many request per second, please check"},
        {"code": 0, "data": {"list": [profile()]}},
        {"code": 0, "data": {"list": [profile()]}},
    ))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=next(responses)))
    ) as http:
        client: Final = AdsPowerClient("http://local:50325", "key", client=http)
        started: Final = monotonic()
        await client.list_profiles(1)
        await client.proxy("profile-1")
    assert monotonic() - started >= 2


@pytest.mark.asyncio
async def test_manager_routes_require_auth_and_reject_invalid_selection() -> None:
    from typing import Annotated

    from account_pool.adspower_api import create_adspower_router
    from fastapi import FastAPI, Header, HTTPException

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        if authorization != "Bearer admin":
            raise HTTPException(401)

    repository: Final = MemoryCatalog()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"code": 0, "data": {"list": [profile()]}}))
    ) as upstream:
        app: Final = FastAPI()
        app.include_router(
            create_adspower_router(
                AdsPowerSyncService(AdsPowerClient("http://local:50325", "key", client=upstream), repository), authorize
            )
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://manager") as client:
            assert (await client.get("/api/adspower/profiles")).status_code == 401
            client.headers["Authorization"] = "Bearer admin"
            assert (await client.post("/api/adspower/sync", json={"profile_ids": []})).status_code == 422
            response: Final = await client.post("/api/adspower/sync", json={"profile_ids": ["profile-1"]})
            assert response.status_code == 200
            assert response.json()["synced"][0]["created"]
            assert "private-password" not in response.text
