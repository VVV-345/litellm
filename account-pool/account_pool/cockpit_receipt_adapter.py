"""通过固定内部回调读取 LiteLLM 原生主体，不复制鉴权 SQL 或返回凭据。"""

from __future__ import annotations

from typing import Final, Literal, Self
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictStr, ValidationError, model_validator

from account_pool.cockpit_admin import validate_http_origin
from account_pool.cockpit_credential_receipts import TrustedCredentialScope

_HASH: Final = r"^[0-9a-f]{64}$"


class CockpitSubjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    url: str
    token: SecretStr = Field(min_length=32)

    @model_validator(mode="after")
    def fixed_origin(self) -> CockpitSubjectConfig:
        validate_http_origin(self.url)
        if any(ord(char) <= 32 or ord(char) >= 127 for char in self.token.get_secret_value()):
            raise ValueError("Subject reader token must be printable ASCII")
        return self


class _ResponseScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    subject_hash: str = Field(pattern=_HASH)
    card_id: UUID
    model_scope: Literal["unrestricted", "restricted"]
    models: tuple[StrictStr, ...]

    @model_validator(mode="after")
    def explicit_scope(self) -> Self:
        if (self.model_scope == "restricted") != bool(self.models):
            raise ValueError("Invalid subject scope")
        return self


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    subject_hash: str = Field(pattern=_HASH)
    scope: _ResponseScope | None


class CockpitSubjectClient:
    def __init__(
        self,
        config: CockpitSubjectConfig,
        client: httpx.AsyncClient,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Subject reader timeout must be between 0 and 30 seconds")
        self._config = config
        self._client = client
        self._timeout = timeout_seconds

    async def read(self, subject_hash: str) -> TrustedCredentialScope | None:
        if len(subject_hash) != 64 or any(char not in "0123456789abcdef" for char in subject_hash):
            return None
        try:
            response = await self._client.post(
                self._config.url.rstrip("/") + "/account_pool/internal/cockpit/native-subject",
                json={"subject_hash": subject_hash},
                headers={"Authorization": "Bearer " + self._config.token.get_secret_value()},
                follow_redirects=False,
                timeout=self._timeout,
            )
            if response.status_code != 200:
                return None
            result = _Response.model_validate_json(response.content)
            if result.subject_hash != subject_hash or result.scope is None:
                return None
            if result.scope.subject_hash != subject_hash:
                return None
            return TrustedCredentialScope(
                subject_hash=subject_hash,
                card_id=result.scope.card_id,
                model_scope=result.scope.model_scope,
                models=result.scope.models,
            )
        except (httpx.HTTPError, ValidationError, ValueError):
            return None
