"""本模块从部署主密钥派生每个环境的独立运行密钥，避免在数据库保存明文密钥。"""

from __future__ import annotations

import base64
import hashlib
import hmac
from enum import StrEnum
from typing import Final
from uuid import UUID


class SecretPurpose(StrEnum):
    MANAGEMENT = "management"
    GATEWAY = "gateway"
    OAUTH_STATE = "oauth-state"


class EnvironmentSecretDeriver:
    def __init__(self, seed: str) -> None:
        self._seed: Final = seed.encode("utf-8")

    def derive(self, environment_id: UUID, purpose: SecretPurpose) -> str:
        context: Final = f"litellm-account-pool:v1:{purpose}:{environment_id.hex}".encode("ascii")
        digest: Final = hmac.new(self._seed, context, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
