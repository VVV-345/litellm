"""本模块读取号池运行配置，并集中校验安全相关部署参数。"""

import ipaddress
import re
from pathlib import Path
from typing import Annotated, Final
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_CLI_PROXY_IMAGE: Final = (
    "eceasy/cli-proxy-api:v7.2.146@sha256:238691ac26ce55e4d1c5219d72e3ad74838f81eda26359912eeb415e2820d163"
)
DEFAULT_FREEBUFF2API_IMAGE: Final = "litellm-freebuff2api-proxy:1.0.0"


DEFAULT_PROXY_GATEWAY_HOST: Final = "host.docker.internal"


def _validate_hostname(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?", value):
        raise ValueError("must be a hostname or IP address")
    return value


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
    freebuff2api_image: str = DEFAULT_FREEBUFF2API_IMAGE
    clash_controller_url: str = ""
    clash_secret: str = ""
    clash_config_path: str = Field(default="", max_length=1024)
    clash_gateway_ports: Annotated[tuple[int, ...], NoDecode] = ()
    proxy_gateway_host: str = DEFAULT_PROXY_GATEWAY_HOST

    @field_validator("clash_controller_url")
    @classmethod
    def validate_clash_controller_url(cls, value: str) -> str:
        if not value.strip():
            return ""
        parsed: Final = urlsplit(value.strip())
        try:
            port: Final = parsed.port
        except ValueError as error:
            raise ValueError("clash_controller_url must use a valid port") from error
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or port is None
            or not 1 <= port <= 65535
        ):
            raise ValueError("clash_controller_url must be a credential-free HTTP(S) origin with an explicit port")
        return value.strip()

    @field_validator("clash_config_path")
    @classmethod
    def normalize_clash_config_path(cls, value: str) -> str:
        return value.strip()

    @field_validator("clash_gateway_ports", mode="before")
    @classmethod
    def parse_clash_gateway_ports(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(int(part.strip()) for part in value.split(",") if part.strip())
        return value

    @field_validator("clash_gateway_ports")
    @classmethod
    def validate_clash_gateway_ports(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(value)) != len(value):
            raise ValueError("clash_gateway_ports must not repeat")
        if any(not 1 <= port <= 65535 for port in value):
            raise ValueError("clash_gateway_ports entries must be within 1-65535")
        return tuple(sorted(value))

    @field_validator("proxy_gateway_host")
    @classmethod
    def validate_proxy_gateway_host(cls, value: str) -> str:
        return _validate_hostname(value.strip())

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
        parsed: Final = urlsplit(normalized)
        try:
            port: Final = parsed.port
        except ValueError as error:
            raise ValueError("docker_host must use a valid TCP port") from error
        if parsed.scheme != "tcp" or not parsed.hostname or port != 2375:
            raise ValueError("docker_host must be the TCP Docker Socket Proxy endpoint on port 2375")
        host: Final = parsed.hostname
        try:
            address: Final = ipaddress.ip_address(host)
        except ValueError:
            if host != "docker-socket-proxy":
                raise ValueError("docker_host must use the docker-socket-proxy service name")
        else:
            if address.is_loopback:
                raise ValueError("docker_host must not use a loopback host")
            raise ValueError("docker_host must use the docker-socket-proxy service name")
        if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
            raise ValueError(
                "docker_host must not include credentials, paths, query strings, fragments, or loopback hosts"
            )
        return normalized


def validate_proxy_profile_url(value: str) -> str:
    normalized: Final = value.strip()
    parsed: Final = urlsplit(normalized)
    try:
        port: Final = parsed.port
    except ValueError as error:
        raise ValueError("proxy URL must use a valid port") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is not None
        and not 1 <= port <= 65535
    ):
        raise ValueError("proxy URL must be a credential-free HTTP(S) origin with a valid port")
    return normalized
