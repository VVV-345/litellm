"""本模块定义项目镜像备份与确认协议，不包含部署凭据或完整 Compose 内容。"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

ReleaseId: TypeAlias = Annotated[str, Field(pattern=r"^[a-f0-9]{24}$")]
Commit: TypeAlias = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
ImageId: TypeAlias = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]


class ReleaseImage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    service: Literal["litellm", "account-pool"]
    image_id: ImageId
    repository: str
    revision: Commit
    size: int = Field(ge=0)
    digests: tuple[str, ...] = ()


class ReleasePair(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: ReleaseId
    commit: Commit
    images: tuple[ReleaseImage, ReleaseImage]


class ReleaseBackup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    pair: ReleasePair
    created_at: float
    archive_bytes: int
    archive_sha256: str
    compose_sha256: str
    schema_fingerprint: str
    configuration_source: Literal["running", "imported_current"]


class ReleaseVersion(BaseModel):
    pair: ReleasePair
    note: str = ""
    current: bool = False
    backup: ReleaseBackup | None = None
    available: bool = False
    problem: str | None = None


class ReleaseAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["apply", "delete", "note", "guide", "scan", "deploy", "recover"]
    version_id: ReleaseId | None = None
    text: str = Field(default="", max_length=12000)
    tag: Annotated[str, Field(pattern=r"^[a-f0-9]{10,40}$")] | None = None
    revision: int = Field(ge=0)


class ReleaseConfirmation(BaseModel):
    token: str
    action: ReleaseAction
    delay_seconds: int
    expires_in_seconds: int = 300
    current_commit: str | None
    rollback: RollbackInspection | None = None


class RollbackCheck(BaseModel):
    key: str
    title: str
    status: Literal["compatible", "blocked", "unverified"]
    detail: str


class RollbackAlternative(BaseModel):
    version_id: ReleaseId
    commit: Commit
    note: str = ""


class RollbackInspection(BaseModel):
    current_commit: Commit
    target_commit: Commit
    status: Literal["compatible", "blocked", "unverified"]
    checks: tuple[RollbackCheck, ...]
    impacts: tuple[str, ...]
    alternatives: tuple[RollbackAlternative, ...] = ()
    alternatives_checked: int = 0
    alternatives_total: int = 0
    scope: str = "核对镜像中的结构定义、关键读写代码与部署配置，不连接业务数据库执行迁移，也不代表上游调用测试通过。"


class ReleaseExecute(BaseModel):
    token: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ReleaseJob(BaseModel):
    id: str
    action: ReleaseAction
    status: Literal["queued", "running", "succeeded", "failed", "recovered", "interrupted"]
    phase: str
    message: str = ""
    created_at: float
    updated_at: float
    recovery_id: ReleaseId | None = None
    expected_current_id: ReleaseId | None = None
    rollback_state: str | None = None


class ReleaseView(BaseModel):
    current: ReleasePair | None
    versions: tuple[ReleaseVersion, ...]
    guide: str
    default_guide: str
    revision: int
    location: str
    free_bytes: int
    job: ReleaseJob | None = None
    problems: tuple[str, ...] = ()


class ReleaseCommands(BaseModel):
    branch: str
    revert: str
