from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from typing_extensions import Self

from litellm.proxy._types import SpecialModelNames
from litellm.proxy.management_endpoints.account_pool_cockpit_subject import (
    NativeSubjectResult,
    inspect_native_subject,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_HASH: Final = re.compile(r"^[0-9a-f]{64}$")
_NATIVE_MODEL_SENTINELS: Final = frozenset(item.value for item in SpecialModelNames)


class TrustedCredentialScope(BaseModel):
    """仅由注入的可信读取器返回；构造 DTO 本身不是主体或模型权限证明。"""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    subject_hash: Digest
    card_id: UUID
    model_scope: Literal["restricted", "unrestricted"]
    models: tuple[str, ...]

    @model_validator(mode="after")
    def explicit_scope(self) -> Self:
        if (
            (self.model_scope == "restricted") != bool(self.models)
            or len(set(self.models)) != len(self.models)
            or bool(_NATIVE_MODEL_SENTINELS.intersection(self.models))
            or any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model) is None for model in self.models)
        ):
            raise ValueError("Scope must contain an explicit model allowlist or be unrestricted")
        return self


class NativeTrustedSubjectReader:
    _inspect_subject: Final[Callable[[str], Awaitable[NativeSubjectResult]]]

    def __init__(
        self,
        inspect_subject: Callable[[str], Awaitable[NativeSubjectResult]] = inspect_native_subject,
    ) -> None:
        self._inspect_subject = inspect_subject

    async def read(self, subject_hash: str) -> TrustedCredentialScope | None:
        if _HASH.fullmatch(subject_hash) is None:
            return None

        try:
            result: Final[NativeSubjectResult] = await self._inspect_subject(subject_hash)
        except Exception:  # noqa: BLE001  # Injected database readers may fail; never return a verified scope on failure.
            return None
        if result.status != "verified" or result.card_id is None or result.model_scope is None:
            return None
        try:
            return TrustedCredentialScope(
                subject_hash=subject_hash,
                card_id=result.card_id,
                model_scope=result.model_scope.kind,
                models=result.model_scope.models,
            )
        except ValidationError:
            return None
