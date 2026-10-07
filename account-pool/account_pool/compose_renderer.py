"""本模块只负责生成号池 CLIProxyAPI 配置及 Compose 描述，不执行 Docker 操作。"""

import re
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID

import yaml

from account_pool.config import Settings, validate_proxy_profile_url
from account_pool.domain import EnvironmentRecord
from account_pool.oauth_browser import OAuthBrowserSession


def render_cli_proxy_config(management_key: str, gateway_key: str, *, proxy_url: str = "") -> str:
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
        "request-retry": 0,
        "max-retry-credentials": 1,
        "max-retry-interval": 0,
        "transient-error-cooldown-seconds": 1,
        "streaming": {"bootstrap-retries": 0},
        "quota-exceeded": {"switch-project": False, "switch-preview-model": False},
        "proxy-url": proxy_url,
        "ws-auth": True,
        "plugins": {"enabled": False, "dir": "/data/plugins"},
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
                "extra_hosts": ["host.docker.internal:host-gateway"],
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


def render_oauth_browser_compose(
    session: OAuthBrowserSession,
    settings: Settings,
    *,
    proxy_url: str,
    authorization_url: str,
    callback_token: str,
) -> str:
    image: Final = settings.oauth_browser_image
    if image is None or re.fullmatch(r"[^\s@]+@sha256:[a-f0-9]{64}", image) is None:
        raise ValueError("OAuth browser image must be pinned by sha256 digest")
    if not callback_token:
        raise ValueError("OAuth browser callback token is required")

    selected_proxy: Final = validate_proxy_profile_url(proxy_url)
    authorization: Final = urlsplit(authorization_url)
    if (
        authorization.scheme != "https"
        or authorization.hostname is None
        or authorization.username is not None
        or authorization.password is not None
    ):
        raise ValueError("OAuth authorization URL must be credential-free HTTPS")

    session_slug: Final = session.id.hex
    project_name: Final = f"account-pool-oauth-browser-{session_slug}"
    compose: Final = {
        "name": project_name,
        "services": {
            "browser": {
                "image": image,
                "init": True,
                "read_only": True,
                "user": "10001:10001",
                "mem_limit": "1g",
                "cpus": "2.0",
                "pids_limit": 256,
                "shm_size": "256m",
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=128m"],
                "environment": {
                    "CHROME_PROXY": "http://egress-relay:8080",
                    "OAUTH_AUTHORIZATION_URL": authorization_url,
                },
                "networks": {"browser": {"aliases": ["browser"]}},
                "restart": "no",
            },
            "callback-relay": {
                "image": image,
                "entrypoint": ["sleep"],
                "command": ["infinity"],
                "read_only": True,
                "user": "10001:10001",
                "mem_limit": "64m",
                "cpus": "0.25",
                "pids_limit": 32,
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=8m"],
                "environment": {"CALLBACK_TOKEN": callback_token},
                "network_mode": "service:browser",
                "restart": "no",
            },
            "egress-relay": {
                "image": image,
                "command": ["egress-relay"],
                "read_only": True,
                "user": "10001:10001",
                "mem_limit": "64m",
                "cpus": "0.25",
                "pids_limit": 32,
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=8m"],
                "environment": {"PROXY_URL": selected_proxy},
                "networks": ["browser", "egress"],
                "restart": "no",
            },
        },
        "networks": {
            "browser": {
                "name": f"{project_name}-browser",
                "driver": "bridge",
                "internal": True,
            },
            "egress": {
                "name": f"{project_name}-egress",
                "driver": "bridge",
                "internal": False,
            },
        },
    }
    return yaml.safe_dump(compose, sort_keys=False, allow_unicode=False)


def data_volume_name(environment_id: UUID) -> str:
    return f"account-pool-{environment_id.hex}-data"
