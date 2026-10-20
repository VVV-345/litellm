"""本模块校验用户上传的反馈附件格式、签名和文件名。"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID, uuid4

from account_pool.feedback_models import (
    MAX_FEEDBACK_ATTACHMENT_SIZE,
    FeedbackAttachmentContentType,
    FeedbackAttachmentRecord,
)


@dataclass(frozen=True, slots=True)
class FeedbackAttachmentValidationFailure:
    status: Literal[413, 422]
    code: str
    message: str


def _validated_content_type(extension: str, content: bytes) -> tuple[FeedbackAttachmentContentType, bool] | None:
    match extension:
        case ".png":
            return "image/png", content.startswith(b"\x89PNG\r\n\x1a\n")
        case ".jpg" | ".jpeg":
            return "image/jpeg", content.startswith(b"\xff\xd8\xff")
        case ".webp":
            return "image/webp", len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
        case ".pdf":
            return "application/pdf", content.startswith(b"%PDF-")
        case ".txt" | ".log":
            try:
                content.decode("utf-8-sig")
                return "text/plain", b"\x00" not in content
            except UnicodeDecodeError:
                return "text/plain", False
        case _:
            return None


def create_attachment(
    issue_id: UUID,
    filename: str,
    content: bytes,
) -> FeedbackAttachmentRecord | FeedbackAttachmentValidationFailure:
    if len(content) > MAX_FEEDBACK_ATTACHMENT_SIZE:
        return FeedbackAttachmentValidationFailure(413, "feedback_attachment_too_large", "附件大小不能超过 10 MiB")
    if not content:
        return FeedbackAttachmentValidationFailure(422, "feedback_attachment_invalid", "附件内容无效")
    basename: Final = filename.replace("\\", "/").rsplit("/", 1)[-1]
    safe_name: Final = "".join(char for char in basename if not unicodedata.category(char).startswith("C")).strip(" .")
    suffix: Final = safe_name.rpartition(".")[2].lower()
    extension: Final = "." + suffix if suffix else ""
    validation: Final = _validated_content_type(extension, content)
    if validation is None:
        return FeedbackAttachmentValidationFailure(422, "feedback_attachment_invalid", "不支持此附件格式")
    content_type, valid = validation
    if not valid or len(safe_name) > 255 or not safe_name:
        return FeedbackAttachmentValidationFailure(422, "feedback_attachment_invalid", "附件内容或文件名无效")
    return FeedbackAttachmentRecord(
        id=uuid4(),
        issue_id=issue_id,
        filename=safe_name,
        content_type=content_type,
        size=len(content),
        content=content,
    )
