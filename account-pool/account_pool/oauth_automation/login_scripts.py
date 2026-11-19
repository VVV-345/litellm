"""校验、原子发布并快照浏览器脚本包；Manager 不执行上传的代码。"""

import base64
import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from account_pool.shared.result import Failure, FailureCode, Result, Success

_MAX_BYTES: Final = 65_536
_MAX_PAYLOAD: Final = 120_000


class LoginScriptFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str = Field(min_length=1, max_length=160)
    data: str = Field(max_length=87_384, repr=False)


class LoginScriptPackage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entrypoint: str = Field(min_length=1, max_length=160)
    files: tuple[LoginScriptFile, ...] = Field(min_length=1, max_length=64, repr=False)


class LoginScriptUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    publisher_key: SecretStr = Field(min_length=32, max_length=128)
    package: LoginScriptPackage | None = Field(repr=False)


class LoginScriptInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entrypoint: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    file_count: int


class LoginScriptStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    upload_enabled: bool = False
    active: LoginScriptInfo | None = None
    invalid: bool = False


@dataclass(frozen=True, slots=True)
class PublishedLoginScript:
    payload: str = field(repr=False)
    info: LoginScriptInfo


def _prepare(package: LoginScriptPackage) -> PublishedLoginScript:
    names: Final = frozenset(file.path for file in package.files)
    if len(names) != len(package.files) or package.entrypoint not in names:
        raise ValueError("missing entrypoint or duplicate path")
    if not package.entrypoint.endswith((".mjs", ".js")):
        raise ValueError("entry must be a JavaScript module")
    for name in names:
        parts: Final = name.split("/")
        if (
            not name.isprintable() or "\\" in name or ":" in name
            or any(part in {"", ".", ".."} for part in parts)
            or any("/".join(parts[:i]) in names for i in range(1, len(parts)))
        ):
            raise ValueError("invalid relative path")
    decoded: Final = tuple(base64.b64decode(file.data, validate=True) for file in package.files)
    source: Final = next(data for file, data in zip(package.files, decoded) if file.path == package.entrypoint)
    if not source.decode("utf-8").strip() or sum(map(len, decoded)) > _MAX_BYTES:
        raise ValueError("empty entrypoint or package too large")
    ordered: Final = package.model_copy(update={"files": tuple(sorted(package.files, key=lambda item: item.path))})
    payload: Final = json.dumps(ordered.model_dump(), sort_keys=True, separators=(",", ":"))
    if len(payload) > _MAX_PAYLOAD:
        raise ValueError("package envelope too large")
    return PublishedLoginScript(
        payload, LoginScriptInfo(
            entrypoint=package.entrypoint, file_count=len(names),
            sha256=hashlib.sha256(payload.encode("ascii")).hexdigest(),
        ),
    )


@dataclass(frozen=True, slots=True)
class LoginScriptStore:
    path: Path
    publisher_key: str = field(repr=False)

    @property
    def enabled(self) -> bool:
        return len(self.publisher_key) >= 32

    def read(self) -> Result[PublishedLoginScript | None]:
        if not self.enabled:
            return Success(None)
        try:
            with self.path.open("rb") as source:
                content: Final = source.read(_MAX_PAYLOAD + 1)
            if len(content) > _MAX_PAYLOAD:
                raise ValueError("script package too large")
            return Success(_prepare(LoginScriptPackage.model_validate_json(content)))
        except FileNotFoundError:
            return Success(None)
        except (OSError, ValueError):
            return Failure(FailureCode.CONFLICT, "已发布脚本包无法读取或校验，请重新发布或切回内置脚本")

    def status(self) -> LoginScriptStatus:
        active: Final = self.read()
        return LoginScriptStatus(
            upload_enabled=self.enabled,
            active=active.value.info if isinstance(active, Success) and active.value else None,
            invalid=isinstance(active, Failure),
        )

    def update(self, request: LoginScriptUpdate) -> Result[LoginScriptStatus]:
        if not self.enabled or not hmac.compare_digest(
            request.publisher_key.get_secret_value().encode(), self.publisher_key.encode()
        ):
            return Failure(FailureCode.CONFLICT, "发布密钥未配置或不正确")
        try:
            bundle: Final = None if request.package is None else _prepare(request.package)
        except ValueError:
            return Failure(FailureCode.INVALID, "检查入口、相对路径和脚本包大小；未替换当前脚本")
        try:
            if bundle is None:
                self.path.unlink(missing_ok=True)
            else:
                with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".oauth-login-", delete=False) as out:
                    temporary: Final = Path(out.name)
                    try:
                        out.write(bundle.payload.encode("ascii"))
                        out.close()
                        os.replace(temporary, self.path)
                    finally:
                        temporary.unlink(missing_ok=True)
        except OSError:
            return Failure(FailureCode.UPSTREAM, "脚本包写入失败，请检查 Manager 数据目录")
        return Success(self.status())
