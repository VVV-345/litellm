"""测试 Clash 控制器封装的请求契约与响应解析。"""

from __future__ import annotations

from typing import Final

import httpx
import pytest

from account_pool.clash import ClashController, ClashError, ClashProxyNode


def _controller(handler) -> tuple[ClashController, httpx.AsyncClient]:
    client: Final = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ClashController("http://clash.test:9090", secret="s3cret", client=client), client


@pytest.mark.asyncio
async def test_list_nodes_parses_proxies_and_filters_builtin_entries() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/proxies"
        assert request.headers["Authorization"] == "Bearer s3cret"
        return httpx.Response(
            200,
            json={
                "proxies": {
                    "美国01": {"type": "Shadowsocks"},
                    "日本02": {"type": "Vmess"},
                    "DIRECT": {"type": "Direct"},
                    "REJECT": {"type": "Reject"},
                }
            },
        )

    controller, client = _controller(handler)
    try:
        nodes: Final = await controller.list_nodes()
    finally:
        await client.aclose()

    assert nodes == (ClashProxyNode(name="日本02", proxy_type="Vmess"), ClashProxyNode(name="美国01", proxy_type="Shadowsocks"))


@pytest.mark.asyncio
async def test_list_nodes_rejects_malformed_payload_and_missing_secret() -> None:
    async def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    controller, client = _controller(malformed)
    try:
        with pytest.raises(ClashError):
            await controller.list_nodes()
    finally:
        await client.aclose()

    async def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    controller, client = _controller(unauthorized)
    try:
        with pytest.raises(ClashError, match="secret"):
            await controller.list_nodes()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_selector_current_returns_now_field() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/proxies/pool-01"
        return httpx.Response(200, json={"name": "pool-01", "type": "Selector", "now": "美国01"})

    controller, client = _controller(handler)
    try:
        current: Final = await controller.selector_current("pool-01")
    finally:
        await client.aclose()

    assert current == "美国01"


@pytest.mark.asyncio
async def test_switch_selector_puts_node_and_rejects_unexpected_status() -> None:
    bodies: list[object] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/proxies/pool-02"
        assert request.method == "PUT"
        bodies.append(request.read())
        return httpx.Response(204)

    controller, client = _controller(handler)
    try:
        await controller.switch_selector("pool-02", "日本02")
    finally:
        await client.aclose()

    assert bodies == ['{"name":"日本02"}'.encode("utf-8")]

    async def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={})

    controller, client = _controller(rejected)
    try:
        with pytest.raises(ClashError):
            await controller.switch_selector("pool-02", "DIRECT")
    finally:
        await client.aclose()
