"""本模块解析 ISP 代理导入数据，不保存、记录或公开导入凭据。"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Literal, cast
from urllib.parse import unquote, urlsplit
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("name", ("name", "label", "备注", "名称")),
    ("protocol", ("protocol", "type", "scheme", "协议")),
    ("host", ("host", "server", "ip", "address", "主机", "服务器", "地址")),
    ("port", ("port", "端口")),
    ("username", ("username", "user", "login", "用户名", "用户")),
    ("password", ("password", "pass", "pwd", "密码")),
    ("expires_on", ("expires_on", "expiry", "expiration", "expires", "expire_at", "end_date", "到期日", "截止日期")),
)


class ISPProxyInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    protocol: Literal["http", "https", "socks5"] = "http"
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    username: str = Field(default="", max_length=512, repr=False)
    password: str = Field(default="", max_length=2048, repr=False)
    expires_on: date | None = None

    @field_validator("name")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        normalized: str = value.strip()
        if not normalized:
            raise ValueError("invalid required text")
        return normalized

    @field_validator("host")
    @classmethod
    def trim_host(cls, value: str) -> str:
        normalized: str = value.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("invalid host")
        return normalized


class ISPProxyProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str = Field(min_length=1, max_length=120)
    protocol: Literal["http", "https", "socks5"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    username: str = Field(default="", max_length=512, repr=False)
    password: str = Field(default="", max_length=2048, repr=False)
    expires_on: date | None = None
    enabled: bool = True

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        normalized: str = value.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("invalid host")
        return normalized

    def expired(self, today: date) -> bool:
        return self.expires_on is not None and self.expires_on <= today


class ISPSubscriptionNodeProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str = Field(min_length=1, max_length=120)
    proxy_type: str
    config: dict[str, JsonValue] = Field(repr=False)
    enabled: bool = True


ISPProxyCatalogProfile = ISPProxyProfile | ISPSubscriptionNodeProfile


class ISPProbeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: UUID
    status: Literal["ok", "timeout", "error"]
    checked_at: datetime
    exit_ip: str | None = None
    delay_ms: int | None = None
    country: str | None = None
    region: str | None = None
    city: str | None = None
    isp: str | None = None
    error: str | None = None


class ISPProxyProfileView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str
    source: Literal["manual", "subscription"] = "manual"
    protocol: Literal["http", "https", "socks5"] | None = None
    host: str | None = None
    port: int | None = None
    proxy_type: str | None = None
    expires_on: date | None
    enabled: bool
    credentials_configured: bool
    bound_ports: tuple[int, ...] = ()
    last_probe: ISPProbeResult | None = None


class ISPSubscriptionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    configured: bool
    refreshed_at: datetime | None = None
    node_count: int = Field(ge=0)
    enabled_node_count: int = Field(ge=0)


class ProxyConfigurationProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, repr=False)
    source_url: str | None = Field(default=None, repr=False)
    created_at: datetime


class ProxyConfigurationProfileView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str
    created_at: datetime
    active: bool


def parse_isp_import(source: str) -> tuple[ISPProxyInput, ...]:
    normalized: str = source.strip()
    if not normalized:
        raise ValueError("import is empty")
    structured: object | None = _parse_structured(normalized)
    if structured is not None:
        entries: tuple[ISPProxyInput, ...] = _structured_entries(structured)
        if entries:
            return entries
        raise ValueError("import contains no proxy entries")
    lines: tuple[tuple[int, str], ...] = tuple(
        (number, line.strip()) for number, line in enumerate(normalized.splitlines(), 1) if line.strip()
    )
    first_line: str = lines[0][1]
    delimiter: str | None = "\t" if "\t" in first_line else "," if "," in first_line else None
    if delimiter is not None:
        rows: tuple[dict[str, str], ...] = _csv_rows(normalized, delimiter)
        if rows:
            return tuple(_mapping_entry(row, index) for index, row in enumerate(rows, 2))
    return tuple(_line_entry(line, number) for number, line in lines)


def _parse_structured(source: str) -> object | None:
    if not source.startswith(("{", "[", "---", "proxies:")):
        return None
    try:
        return cast(object, yaml.safe_load(source))
    except yaml.YAMLError as error:
        raise ValueError("invalid YAML or JSON import") from error


def _structured_entries(value: object) -> tuple[ISPProxyInput, ...]:
    if isinstance(value, dict):
        mapping: dict[object, object] = cast(dict[object, object], value)
        proxies: object = mapping.get("proxies")
        if isinstance(proxies, list):
            return tuple(_mapping_entry(_string_mapping(item), index) for index, item in enumerate(proxies, 1))
        if any(key in mapping for key in ("host", "server", "address")):
            return (_mapping_entry(_string_mapping(mapping), 1),)
        return ()
    if isinstance(value, list):
        return tuple(_mapping_entry(_string_mapping(item), index) for index, item in enumerate(value, 1))
    return ()


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("proxy entry must be an object")
    mapping: dict[object, object] = cast(dict[object, object], value)
    return {str(key): str(item) for key, item in mapping.items() if item is not None}


def _csv_rows(source: str, delimiter: str) -> tuple[dict[str, str], ...]:
    reader: csv.DictReader[str] = csv.DictReader(io.StringIO(source), delimiter=delimiter)
    headers: tuple[str, ...] = tuple((header or "").strip() for header in reader.fieldnames or ())
    if not any(_canonical_header(header) is not None for header in headers):
        return ()
    rows: tuple[dict[str | None, str | None], ...] = tuple(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("invalid CSV row")
    return tuple(
        {
            canonical: value.strip()
            for header, value in row.items()
            if (canonical := _canonical_header(header or "")) is not None and value is not None
        }
        for row in rows
    )


def _canonical_header(header: str) -> str | None:
    normalized: str = header.strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    return next(
        (
            field
            for field, aliases in _ALIASES
            if normalized
            in {alias.lower().replace("_", "").replace("-", "").replace(" ", "") for alias in aliases}
        ),
        None,
    )


def _mapping_entry(row: dict[str, str], row_number: int) -> ISPProxyInput:
    host: str = row.get("host", row.get("server", row.get("address", ""))).strip()
    port_text: str = row.get("port", "").strip()
    try:
        port: int = int(port_text)
        expiry: date | None = _expiry(row.get("expires_on", row.get("expiry", "")))
        protocol: str = row.get("protocol", row.get("type", "http")).strip().lower()
        normalized_protocol: str = "socks5" if protocol == "socks5h" else protocol
        return ISPProxyInput(
            name=row.get("name", "").strip() or f"{host}:{port}",
            protocol=normalized_protocol,
            host=host,
            port=port,
            username=row.get("username", row.get("user", "")).strip(),
            password=row.get("password", row.get("pass", "")),
            expires_on=expiry,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid proxy entry at row {row_number}") from error


def _expiry(value: str) -> date | None:
    normalized: str = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date()
        except ValueError as error:
            raise ValueError("invalid expiration date") from error


def _line_entry(line: str, row_number: int) -> ISPProxyInput:
    try:
        if "://" in line:
            parsed = urlsplit(line)
            host: str = parsed.hostname or ""
            port: int = parsed.port or 0
            protocol: str = "socks5" if parsed.scheme.lower() == "socks5h" else parsed.scheme.lower()
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                raise ValueError("not a proxy origin")
            return ISPProxyInput(
                name=f"{host}:{port}",
                protocol=protocol,
                host=host,
                port=port,
                username=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
            )
        host, port_text, username, password = line.split(":", 3)
        port = int(port_text)
        return ISPProxyInput(
            name=f"{host}:{port}", host=host, port=port, username=username, password=password
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid proxy entry at row {row_number}") from error
