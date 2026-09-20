"""本模块以不可逆凭据指纹维护独占归属，并提供跨进程环境操作锁。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from pydantic import JsonValue, TypeAdapter

from account_pool.repository import database_connection
from account_pool.shared.secrets import EnvironmentSecretDeriver, SecretPurpose

_JSON: Final = TypeAdapter(dict[str, JsonValue])


class CredentialConflict(ValueError):
    """凭据归属不唯一时禁止继续写入或路由。"""


@dataclass(frozen=True, slots=True)
class CredentialIdentity:
    fingerprints: tuple[str, ...]
    email: str | None = None
    account_id: str | None = None


def _text(values: Mapping[str, JsonValue], name: str) -> str | None:
    value: Final = values.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def credential_identity(content: bytes, supplier: str, secrets: EnvironmentSecretDeriver) -> CredentialIdentity:
    try:
        values: Final = _JSON.validate_json(content)
    except ValueError:
        raise CredentialConflict("认证文件必须是包含有效凭据的 JSON 对象") from None
    nested: Final = values.get("service_account")
    credential_values: Final = nested if isinstance(nested, dict) else values
    encoded: Final = _text(values, "id_token")
    try:
        claims: Final = (
            _JSON.validate_json(base64.urlsafe_b64decode(encoded.split(".")[1] + "==="))
            if encoded and len(encoded.split(".")) == 3
            else {}
        )
    except (ValueError, IndexError):
        raise CredentialConflict("认证文件的账号身份信息无效") from None
    account_claims: Final = claims.get("https://api.openai.com/auth")
    email: Final = _text(values, "email") or _text(credential_values, "client_email") or _text(claims, "email")
    account_id: Final = (
        _text(values, "account_id")
        or (_text(account_claims, "chatgpt_account_id") if isinstance(account_claims, dict) else None)
        or _text(claims, "sub")
    )
    identifiers: Final = tuple(
        (name, value)
        for name, value in (
            ("refresh_token", _text(values, "refresh_token")),
            ("api_key", _text(values, "api_key")),
            ("private_key", _text(credential_values, "private_key")),
            ("account", account_id),
            ("email", None if email is None else email.casefold()),
        )
        if value is not None
    )
    if not identifiers or not any(name in ("refresh_token", "api_key", "private_key") for name, _ in identifiers):
        raise CredentialConflict("认证文件缺少可验证归属的刷新令牌或密钥")
    key: Final = secrets.derive(UUID(int=0), SecretPurpose.CREDENTIAL_IDENTITY).encode()
    return CredentialIdentity(
        fingerprints=tuple(
            hmac.new(
                key,
                json.dumps(
                    ("credential" if name in ("refresh_token", "api_key", "private_key") else supplier, name, value)
                ).encode(),
                hashlib.sha256,
            ).hexdigest()
            for name, value in identifiers
        ),
        email=email,
        account_id=account_id,
    )


class CredentialOwnership:
    """测试使用内存存储，生产注入数据库地址以获得持久归属和跨进程互斥。"""

    def __init__(self, database_url: str | None = None) -> None:
        self._database_url: Final = database_url
        self._owners: dict[str, UUID] = {}
        self._operations: dict[UUID, asyncio.Lock] = {}

    async def initialize(self) -> None:
        if self._database_url is None:
            return
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS account_pool_credential_owners "
                "(fingerprint text PRIMARY KEY, environment_id uuid NOT NULL)"
            )

    @asynccontextmanager
    async def operation(self, owner: UUID) -> AsyncGenerator[None, None]:
        if self._database_url is None:
            async with self._operations.setdefault(owner, asyncio.Lock()):
                yield
            return
        async with database_connection(self._database_url) as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(owner),))
            yield

    async def claim(self, owner: UUID, fingerprints: tuple[str, ...]) -> None:
        if not fingerprints:
            raise CredentialConflict("凭据身份未验证，请刷新认证文件或重新授权")
        if self._database_url is None:
            if any(self._owners.get(key, owner) != owner for key in fingerprints):
                raise CredentialConflict("该账号或凭证已绑定其他卡片，一张凭证只能由一张卡片使用")
            self._owners.update(dict.fromkeys(fingerprints, owner))
            return
        # 所有指纹在同一事务内领取，冲突时回滚，避免并发导入分别占有半组身份。
        async with database_connection(self._database_url) as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(714205991)")
            for fingerprint in fingerprints:
                cursor = await connection.execute(
                    "INSERT INTO account_pool_credential_owners (fingerprint, environment_id) VALUES (%s, %s) "
                    "ON CONFLICT (fingerprint) DO UPDATE SET fingerprint = EXCLUDED.fingerprint "
                    "WHERE account_pool_credential_owners.environment_id = EXCLUDED.environment_id RETURNING environment_id",
                    (fingerprint, owner),
                )
                if await cursor.fetchone() is None:
                    raise CredentialConflict("该账号或凭证已绑定其他卡片，一张凭证只能由一张卡片使用")

    async def owns(self, owner: UUID, fingerprints: tuple[str, ...]) -> bool:
        if not fingerprints:
            return False
        if self._database_url is None:
            return all(self._owners.get(key) == owner for key in fingerprints)
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT fingerprint FROM account_pool_credential_owners "
                "WHERE environment_id = %s AND fingerprint = ANY(%s)",
                (owner, list(fingerprints)),
            )
            return len(await cursor.fetchall()) == len(set(fingerprints))

    async def retain(self, owner: UUID, fingerprints: tuple[str, ...] = ()) -> None:
        if self._database_url is None:
            self._owners = {key: value for key, value in self._owners.items() if value != owner or key in fingerprints}
            return
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "DELETE FROM account_pool_credential_owners WHERE environment_id = %s AND NOT (fingerprint = ANY(%s))",
                (owner, list(fingerprints)),
            )
