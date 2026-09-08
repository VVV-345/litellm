"""本模块封装 Clash RESTful 控制器对话：拉取节点与代理组、切换出口，供代理网关编排层调用。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import quote, urlsplit

import httpx

_TIMEOUT_SECONDS: Final = 10.0
_DELAY_TEST_URL: Final = "https://www.gstatic.com/generate_204"
_DELAY_TIMEOUT_MS: Final = 5000

ClashDelayStatus = Literal["ok", "timeout", "error"]


@dataclass(frozen=True, slots=True)
class ClashDelayResult:
    status: ClashDelayStatus
    delay_ms: int | None = None


class ClashError(Exception):
    """Clash 控制器不可达、响应畸形或指令被拒绝时抛出。"""


@dataclass(frozen=True, slots=True)
class ClashProxyNode:
    name: str
    proxy_type: str


def _proxies_payload_to_nodes(payload: object) -> tuple[ClashProxyNode, ...]:
    if not isinstance(payload, dict):
        raise ClashError("clash /proxies response must be an object")
    proxies: Final = payload.get("proxies")
    if not isinstance(proxies, dict):
        raise ClashError("clash /proxies response missing proxies map")
    nodes: list[ClashProxyNode] = []
    for name, entry in proxies.items():
        if not isinstance(entry, dict):
            continue
        proxy_type: Final = entry.get("type")
        if not isinstance(name, str) or not isinstance(proxy_type, str):
            continue
        if proxy_type in {"Direct", "Reject", "Compatible", "Pass"}:
            continue
        nodes.append(ClashProxyNode(name=name, proxy_type=proxy_type))
    return tuple(sorted(nodes, key=lambda node: node.name))


class ClashController:
    """与单个 Clash 外部控制器（external-controller）通信的薄封装。"""

    def __init__(
        self,
        controller_url: str,
        secret: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed: Final = urlsplit(controller_url)
        self._base_url: Final = f"{parsed.scheme}://{parsed.netloc}"
        self._secret: Final = secret
        self._client: Final = client or httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, trust_env=False)
        self._owns_client: Final = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_nodes(self) -> tuple[ClashProxyNode, ...]:
        payload: Final = await self._get_json("/proxies")
        return _proxies_payload_to_nodes(payload)

    async def selector_current(self, selector_name: str) -> str | None:
        payload: Final = await self._get_json(f"/proxies/{_quote(selector_name)}")
        if not isinstance(payload, dict):
            raise ClashError("clash proxy response must be an object")
        now: Final = payload.get("now")
        return now if isinstance(now, str) else None

    async def selector_currents(self, selector_names: tuple[str, ...]) -> tuple[str | None, ...]:
        try:
            payload: Final = await self._get_json("/proxies")
        except httpx.HTTPError as error:
            raise ClashError("clash controller is unreachable") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("proxies"), dict):
            raise ClashError("clash /proxies response missing proxies map")
        proxies: Final = payload["proxies"]
        return tuple(
            entry["now"] if isinstance(entry, dict) and isinstance(entry.get("now"), str) else None
            for name in selector_names
            for entry in (proxies.get(name),)
        )

    async def measure_delay(self, node_name: str) -> ClashDelayResult:
        try:
            response: Final = await self._client.get(
                f"{self._base_url}/proxies/{_quote(node_name)}/delay",
                params={"url": _DELAY_TEST_URL, "timeout": _DELAY_TIMEOUT_MS},
                headers=self._headers(),
            )
        except httpx.HTTPError:
            return ClashDelayResult("error")
        if response.status_code == 504:
            return ClashDelayResult("timeout")
        if response.status_code != 200:
            return ClashDelayResult("error")
        try:
            payload: Final = response.json()
        except ValueError:
            return ClashDelayResult("error")
        delay: Final = payload.get("delay") if isinstance(payload, dict) else None
        if type(delay) is not int or delay < 0:
            return ClashDelayResult("error")
        return ClashDelayResult("ok", delay)

    async def switch_selector(self, selector_name: str, node_name: str) -> None:
        response: Final = await self._client.put(
            f"{self._base_url}/proxies/{_quote(selector_name)}",
            json={"name": node_name},
            headers=self._headers(),
        )
        if response.status_code == 404:
            raise ClashError(f"clash selector {selector_name!r} does not exist")
        if response.status_code != 204:
            raise ClashError(f"clash switch rejected with status {response.status_code}")

    async def _get_json(self, path: str) -> object:
        response: Final = await self._client.get(f"{self._base_url}{path}", headers=self._headers())
        if response.status_code == 401:
            raise ClashError("clash controller rejected the secret")
        if response.status_code == 404:
            raise ClashError(f"clash resource {path!r} does not exist")
        if response.status_code != 200:
            raise ClashError(f"clash controller returned status {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise ClashError("clash controller returned invalid JSON") from error

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret}"} if self._secret else {}


def _quote(value: str) -> str:
    return quote(value, safe="")
