"""加密环境快照中的直连 ISP 地址，兼容已有不含代理凭据的记录。"""

from typing import Final

from account_pool.config import direct_isp_id
from account_pool.domain import EnvironmentRecord
from account_pool.shared.secrets import StateCipher

_PREFIX: Final = "isp-encrypted:v1:"


def transform_proxy_storage(
    record: EnvironmentRecord, cipher: StateCipher | None, *, encrypt: bool,
) -> EnvironmentRecord:
    def transform(profile_id: str | None, url: str | None) -> str | None:
        if url is None or not url:
            return url
        if not encrypt and url.startswith(_PREFIX):
            if cipher is None:
                raise ValueError("ISP proxy snapshot encryption is not configured")
            return cipher.open(record.id, url.removeprefix(_PREFIX))
        if encrypt and direct_isp_id(profile_id) is not None:
            if cipher is None:
                raise ValueError("ISP proxy snapshot encryption is not configured")
            return _PREFIX + cipher.seal(record.id, url)
        return url

    desired: Final = record.desired_configuration
    compatible: Final = record.openai_compatible
    return record.model_copy(update={
        "desired_configuration": None if desired is None else desired.model_copy(update={
            "proxy_url": transform(desired.proxy_profile_id, desired.proxy_url),
        }),
        "openai_compatible": None if compatible is None else compatible.model_copy(update={
            "credentials": tuple(
                credential.model_copy(update={
                    "proxy_url": transform(credential.proxy_profile_id, credential.proxy_url),
                })
                for credential in compatible.credentials
            ),
        }),
    })
