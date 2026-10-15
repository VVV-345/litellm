"""验证 ISP 检测出口、近似位置及失败脱敏，不接触真实代理。"""

from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from account_pool.proxy_catalog import ISPProxyProfile


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "timeout", "connect", "geo", "invalid_ip", "private_ip", "geo_timeout", "mismatch"])
async def test_isp_probe_uses_proxy_and_preserves_partial_results(failure):
    from account_pool.isp_probe import ISPProbe

    profile = ISPProxyProfile(
        id=uuid4(), name="not a location", protocol="socks5", host="proxy.example", port=1080,
        username="user@name", password="secret:/@",
    )
    requests = []
    proxies = []

    def handler(request):
        requests.append(request)
        assert "proxy-authorization" not in request.headers
        if request.url.host == "api.ipify.org":
            if failure == "timeout":
                raise httpx.ReadTimeout("secret:/@", request=request)
            if failure == "connect":
                raise httpx.ConnectError("secret:/@", request=request)
            return httpx.Response(200, json={"ip": "invalid" if failure == "invalid_ip" else "127.0.0.1" if failure == "private_ip" else "8.8.8.8"})
        assert str(request.url) == "https://ipwho.is/8.8.8.8"
        if failure == "geo_timeout":
            raise httpx.ReadTimeout("secret:/@", request=request)
        if failure == "geo":
            return httpx.Response(429)
        return httpx.Response(200, json={
            "success": True, "ip": "1.1.1.1" if failure == "mismatch" else "8.8.8.8", "country": "United States", "region": "California",
            "city": "Mountain View", "connection": {"isp": "Example operator"},
        })

    @asynccontextmanager
    async def factory(proxy):
        proxies.append(proxy)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    result = await ISPProbe(client_factory=factory).probe(profile)
    assert result.profile_id == profile.id
    assert result.checked_at.tzinfo is not None
    assert proxies == ["socks5://user%40name:secret%3A%2F%40@proxy.example:1080"]
    assert "secret" not in result.model_dump_json()
    if failure in ("timeout", "connect", "invalid_ip", "private_ip"):
        assert result.status == ("timeout" if failure == "timeout" else "error")
        assert result.exit_ip is None
        assert result.delay_ms is None
        assert len(requests) == 1
    else:
        assert result.status == "ok"
        assert result.exit_ip == "8.8.8.8"
        assert result.delay_ms >= 0
        assert result.country == (None if failure in ("geo", "geo_timeout", "mismatch") else "United States")
        assert result.city == (None if failure in ("geo", "geo_timeout", "mismatch") else "Mountain View")
        assert result.isp == (None if failure in ("geo", "geo_timeout", "mismatch") else "Example operator")


@pytest.mark.asyncio
async def test_catalog_probe_does_not_change_or_save_profile():
    from datetime import datetime, timezone

    from account_pool.isp_probe import ISPProbeResult
    from account_pool.proxy_catalog_service import ProxyCatalogService

    profile = ISPProxyProfile(id=uuid4(), name="ISP", protocol="http", host="proxy.example", port=80)

    class Repository:
        async def get_isp(self, identity):
            return profile if identity == profile.id else None

    class Probe:
        async def probe(self, value):
            assert value is profile
            return ISPProbeResult(profile_id=value.id, status="timeout", checked_at=datetime.now(timezone.utc))

    service = ProxyCatalogService(Repository(), probe=Probe())
    assert (await service.probe_isp(profile.id)).status == "timeout"
    with pytest.raises(ValueError, match="not found"):
        await service.probe_isp(uuid4())


@pytest.mark.asyncio
async def test_manager_probe_route_auth_unknown_profile_and_read_only_result():
    from datetime import datetime, timezone

    from account_pool.api import create_router
    from account_pool.isp_probe import ISPProbeResult
    from account_pool.proxy_catalog_service import ProxyCatalogService
    from fastapi import FastAPI

    profile = ISPProxyProfile(id=uuid4(), name="ISP", protocol="http", host="proxy.example", port=80)
    calls = []

    class Repository:
        async def get_isp(self, identity):
            calls.append(identity)
            return profile if identity == profile.id else None

    class Probe:
        async def probe(self, value):
            return ISPProbeResult(profile_id=value.id, status="ok", checked_at=datetime.now(timezone.utc),
                                  exit_ip="8.8.8.8", delay_ms=12)

    app = FastAPI()
    app.include_router(create_router(object(), "manager-test", proxy_catalog=ProxyCatalogService(Repository(), probe=Probe())))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        path = f"/api/proxy-isps/{profile.id}/probe"
        assert (await client.post(path)).status_code == 401
        assert calls == []
        headers = {"Authorization": "Bearer manager-test"}
        response = await client.post(path, headers=headers)
        assert response.status_code == 200
        assert response.json()["exit_ip"] == "8.8.8.8"
        assert response.json()["delay_ms"] == 12
        missing = await client.post(f"/api/proxy-isps/{uuid4()}/probe", headers=headers)
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_default_probe_client_disables_environment_proxies_and_redirects():
    from account_pool.isp_probe import _client

    async with _client("http://user:secret@proxy.example:1080") as client:
        assert client.trust_env is False
        assert client.follow_redirects is False
        assert client.timeout.read == 6


@pytest.mark.asyncio
async def test_invalid_saved_proxy_address_returns_redacted_failure():
    from account_pool.isp_probe import ISPProbe

    profile = ISPProxyProfile(id=uuid4(), name="bad", protocol="http", host="bad:host", port=80,
                              password="do-not-return")
    result = await ISPProbe().probe(profile)
    assert result.status == "error"
    assert "do-not-return" not in result.model_dump_json()
