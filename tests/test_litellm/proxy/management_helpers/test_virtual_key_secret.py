"""验证密钥密文不能被当作其他密钥使用，并且明文不会写入存储字段。"""

import hashlib

from litellm.proxy.management_helpers.virtual_key_secret import open_virtual_key, seal_virtual_key


def test_secret_is_encrypted_randomly_and_bound_to_the_original_hash(monkeypatch):
    monkeypatch.setenv("LITELLM_SALT_KEY", "test-encryption-key-only")
    key = "sk-test-virtual-key-secret"
    ciphertext = seal_virtual_key(key)
    assert key not in ciphertext
    assert ciphertext != seal_virtual_key(key)
    assert open_virtual_key(ciphertext, hashlib.sha256(key.encode()).hexdigest()) == key
    assert open_virtual_key(ciphertext, "0" * 64) is None
    monkeypatch.setenv("LITELLM_SALT_KEY", "other-encryption-key")
    assert open_virtual_key(ciphertext, hashlib.sha256(key.encode()).hexdigest()) is None
