"""加密保存虚拟密钥，列表查询和推理鉴权仍只使用原生密钥哈希。"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final

from pydantic import BaseModel, Field

from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper, encrypt_value_helper


class VirtualKeySecretRequest(BaseModel):
    token: str = Field(pattern=r"^[a-f0-9]{64}$")


class VirtualKeySecretResponse(BaseModel):
    key: str


def seal_virtual_key(token: str) -> str:
    encrypted: Final = encrypt_value_helper(token)
    if encrypted == token:
        raise ValueError("Virtual key encryption failed")
    return encrypted


def open_virtual_key(ciphertext: str, token_hash: str) -> str | None:
    plaintext: Final = decrypt_value_helper(ciphertext, "virtual_key", exception_type="debug")
    if not isinstance(plaintext, str) or not plaintext.startswith("sk-"):
        return None
    return plaintext if hmac.compare_digest(hashlib.sha256(plaintext.encode()).hexdigest(), token_hash) else None
