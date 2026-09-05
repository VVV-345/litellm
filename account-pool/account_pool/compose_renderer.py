"""本模块只负责生成号池 CLIProxyAPI 和 FreeBuff2API 配置及 Compose 描述，不执行 Docker 操作。"""

import json
from typing import Final
from uuid import UUID

import yaml

from account_pool.config import Settings
from account_pool.domain import EnvironmentRecord


_FREEBUFF2API_SERVICE: Final = "freebuff2api"
_FREEBUFF2API_PORT: Final = 8787


def render_freebuff_credentials(auth_token: str) -> str:
    """server.js 兼容的多账号聚合格式：accounts.<key>.authToken。"""
    credential: Final = {
        "accounts": {
            "default": {
                "email": "freebuff",
                "authToken": auth_token,
                "name": "freebuff",
            }
        }
    }
    return json.dumps(credential, ensure_ascii=False)


def render_cli_proxy_config(management_key: str, gateway_key: str) -> str:
    config: Final = {
        "host": "0.0.0.0",
        "port": 8317,
        "remote-management": {
            "allow-remote": True,
            "secret-key": management_key,
            "disable-control-panel": True,
        },
        "auth-dir": "/data/auths",
        "api-keys": [gateway_key],
        "debug": False,
        "logging-to-file": False,
        "usage-statistics-enabled": False,
        "save-cooldown-status": True,
        "proxy-url": "",
        "ws-auth": True,
    }
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=False)


def render_compose(record: EnvironmentRecord, settings: Settings) -> str:
    environment_slug: Final = record.id.hex
    service_name: Final = f"cliproxy-{environment_slug}"
    network_name: Final = f"account-pool-{environment_slug}"
    volume_name: Final = data_volume_name(record.id)
    compose: Final = {
        "name": f"account-pool-{environment_slug}",
        "services": {
            "cli-proxy-api": {
                "image": settings.cli_proxy_image,
                "command": ["./CLIProxyAPI", "-config", "/data/config/config.yaml"],
                "restart": "unless-stopped",
                "read_only": True,
                "user": settings.cli_proxy_user,
                "mem_limit": "512m",
                "cpus": "1.0",
                "pids_limit": 256,
                "ulimits": {"nofile": {"soft": 4096, "hard": 4096}},
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "10m", "max-file": "3"},
                },
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=32m"],
                "volumes": [
                    "cliproxy-data:/data:rw",
                ],
                "networks": {"environment": {"aliases": [service_name]}},
            }
        },
        "networks": {
            "environment": {"name": network_name, "driver": "bridge", "internal": False},
        },
        "volumes": {"cliproxy-data": {"name": volume_name}},
    }
    return yaml.safe_dump(compose, sort_keys=False, allow_unicode=False)


def data_volume_name(environment_id: UUID) -> str:
    return f"account-pool-{environment_id.hex}-data"


def render_freebuff_compose(record: EnvironmentRecord, settings: Settings, gateway_key: str) -> str:
    environment_slug: Final = record.id.hex
    service_name: Final = f"freebuff-{environment_slug}"
    network_name: Final = f"account-pool-{environment_slug}"
    volume_name: Final = data_volume_name(record.id)
    compose: Final = {
        "name": f"account-pool-{environment_slug}",
        "services": {
            _FREEBUFF2API_SERVICE: {
                "image": settings.freebuff2api_image,
                # 上游镜像的引导器会在启动时拉取未固定的最新代码，固定 entrypoint 让容器只运行镜像内置版本。
                "entrypoint": ["node", "/app/server.js"],
                "environment": [
                    f"PORT={_FREEBUFF2API_PORT}",
                    "HOST=0.0.0.0",
                    f"FREEBUFF_API_KEY={gateway_key}",
                    "FREEBUFF_DEBUG=false",
                ],
                "restart": "unless-stopped",
                "read_only": True,
                "user": "1000:1000",
                "mem_limit": "512m",
                "cpus": "1.0",
                "pids_limit": 256,
                "ulimits": {"nofile": {"soft": 4096, "hard": 4096}},
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "10m", "max-file": "3"},
                },
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=32m"],
                "volumes": [
                    "freebuff-data:/app/credentials:ro",
                ],
                "networks": {"environment": {"aliases": [service_name]}},
            }
        },
        "networks": {
            "environment": {"name": network_name, "driver": "bridge", "internal": False},
        },
        "volumes": {"freebuff-data": {"name": volume_name}},
    }
    return yaml.safe_dump(compose, sort_keys=False, allow_unicode=False)
