"""本模块校验 ISP Clash 订阅节点，并保留可用于 Mihomo 的完整出站配置。"""

from __future__ import annotations

from typing import Final, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

_MAX_SUBSCRIPTION_BYTES: Final = 2 * 1024 * 1024
_PROXY_TYPES: Final = frozenset(
    {
        "anytls",
        "http",
        "hysteria",
        "hysteria2",
        "juicity",
        "mieru",
        "snell",
        "socks5",
        "ss",
        "ssr",
        "ssh",
        "trojan",
        "tuic",
        "vless",
        "vmess",
        "wireguard",
    }
)
_NODE_CONFIG: Final = TypeAdapter(dict[str, JsonValue])


class MihomoISPNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    proxy_type: str
    config: dict[str, JsonValue] = Field(repr=False)


def parse_isp_subscription(content: str) -> tuple[MihomoISPNode, ...]:
    if len(content.encode("utf-8")) > _MAX_SUBSCRIPTION_BYTES:
        raise ValueError("ISP subscription is too large")
    try:
        document_value: Final = cast(object, yaml.safe_load(content))
    except yaml.YAMLError as error:
        raise ValueError("ISP subscription is invalid YAML") from error
    if not isinstance(document_value, dict):
        raise ValueError("ISP subscription must be a YAML object")
    document_mapping: Final = cast(dict[object, object], document_value)
    if any(not isinstance(key, str) for key in document_mapping):
        raise ValueError("ISP subscription must use string mapping keys")
    document: Final = {key: value for key, value in document_mapping.items() if isinstance(key, str)}
    proxies: Final = document.get("proxies")
    if not isinstance(proxies, list) or not proxies:
        raise ValueError("ISP subscription has no top-level proxy nodes")
    entries: Final = cast(list[object], proxies)
    nodes: Final = tuple(_parse_node(entry) for entry in entries)
    names: Final = tuple(node.name for node in nodes)
    if len(set(names)) != len(names):
        raise ValueError("ISP subscription contains duplicate node names")
    return nodes


def _parse_node(value: object) -> MihomoISPNode:
    if not isinstance(value, dict):
        raise ValueError("ISP subscription contains an invalid proxy node")
    value_mapping: Final = cast(dict[object, object], value)
    if any(not isinstance(key, str) for key in value_mapping):
        raise ValueError("ISP subscription contains an invalid proxy node")
    try:
        node_mapping: Final = {key: item for key, item in value_mapping.items() if isinstance(key, str)}
        config: Final = _NODE_CONFIG.validate_python(node_mapping)
        name: Final = config.get("name")
        proxy_type: Final = config.get("type")
        server: Final = config.get("server")
        port: Final = config.get("port")
    except ValidationError as error:
        raise ValueError("ISP subscription contains an invalid proxy node") from error
    if (
        not isinstance(name, str)
        or not name.strip()
        or name != name.strip()
        or len(name) > 120
        or not isinstance(proxy_type, str)
        or proxy_type not in _PROXY_TYPES
        or not isinstance(server, str)
        or not server.strip()
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
    ):
        raise ValueError("ISP subscription contains an invalid proxy node")
    return MihomoISPNode(name=name, proxy_type=proxy_type, config=config)
