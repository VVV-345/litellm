"""本模块读取号池运行配置，并集中校验安全相关部署参数。"""

import ipaddress
import re
from pathlib import Path
from typing import Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CLI_PROXY_IMAGE: Final = (
    "eceasy/cli-proxy-api:v7.2.146@sha256:238691ac26ce55e4d1c5219d72e3ad74838f81eda26359912eeb415e2820d163"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACCOUNT_POOL_", frozen=True)

    database_url: str
    data_root: Path = Path("/var/lib/litellm-account-pool/environments")
    manager_token: str = Field(min_length=32)
    secret_seed: str = Field(min_length=32)
    manager_container: str = Field(default="litellm-account-pool", pattern=r"^[a-zA-Z0-9_.-]+$")
    gateway_container: str = Field(default="litellm", pattern=r"^[a-zA-Z0-9_.-]+$")
    ssh_host: str = Field(min_length=1, max_length=255)
    ssh_user: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_.-]+$")
    callback_bind_host: str = Field(default="127.0.0.1")
    callback_port: int = Field(default=8091, ge=1, le=65535)
    docker_host: str = Field(default="tcp://docker-socket-proxy:2375", max_length=255)
    docker_command_timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    cli_proxy_user: str = Field(default="65532:65532", pattern=r"^[1-9][0-9]{0,9}:[1-9][0-9]{0,9}$")
    cli_proxy_image: str = DEFAULT_CLI_PROXY_IMAGE

    @field_validator("ssh_host")
    @classmethod
    def validate_ssh_host(cls, value: str) -> str:
        normalized: Final = value.strip()
        if not normalized or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", normalized):
            raise ValueError("ssh_host must be a hostname or IP address")
        return normalized

    @field_validator("callback_bind_host")
    @classmethod
    def validate_callback_bind_host(cls, value: str) -> str:
        normalized: Final = value.strip()
        if normalized == "localhost":
            return normalized
        try:
            address: Final = ipaddress.ip_address(normalized)
        except ValueError as error:
            raise ValueError("callback_bind_host must be localhost or a loopback address") from error
        if not address.is_loopback:
            raise ValueError("callback_bind_host must be localhost or a loopback address")
        return normalized

    @field_validator("docker_host")
    @classmethod
    def validate_docker_host(cls, value: str) -> str:
        normalized: Final = value.strip()
        if not normalized.startswith(("tcp://", "http://", "https://")):
            raise ValueError("docker_host must use a TCP or HTTP Docker Socket Proxy endpoint")
        if "/var/run/docker.sock" in normalized or "@" in normalized:
            raise ValueError("docker_host must not point to a raw Docker socket or include credentials")
        return normalized
