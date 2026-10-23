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
        def __init__(self):
            self.saved = []

        async def get_isp(self, identity):
            return profile if identity == profile.id else None

        async def save_isp_probe(self, result):
            self.saved.append(result)
            return result

    class Probe:
        async def probe(self, value):
            assert value is profile
            return ISPProbeResult(profile_id=value.id, status="timeout", checked_at=datetime.now(timezone.utc))

    repository = Repository()
    service = ProxyCatalogService(repository, probe=Probe())
    result = await service.probe_isp(profile.id)
    assert result.status == "timeout"
    assert repository.saved == [result]
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

        async def save_isp_probe(self, result):
            return result

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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "ip_timeout", "ip_error", "openai_timeout", "openai_error"])
async def test_gateway_probe_uses_listener_and_separates_ip_and_openai_outcomes(failure):
    from account_pool.isp_probe import GatewayISPProbe

    requests = []
    proxies = []

    def handler(request):
        requests.append(request)
        if request.url.host == "api.ipify.org":
            if failure == "ip_timeout":
                raise httpx.ReadTimeout("sensitive upstream body", request=request)
            if failure == "ip_error":
                raise httpx.ConnectError("sensitive upstream body", request=request)
            return httpx.Response(200, json={"ip": "8.8.8.8"})
        if request.url.host == "ipwho.is":
            return httpx.Response(200, json={
                "success": True,
                "ip": "8.8.8.8",
                "country": "United States",
                "region": "California",
                "city": "Mountain View",
                "connection": {"isp": "Example operator"},
            })
        assert str(request.url) == "https://api.openai.com/v1/models"
        if failure == "openai_timeout":
            raise httpx.ReadTimeout("sensitive upstream body", request=request)
        if failure == "openai_error":
            raise httpx.ConnectError("sensitive upstream body", request=request)
        return httpx.Response(401, json={"error": {"message": "sensitive upstream body"}})

    @asynccontextmanager
    async def factory(proxy):
        proxies.append(proxy)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    result = await GatewayISPProbe(client_factory=factory).probe(7891, "http://host.docker.internal:7891")

    assert result.port == 7891
    assert proxies == ["http://host.docker.internal:7891"]
    expected_hosts = ["api.ipify.org"]
    if failure not in {"ip_timeout", "ip_error"}:
        expected_hosts.append("ipwho.is")
    expected_hosts.append("api.openai.com")
    assert [request.url.host for request in requests] == expected_hosts
    assert result.ip_status == (
        "timeout" if failure == "ip_timeout" else "error" if failure == "ip_error" else "ok"
    )
    assert result.exit_ip == (None if failure in {"ip_timeout", "ip_error"} else "8.8.8.8")
    assert result.openai_status == (
        "timeout" if failure == "openai_timeout" else "error" if failure == "openai_error" else "response"
    )
    assert result.openai_http_status == (None if failure in {"openai_timeout", "openai_error"} else 401)
    assert "sensitive upstream body" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("list", "probe", "import", "update", "delete"))
async def test_manager_isp_storage_failure_is_explicit_and_redacted(operation):
    import psycopg
    from account_pool.api import create_router
    from account_pool.proxy_catalog_service import ProxyCatalogService
    from fastapi import FastAPI

    class Repository:
        async def list_isps(self):
            raise psycopg.OperationalError("postgresql://private-user:private-password@database")

        async def get_isp(self, identity):
            raise psycopg.OperationalError("postgresql://private-user:private-password@database")

        async def save_isps(self, profiles):
            raise psycopg.OperationalError("postgresql://private-user:private-password@database")

        async def delete_isp(self, identity):
            raise psycopg.OperationalError("postgresql://private-user:private-password@database")

    identity = uuid4()
    requests = {
        "list": ("GET", "/api/proxy-isps", None),
        "probe": ("POST", f"/api/proxy-isps/{identity}/probe", None),
        "import": ("POST", "/api/proxy-isps/import", {"content": "http://proxy.test:8080"}),
        "update": ("PUT", f"/api/proxy-isps/{identity}", {
            "name": "ISP", "protocol": "http", "host": "proxy.test", "port": 8080, "enabled": True,
        }),
        "delete": ("DELETE", f"/api/proxy-isps/{identity}", None),
    }
    app = FastAPI()
    app.include_router(create_router(object(), "manager-test", proxy_catalog=ProxyCatalogService(Repository())))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test",
    ) as client:
        method, path, body = requests[operation]
        response = await client.request(method, path, json=body, headers={"Authorization": "Bearer manager-test"})
    assert response.status_code == 503
    assert response.json()["detail"] == "ISP catalog storage unavailable; retry"
    assert "private-" not in response.text


@pytest.mark.asyncio
async def test_probe_save_failure_is_not_reported_as_a_successful_measurement():
    from datetime import datetime, timezone

    import psycopg
    from account_pool.api import create_router
    from account_pool.isp_probe import ISPProbeResult
    from account_pool.proxy_catalog_service import ProxyCatalogService
    from fastapi import FastAPI

    profile = ISPProxyProfile(id=uuid4(), name="ISP", protocol="http", host="proxy.example", port=80)

    class Repository:
        async def get_isp(self, identity):
            return profile

        async def save_isp_probe(self, result):
            raise psycopg.OperationalError("private database connection details")

    class Probe:
        async def probe(self, value):
            return ISPProbeResult(profile_id=value.id, status="ok", checked_at=datetime.now(timezone.utc),
                                  exit_ip="8.8.8.8", delay_ms=12)

    app = FastAPI()
    app.include_router(create_router(object(), "manager-test", proxy_catalog=ProxyCatalogService(Repository(), probe=Probe())))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test",
    ) as client:
        response = await client.post(f"/api/proxy-isps/{profile.id}/probe", headers={"Authorization": "Bearer manager-test"})
    assert response.status_code == 503
    assert response.json()["detail"] == "ISP catalog storage unavailable; retry"
    assert "private" not in response.text and "8.8.8.8" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("import", "update"))
async def test_invalid_isp_request_does_not_echo_credentials(operation):
    from account_pool.api import create_router
    from fastapi import FastAPI

    secret = "must-not-echo-private-password"
    path, body = (
        ("/api/proxy-isps/import", {"content": {"password": secret}})
        if operation == "import" else
        (f"/api/proxy-isps/{uuid4()}", {
            "name": "ISP", "protocol": "http", "host": "proxy.test", "port": 80,
            "enabled": True, "password": {"invalid": secret},
        })
    )
    app = FastAPI()
    app.include_router(create_router(object(), "manager-test", proxy_catalog=object()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(
            "POST" if operation == "import" else "PUT", path, json=body,
            headers={"Authorization": "Bearer manager-test"},
        )
    assert response.status_code == 422
    assert secret not in response.text
