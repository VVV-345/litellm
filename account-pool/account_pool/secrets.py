"""本模块从部署主密钥派生每个环境的独立运行密钥，避免在数据库保存明文密钥。"""

from __future__ import annotations

import base64
import hashlib
import hmac
from enum import StrEnum
from typing import Final
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken


class SecretPurpose(StrEnum):
    MANAGEMENT = "management"
    GATEWAY = "gateway"
    OAUTH_STATE = "oauth-state"
    AUTHORIZATION_STATE = "authorization-state"


class EnvironmentSecretDeriver:
    def __init__(self, seed: str) -> None:
        self._seed: Final = seed.encode("utf-8")

    def derive(self, environment_id: UUID, purpose: SecretPurpose) -> str:
        context: Final = f"litellm-account-pool:v1:{purpose}:{environment_id.hex}".encode("ascii")
        digest: Final = hmac.new(self._seed, context, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class StateCipher:
    """对落库的授权操作凭据做环境绑定加密，数据库只见密文。"""

    def __init__(self, secrets: EnvironmentSecretDeriver) -> None:
        self._secrets: Final = secrets

    def _fernet(self, environment_id: UUID) -> Fernet:
        # derive() 去掉了 base64 填充，Fernet 需要标准 44 字符 key，这里补回。
        key: Final = self._secrets.derive(environment_id, SecretPurpose.AUTHORIZATION_STATE)
        return Fernet(key + "=" * (-len(key) % 4))

    def seal(self, environment_id: UUID, plaintext: str) -> str:
        return self._fernet(environment_id).encrypt(plaintext.encode("utf-8")).decode("ascii")

    def open(self, environment_id: UUID, ciphertext: str) -> str:
        try:
            return self._fernet(environment_id).decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as error:
            raise ValueError("authorization state cannot be decrypted") from error
