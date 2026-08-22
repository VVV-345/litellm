"""定义公开元数据任务完成、重试和失败事件的脱敏详情。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from account_pool.models import FrozenModel


class PublicMetadataTaskCompletedDetails(FrozenModel):
    kind: Literal["public_metadata_task_completed"] = "public_metadata_task_completed"
    task_id: UUID
    parser_run_id: UUID
    provider_id: str = Field(min_length=1, max_length=100)
    attempt_count: int = Field(ge=1)


class PublicMetadataTaskRetryScheduledDetails(FrozenModel):
    kind: Literal["public_metadata_task_retry_scheduled"] = "public_metadata_task_retry_scheduled"
    task_id: UUID
    parser_run_id: UUID
    provider_id: str = Field(min_length=1, max_length=100)
    attempt_count: int = Field(ge=1)
    next_attempt_at: AwareDatetime
    failure_code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]{0,99}$")


class PublicMetadataTaskFailedDetails(FrozenModel):
    kind: Literal["public_metadata_task_failed"] = "public_metadata_task_failed"
    task_id: UUID
    parser_run_id: UUID
    provider_id: str = Field(min_length=1, max_length=100)
    attempt_count: int = Field(ge=1)
    failure_code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]{0,99}$")


PublicMetadataOperationalDetails = (
    PublicMetadataTaskCompletedDetails | PublicMetadataTaskRetryScheduledDetails | PublicMetadataTaskFailedDetails
)
