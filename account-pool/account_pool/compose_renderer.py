"""本模块只负责生成号池 CLIProxyAPI 配置和 Compose 描述，不执行 Docker 操作。"""

from typing import Final
from uuid import UUID

import yaml

from account_pool.config import Settings
from account_pool.domain import EnvironmentRecord


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
